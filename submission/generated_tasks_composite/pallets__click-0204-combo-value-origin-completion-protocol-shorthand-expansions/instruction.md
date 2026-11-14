# Feature bundle: Show each option's effective value and its origin in help pages; Add a `line` shell completion protocol driven by `COMP_LINE` and `COMP_POINT`; Option shorthands: declarative token expansions resolved by the parser

## Overview

This change request bundles 3 improvements to click that must land together in a single change. Implement all of them. Each feature section below is a complete specification of that feature. All features must work in the same build without interfering with each other, and any existing behaviour that a section does not mention stays exactly as it is.

## Feature 1: Show each option's effective value and its origin in help pages


### Background

When a CLI reads its configuration from environment variables, a `default_map` (for example loaded from a config file), and parameter defaults, users cannot tell from `--help` what value an option will actually get if they leave it out. `show_default` only shows the declared default, and `show_envvar` only shows variable *names*. This feature adds an opt-in help annotation that shows the value an omitted option would take and where it comes from. It also adds a public method that computes this without parsing.

### Public interface

- `click.Option(..., show_effective: bool | None = None)`. This is a new keyword parameter, stored as the attribute `Option.show_effective`. `@click.option(..., show_effective=...)` forwards it as usual.
- `click.Context(..., show_effective: bool | None = None)`. This is a new keyword parameter, added after all existing parameters and stored as the attribute `Context.show_effective`. It can also be set with `context_settings={"show_effective": True}`.
- `Option.get_effective_value(self, ctx: Context) -> tuple[Any, ParameterSource] | None`. This is a new public method.
- `Option.get_help_extra(ctx)` may return a new key `"current"` (a `str`). `click.types.OptionHelpExtra` gains the optional key `current: str`.

### Behaviour

1. **Enabling.** If `Option.show_effective` is not `None`, it decides whether the option shows the annotation. Otherwise `Context.show_effective` decides. When `Context.show_effective` is `None` at construction, it takes the parent context's value, and it is falsy at the root. The annotation is shown only when the resolved setting is `True`.

2. **`get_effective_value(ctx)`** returns the value the option would receive if it were not given on the command line, together with its source. It checks the following steps in order and stops at the first that applies:
   1. **Environment.** The first non-empty variable named in `envvar` (a string, or a sequence checked in order) supplies the value. If none does, and `allow_from_autoenv` is true, `ctx.auto_envvar_prefix` is not `None` and the option has a name, the variable `f"{ctx.auto_envvar_prefix}_{name.upper()}"` is checked. If one of these is set and non-empty, the method returns `(raw_string, ParameterSource.ENVIRONMENT)`. The string is not split and not type converted.
   2. **Prompt.** If `prompt` is set and either `required` or `prompt_required` is true, the method returns `(None, ParameterSource.PROMPT)`.
   3. **default_map.** If `ctx.default_map` contains the option's name with a value other than Click's internal unset sentinel, the method returns `(value, ParameterSource.DEFAULT_MAP)`. The value is returned as stored, and callables are not called.
   4. **Default.** Otherwise it takes `value = self.get_default(ctx, call=False)`. This includes the auto-derived defaults, such as `False` for a non-required boolean flag and `()` for a multiple flag. If that value is Click's internal unset sentinel (no default at all), the method returns `None`. Otherwise it returns `(value, ParameterSource.DEFAULT)`. A `default=None` therefore yields `(None, ParameterSource.DEFAULT)`.

   The method works whatever `show_effective` is set to, and it ignores `ctx.resilient_parsing`. It never calls callables, performs type conversion, runs callbacks, or prompts.

3. **Help item.** When the option shows the annotation and the item is not omitted (rule 5), `get_help_extra` sets `extra["current"]` as follows:
   - For `PROMPT`: `(prompt)`.
   - Otherwise: `<rendered value> (<origin>)`. The origin is `env var NAME` (where `NAME` is the variable that actually supplied the value), `default map`, or `default`.

   `get_help_record` renders this as `current: <extra["current"]>`. Items inside the brackets appear in this order, separated by `; `: env var, default, **current**, range, required. Examples: `[current: 8000 (default)]` and `[env var: APP_PORT; current: 9000 (env var APP_PORT); required]`.

