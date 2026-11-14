# Feature bundle: Add before/after subcommand lifecycle hooks to `Group`; Option shorthands: declarative token expansions resolved by the parser; Numbered menus for Choice prompts

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

## Feature 2: Option shorthands: declarative token expansions resolved by the parser


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

## Feature 3: Numbered menus for Choice prompts


### Background

When a `click.Choice` option prompts interactively, the user sees `Color (red, green, blue): ` and has to type the exact choice name. Long or awkward choice values (enum names, file-like identifiers, ten or more entries) are tedious to type and easy to misspell. Many CLIs show a numbered menu and let the user pick by number. Click should support that for `click.prompt()` and for prompting options, with deterministic rendering so it can be checked with `CliRunner`.

### Public interface

1. `click.prompt(text, default=None, hide_input=False, confirmation_prompt=False, type=None, value_proc=None, prompt_suffix=": ", show_default=True, err=False, show_choices=True, numbered=False)`. The new parameter `numbered: bool = False` is appended after `show_choices`. Both typing overloads gain it too.
2. `click.Option(..., prompt_numbered: bool = False, ...)`. This is a new keyword parameter, also accepted by `@click.option(...)`. The value is stored on the instance as the public attribute `Option.prompt_numbered`.

No other new names are added. Everything stays importable from `click` as before.

### Behaviour

1. **Validation.**
   - `click.prompt(..., numbered=True)` raises `TypeError` with the message `'numbered' requires a 'click.Choice' type.` when `type` is not a `click.Choice` instance.
   - It raises `ValueError` with the message `'numbered' requires at least one choice.` when the Choice has no choices.
   - Both checks happen before anything is printed.
   - `click.Option(..., prompt_numbered=True)` raises `TypeError` with the message `'prompt_numbered' requires a 'click.Choice' type.` when the option's type is not a `click.Choice`.
   - It raises `TypeError` with the message `'prompt_numbered' cannot be used with 'multiple=True' or 'nargs' other than 1.` when `multiple=True` or `nargs != 1`.
   - The Option checks happen at construction time, whether or not `prompt` is set.

2. **Menu.** When `numbered=True`, each `prompt()` call first echoes one line per choice, in choice order, before the first prompt line. Output goes through `click.echo` to stdout, or to stderr when `err=True`.
   - Each line is: two spaces, then the 1-based number right-aligned to the width of `str(N)` (N = number of choices), then `) `, then the label.
   - The label is `choice.name` for `enum.Enum` members and `str(choice)` for everything else.
   - With 3 choices the first line is `  1) red`. With 10 choices the lines are `   1) ...` through `  10) ...`.
   - The menu is printed only once per call. It is **not** printed again after an invalid input, and not for the confirmation prompt.

3. **Prompt line.**
   - The prompt text is `{text} [1-{N}]`, followed by the default preview and then `prompt_suffix`. With one choice the marker is `[1-1]`.
   - The parenthesized choice list (`(red, green, blue)`) is never appended in numbered mode, whatever `show_choices` is.
   - Default preview when `show_default` is a non-empty string: ` [({show_default})]`, as today.
   - Default preview when `show_default` is truthy and not a string, and `default is not None`: the choices are scanned in order. The first choice where `default == choice`, or where `default` is a `str` equal to that choice's label, gives the preview ` [{number}]`. If nothing matches, the default is previewed exactly as click does today (` [{default}]`).
   - When `show_default` is false, there is no preview.

4. **Input interpretation.** This applies only to text the user types at the main prompt.
   - Input that fully matches the regular expression `[0-9]+` is treated as a menu number. Leading zeros are allowed (`02` is 2). Input is not stripped, so ` 2` does not match.
   - If the number is between 1 and N, the choice `choices[number - 1]` (the original choice object) is passed to `value_proc`. Without `value_proc`, it goes through the normal Choice conversion.
   - Any other number echoes `Error: '{input}' is not a valid selection. Enter a number from 1 to {N}.` (to the same stream as the prompt) and the prompt line is shown again. Example: `Error: '9' is not a valid selection. Enter a number from 1 to 3.`
   - Input that does not match `[0-9]+` is passed unchanged to `value_proc` or conversion, exactly as today. Typing a choice name still works, and an unknown name gives the existing error, e.g. `Error: 'purple' is not one of 'red', 'green', 'blue'.`
   - Empty input when a default exists uses the default exactly as today. The default is **never** read as a menu number, even if it is a digit string.

