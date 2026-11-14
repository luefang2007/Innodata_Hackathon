# Show each option's effective value and its origin in help pages

## Background

When a CLI reads its configuration from environment variables, a `default_map` (for example loaded from a config file), and parameter defaults, users cannot tell from `--help` what value an option will actually get if they leave it out. `show_default` only shows the declared default, and `show_envvar` only shows variable *names*. This feature adds an opt-in help annotation that shows the value an omitted option would take and where it comes from. It also adds a public method that computes this without parsing.

## Public interface

- `click.Option(..., show_effective: bool | None = None)`. This is a new keyword parameter, stored as the attribute `Option.show_effective`. `@click.option(..., show_effective=...)` forwards it as usual.
- `click.Context(..., show_effective: bool | None = None)`. This is a new keyword parameter, added after all existing parameters and stored as the attribute `Context.show_effective`. It can also be set with `context_settings={"show_effective": True}`.
- `Option.get_effective_value(self, ctx: Context) -> tuple[Any, ParameterSource] | None`. This is a new public method.
- `Option.get_help_extra(ctx)` may return a new key `"current"` (a `str`). `click.types.OptionHelpExtra` gains the optional key `current: str`.

## Behaviour

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

## Interaction with existing features

6. **Environment variables.** An environment value always outranks a prompt, the `default_map` and the default, which matches how Click resolves values at runtime. Empty variables are ignored. The auto variable is used only when `allow_from_autoenv` is true. `show_envvar` has no influence on the current item: the item names the supplying variable even when `show_envvar` is false, and the existing `env var:` item is still controlled only by `show_envvar`.

7. **default_map and defaults.** The context's own `default_map` is used. For a subcommand, that is the entry of the parent's `default_map` named after the subcommand. A `default_map` entry whose value is `None` yields `(None, ParameterSource.DEFAULT_MAP)`, so no item is shown and the parameter default is not consulted. Callable `default_map` values and callable defaults are shown as `(dynamic)` (when they are functions) and are never called.

8. **Prompts.** An option with `prompt` set, `prompt_required=True` (the default) or `required=True`, and no environment value shows `current: (prompt)`, whatever its `default` or `default_map` value. An option with `prompt_required=False` and `required=False` skips the prompt step and continues with `default_map` and the default.

9. **show_default conflict.** If the current item is shown with origin `default` or `default map`, the `default:` item is removed from both `get_help_extra` and the record. This applies even when `show_default` is a custom string. With origin `env var NAME` or `(prompt)`, the `default:` item (if any) is kept, followed by the current item. The `range` and `required` items are unaffected.

## Examples

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

## Compatibility

- When `show_effective` is not enabled anywhere, help output and `get_help_extra()` results are exactly as before.
- Parsing, value resolution, `ParameterSource` values recorded during invocation, prompts, and callbacks are unchanged. Computing the annotation never prompts, never calls callables, and never fails on values that would not convert.
- `Option.to_info_dict()` and `Context.to_info_dict()` are unchanged and do not include the new setting.
- Existing constructor signatures change only by the new optional keyword parameters described above.