4. **Value rendering** (not applied to `PROMPT`):
   - A value for which `inspect.isfunction` is true is rendered as `(dynamic)`.
   - An `enum.Enum` member is rendered as its `.name`.
   - A `bool` value of a boolean flag that has secondary options, with source `DEFAULT` or `DEFAULT_MAP`, is rendered as the option name without its prefix. That is the first entry of `opts` if the value is true, and of `secondary_opts` otherwise. For example, `--color/--no-color` gives `color` or `no-color`.
   - A `list` or `tuple` has its elements rendered with `str()` and joined with `, `. An element that is itself a list or tuple has its items `str()`-ed and joined with a single space. For example, `[(1, 2), (3, 4)]` becomes `1 2, 3 4`.
   - The empty string is rendered as `""`.
   - Anything else is rendered with `str()`. This includes environment strings, which are shown exactly as set, so `"a b"` stays `a b`.
   - If the option has `hide_input=True`, the rendered value is replaced by `***`.

5. **Omission.** No `"current"` key is produced, and no item is shown, when any of these holds:
   - `get_effective_value` returns `None`.
   - The value is `None`.
   - The value is an empty list or tuple.
   - The option is a boolean flag without secondary options, the source is `DEFAULT`, and the value is `False`. This means the automatic `--help` option never shows an item.

### Interaction with existing features

6. **Environment variables.** An environment value always outranks a prompt, the `default_map` and the default, which matches how Click resolves values at runtime. Empty variables are ignored. The auto variable is used only when `allow_from_autoenv` is true. `show_envvar` has no influence on the current item: the item names the supplying variable even when `show_envvar` is false, and the existing `env var:` item is still controlled only by `show_envvar`.

7. **default_map and defaults.** The context's own `default_map` is used. For a subcommand, that is the entry of the parent's `default_map` named after the subcommand. A `default_map` entry whose value is `None` yields `(None, ParameterSource.DEFAULT_MAP)`, so no item is shown and the parameter default is not consulted. Callable `default_map` values and callable defaults are shown as `(dynamic)` (when they are functions) and are never called.

8. **Prompts.** An option with `prompt` set, `prompt_required=True` (the default) or `required=True`, and no environment value shows `current: (prompt)`, whatever its `default` or `default_map` value. An option with `prompt_required=False` and `required=False` skips the prompt step and continues with `default_map` and the default.

9. **show_default conflict.** If the current item is shown with origin `default` or `default map`, the `default:` item is removed from both `get_help_extra` and the record. This applies even when `show_default` is a custom string. With origin `env var NAME` or `(prompt)`, the `default:` item (if any) is kept, followed by the current item. The `range` and `required` items are unaffected.

### Examples

```python
@click.command(context_settings={"show_effective": True})
@click.option("--port", default=8000, envvar="APP_PORT", help="Port to bind.")
@click.option("--host", help="Host name.")
@click.option("--tag", multiple=True, default=["a", "b"], help="Tags.")
def serve(port, host, tag): ...
```

`serve --help`:

```
Usage: serve [OPTIONS]

Options:
  --port INTEGER  Port to bind.  [current: 8000 (default)]
  --host TEXT     Host name.
  --tag TEXT      Tags.  [current: a, b (default)]
  --help          Show this message and exit.
```

With `APP_PORT=9000`, the first option line becomes `  --port INTEGER  Port to bind.  [current: 9000 (env var APP_PORT)]`.

```python
@click.command(context_settings={"show_effective": True, "show_default": True})
@click.option("--level", default="info")
@click.option("--user", prompt=True)
@click.option("--token", envvar="TOKEN", hide_input=True)
def cli(level, user, token): ...

runner.invoke(cli, ["--help"], env={"TOKEN": "secret"}, default_map={"level": "debug"})
```

```
Usage: cli [OPTIONS]

Options:
  --level TEXT  [current: debug (default map)]
  --user TEXT   [current: (prompt)]
  --token TEXT  [current: *** (env var TOKEN)]
  --help        Show this message and exit.
```

