# Feature bundle: Add before/after subcommand lifecycle hooks to `Group`; Show each option's effective value and its origin in help pages; Add a `line` shell completion protocol driven by `COMP_LINE` and `COMP_POINT`

## Overview

This change request bundles 3 improvements to click that must land together in a single change. Implement all of them. Each feature section below is a complete specification of that feature. All features must work in the same build without interfering with each other, and any existing behaviour that a section does not mention stays exactly as it is.

## Feature 1: Add before/after subcommand lifecycle hooks to `Group`


### Background

A group's callback runs before Click even parses which subcommand comes next. `result_callback` runs once, after every subcommand has finished. There is no place to run code around *each* subcommand with access to that subcommand's parsed `Context`. Examples are validating a subcommand against group-level options, logging each step of a chained pipeline, or timing every command. Users currently override `Group.invoke` and copy its internals.

This issue adds per-subcommand lifecycle hooks to `click.Group`.

### Public interface

All names live on `click.Group` (importable as `click.Group`, and used by `@click.group()`):

```python
class Group(Command):
    def __init__(
        self,
        name=None,
        commands=None,
        invoke_without_command=False,
        no_args_is_help=None,
        subcommand_metavar=None,
        chain=False,
        result_callback=None,
        before_subcommand: Callable[[Context], Any] | None = None,
        after_subcommand: Callable[[Context, Any], Any] | None = None,
        **kwargs,
    ) -> None: ...

    def before_subcommand(self, f: F) -> F: ...
    def after_subcommand(self, f: F) -> F: ...
```

- The two new constructor parameters are keyword parameters with default `None`. They are also accepted by `@click.group(...)` and `Group.group(...)`, because those forward keyword arguments to the class.
- `before_subcommand(f)` and `after_subcommand(f)` register `f` and return `f` unchanged. They take the function directly, so use them as `@cli.before_subcommand`, without parentheses.
- `CommandCollection` inherits both the parameters and the methods.

### Behaviour

1. **Registration.** Any number of hooks may be registered on a group, at any time before it is invoked. Registering the same kind of hook again adds another hook; it does not replace the previous one. A hook passed as the `before_subcommand=` / `after_subcommand=` constructor keyword is registered first, before any hook added with the methods.
2. **Before hooks** are called as `hook(sub_ctx)`.
   - `sub_ctx` is the `Context` created for the subcommand that is about to run. Its parameters have already been fully parsed and processed, including type conversion, parameter callbacks and prompts.
   - The call happens immediately before the subcommand's `Command.invoke`, so before the subcommand's deprecation message (if any) and before its callback.
   - During the call, `click.get_current_context()` returns `sub_ctx`. This is the same object the subcommand's callback receives through `@click.pass_context`.
   - The return value of a before hook is ignored.
3. **After hooks** are called as `hook(sub_ctx, result)`.
   - `result` is the value returned by the subcommand's `Command.invoke`.
   - They are called only when that invoke returns normally. During the call, `click.get_current_context()` returns `sub_ctx`.
   - The return value of an after hook is ignored. The value passed to `result_callback`, collected into the chain-mode result list, or returned by `main(standalone_mode=False)` is always the original `result`.
4. **Ordering.** Before hooks run in registration order. After hooks run in **reverse** registration order, like a teardown stack.
5. **Errors.**
   - If a before hook raises, the remaining before hooks do not run, the subcommand is not invoked, and no after hooks run.
   - If the subcommand raises any exception, no after hooks run. This includes `click.exceptions.Exit` raised by `ctx.exit()`, `click.Abort`, and `click.ClickException`.
   - If an after hook raises, the remaining after hooks do not run and the group's `result_callback` is not called.
   - Exceptions from hooks propagate exactly like exceptions from the subcommand callback. In standalone mode, a `ClickException` is shown and the program exits with its exit code.
   - A `click.UsageError` (or subclass) raised by a hook with `ctx` set to `None` gets `sub_ctx` attached before it propagates. The usage line and help hint shown are therefore the subcommand's.
6. **Chain mode** (`chain=True`):
   - The group callback runs first.
   - Next, the contexts of *all* subcommands on the command line are created, as Click does today.
   - Then, for each subcommand in command-line order, its before hooks run, then the subcommand, then its after hooks. Only after that does Click move to the next subcommand.
   - `result_callback` runs last and receives the list of the subcommands' original return values.