5. **Result and confirmation.**
   - The returned value is the converted choice, i.e. the original choice object (an enum member for enum choices).
   - With `confirmation_prompt`, the confirmation prompt text is unchanged: no menu and no `[1-N]` marker.
   - The confirmation compares the raw typed texts, so a selection typed as `2` must be confirmed as `2`.
   - With `hide_input=True`, the out-of-range error is masked like other prompt errors, giving `Error: '***' is not a valid selection. Enter a number from 1 to {N}.`

### Interaction with existing features

6. **Option prompting.** For an option with `prompt_numbered=True` and a `prompt`, `Option.prompt_for_value` calls `click.prompt` with `numbered=True`. This covers every case where the option already prompts, including a `prompt_required=False` option given on the command line without a value. The parameter source is `ParameterSource.PROMPT`, as for any prompt. If the option has no `prompt`, `prompt_numbered` has no effect.

7. **Other value sources are never numbered.** Values from the command line, from environment variables (`envvar` or `auto_envvar_prefix`), from `default_map`, and from `default` are converted exactly as today, with no index interpretation. With choices `red, green`, `--color 2` fails with the existing `Invalid value for '--color': '2' is not one of 'red', 'green'.` When a value is supplied from one of these sources, no prompt and no menu are shown.

8. **Defaults from any source are previewed as numbers.**
   - Rule 3 applies to whatever default the option prompts with: the parameter `default`, a callable default (after calling it), or a value from `ctx.default_map`. For example, with choices `red, green, blue` and `default_map={"color": "blue"}`, the prompt line is `Color [1-3] [3]: `.
   - `Option(show_default=False)` removes the preview, and `Option(show_default="auto")` shows ` [(auto)]`.
   - When the option has no default, there is no preview.

9. **Help, completion, introspection, CliRunner.**
   - `--help` output, `Option.get_help_record`, shell completion of choice values, and `to_info_dict()` are identical with and without `prompt_numbered`.
   - All menu, error and prompt output goes through `click.echo`, so `CliRunner` captures it in order. The runner's normal echo of typed input follows the prompt line.

### Examples

Given:

```python
@click.command()
@click.option("--color", type=click.Choice(["red", "green", "blue"]),
              prompt=True, prompt_numbered=True, default="green")
def cli(color):
    click.echo(f"color={color}")
```

`runner.invoke(cli, input="3\n")` produces the output:

```
  1) red
  2) green
  3) blue
Color [1-3] [2]: 3
color=blue
```

`runner.invoke(cli, input="9\nred\n")` produces:

```
  1) red
  2) green
  3) blue
Color [1-3] [2]: 9
Error: '9' is not a valid selection. Enter a number from 1 to 3.
Color [1-3] [2]: red
color=red
```

`runner.invoke(cli, input="\n")` produces `Color [1-3] [2]: ` followed by `color=green`. `runner.invoke(cli, ["--color", "2"])` fails with exit code 2 and `Error: Invalid value for '--color': '2' is not one of 'red', 'green', 'blue'.`

With ten choices:

```python
@click.command()
@click.option("--level", type=click.Choice([f"l{i}" for i in range(1, 11)]),
              prompt="Level", prompt_numbered=True)
def cli(level):
    click.echo(f"level={level}")
```

`runner.invoke(cli, input="10\n")` produces:

```
   1) l1
   2) l2
   3) l3
   4) l4
   5) l5
   6) l6
   7) l7
   8) l8
   9) l9
  10) l10
Level [1-10]: 10
level=l10
```

### Compatibility

- With `numbered=False` / `prompt_numbered=False` (the defaults), prompt output, input handling, error messages, help output, completion, and `to_info_dict()` are byte-for-byte unchanged.
- No existing parameter is renamed, reordered, or removed. The existing positional order of `click.prompt` is preserved, and `numbered` is appended at the end.
- `click.confirm` and boolean-flag prompts are unaffected.
- No new warnings are emitted.
