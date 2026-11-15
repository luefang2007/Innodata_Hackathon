"""Prompt text and JSON schemas for the model calls: design, implement, repair, deepen."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, repo

RULES = """You are a senior maintainer of the open-source Python library `click` (pallets/click). You design
feature-development tasks that are used to evaluate AI coding agents. Each task is a realistic new
feature for click at the exact source version shown below.

A task has three parts that must agree perfectly:
1. instruction.md - the ONLY text the solver sees. A competent engineer must be able to implement the
   feature from this text alone, without seeing any tests.
2. A reference implementation (the "gold" patch) that edits click's existing source under src/click/.
3. A new pytest file under tests/ that checks the feature through click's public API.

Non-negotiable rules
- Novel: the feature does not exist in click at this version, is not one of the upstream changes listed
  below, and is not a drop-in copy of a well-known third-party library (click-option-group, cloup,
  rich-click, typer, ...). Build something click itself could plausibly ship.
- Grounded: the gold extends real click classes/functions and wires the feature into existing code paths
  (parsing, Context, Command/Group, help formatting, types, shell completion, termui, CliRunner). A
  stand-alone helper that nothing in click calls is not acceptable.
- Backward compatible: every existing behaviour stays exactly the same unless the new feature is used.
  The existing test suite must keep passing. Do not change existing public signatures except by adding
  optional keyword parameters with defaults that preserve old behaviour.
- Fair: every behaviour a test checks is stated explicitly in instruction.md - names, signatures,
  parameter names and defaults, return values, ordering, exact output strings and exact error messages
  (verbatim, in backticks), and edge cases. Tests may only use names the spec defines plus click's
  existing public API. Never assert on private helpers, internal attribute names, or wording the spec
  does not give.
- Hard: the task should be genuinely difficult for a strong coding agent while staying fully specified.
  Difficulty must come from breadth and interaction, never from hidden information: the feature needs
  coordinated changes in two or more places of click, has 6-9 numbered behaviour rules, and at least two
  of those rules govern interaction with existing click features (value sources such as environment
  variables, default_map, defaults and prompts; multiple/nargs; required; Context inheritance; help
  rendering; shell completion; ParameterSource; to_info_dict).
- Deterministic: no network, no time-of-day, no randomness, no terminal-size dependence (pass explicit
  widths where formatting matters), no reliance on OS-specific behaviour.
- Clean writing: instruction.md reads like a GitHub feature issue written by a maintainer. Do not mention
  tests, test files, graders, reference solutions or patches, and do not describe yourself.

Code conventions: Python >= 3.10, `from __future__ import annotations` is already used in click modules,
keep click's style (type hints, docstrings), keep `filterwarnings = error` in mind (no new warnings),
export new public names from `click/__init__.py` when the spec says they are importable from `click`.
"""

DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string", "description": "2-5 lowercase words joined by hyphens"},
        "title": {"type": "string"},
        "capability": {"type": "string", "description": "one sentence: what new capability click gains"},
        "novelty_argument": {"type": "string", "description": "why click 8.5.1 and upstream main lack it"},
        "touch_points": {"type": "array", "items": {"type": "string"},
                         "description": "existing click functions/classes the gold must change"},
        "rules": {"type": "array", "items": {"type": "string"},
                  "description": "the numbered behaviour rules, one string each"},
        "instruction_md": {"type": "string", "description": "the complete instruction.md"},
    },
    "required": ["slug", "title", "capability", "novelty_argument", "touch_points", "rules", "instruction_md"],
    "additionalProperties": False,
}

IMPL_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "search": {"type": "string"},
                    "replace": {"type": "string"},
                },
                "required": ["path", "search", "replace"],
                "additionalProperties": False,
            },
        },
        "test_file_content": {"type": "string"},
        "instruction_md": {"type": "string",
                           "description": "full revised instruction.md, or empty string if unchanged"},
        "notes": {"type": "string"},
    },
    "required": ["edits", "test_file_content", "instruction_md", "notes"],
    "additionalProperties": False,
}


def repo_context(base_dir: Path, upstream_log: str) -> str:
    parts = [f"# click source at tag {config.BASE_TAG} (commit {config.BASE_COMMIT})\n"]
    for path in sorted((base_dir / "src" / "click").glob("*.py")):
        rel = path.relative_to(base_dir).as_posix()
        parts.append(f"\n===== FILE: {rel} =====\n{repo.read_text(path)}")
    conftest = base_dir / "tests" / "conftest.py"
    parts.append(f"\n===== FILE: tests/conftest.py =====\n{repo.read_text(conftest)}")
    sample = repo.read_text(base_dir / "tests" / "test_options.py").splitlines()[:120]
    parts.append("\n===== EXCERPT: tests/test_options.py (first 120 lines, for test style) =====\n"
                 + "\n".join(sample))
    parts.append("\n===== Upstream commits merged AFTER this version (these features are NOT novel) =====\n"
                 + upstream_log)
    return "\n".join(parts)


def design(cell: dict, siblings: list[str], depth: str = "") -> str:
    """`depth` is a mechanism text from matrix.DEPTH. Empty leaves the prompt byte-for-byte as it was,
    which keeps the recorded answers of earlier runs replayable from the response cache."""
    sib = "\n".join(f"- {s}" for s in siblings) or "- (none)"
    extra = (f"\n\nWhere the difficulty must come from - {depth}\nBuild the feature around this mechanism "
             f"from the start. Adding more independent rules is not enough: a solver that gets each rule "
             f"right still passes. At least half of the numbered rules must only matter in combination with "
             f"another rule or with existing click behaviour, and every such combination is stated as an "
             f"outcome in instruction.md.") if depth else ""
    return f"""Design ONE new feature task for click.