```python
@click.group(context_settings={"show_effective": True, "auto_envvar_prefix": "APP"})
def cli(): ...

@cli.command()
@click.option("--verbose/--quiet", default=False)
@click.option("--count", type=int, default=3, show_effective=False)
def run(verbose, count): ...
```

`APP_RUN_VERBOSE=1 cli run --help`:

```
Usage: cli run [OPTIONS]

Options:
  --verbose / --quiet  [current: 1 (env var APP_RUN_VERBOSE)]
  --count INTEGER
  --help               Show this message and exit.
```

Without the variable, the first option line is `  --verbose / --quiet  [current: quiet (default)]`.

### Compatibility

- When `show_effective` is not enabled anywhere, help output and `get_help_extra()` results are exactly as before.
- Parsing, value resolution, `ParameterSource` values recorded during invocation, prompts, and callbacks are unchanged. Computing the annotation never prompts, never calls callables, and never fails on values that would not convert.
- `Option.to_info_dict()` and `Context.to_info_dict()` are unchanged and do not include the new setting.
- Existing constructor signatures change only by the new optional keyword parameters described above.

## Feature 2: Add a `line` shell completion protocol driven by `COMP_LINE` and `COMP_POINT`


### Background

Click's built-in completion scripts (bash, zsh, fish, powershell) all use the `COMP_WORDS`/`COMP_CWORD` protocol. In that protocol a shell function splits the command line before calling the program.

Bash has a simpler mechanism, `complete -C command`. Bash runs `command` with the raw line in `COMP_LINE` and the cursor offset in `COMP_POINT`, then reads one candidate per line from stdout. It needs no shell function, works under zsh's `bashcompinit`, and is trivial to drive from editors and other tools. Click cannot serve it today, because it would have to tokenise the raw line itself and decide which word the cursor is on.

A completion class also has no way to report a malformed request. Problems either crash with a traceback or exit with status 1 without any message.

### Public interface

Both names live in `click.shell_completion` and are not re-exported from `click`.

- `class CompletionRequestError(click.ClickException)`, constructed as `CompletionRequestError(message: str)`. Its `exit_code` is `1`. A completion class raises it when the completion request in the environment is malformed.
- `class LineComplete(ShellComplete)`. Its constructor signature is the same as `ShellComplete`.
  - `name = "line"`
  - `source_template` is exactly:
    `complete -o default -o nosort -C '%(complete_var)s=line_complete %(prog_name)s' %(prog_name)s` followed by a single `\n`.

`LineComplete` is registered like the built-in shells, so `click.shell_completion.get_completion_class("line")` returns `LineComplete`.

### Behaviour

The following rules apply when the completion variable is set to `line_complete`.

1. **`COMP_LINE` is required.** If `COMP_LINE` is not present in the environment, the request fails (see rule 6) with the message `COMP_LINE is not set.` An empty string is a valid line. This check happens before `COMP_POINT` is examined.

2. **`COMP_POINT` sets the cursor position.**
   - If `COMP_POINT` is unset or the empty string, the cursor is at the end of the line, `len(COMP_LINE)`.
   - Otherwise it must consist solely of ASCII digits `0`-`9`. Leading zeros are allowed. A sign, whitespace or any other character is not allowed.
   - An invalid value fails with `Invalid COMP_POINT value {value!r}: expected a non-negative integer.`, where `{value!r}` is the Python `repr` of the raw string. Example: `Invalid COMP_POINT value '-1': expected a non-negative integer.`
   - A valid integer greater than `len(COMP_LINE)` fails with `COMP_POINT {point} is past the end of COMP_LINE ({length} characters).` The word `character` is used instead of `characters` when the length is exactly 1.
     - `{point}` is the integer value without leading zeros. Example: `COMP_POINT 12 is past the end of COMP_LINE (7 characters).`
     - A point equal to the length is valid.
   - Offsets count characters of the decoded string, and only `COMP_LINE[:point]` is considered. Everything after the cursor is ignored.

