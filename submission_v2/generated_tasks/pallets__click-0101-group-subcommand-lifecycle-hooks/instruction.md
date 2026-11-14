# Per-subcommand lifecycle hooks for `Group`

## Background

A group's callback runs before Click even parses which subcommand comes next, and `result_callback` runs once at the very end. There is no supported place to run code around *each* subcommand, with access to that subcommand's parsed `Context`, and with guaranteed cleanup when something fails midway (validating a subcommand against group options, logging every step of a chained pipeline, opening/closing a transaction per command). Users currently override `Group.invoke` and copy its internals.

This issue adds before / after / teardown hooks to `click.Group`, plus a `click.SkipSubcommand` exception that a before hook can raise to skip the subcommand.

## Public API

```python
class Group(Command):
    def __init__(
        self, name=None, commands=None, invoke_without_command=False,
        no_args_is_help=None, subcommand_metavar=None, chain=False,
        result_callback=None,
        before_subcommand: Callable[[Context], Any] | None = None,
        after_subcommand: Callable[[Context, Any], Any] | None = None,
        teardown_subcommand: Callable[[Context, BaseException | None], Any] | None = None,
        **kwargs,
    ) -> None: ...

    def before_subcommand(self, f: F) -> F: ...
    def after_subcommand(self, f: F) -> F: ...
    def teardown_subcommand(self, f: F) -> F: ...

class SkipSubcommand(ClickException):
    result: Any
    def __init__(self, result: Any = None) -> None: ...
```

- The three constructor keywords default to `None` (no hook). `@click.group(...)`, `Group.group(...)` and `click.CommandCollection(...)` accept them too.
- The three methods register `f` and return it unchanged. They take the function directly: `@cli.before_subcommand` (no parentheses), or `cli.before_subcommand(f)`.
- `SkipSubcommand` is importable as `click.SkipSubcommand` and `click.exceptions.SkipSubcommand`. It subclasses `click.ClickException` (exit code 1), stores its argument as `.result`, and its message (`str(exc)`) is always `SkipSubcommand raised outside of a before_subcommand hook.`

## Definitions

- `sub_ctx` is the `Context` created for a **direct** subcommand of the group.
- A subcommand **starts** once its context has been created successfully (all its parameters parsed, converted, parameter callbacks run, prompts answered). Every started subcommand goes through the *hook sequence* (rule 2), whichever kinds of hooks happen to be registered.

## Behaviour

### 1. Registration

- Any number of hooks of each kind may be registered; registering again adds a hook, it never replaces one. The hook given as a constructor keyword is registered first for its kind.
- Hooks may be registered at any time, including while the group is running. The hooks applied to a subcommand are exactly those registered on the group at the moment that subcommand starts. A hook registered later, even by a hook or callback running for that same subcommand, only applies to subcommands that start afterwards.

### 2. Hook sequence of one started subcommand

1. Before hooks, in registration order: `hook(sub_ctx)`.
2. The subcommand's `Command.invoke` (its deprecation message, if any, then its callback).
3. After hooks, in **reverse** registration order: `hook(sub_ctx, result)`, where `result` is what step 2 returned.
4. Teardown hooks, in **reverse** registration order: `hook(sub_ctx, exc)`. They run exactly once per started subcommand, whatever happened in steps 1-3. `exc` is `None` when steps 1-3 finished without an exception propagating (a skip counts as no exception, see rule 5).

During every hook call, `click.get_current_context()` is `sub_ctx`. All hook return values are ignored: the value reported for the subcommand (to `result_callback`, in the chain-mode result list, and from `main(standalone_mode=False)`) is always the original `result`, or the skip result.

### 3. Placement within a group invocation (success)

"Context closed" means its `call_on_close` callbacks run and its `with_resource` resources are exited.

| Mode | Complete order for `cli s1 s2 ...` |
|---|---|
| non-chain | group callback -> `s1` context created -> `s1` hook sequence -> group `result_callback` -> `s1` context closed -> group context closed |
| chain | group callback -> contexts of `s1`, `s2`, ... created, in command-line order -> `s1` hook sequence -> `s1` context closed -> `s2` hook sequence -> `s2` context closed -> ... -> `result_callback` (list of results) -> group context closed |

`result_callback` is outside the hook sequence: teardown hooks never see its exceptions.

### 4. Exceptions

"Exception" means any exception, including `click.exceptions.Exit` (from `ctx.exit()`), `click.Abort` and other `BaseException` subclasses.

| Exception escapes from | remaining before hooks | subcommand invoked | after hooks | teardown hooks receive | `result_callback` |
|---|---|---|---|---|---|
| nothing | - | yes | all run | `None` | called |
| a before hook | not run | no | none run | that exception | not called |
| `SkipSubcommand` from a before hook | not run | no | none run | `None` | called, with `result` |
| the subcommand | - | - | none run | that exception | not called |
| an after hook | - | - | remaining not run | that exception | not called |

