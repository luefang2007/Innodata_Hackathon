"""Per-test coverage map of the base repo, used to curate the regression (P2P) slice.

`setup()` runs the whole click suite once at the base commit under coverage with per-test contexts and
stores, for every source line, the tests that executed it. `select_p2p()` then picks the existing tests
that execute the code a gold patch touches -- the regression slice is chosen by evidence, not by
dumping the whole suite.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from . import config, repo
from .runner import pytest_env

_CTX_SUFFIX = re.compile(r"\|(run|setup|teardown)$")
_PLATFORM_HINT = re.compile(r"skipif|xfail|sys\.platform|os\.name|WIN\b|platform\.system|stress", re.I)


def setup(force: bool = False) -> dict:
    """Build covmap.json and base_health.json for the base snapshot."""
    if config.COVMAP_FILE.exists() and config.BASE_HEALTH_FILE.exists() and not force:
        return json.loads(config.BASE_HEALTH_FILE.read_text())
    snap = repo.build_snapshot(config.BASE_COMMIT, config.BASE_SNAPSHOT)
    with repo.workcopy(snap) as wc:
        covfile = wc / ".coverage.factory"
        env = pytest_env(wc)
        env["COVERAGE_FILE"] = str(covfile)
        cmd = [sys.executable, "-m", "pytest", "tests", "-rA", "--tb=short", "-p", "no:cacheprovider",
               "--color=no", "-q", "--continue-on-collection-errors",
               f"--cov={wc / 'src' / 'click'}", "--cov-context=test", "--cov-report="]
        proc = subprocess.run(cmd, cwd=wc, env=env, capture_output=True, timeout=1800)
        out = proc.stdout.decode("utf-8", errors="replace")
        from .runner import _kit_parser
        statuses = {r.name: r.status.name for r in _kit_parser.parse_test_output(out, proc.stderr.decode(errors="replace"))}
        jfile = wc / "cov.json"
        subprocess.run([sys.executable, "-m", "coverage", "json", "--show-contexts", "-o", str(jfile)],
                       cwd=wc, env=env, capture_output=True, check=True)
        raw = json.loads(jfile.read_text())
        lines: dict[str, dict[str, list[str]]] = {}
        for fname, fdata in raw["files"].items():
            rel = Path(fname)
            try:
                rel = Path(fname).resolve().relative_to(wc.resolve())
            except ValueError:
                pass
            rel_s = rel.as_posix()
            per_line = {}
            for ln, ctxs in (fdata.get("contexts") or {}).items():
                tests = sorted({_CTX_SUFFIX.sub("", c) for c in ctxs if c})
                if tests:
                    per_line[ln] = tests
            lines[rel_s] = per_line
    config.COVMAP_FILE.write_text(json.dumps(lines))
    passed = sorted(t for t, s in statuses.items() if s == "PASSED")
    failed = sorted(t for t, s in statuses.items() if s in ("FAILED", "ERROR"))
    health = {"base_commit": config.BASE_COMMIT, "n_passed": len(passed), "n_failed": len(failed),
              "failed": failed, "passed": passed, "summary_tail": out.strip().splitlines()[-3:]}
    config.BASE_HEALTH_FILE.write_text(json.dumps(health, indent=1))
    return health


def _load():
    return json.loads(config.COVMAP_FILE.read_text()), json.loads(config.BASE_HEALTH_FILE.read_text())


def touched_regions(patch_text: str, base_dir: Path) -> dict[str, set[int]]:
    """Old-file line numbers a patch touches, widened to the enclosing function/method."""
    regions: dict[str, set[int]] = defaultdict(set)
    cur = None
    for line in patch_text.splitlines():
        if line.startswith("--- a/"):
            cur = line[6:].strip()
        elif line.startswith("--- /dev/null"):
            cur = None
        elif line.startswith("@@") and cur:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or "1")
                regions[cur].update(range(max(1, start), start + max(count, 1)))
    widened: dict[str, set[int]] = {}
    for rel, lns in regions.items():
        path = base_dir / rel
        if not rel.endswith(".py") or not path.exists():
            continue
        tree = ast.parse(repo.read_text(path))
        extra: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
                if span & lns:
                    extra |= span
        widened[rel] = lns | extra
    return widened


def _platform_sensitive(nodeid: str, base_dir: Path, cache: dict) -> bool:
    """Skip tests whose outcome may depend on the OS (they could pass here and skip in Linux)."""
    fpath = nodeid.split("::")[0]
    if fpath not in cache:
        src = (base_dir / fpath)
        cache[fpath] = repo.read_text(src) if src.exists() else ""
    text = cache[fpath]
    if re.search(r"^pytestmark\s*=.*(skipif|platform)", text, re.M):
        return True
    func = nodeid.split("::")[-1].split("[")[0]
    m = re.search(rf"((?:^[ \t]*@.*\n)*)^[ \t]*(?:async )?def {re.escape(func)}\(", text, re.M)
    return bool(m and _PLATFORM_HINT.search(m.group(1)))


def select_p2p(patch_text: str, base_dir: Path, limit: int = config.MAX_P2P_TESTS) -> list[str]:
    covmap, health = _load()
    passed = set(health["passed"])
    regions = touched_regions(patch_text, base_dir)
    score: dict[str, int] = defaultdict(int)
    for rel, lns in regions.items():
        per_line = covmap.get(rel, {})
        for ln in lns:
            for t in per_line.get(str(ln), []):
                score[t] += 1
    cache: dict = {}
    ranked = sorted((t for t in score if t in passed and not _platform_sensitive(t, base_dir, cache)),
                    key=lambda t: (-score[t], t))
    chosen = ranked[:limit]
    if len(chosen) < config.MIN_P2P_TESTS:
        # fallback: the test module named after each touched source module
        for rel in regions:
            stem = Path(rel).stem.lstrip("_")
            for t in sorted(passed):
                if t.startswith(f"tests/test_{stem}") and t not in chosen and not _platform_sensitive(t, base_dir, cache):
                    chosen.append(t)
                    if len(chosen) >= config.MIN_P2P_TESTS * 2:
                        break
    return chosen