3. **Tokenisation and the incomplete word.**
   - The text before the cursor is split into words with the same rules as `click.shell_completion.split_arg_string`:
     - POSIX single quotes, double quotes and backslash escapes;
     - no comment handling;
     - an unterminated quote or a trailing backslash yields the partial word as-is.
   - Whitespace means space, tab, carriage return and line feed.
   - If the text before the cursor is empty, or its last character is whitespace that is neither inside quotes nor escaped by a backslash, the incomplete word is `""` and every word is complete.
   - Otherwise the last word, after quote removal, is the incomplete word and the words before it are complete.

   Examples (text before the cursor → complete words / incomplete word):
   - `tool pa` → `[tool]` / `pa`
   - `tool pa ` → `[tool, pa]` / `""`
   - `tool 'my ` → `[tool]` / `my ` (the space is inside an unterminated quote)
   - `tool my\ ` → `[tool]` / `my ` (the space is escaped)
   - `tool ''` → `[tool]` / `""` (an empty quoted word)
   - `tool "a b"` → `[tool]` / `a b`

4. **The program word.**
   - The first complete word is the program name. It is discarded and is not compared with the program's name, so `./tool` or a full path works too.
   - If there are no complete words (for example the text before the cursor is `""`, `"  "` or `"to"`), no completions are produced: stdout is empty and the exit status is 0.
   - Otherwise the remaining complete words are the completion args. They are resolved together with the incomplete word exactly like the other shells do.

5. **Output format.**
   - Completion items are processed in the order Click produces them.
   - An item whose `type` is `"plain"` is written as `str(item.value)` followed by `\n`. The value is written verbatim, with no quoting or escaping, and the item's help is never written.
   - Items of any other type are omitted.
   - Plain items whose value contains `\n` or `\r` are omitted.
   - If no line is written, stdout is completely empty, with no blank line.
   - The exit status is 0.

6. **Error reporting.** This applies whenever a `CompletionRequestError` is raised while Click handles the `complete` or `source` instruction of any completion class, including custom `ShellComplete` subclasses:
   - Click writes `Error: {message}` followed by a newline to stderr, where `{message}` is the exception's message.
   - Click writes nothing to stdout.
   - The process exits with status 1.

   Exceptions of any other type propagate exactly as they do today.

### Interaction with existing features

7. **Activation and the completion variable.**
   - The protocol uses the same completion variable as every other shell: `_{PROG_NAME}_COMPLETE` (upper-cased, with `-` and `.` replaced by `_`), or the `complete_var` passed to `Command.main`.
   - The value `line_complete` runs rules 1-6.
   - The value `line_source` prints `source_template` with `complete_var` and `prog_name` substituted, then exits with status 0. It performs no Bash version check and does not read `COMP_LINE` or `COMP_POINT`.
   - Any other `line_*` instruction prints nothing and exits with status 1, as for the other shells.
   - Command-line arguments given to the program are ignored while completing. Bash appends the command name, the current word and the previous word.

8. **Resolution is shared with the other shells.** The complete words and the incomplete word go through the same context and parameter resolution as the `COMP_WORDS`-based shells. In particular:
   - hidden commands and hidden options are never offered;
   - an incomplete word of the form `--opt=val` completes the value of `--opt` with incomplete value `val`, and only the bare value is printed (for example `red`, not `--color=red`);
   - a `--` among the complete words stops option-name completion;
   - a non-multiple option that already appears among the complete words is not offered again;
   - chained groups keep offering sibling commands;
   - extra keyword arguments given to `main` are forwarded to context creation as for other shells.

9. **Item types.**
   - Completions from `click.Path` and `click.File` have type `file` or `dir`, so they are omitted by rule 5. Bash's `-o default` filename fallback (set in the source line) then takes over.
   - Plain strings returned by a custom `shell_complete` callback become `plain` items and are printed.
   - A `CompletionItem` with a non-string value is printed as `str(value)`.
   - Registering a class under the name `"line"` with `add_completion_class` replaces `LineComplete`, as it would for any other shell.

### Examples

Given this program, installed as `tool`:

```python
import click

@click.group()
def cli():
    pass

@cli.command()
@click.option("--color", type=click.Choice(["red", "green", "rose"]))
@click.argument("path", type=click.Path())
def paint(color, path):
    pass

@cli.command()
def status():
    pass

@cli.command(hidden=True)
def secret():
    pass
```

