# Add before/after subcommand lifecycle hooks to `Group`

## Background

A group's callback runs before Click even parses which subcommand comes next. `result_callback` runs once, after every subcommand has finished. There is no place to run code around *each* subcommand with access to that subcommand's parsed `Context`. Examples are validating a subcommand against group-level options, logging each step of a chained pipeline, or timing every command. Users currently override `Group.invoke` and copy its internals.

This issue adds per-subcommand lifecycle hooks to `click.Group`.

## Public interface

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

## Behaviour

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

## Interaction with existing features

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

## Examples

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

## Compatibility

- A group with no registered hooks behaves exactly as before. This covers invocation order, chain mode, `result_callback`, `invoke_without_command`, error output, exit codes and return values of `main(standalone_mode=False)`.
- No existing signature changes, except the two new optional keyword parameters on `Group.__init__`, which default to `None`.
- `Command`, `Context.invoke`, `Context.forward`, shell completion, help output and `to_info_dict` are unchanged.
- No new warnings are emitted.