7. **Scope.** Hooks fire only for the **direct** subcommands of the group they are registered on. They are not inherited by nested groups.
   - When a direct subcommand is itself a group, the outer group's before hooks run before that inner group's callback.
   - The outer group's after hooks run after the inner group has completely finished, including its own subcommand, its own hooks and its own `result_callback`. The `result` they receive is the inner group's return value.
   - Hooks are **not** called for commands run through `Context.invoke(cmd, ...)` or `Context.forward(cmd, ...)`.
   - Hooks are not called when a group with `invoke_without_command=True` runs without a subcommand.

### Interaction with existing features

1. **Parameters, `ParameterSource` and context inheritance.**
   - Inside a before hook, `sub_ctx.params` holds the subcommand's processed values, and `sub_ctx.get_parameter_source(name)` returns their sources (`COMMANDLINE`, `ENVIRONMENT`, `DEFAULT_MAP`, `DEFAULT`, `PROMPT`).
   - The parent group's values are available as `sub_ctx.parent.params`.
   - Changes a before hook makes to `sub_ctx.params` (adding, removing or replacing keys) are what the subcommand's callback receives as keyword arguments.
2. **Help and parse errors.** Hooks only run once the subcommand's context has been created successfully. No hook runs in these cases:
   - `--help` is given to the subcommand.
   - The subcommand fails parsing, for example a missing required option, a bad value or an unexpected extra argument.
   - The subcommand's `no_args_is_help` triggers.
   - The group itself shows help or errors.
   
   In chain mode, a parse error in *any* subcommand means no hook runs for *any* subcommand on that command line, because all contexts are created first.
3. **Shell completion.** Resolving completions creates contexts but never invokes commands, so hooks are never called during shell completion.
4. **`result_callback`.** For a given subcommand, its after hooks run before the group's `result_callback`. The values `result_callback` sees are unaffected by hooks.
5. **Context cleanup.** After hooks run while `sub_ctx` is still open, i.e. *before* the callbacks registered on `sub_ctx` with `call_on_close` and before resources entered with `with_resource` are closed.
6. **`CommandCollection`.** A collection's own hooks fire for every subcommand it runs, including commands found in its sources. Hooks registered on the source groups are not called when their commands run through the collection.
7. **`to_info_dict`.** The output of `Context.to_info_dict()` / `Command.to_info_dict()` is unchanged.

### Examples

Chained group with hooks and a result callback:

```python
@click.group(chain=True)
def cli():
    click.echo("group")

@cli.before_subcommand
def before(ctx):
    click.echo(f"before {ctx.info_name}")

@cli.after_subcommand
def after(ctx, result):
    click.echo(f"after {ctx.info_name} -> {result!r}")

@cli.result_callback()
def done(results):
    click.echo(f"results {results!r}")

@cli.command()
def a():
    click.echo("a")
    return 1

@cli.command()
def b():
    click.echo("b")
    return 2
```

```
$ cli a b
group
before a
a
after a -> 1
before b
b
after b -> 2
results [1, 2]
```

Ordering of several hooks (constructor hook first, after hooks reversed):

```python
def first(ctx):
    click.echo("first")

@click.group(before_subcommand=first)
def cli():
    pass

@cli.before_subcommand
def second(ctx):
    click.echo("second")

@cli.after_subcommand
def after_a(ctx, result):
    click.echo("after-a")

@cli.after_subcommand
def after_b(ctx, result):
    click.echo("after-b")

@cli.command()
def run():
    click.echo("run")
```

```
$ cli run
first
second
run
after-b
after-a
```

A before hook rejecting a subcommand. The usage line is the subcommand's, and the exit code is 2:

```python
@click.group()
@click.option("--dry-run", is_flag=True)
def cli(dry_run):
    pass

@cli.before_subcommand
def check(ctx):
    if ctx.parent.params["dry_run"] and ctx.info_name == "deploy":
        raise click.UsageError("deploy cannot run with --dry-run")

@cli.command()
def deploy():
    click.echo("deploying")
```

```
$ cli --dry-run deploy
Usage: cli deploy [OPTIONS]
Try 'cli deploy --help' for help.

Error: deploy cannot run with --dry-run
```

### Compatibility

- A group with no registered hooks behaves exactly as before. This covers invocation order, chain mode, `result_callback`, `invoke_without_command`, error output, exit codes and return values of `main(standalone_mode=False)`.
- No existing signature changes, except the two new optional keyword parameters on `Group.__init__`, which default to `None`.
- `Command`, `Context.invoke`, `Context.forward`, shell completion, help output and `to_info_dict` are unchanged.
- No new warnings are emitted.

## Feature 2: Show each option's effective value and its origin in help pages


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

## Feature 3: Add a `line` shell completion protocol driven by `COMP_LINE` and `COMP_POINT`


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