Variation cell assigned to this task (the batch uses a different cell for every task):
- Primary area of click: {cell['area']}
  (likely files: {', '.join(cell['files'])}; you may touch other click modules too)
- Behaviour shape: {cell['shape_desc']}

Other tasks in this batch cover these cells - do not overlap with them:
{sib}

Write the complete instruction.md with these sections:
# <title>
## Background  (the user problem, 2-4 sentences)
## Public interface  (exact signatures, parameter names, defaults, where names are importable from)
## Behaviour  (numbered rules; exact output strings and error messages verbatim in backticks)
## Interaction with existing features  (numbered rules about env vars / defaults / help / completion / etc.)
## Examples  (2-3 short command-line sessions with exact output)
## Compatibility  (what must stay unchanged)

The gold implementation should be roughly 60-200 lines of library code. Choose a feature whose rules
leave no room for interpretation: when two behaviours are possible, the spec picks one explicitly.{extra}"""


def implement(design_obj: dict, spec: str, test_path: str, combined: bool = False) -> str:
    """`combined` goes with a design that was asked for depth: the tests must then exercise rules together."""
    more = ("\n- Because this feature is built around combined behaviour: at least half of the tests must each "
            "depend on two or more rules at once and assert the complete, exact outcome.") if combined else ""
    return f"""Implement the task below: the gold patch and the test file.

Task design (for you only):
{json.dumps({k: design_obj[k] for k in ('title', 'capability', 'touch_points', 'rules')}, indent=1)}

instruction.md (exactly what the solver will see):
<<<SPEC
{spec}
SPEC>>>

Return:
- edits: search/replace edits to EXISTING files under src/click/ only. `search` must be copied verbatim
  from the current file (including indentation) and must match exactly once; keep each search block
  small (3-15 lines) but unique. To append, search for the last lines of the file. Never edit tests/,
  docs/ or CHANGES.
- test_file_content: the full content of the new test file `{test_path}`. Requirements:
  * 8-16 test functions, each checking one stated rule; name them after the behaviour
    (e.g. test_env_var_overrides_default). Use the `runner` fixture (CliRunner) for command-line
    behaviour and plain calls for API behaviour.
  * Assert exact values: full output lines, exact error messages, exact return values - never loose
    checks like `is not None` or `in` on single characters. A broken implementation must fail.
  * Every asserted message or output string must be stated in instruction.md or follow
    mechanically from an explicit rule there.
  * Import new names inside the test functions (e.g. `from click import NewThing` inside the test)
    so that each test fails individually when the feature is missing.
  * Deterministic and OS-independent; pass the width explicitly when formatting matters, e.g.
    `runner.invoke(cli, args, terminal_width=80)` (extra keyword arguments reach the Context).