Completing subcommands at the end of the line. The hidden `secret` command is not offered:

```
$ COMP_LINE='tool ' COMP_POINT=5 _TOOL_COMPLETE=line_complete tool
paint
status
```

Completing an option value written with `=`, and completing in the middle of the line:

```
$ COMP_LINE='tool paint --color=r' _TOOL_COMPLETE=line_complete tool
red
rose
$ COMP_LINE='tool st --help' COMP_POINT=7 _TOOL_COMPLETE=line_complete tool
status
$ COMP_LINE='tool paint --color red --' _TOOL_COMPLETE=line_complete tool
--help
```

A path argument produces only `file` items, so stdout is empty. A malformed request is reported on stderr. The source line is for `~/.bashrc`:

```
$ COMP_LINE="tool paint 'my " _TOOL_COMPLETE=line_complete tool
$ COMP_LINE='tool st' COMP_POINT=-1 _TOOL_COMPLETE=line_complete tool
Error: Invalid COMP_POINT value '-1': expected a non-negative integer.
$ echo $?
1
$ _TOOL_COMPLETE=line_source tool
complete -o default -o nosort -C '_TOOL_COMPLETE=line_complete tool' tool
```

### Compatibility

- The `bash`, `zsh`, `fish` and `powershell` completion classes, their source scripts and their output formats are unchanged. This includes the trailing newline they print even when there are no completions.
- `shell_complete` still returns 1 silently for unknown shells and unknown instructions. Only `CompletionRequestError` gets the new stderr handling.
- `split_arg_string`, `CompletionItem`, `ShellComplete`, `add_completion_class` and `get_completion_class` keep their existing signatures and behaviour.
- Programs that never set the completion variable to a `line_*` value see no difference.

## Feature 3: Option shorthands: declarative token expansions resolved by the parser


### Background

Many CLIs want one switch that stands for a bundle of other options, such as `-q` meaning `--log-level error --no-color`, or `-D` meaning `--profile prod deploy`. Today there are two workarounds, and both are wrong in important ways:

- An option callback that mutates other parameters bypasses parsing, ordering and `ParameterSource`.
- Overriding `parse_args` to rewrite `argv` blindly also rewrites option *values* and tokens after `--`.

Click should support shorthands as a parser feature. The option parser replaces a shorthand token by its expansion at the exact point where it would otherwise process an option. Everything downstream then behaves as if the user had typed the expansion.

### Public interface

Both new names are importable from `click` (and from `click.core` / `click.decorators` respectively).

```python
class Shorthand:
    def __init__(
        self,
        name: str,
        expansion: collections.abc.Sequence[str],
        help: str | None = None,
        hidden: bool = False,
    ) -> None: ...

    name: str                    # as given
    expansion: tuple[str, ...]   # tuple(expansion)
    help: str | None             # stored unchanged
    hidden: bool

    def to_info_dict(self) -> dict[str, typing.Any]: ...
    def __repr__(self) -> str: ...   # "<Shorthand -q>"


def shorthand(
    name: str, *expansion: str, help: str | None = None, hidden: bool = False
) -> Callable[[FC], FC]: ...
```

- `Shorthand.to_info_dict()` returns exactly `{"name": name, "expansion": list(expansion), "help": help, "hidden": hidden}`.
- `click.shorthand(...)` builds `Shorthand(name, expansion, help=help, hidden=hidden)` immediately, so validation errors are raised when the decorator is called. It then attaches the shorthand to the decorated function or command, the same way `@click.option` attaches options.
- `click.Command.__init__` gains a new trailing keyword parameter `shorthands: Sequence[Shorthand] | None = None`, stored as the public attribute `Command.shorthands: list[Shorthand]` (empty list when not given).
  - `Group` and `CommandCollection` accept it through their keyword arguments.
  - `@click.command(shorthands=[...])` and `@click.group(shorthands=[...])` forward it.
- New method `Command.format_shorthands(self, ctx: Context, formatter: HelpFormatter) -> None` writes the `Shorthands` help section (see Interaction rule 5).

### Behaviour

In the messages below, `{name!r}` stands for Python `repr()` of the value.

