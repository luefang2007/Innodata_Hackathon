"""Prompt text and JSON schemas for the three model calls: design, implement, repair."""
from __future__ import annotations

import json
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


def design(cell: dict, siblings: list[str]) -> str:
    sib = "\n".join(f"- {s}" for s in siblings) or "- (none)"
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
leave no room for interpretation: when two behaviours are possible, the spec picks one explicitly."""


def implement(design_obj: dict, spec: str, test_path: str) -> str:
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
- notes: one or two sentences on what you implemented."""


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
