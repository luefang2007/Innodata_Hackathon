#!/usr/bin/env python
"""Offline self-test of the deepening path. No model is called and no key is needed.

    cd factory
    python selftest_deepen.py [--task ../submission_v2/generated_tasks/<package>]

What it checks, on a real emitted package:
1. the design and implement prompts are unchanged unless depth is asked for (FACTORY_DESIGN_DEPTH)
2. a delta (edits to the gold-applied source, to the test file and to instruction.md) rebuilds into a
   candidate that clears every gate, with the old gold kept and the new test graded
3. a bad delta is reported as text for the repair prompt, never raised
4. the jury loop with a stubbed model: a deepen answer that is cut off at the output limit is retried
   with less reasoning effort, the delta is validated, and the harder candidate is the one returned

The model transport is replaced by stubs before anything runs, so this cannot spend money.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from taskfactory import checks, config, jury, llm, matrix, package, pipeline, prompts, repo

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return bool(ok)


def no_network(*_a, **_k):
    raise AssertionError("selftest: a real model call was attempted")


def synthetic_delta(prev: checks.Candidate, test_path: str) -> list[dict]:
    """A harmless delta in the same shape a model returns: one source edit, one new test, one spec line."""
    files = re.findall(r"^\+\+\+ b/(\S+)", prev.gold_patch, re.M)
    with repo.workcopy(config.BASE_SNAPSHOT) as wc:
        ok, err = repo.apply_patch(wc, prev.gold_patch)
        assert ok, err
        text = repo.read_text(wc / files[-1])
    lines = text.rstrip("\n").split("\n")
    n = 2
    while text.count("\n".join(lines[-n:])) != 1:
        n += 1
    tail = "\n".join(lines[-n:])
    tree = ast.parse(prev.test_content)
    fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name.startswith("test_")
              and not x.decorator_list)
    copy = ast.get_source_segment(prev.test_content, fn).replace(f"def {fn.name}(", "def test_selftest_copy(", 1)
    return [
        {"path": files[-1], "search": tail,
         "replace": tail + "\n\n\ndef _selftest_marker() -> int:\n    return 1"},
        {"path": test_path, "search": "", "replace": copy},
        {"path": "instruction.md", "search": "", "replace": "## Notes\n\nNothing else changes."},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="offline self-test of the deepening path")
    ap.add_argument("--task", type=Path, default=None, help="an emitted package to use as the starting task")
    a = ap.parse_args()
    task = a.task or sorted((config.ROOT.parent / "submission_v2" / "generated_tasks").iterdir())[1]

    # no key, no transport: nothing below can reach a model
    for key in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        os.environ.pop(key, None)
    llm._or_request = no_network
    llm.client = no_network

    pipeline.prepare()
    t0 = time.time()
    prev = checks.Candidate(**package.load(task))
    test_path = prev.test_path
    f2p = json.loads((task / "tests" / "fail_to_pass.json").read_text(encoding="utf-8"))
    p2p = json.loads((task / "tests" / "pass_to_pass.json").read_text(encoding="utf-8"))
    print(f"starting task: {task.name}  (F2P {len(f2p)}, P2P {len(p2p)})\n")

    # 1 ---- prompts
    cell = matrix.plan(5, 1)[0]
    sib = ["help x interaction: help pages / a rule system", "parser x api: parsing / an extension API"]
    mech = matrix.depth_for(cell["shape"], 1)[1]
    plain = prompts.design(cell, sib)
    deep = prompts.design(cell, sib, mech)
    check("design prompt is unchanged without depth", plain == prompts.design(cell, sib, ""))
    check("design prompt with depth adds the mechanism", deep.startswith(plain) and mech in deep
          and "Where the difficulty must come from" in deep)
    d_obj = {"title": "t", "capability": "c", "touch_points": [], "rules": []}
    check("implement prompt is unchanged without depth",
          prompts.implement(d_obj, "spec", test_path) == prompts.implement(d_obj, "spec", test_path, combined=False))
    check("implement prompt with depth asks for combined tests",
          "two or more rules at once" in prompts.implement(d_obj, "spec", test_path, combined=True))
    post = prompts.post_image(prev.gold_patch)
    added = [l[1:] for l in prev.gold_patch.splitlines() if l.startswith("+") and not l.startswith("+++")]
    check("current-content view holds every line the gold added", all(l in post for l in added if l.strip()),
          f"{len(added)} added lines")
    check("current-content view has no diff markers",
          not any(l.startswith(("+++", "@@", "diff --git")) for l in post.splitlines()))
    probe_easy = {"model": "stub", "k": 3, "solved": 3, "solved_strict": 3, "rate": 1.0, "rewards": [1.0] * 3,
                  "not_graded": 0, "traps": [], "failed_f2p": {}, "regressions": {}}
    rewrite_p = prompts.deepen(prev.spec, prev.gold_patch, prev.test_content, probe_easy, test_path, mech)
    delta_p = prompts.deepen_delta(prev.spec, prev.gold_patch, prev.test_content, probe_easy, test_path, mech)
    check("delta prompt states the edit format", "empty\n  `search`" in delta_p and "instruction.md" in delta_p
          and "never repeat the parts" in delta_p, f"{len(delta_p):,} chars; rewrite prompt {len(rewrite_p):,}")

    # 2 ---- a delta rebuilds into a valid candidate
    delta = synthetic_delta(prev, test_path)
    cand, err = checks.build_delta_candidate(prev, delta, test_path)
    if check("delta applies on top of the current gold", cand is not None, err[:200]):
        old_added = sum(1 for l in prev.gold_patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
        new_added = sum(1 for l in cand.gold_patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
        check("new gold is one diff against the base that keeps the old gold",
              "_selftest_marker" in cand.gold_patch and new_added >= old_added + 2
              and all(l in cand.gold_patch for l in added[:40]), f"+{old_added} -> +{new_added} lines")
        check("test file gained the appended test", cand.test_content.count("\ndef test_")
              == prev.test_content.count("\ndef test_") + 1 and "def test_selftest_copy(" in cand.test_content)
        check("instruction.md gained the appended text", cand.spec.rstrip().endswith("Nothing else changes.")
              and cand.spec.startswith(prev.spec.rstrip("\n")[:200]))
        check("the starting task is untouched", prev.spec == package.load(task)["spec"]
              and "_selftest_marker" not in prev.gold_patch)
        rep = checks.run_gates(cand)
        check("the rebuilt candidate clears every gate", rep.ok,
              f"F2P {len(rep.f2p)}, P2P {len(rep.p2p)}, mutants {rep.mutation.get('killed')}/{rep.mutation.get('n')}"
              + ("" if rep.ok else " | " + rep.failures_text()[:300]))
        check("the appended test is graded", len(rep.f2p) == len(f2p) + 1, f"{len(f2p)} -> {len(rep.f2p)}")

    # 3 ---- bad deltas come back as text
    bad = [
        ([{"path": test_path, "search": "this text is not in the file", "replace": "x"}], "matches 0 times"),
        ([{"path": "docs/index.rst", "search": "a", "replace": "b"}], "outside src/click"),
        ([], "no edit"),
        ([{"path": "src/click/core.py", "search": "def nowhere_at_all():", "replace": "pass"}], "matches 0 times"),
    ]
    for edits, needle in bad:
        c, e = checks.build_delta_candidate(prev, edits, test_path)
        check(f"bad delta reported: {needle}", c is None and needle in e)

    # 4 ---- the jury loop with a stubbed model
    calls: list[tuple[str, str | None]] = []

    def fake_model(system, user_text, schema, telemetry, log_dir, tag, t_start, effort=None):
        calls.append((tag, effort))
        if len(calls) == 1:
            raise llm.Truncated("response truncated at max_tokens")
        return {"edits": delta, "test_file_content": "", "instruction_md": "", "notes": "selftest"}

    probe_hard = {**probe_easy, "solved": 1, "solved_strict": 1, "rate": 1 / 3, "rewards": [1.0, 0.9, 0.9],
                  "failed_f2p": {f"{test_path}::test_selftest_copy": 2}}
    probes = iter([probe_easy, probe_hard])
    saved = (llm._call_json_uncached, jury.weak_probe, config.OFFLINE, config.DEEPEN_MODE)
    llm._call_json_uncached = fake_model
    jury.weak_probe = lambda *args, **kw: next(probes)
    config.OFFLINE, config.DEEPEN_MODE = False, "delta"
    llm.init_budget(0.0)
    llm.reset_cache_gates()
    record = {"attempts": 0, "reports": [], "jury": []}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rep0 = checks.Report(f2p=list(f2p), p2p=list(p2p))
            best = pipeline._jury_rounds((prev, rep0, {}, prev.spec), test_path, [{"type": "text", "text": "stub"}],
                                         llm.Telemetry(), llm.Telemetry(model="stub"), Path(tmp), record,
                                         "selftest", "interaction")
    finally:
        llm._call_json_uncached, jury.weak_probe, config.OFFLINE, config.DEEPEN_MODE = saved
    check("a cut-off deepen answer is retried with less effort",
          calls == [("deepen1", config.DEEPEN_EFFORT or None), ("deepen1-retry", config.DEEPEN_RETRY_EFFORT)],
          str(calls))
    check("the jury loop returns the deepened, harder candidate",
          best[0].spec.rstrip().endswith("Nothing else changes.") and best[2]["rate"] < 1.0
          and len(record["jury"]) == 2 and record["jury"][0].get("deepen_mode") == "delta",
          f"rounds {[(j['round'], j['solved_strict'], j.get('deepen_with')) for j in record['jury']]}")
    check("the offline switch is back on" if saved[2] else "the offline switch is restored",
          config.OFFLINE == saved[2])

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed in {time.time() - t0:.0f} s; "
          f"model calls made: 0")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