1. **Validation.** `Shorthand` validates its arguments when constructed, in this order:
   1. The name must be a `str`. It must start with a non-alphanumeric prefix, which is one character or two identical characters (as for options: `-q`, `--quiet`, `+q`, `/q`). At least one character must follow the prefix. It must contain no `=` and no whitespace. Otherwise raise `ValueError` with `Invalid shorthand name {name!r}.`, for example `Invalid shorthand name 'q'.`, `Invalid shorthand name '--'.` or `Invalid shorthand name '-'.`
   2. If `expansion` is a single `str`, raise `TypeError` with `Shorthand expansion must be a sequence of strings, not a single string.`
   3. If any expansion token is not a `str`, raise `TypeError` with `Shorthand expansion tokens must be strings.`
   4. If the expansion is empty, raise `ValueError` with `Shorthand {name!r} must expand to at least one token.`

2. **Registration and order.**
   - Every item of `Command(shorthands=...)` must be a `Shorthand`; otherwise `Command.__init__` raises `TypeError` with `'shorthands' must contain only Shorthand instances.`
   - With the decorators, `Command.shorthands` contains any explicitly passed `shorthands=` list first, then decorated shorthands in top-to-bottom source order. This mirrors how `@click.option` orders parameters.
   - `@click.shorthand` applied on top of an already created `Command` appends to its `shorthands` list.
   - This order is used for help output and `to_info_dict`.

3. **Where shorthands are recognised.** Only a token that the parser examines as the start of a possible option is compared against shorthand names. Such a token:
   - begins with a recognised option prefix and is longer than one character;
   - is not being consumed as the value of a preceding option (in `--name -q`, the `-q` is the value of `--name` and is not expanded);
   - comes before `--`;
   - comes before option processing stops. With `allow_interspersed_args=False`, which groups use, processing stops at the first non-option token.

   The comparison is against the whole token, so clusters such as `-vq` and tokens with an attached value such as `-q=x` never match. They are processed exactly as today. Each shorthand's prefix is added to the parser's recognised option prefixes, so `+q` works even if no option uses `+`.

4. **Expansion.**
   - A matched token is removed and its expansion tokens are inserted in its place. They are then processed exactly as if the user had typed them there.
   - Expansion tokens may be options, option values, positional tokens, or `--`.
   - An expansion may end with an option that takes a value. That value is then taken from the token following the shorthand: with `Shorthand("-L", ["--log-level"])`, `-L debug` sets `log_level` to `debug`.
   - Errors caused by expanded tokens are reported exactly as for typed tokens.
   - Tokens produced by an expansion are never expanded again by the same command's parser, even if they equal a shorthand name.

5. **Conflicts.** Conflicts are checked every time the command builds its parser (`Command.make_parser`), i.e. on every parse, including `--help` and resilient parsing.
   - If a shorthand's normalised name equals the normalised form of any name in `opts` or `secondary_opts` of a parameter returned by `get_params(ctx)`, raise `ValueError` with `Shorthand {name!r} conflicts with an option of the same name.`
   - If two shorthands of the same command have equal normalised names, raise `ValueError` with `Shorthand {name!r} is defined more than once.`, naming the later one.
   - `{name!r}` is the name as declared. These are not `ClickException`s and propagate out of `main()`.

6. **Subcommands.** Tokens that a group leaves for a subcommand reach the subcommand as ordinary tokens. This includes tokens that came from the group's own expansion. The subcommand applies its own shorthands to them.

### Interaction with existing features

1. **Value sources.**
   - Values supplied through an expansion have `ParameterSource.COMMANDLINE`.
   - They take precedence over environment variables, `default_map` and defaults.
   - They suppress prompts for that option.
   - They emit deprecation warnings for deprecated parameters, exactly like typed values. For example, if an option `--old` is declared with `deprecated=True` and a shorthand expands to `--old x`, using the shorthand writes `DeprecationWarning: The option 'old' is deprecated.` to standard error, the same line Click already writes when `--old x` is typed.
   - When the shorthand is not used, all of these behave unchanged.