- instruction_md: return "" unless you must clarify the spec so that it states something the tests need.
- notes: one or two sentences on what you implemented.{more}"""


def post_image(gold_patch: str, limit: int = 60000) -> str:
    """The regions the gold changed, as they are NOW (context and added lines of every hunk).

    A delta is written against the gold-applied source, so its search blocks must be copied from text
    that exists there: these regions for changed code, the original source for everything else."""
    out: list[str] = []
    path = ""
    for line in gold_patch.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            out.append(f"\n----- {path}, from line {m.group(1) if m else '?'} (current content) -----")
        elif line.startswith(("diff --git", "index ", "--- ", "new file", "deleted file", "similarity ",
                              "rename ", "\\")):
            continue
        elif line.startswith(("+", " ")):
            out.append(line[1:])
        elif line == "":
            out.append("")
    return "\n".join(out).strip("\n")[:limit]


def _too_easy(probe: dict, mechanism: str) -> tuple[str, str]:
    depth = (f"\nWhere the difficulty must come from - {mechanism}\nAdding more independent rules is not "
             f"enough: a solver that gets each rule right still passes. Build the rework around this "
             f"mechanism.\n") if mechanism else ""
    failed = probe.get("failed_f2p") or {}
    if failed:
        fail_note = ("\nFeature tests the weaker model failed at least once - keep and build on this kind of "
                     "difficulty:\n" + "\n".join(f"- {t} ({n}x)" for t, n in list(failed.items())[:12]))
    else:
        fail_note = "\nIt did not fail a single feature test."
    return depth, fail_note


def _current_task(spec: str, gold_patch: str, test_content: str, test_path: str) -> str:
    return f"""Current instruction.md:
<<<SPEC
{spec}
SPEC>>>

Current implementation. The original click source is in the system prompt; these are the regions the
current solution changed, shown as they are NOW (everything else is still the original source):
<<<CURRENT
{post_image(gold_patch)}
CURRENT>>>

Current test file `{test_path}`:
<<<TESTS
{test_content[:60000]}
TESTS>>>"""


def _delta_format(test_path: str) -> str:
    return f"""- edits: search/replace edits against the CURRENT state shown above. `path` is one of
  * a file under src/click/ - the `search` text must exist in that file as it is NOW: copy it from the
    regions above where the current solution changed the file, and from the original source elsewhere;
  * `{test_path}` - the test file above;
  * `instruction.md` - the text above.
  Every `search` is copied verbatim (including indentation), matches exactly once, and stays small
  (3-15 lines) but unique. To add text at the end of the test file or of instruction.md, use an empty
  `search`: the `replace` text is appended.
- test_file_content: "" (always empty in this format).
- instruction_md: "" (always empty in this format)."""


def deepen_delta(spec: str, gold_patch: str, test_content: str, probe: dict, test_path: str,
                 mechanism: str = "") -> str:
    """Escalation as a delta: the model returns only what changes, as edits against the current task.

    The full rewrite (`deepen`) re-emits the spec, every edit and the whole test file after long
    reasoning; three of five such answers were cut off at the output limit in the first jury run."""
    depth, fail_note = _too_easy(probe, mechanism)
    return f"""This task passed validation but is too easy: a smaller model that saw only instruction.md and the
click source solved it in {probe['solved_strict']} of {probe['k']} one-shot attempts.{fail_note}

Make the task substantially harder to implement correctly, while staying fair and realistic. Keep the
feature, its public names and everything that already works: extend it in depth, do not replace it.
{depth}
Do all of these:
1. Add 4-6 behaviour rules that only matter when the feature meets existing click behaviour or its own
   other rules: precedence when two rules apply at once, interaction with value sources (command line,
   environment variables, default_map, defaults, prompts), nested and chained commands with context
   inheritance, resilient parsing and shell completion, the order of errors when several rules are
   violated together, and corner cases of the existing functions you change that must keep working.
2. State the new rules in instruction.md as observable outcomes, invariants and exact messages. Do not
   describe your algorithm, the order of internal checks, or which internal function to modify; where an
   order is observable, state it as an outcome ("when both A and B hold, the result is C").
3. Add 6-10 tests. Each new test depends on two or more rules at once (at least one of them new) and
   asserts exact output. Every expected value must follow from rules stated in instruction.md.
4. Taken together, the implementation must change existing click code paths in at least two modules.

Fairness is non-negotiable: a careful engineer must still be able to implement everything from
instruction.md alone. Never rely on behaviour the text does not state.

{_current_task(spec, gold_patch, test_content, test_path)}

Return ONLY what changes; never repeat the parts of the task that stay as they are:
{_delta_format(test_path)}
- notes: one or two sentences on what you added.
Change or remove an existing rule or test only where a new rule makes it wrong."""


def repair_delta(spec: str, gold_patch: str, test_content: str, prev: dict, failures: str,
                 test_path: str) -> str:
    """Repair of a failed delta. The delta is re-applied from scratch to the same (unchanged) task."""
    return f"""Your edits to deepen this task failed the automatic validation. They were applied on top of the