- **Teardown failures.** All teardown hooks run even if some of them raise. Each teardown hook receives the exception propagating at the moment it is called: the original one, or, if an earlier teardown hook raised, the most recent exception raised by a teardown hook. After the last teardown hook, the most recent exception raised by a teardown hook propagates; if none raised, the original exception (if any) propagates; otherwise the subcommand counts as completed.
- The exception that finally propagates is handled exactly like the same exception raised by the subcommand's callback: in standalone mode a `ClickException` is shown and the program exits with its exit code, `Exit` exits with its code, `Abort` prints `Aborted!`.
- A `click.UsageError` (or subclass) raised by **any** hook with `ctx` set to `None` gets `ctx = sub_ctx` as soon as it leaves the hook. Later teardown hooks therefore already see `exc.ctx is sub_ctx`, and the error is shown with the subcommand's usage line and `Try '<command path> --help' for help.` hint.
- `Context.exit()` closes the context before raising `Exit` (existing behaviour). So when a hook or the subcommand calls `sub_ctx.exit(...)`, the close callbacks registered on `sub_ctx` so far run at that moment, i.e. before the (remaining) teardown hooks, and they do not run a second time later.
- If the subcommand raises, its context is closed after its teardown hooks have run (as in the placement table).
- **Chain mode.** Once a subcommand's hook sequence ends with an exception, the remaining subcommands on the command line never start: none of their hooks run, they are not invoked, and, as today, their already-created contexts are never closed (close callbacks registered on them while parsing do not run). `result_callback` is not called.

### 5. `SkipSubcommand`

- Propagating out of a before hook of the group that is running the subcommand, it skips that subcommand (see table above): remaining before hooks, the subcommand (including its deprecation message) and its after hooks do not run; teardown hooks run with `exc=None`; the reported value is `exc.result`. In chain mode the next subcommand then starts normally. If a teardown hook raises after a skip, that exception propagates as usual.
- Raised anywhere else (a subcommand callback, an after or teardown hook, a group callback, a result callback), it is an ordinary `ClickException`: teardown hooks receive it as `exc`, and in standalone mode the output is `Error: SkipSubcommand raised outside of a before_subcommand hook.` with exit code 1. A `SkipSubcommand` raised by a nested group's subcommand is an error for the inner group *and* for every outer group it propagates through.

### 6. Scope and nesting

- Hooks fire only for **direct** subcommands of the group they are registered on; nested groups do not inherit them.
- No hooks run for commands run through `Context.invoke(cmd, ...)` or `Context.forward(cmd, ...)`, nor when a group with `invoke_without_command=True` runs without a subcommand.
- When a direct subcommand is itself a group, `result` for the outer after hooks is the inner group's return value (its `result_callback`'s return value if it has one). For `outer inner leaf`:

| Outcome | Order |
|---|---|
| success | outer callback -> outer before(inner) -> inner callback -> inner before(leaf) -> leaf -> inner after(leaf) -> inner teardown(leaf) -> inner `result_callback` -> leaf context closed -> outer after(inner) -> outer teardown(inner) -> outer `result_callback` -> inner context closed -> outer context closed |
| leaf raises E | outer callback -> outer before(inner) -> inner callback -> inner before(leaf) -> leaf -> inner teardown(leaf, E) -> leaf context closed -> outer teardown(inner, E) -> inner context closed -> E reported |

### 7. Re-entrancy

A hook or a subcommand may run the same group again, e.g. `cli.main([...], prog_name="cli", standalone_mode=False)`. The nested run performs its own complete sequence (group callback, hooks registered at that time, subcommand) independently of the outer run, and when it returns, `click.get_current_context()` is again the outer `sub_ctx`. The outer subcommand's hook sequence then continues where it left off.

### 8. When hooks never run

No hook of any kind (including teardown) runs for a subcommand that does not start:

- `--help` given to the subcommand, a parse error in it (missing required option, bad value, unexpected extra argument), its `no_args_is_help`, or the group itself showing help or failing.
- Chain mode: a parse error in *any* subcommand means no hook runs for *any* subcommand on that command line, because all contexts are created first.
- Shell completion: resolving completions never invokes commands, so hooks are never called.

### 9. Parameters inside hooks

- `sub_ctx.params` holds the subcommand's processed values and `sub_ctx.get_parameter_source(name)` their sources (`COMMANDLINE`, `ENVIRONMENT`, `DEFAULT_MAP`, `DEFAULT`, `PROMPT`). The parent group's values are `sub_ctx.parent.params`.
- Changes a before hook makes to `sub_ctx.params` (adding, removing, replacing keys) are exactly the keyword arguments the subcommand's callback receives.

### 10. `CommandCollection` and introspection

- A collection's own hooks fire for every subcommand it runs, including commands found in its sources. Hooks registered on a source group are not called when its commands run through the collection.
- `Context.to_info_dict()` / `Command.to_info_dict()` output is unchanged by hooks.

## Examples

```python
@click.group(chain=True)
def cli():
    click.echo("group")

@cli.before_subcommand
def before(ctx):
    click.echo(f"before {ctx.info_name}")
    if ctx.info_name == "b":
        raise click.SkipSubcommand("skipped")

@cli.after_subcommand
def after(ctx, result):
    click.echo(f"after {ctx.info_name} -> {result!r}")

@cli.teardown_subcommand
def teardown(ctx, exc):
    click.echo(f"teardown {ctx.info_name} {exc!r}")

@cli.result_callback()
def done(results):
    click.echo(f"results {results!r}")

@cli.command()
def a():
    click.echo("a")
    return 1

@cli.command()
def b():
    return 2
```

```
$ cli a b
group
before a
a
after a -> 1
teardown a None
before b
teardown b None
results [1, 'skipped']
```

A before hook rejecting a subcommand (exit code 2; the usage line is the subcommand's):

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

## Compatibility

- A group with no registered hooks behaves exactly as before: invocation order, chain mode, `result_callback`, `invoke_without_command`, context cleanup, error output, exit codes and `main(standalone_mode=False)` return values.
- No existing signature changes except the three new optional keyword parameters of `Group.__init__`, defaulting to `None`.
- `Command`, `Context.invoke`, `Context.forward`, shell completion, help output and `to_info_dict` are unchanged.
- No new warnings are emitted.
