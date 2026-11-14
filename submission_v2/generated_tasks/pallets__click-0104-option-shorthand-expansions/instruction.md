# Option shorthands: declarative token expansions resolved by the parser

## Background

Many CLIs want one switch that stands for a bundle of other options, such as `-q` meaning `--log-level error --no-color`, or `-D` meaning `--profile prod deploy`. Today there are two workarounds, and both are wrong in important ways:

- An option callback that mutates other parameters bypasses parsing, ordering and `ParameterSource`.
- Overriding `parse_args` to rewrite `argv` blindly also rewrites option *values* and tokens after `--`.

Click should support shorthands as a parser feature. The option parser replaces a shorthand token by its expansion at the exact point where it would otherwise process an option. Everything downstream then behaves as if the user had typed the expansion.

## Public interface

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

## Behaviour

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

## Interaction with existing features

1. **Value sources.**
   - Values supplied through an expansion have `ParameterSource.COMMANDLINE`.
   - They take precedence over environment variables, `default_map` and defaults.
   - They suppress prompts for that option.
   - They emit deprecation warnings for deprecated parameters, exactly like typed values.
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
   - The description is `{help}  [expands to: {tokens}]` (two spaces) or `[expands to: {tokens}]` when `help` is `None` or empty, where `{tokens}` is `shlex.join(expansion)`.
   - If no visible shorthand exists, no section is written.
   - The usage line is unchanged.
6. **Introspection.** `Command.to_info_dict(ctx)` (and therefore `Context.to_info_dict()`) gains a key `"shorthands"`, a list of `Shorthand.to_info_dict()` in `Command.shorthands` order. The key is present only when the command has at least one shorthand.

## Examples

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

## Compatibility

- Commands without shorthands parse, render help, compute help option names, and produce `to_info_dict` output exactly as before. In particular, no `"shorthands"` key and no `Shorthands` section appear.
- Existing signatures are unchanged apart from the new optional trailing `shorthands` keyword of `Command`.
- Option clustering, `--`, `=`-attached values, `ignore_unknown_options`, `allow_interspersed_args` and existing error messages behave as today for every token that is not a recognised shorthand.
- No new warnings are emitted.