current task, and the current task has NOT changed: what is shown below is still the state before
your edits.

{_current_task(spec, gold_patch, test_content, test_path)}

Your previous edits:
<<<PREVIOUS
{json.dumps(prev.get("edits", []), indent=1)[:60000]}
PREVIOUS>>>

Validation failures:
<<<FAILURES
{failures}
FAILURES>>>

Fix the problems and return the complete set of edits again, in the same format (the edits are applied
to the current state shown above, not on top of your previous edits):
{_delta_format(test_path)}
- notes: one or two sentences on what you fixed.
If a test asserted something instruction.md does not state, remove that assertion or add the statement
to instruction.md. Keep the feature itself the same."""


def deepen(spec: str, gold_patch: str, test_content: str, probe: dict, test_path: str,
           mechanism: str = "") -> str:
    """Escalation: the measured solve rate of a weaker model was too high; make the task deeper."""
    depth = (f"\nWhere the difficulty must come from - {mechanism}\nAdding more independent rules is not "
             f"enough: a solver that gets each rule right still passes. Build the rework around this "
             f"mechanism.\n") if mechanism else ""
    failed = probe.get("failed_f2p") or {}
    if failed:
        fail_note = ("\nFeature tests the weaker model failed at least once - keep and build on this kind of "
                     "difficulty:\n" + "\n".join(f"- {t} ({n}x)" for t, n in list(failed.items())[:12]))
    else:
        fail_note = "\nIt did not fail a single feature test."
    return f"""This task passed validation but is too easy: a smaller model that saw only instruction.md and the
click source solved it in {probe['solved_strict']} of {probe['k']} one-shot attempts.{fail_note}

Rework the task so that it is substantially harder to implement correctly, while staying fair and
realistic. Keep the feature idea and its public names: make it deeper, not different.
{depth}
Also do all of these:
1. Add 4-6 behaviour rules that only matter when the feature meets existing click behaviour or its own
   other rules: precedence when two rules apply at once, interaction with value sources (command line,
   environment variables, default_map, defaults, prompts), nested and chained commands with context
   inheritance, resilient parsing and shell completion, the order of errors when several rules are
   violated together, and corner cases of the existing functions you change that must keep working.
2. Rewrite instruction.md declaratively. State observable outcomes, invariants and exact messages. Do not
   describe your algorithm, the order of internal checks, or which internal function to modify; where an
   order is observable, state it as an outcome ("when both A and B hold, the result is C"). Use compact
   input -> output tables for combinations of rules. Keep at most two short examples.
3. Tests: 14-22 tests. At least half must each depend on two or more rules at once and assert exact
   output. Every expected value must follow from rules stated in instruction.md.
4. The gold must change existing click code paths in at least two modules.

Fairness is non-negotiable: a careful engineer must still be able to implement everything from
instruction.md alone. Never rely on behaviour the text does not state.

Current instruction.md:
<<<SPEC
{spec}
SPEC>>>

Current gold, as a unified diff against the ORIGINAL click source:
<<<GOLD
{gold_patch[:60000]}
GOLD>>>

Current test file `{test_path}`:
<<<TESTS
{test_content[:60000]}
TESTS>>>

Return a complete new answer in the usual format: `edits` are search/replace edits against the ORIGINAL
click source shown in the system prompt (not against the current gold), covering the whole reworked
feature; `test_file_content` is the full test file `{test_path}`; `instruction_md` is the full revised
instruction.md."""


def repair(spec: str, prev: dict, failures: str, test_path: str) -> str:
    return f"""Your previous gold patch and tests for this task failed the automatic validation.

Current instruction.md:
<<<SPEC
{spec}
SPEC>>>

Your previous answer (edits are relative to the ORIGINAL click source; they are re-applied from scratch):
<<<PREVIOUS
{json.dumps(prev, indent=1)[:60000]}
PREVIOUS>>>

Validation failures:
<<<FAILURES
{failures}
FAILURES>>>

Fix the problems and return a complete new answer in the same format (all edits again, against the
original source, plus the full test file `{test_path}`). If a test asserted something the spec does not
state, either remove that assertion or add the statement to the spec and return the full revised
instruction_md. Keep the feature itself the same."""
