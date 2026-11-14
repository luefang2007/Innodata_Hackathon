"""
Test Results Parser for pytest.

Parses the `-rA` short test summary section into the SWE-bench-Pro structured format:
  {"tests": [{"name": "path/to/test_x.py::TestClass::test_y", "status": "PASSED|FAILED|SKIPPED|ERROR"}, ...]}

Test ids are pytest nodeids verbatim, so they match the fail_to_pass / pass_to_pass ids.
"""

import dataclasses
import json
import re
import sys
from enum import Enum
from pathlib import Path
from typing import List


class TestStatus(Enum):
    PASSED = 1
    FAILED = 2
    SKIPPED = 3
    ERROR = 4


@dataclasses.dataclass
class TestResult:
    name: str
    status: TestStatus


### DO NOT MODIFY THE CODE ABOVE ###

# `pytest -rA` emits one line per test in the "short test summary info" block:
#   PASSED tests/test_mod.py::test_thing
#   PASSED tests/test_mod.py::TestGroup::test_beta
#   PASSED tests/test_mod.py::test_p[case1-1.0 MB]     <- NOTE: spaces in the id
#   FAILED tests/test_mod.py::test_other - AssertionError: ...
#   ERROR  tests/test_mod.py
#
# The parametrised form is why the node id cannot be matched with \S+: pytest
# builds ids from the argument values, so any string argument containing a space
# puts a space inside the [...] suffix. Matching \S+ silently drops every such
# test, which shows up as an F2P set far smaller than the number of cases the
# test_patch adds. Match the bracket suffix explicitly instead.
#
# The suffix cannot be [^\]]* either: an argument that is itself a list renders
# as test_x[["not", "a", "dict"]], so the id contains nested brackets and a
# match that stops at the first ] leaves a stray ] behind and fails the anchor.
# Take the shortest bracket run that ends where the id ends — at end of line, or
# at the " - <reason>" tail pytest appends to a failure — so a reason containing
# a ] cannot be swallowed into the node id either.
_SUMMARY = re.compile(
    r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+"
    r"(\S+?\.py(?:::[^\[\s]+)*(?:\[.*?\](?=\s+-\s|$))?)"
    r"(?:\s+-\s.*)?$"
)

# Fallback for collection errors, which are reported per-file without a nodeid.
_COLLECT_ERR = re.compile(r"^ERROR\s+(\S+\.py)\b")

_MAP = {
    "PASSED": TestStatus.PASSED,
    "XPASS": TestStatus.PASSED,
    "FAILED": TestStatus.FAILED,
    "XFAIL": TestStatus.SKIPPED,
    "SKIPPED": TestStatus.SKIPPED,
    "ERROR": TestStatus.ERROR,
}


# Start/end of the `-rA` summary block. Only lines inside it are test results.
_SUMMARY_START = re.compile(r"^=+\s*short test summary info\s*=+$")
_SUMMARY_END = re.compile(r"^=+.*\b(passed|failed|error|errors|no tests ran|skipped)\b.*=+$")

# `logger:file.py` — a captured-log record, NOT a test. pytest prints these as
# "ERROR    root:log.py:224 some message" inside the failure detail sections.
# They end in ".py", so a naive path match scores them as ERRORed tests and a
# perfectly good gold state C looks dirty.
_LOG_RECORD = re.compile(r"^[\w.]+:[\w.]+\.py$")


def _looks_like_nodeid(name: str) -> bool:
    if _LOG_RECORD.match(name):
        return False
    return "::" in name or "/" in name or name.endswith(".py")


def parse_test_output(stdout_content: str, stderr_content: str) -> List[TestResult]:
    results: List[TestResult] = []
    seen = set()
    all_lines = (stdout_content + "\n" + stderr_content).splitlines()

    # Prefer the summary block; fall back to the whole log if pytest died before
    # printing one (collection error, usage error).
    lines, inside = [], False
    for raw in all_lines:
        s = raw.strip()
        if _SUMMARY_START.match(s):
            inside = True
            continue
        if inside:
            if _SUMMARY_END.match(s):
                inside = False
                continue
            lines.append(s)
    if not lines:
        lines = [raw_line.strip() for raw_line in all_lines]

    for line in lines:
        m = _SUMMARY.match(line)
        if not m:
            m2 = _COLLECT_ERR.match(line)
            if m2:
                name, status = m2.group(1), TestStatus.ERROR
            else:
                continue
        else:
            status = _MAP.get(m.group(1))
            name = m.group(2)
        if status is None or not _looks_like_nodeid(name):
            continue
        key = (name, status.name)
        if key in seen:
            continue
        seen.add(key)
        results.append(TestResult(name=name, status=status))
    return results


### DO NOT MODIFY THE CODE BELOW ###

def main():
    stdout_file, stderr_file, output_file = sys.argv[1], sys.argv[2], sys.argv[3]
    stdout_content = Path(stdout_file).read_text(errors="replace") if Path(stdout_file).exists() else ""
    stderr_content = Path(stderr_file).read_text(errors="replace") if Path(stderr_file).exists() else ""
    results = parse_test_output(stdout_content, stderr_content)
    payload = {"tests": [{"name": r.name, "status": r.status.name} for r in results]}
    Path(output_file).write_text(json.dumps(payload, indent=2))
    print(f"Parsed {len(results)} test results -> {output_file}")


if __name__ == "__main__":
    main()
