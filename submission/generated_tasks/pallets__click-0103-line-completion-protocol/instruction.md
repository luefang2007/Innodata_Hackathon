# Add a `line` shell completion protocol driven by `COMP_LINE` and `COMP_POINT`

## Background

Click's built-in completion scripts (bash, zsh, fish, powershell) all use the `COMP_WORDS`/`COMP_CWORD` protocol. In that protocol a shell function splits the command line before calling the program.

Bash has a simpler mechanism, `complete -C command`. Bash runs `command` with the raw line in `COMP_LINE` and the cursor offset in `COMP_POINT`, then reads one candidate per line from stdout. It needs no shell function, works under zsh's `bashcompinit`, and is trivial to drive from editors and other tools. Click cannot serve it today, because it would have to tokenise the raw line itself and decide which word the cursor is on.

A completion class also has no way to report a malformed request. Problems either crash with a traceback or exit with status 1 without any message.

## Public interface

Both names live in `click.shell_completion` and are not re-exported from `click`.

- `class CompletionRequestError(click.ClickException)`, constructed as `CompletionRequestError(message: str)`. Its `exit_code` is `1`. A completion class raises it when the completion request in the environment is malformed.
- `class LineComplete(ShellComplete)`. Its constructor signature is the same as `ShellComplete`.
  - `name = "line"`
  - `source_template` is exactly:
    `complete -o default -o nosort -C '%(complete_var)s=line_complete %(prog_name)s' %(prog_name)s` followed by a single `\n`.

`LineComplete` is registered like the built-in shells, so `click.shell_completion.get_completion_class("line")` returns `LineComplete`.

## Behaviour

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

## Interaction with existing features

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

## Examples

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

## Compatibility

- The `bash`, `zsh`, `fish` and `powershell` completion classes, their source scripts and their output formats are unchanged. This includes the trailing newline they print even when there are no completions.
- `shell_complete` still returns 1 silently for unknown shells and unknown instructions. Only `CompletionRequestError` gets the new stderr handling.
- `split_arg_string`, `CompletionItem`, `ShellComplete`, `add_completion_class` and `get_completion_class` keep their existing signatures and behaviour.
- Programs that never set the completion variable to a `line_*` value see no difference.
