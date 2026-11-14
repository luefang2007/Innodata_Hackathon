# Numbered menus for Choice prompts

## Background

When a `click.Choice` option prompts interactively, the user sees `Color (red, green, blue): ` and has to type the exact choice name. Long or awkward choice values (enum names, file-like identifiers, ten or more entries) are tedious to type and easy to misspell. Many CLIs show a numbered menu and let the user pick by number. Click should support that for `click.prompt()` and for prompting options, with deterministic rendering so it can be checked with `CliRunner`.

## Public interface

1. `click.prompt(text, default=None, hide_input=False, confirmation_prompt=False, type=None, value_proc=None, prompt_suffix=": ", show_default=True, err=False, show_choices=True, numbered=False)`. The new parameter `numbered: bool = False` is appended after `show_choices`. Both typing overloads gain it too.
2. `click.Option(..., prompt_numbered: bool = False, ...)`. This is a new keyword parameter, also accepted by `@click.option(...)`. The value is stored on the instance as the public attribute `Option.prompt_numbered`.

No other new names are added. Everything stays importable from `click` as before.

## Behaviour

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

## Interaction with existing features

6. **Option prompting.** For an option with `prompt_numbered=True` and a `prompt`, `Option.prompt_for_value` calls `click.prompt` with `numbered=True`. This covers every case where the option already prompts, including a `prompt_required=False` option given on the command line without a value. The parameter source is `ParameterSource.PROMPT`, as for any prompt. If the option has no `prompt`, `prompt_numbered` has no effect.

7. **Other value sources are never numbered.** Values from the command line, from environment variables (`envvar` or `auto_envvar_prefix`), from `default_map`, and from `default` are converted exactly as today, with no index interpretation. With choices `red, green`, `--color 2` fails with the existing `Invalid value for '--color': '2' is not one of 'red', 'green'.` When a value is supplied from one of these sources, no prompt and no menu are shown.

8. **Defaults from any source are previewed as numbers.**
   - Rule 3 applies to whatever default the option prompts with: the parameter `default`, a callable default (after calling it), or a value from `ctx.default_map`. For example, with choices `red, green, blue` and `default_map={"color": "blue"}`, the prompt line is `Color [1-3] [3]: `.
   - `Option(show_default=False)` removes the preview, and `Option(show_default="auto")` shows ` [(auto)]`.
   - When the option has no default, there is no preview.

9. **Help, completion, introspection, CliRunner.**
   - `--help` output, `Option.get_help_record`, shell completion of choice values, and `to_info_dict()` are identical with and without `prompt_numbered`.
   - All menu, error and prompt output goes through `click.echo`, so `CliRunner` captures it in order. The runner's normal echo of typed input follows the prompt line.

## Examples

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

## Compatibility

- With `numbered=False` / `prompt_numbered=False` (the defaults), prompt output, input handling, error messages, help output, completion, and `to_info_dict()` are byte-for-byte unchanged.
- No existing parameter is renamed, reordered, or removed. The existing positional order of `click.prompt` is preserved, and `numbered` is appended at the end.
- `click.confirm` and boolean-flag prompts are unaffected.
- No new warnings are emitted.