2. **multiple / count / single values.** All tokens, typed and expanded, are processed in their final order.
   - A `multiple=True` option accumulates values from expansions and typed occurrences in that order.
   - A `count=True` option counts every occurrence.
   - For single-value options the last occurrence wins, so `-q --log-level debug` yields `debug`.
3. **Automatic help option.** A name in `Context.help_option_names` that equals (as written) the name of one of the command's shorthands is dropped from the automatic help option, exactly as if an option declared that name. Remaining names keep their order. With `help_option_names=["-h", "--help"]` and a shorthand `-h`, only `--help` shows help.
4. **Token normalisation.** When `Context.token_normalize_func` is set, it is applied to both the shorthand names and the incoming tokens before comparison. As for options, it is applied to the part after the prefix. For example, with `str.lower`, `-Q` matches a shorthand named `-q`.
5. **Help page.** Visible (non-hidden) shorthands are listed in a section titled `Shorthands`, written with `formatter.write_dl`.
   - The section comes after the `Options` section and, for groups, before the `Commands` section.
   - Each row's term is the shorthand name.
   - The description is `{help}  [expands to: {tokens}]` (two spaces) or `[expands to: {tokens}]` when `help` is `None` or empty, where `{tokens}` is `shlex.join(expansion)`. Tokens that need quoting are therefore quoted: an expansion `["--profile", "my env"]` without help is described as `[expands to: --profile 'my env']`.
   - If no visible shorthand exists, no section is written.
   - The usage line is unchanged.
6. **Introspection.** `Command.to_info_dict(ctx)` (and therefore `Context.to_info_dict()`) gains a key `"shorthands"`, a list of `Shorthand.to_info_dict()` in `Command.shorthands` order. The key is present only when the command has at least one shorthand.

### Examples

```python
@click.command()
@click.option("--log-level", default="info")
@click.option("--color/--no-color", default=True)
@click.shorthand("-q", "--log-level", "error", "--no-color", help="Quiet mode.")
def cli(log_level, color):
    click.echo(f"log_level={log_level} color={color}")
```

```
$ cli -q
log_level=error color=False
$ cli -q --log-level debug
log_level=debug color=False
$ cli --log-level -q
log_level=-q color=True
$ cli -- -q
Usage: cli [OPTIONS]
Try 'cli --help' for help.

Error: Got unexpected extra argument (-q)
$ cli --help
Usage: cli [OPTIONS]

Options:
  --log-level TEXT
  --color / --no-color
  --help                Show this message and exit.

Shorthands:
  -q  Quiet mode.  [expands to: --log-level error --no-color]
```

Groups and subcommands:

```python
@click.group()
@click.option("--profile", default="dev")
@click.shorthand("-D", "--profile", "prod", "deploy")
def tool(profile):
    click.echo(f"profile={profile}")

@tool.command()
@click.option("--dry-run", is_flag=True)
@click.shorthand("-n", "--dry-run")
def deploy(dry_run):
    click.echo(f"dry_run={dry_run}")
```

```
$ tool -D
profile=prod
dry_run=False
$ tool -D -n
profile=prod
dry_run=True
```

Help option names and hidden shorthands:

```python
@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--host", default="localhost")
@click.shorthand("-h", "--host", "0.0.0.0", help="Listen on all interfaces.")
@click.shorthand("--debug-all", "--host", "127.0.0.1", hidden=True)
def serve(host):
    click.echo(f"host={host}")
```

```
$ serve -h
host=0.0.0.0
$ serve --debug-all
host=127.0.0.1
$ serve --help
Usage: serve [OPTIONS]

Options:
  --host TEXT
  --help       Show this message and exit.

Shorthands:
  -h  Listen on all interfaces.  [expands to: --host 0.0.0.0]
```

### Compatibility

- Commands without shorthands parse, render help, compute help option names, and produce `to_info_dict` output exactly as before. In particular, no `"shorthands"` key and no `Shorthands` section appear.
- Existing signatures are unchanged apart from the new optional trailing `shorthands` keyword of `Command`.
- Option clustering, `--`, `=`-attached values, `ignore_unknown_options`, `allow_interspersed_args` and existing error messages behave as today for every token that is not a recognised shorthand.
- No new warnings are emitted.
