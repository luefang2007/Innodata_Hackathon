#!/usr/bin/env python
"""Live demo with no API spend (run it through `demo.cmd` in the repo folder).

1. generate  five packages from pallets/click. The model's answers are replayed from the response cache
             (FACTORY_OFFLINE=1 is forced here, so no model can be called); every check runs live.
2. show      one fresh package: the feature request, each check on base and reference, the existing
             tests that ran.
3. measure   how often Haiku 5.5 solved each submitted task (recorded jury run), and the recorded Harbor
             self-check. With --regrade the jury's attempts are graded again inside the task containers
             (Docker in WSL, about 50 s).

Offline, a step that needs a new model answer stops: a task whose first draft fails a check cannot be
repaired without a live call. The script says so when it happens.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACTORY = ROOT / "factory"
SUB = ROOT / "submission_v2"
# The factory's own interpreter (it has anthropic, pytest, coverage). This script itself needs only the
# standard library, so it can be started with any Python; the pipeline steps always run in this venv.
VENV_PY = FACTORY / ".cache" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

GATES = {
    "hygiene": "the feature request gives no hints and leaks no solution",
    "grounding": "the reference solution extends existing click code",
    "gold_tests": "the new tests PASS with the reference solution",
    "feature_absent": "the new tests FAIL on the base (click 8.5.1)",
    "regression": "the chosen existing tests pass on the base and with the reference",
    "mutation": "the tests catch bugs injected into the reference",
    "fairness": "the tests assert only what the feature request states",
    "novelty": "the feature is not already in upstream click",
}


def banner(text: str) -> None:
    print(f"\n\033[96m== {text} ==\033[0m", flush=True)


def latest_summary(d: Path) -> dict:
    runs = sorted(p for p in (d / "runs").iterdir() if (p / "summary.json").exists())
    return json.loads((runs[-1] / "summary.json").read_text(encoding="utf-8"))


def generate(demo: Path) -> None:
    env = {**os.environ, "FACTORY_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
    cmd = [str(VENV_PY), "-u", "-m", "taskfactory", "generate", "--n", "5", "--seed", "1",
           "--out", str(demo / "generated_tasks")]
    print("FACTORY_OFFLINE=1  python -m taskfactory generate --n 5 --seed 1 --out ../demo/generated_tasks\n",
          flush=True)
    subprocess.run(cmd, cwd=FACTORY, env=env, check=False)


def show_fresh(demo: Path) -> None:
    s = latest_summary(demo)
    ok = [r for r in s["tasks"] if r["status"] == "valid"]
    print(f"{len(ok)} of {len(s['tasks'])} packages written to {demo / 'generated_tasks'}")
    for r in s["tasks"]:
        if r["status"] == "valid":
            continue
        gates = (r.get("reports") or [{}])[-1].get("gates", {})
        bad = ", ".join(k for k, v in gates.items() if not v) or "a check"
        print(f"  {r['number']:04d} stopped: its first draft was rejected by the check `{bad}`. The repair step "
              f"needs a live model call,\n       so the offline replay ends here (in the recorded run it passed "
              f"on attempt 2).")
    if not ok:
        return
    r = ok[0]
    pkg = demo / "generated_tasks" / r["instance_id"]
    text = (pkg / "instruction.md").read_text(encoding="utf-8")
    f2p = json.loads((pkg / "tests" / "fail_to_pass.json").read_text(encoding="utf-8"))
    p2p = json.loads((pkg / "tests" / "pass_to_pass.json").read_text(encoding="utf-8"))
    rep = r["reports"][-1]

    banner(f"2a. the feature request: {r['instance_id']}/instruction.md")
    shown = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        print("   " + (line if len(line) <= 160 else line[:157] + "..."))
        shown += 1
        if shown == 4:
            break
    print(f"   ... {len(text.split())} words in total")

    banner("2b. the checks on the base and on the reference solution")
    for name, passed in rep["gates"].items():
        extra = ""
        if name in ("gold_tests", "feature_absent"):
            extra = f"  ({len(f2p)} new tests)"
        elif name == "regression":
            extra = f"  ({len(p2p)} existing tests)"
        elif name == "mutation":
            extra = f"  ({rep['mutation'].get('killed')} of {rep['mutation'].get('n')} caught)"
        print(f"   {'PASS' if passed else 'FAIL'}  {GATES.get(name, name)}{extra}")

    banner(f"2c. the existing click tests that also ran: {len(p2p)}, chosen by coverage of the changed lines")
    for t in p2p[:6]:
        print("   " + t)
    if len(p2p) > 6:
        print(f"   ... and {len(p2p) - 6} more (tests/pass_to_pass.json)")


def show_measured(sub: Path) -> None:
    s = latest_summary(sub)
    print(f"Haiku 5.5, one-shot, 3 attempts per round, inside the generation loop (recorded run {s['run_id']}).")
    print("A task that Haiku solves more than once is deepened and measured again.\n")
    for r in s["tasks"]:
        steps = []
        for j in r.get("jury") or []:
            steps.append(f"round {j['round']}: solved {j['solved_strict']}/{j['k']}")
            if j.get("deepen_with"):
                steps.append(f"deepen ({j['deepen_with']})")
        d = r.get("difficulty") or {}
        shipped = f"shipped at {d['weak_solved']}/{d['weak_k']}" if d.get("weak_k") else ""
        note = f"   [{r['jury_note']}]" if r.get("jury_note") else ""
        print(f"   {r['number']:04d} {r['instance_id'].split('-', 2)[-1]:36s} " + " -> ".join(steps)
              + f"  =>  {shipped}{note}")
    sc = sub / "selfcheck" / "SUMMARY.txt"
    if sc.exists():
        lines = sc.read_text(encoding="utf-8").splitlines()
        print("\nHarbor self-check of the submitted packages (recorded; harness/selfcheck.sh):")
        for line in lines[:2]:
            print("   " + line.lstrip("# "))
        for i, line in enumerate(lines):
            if line.startswith("== "):
                print("   " + line.strip("= ") + "   " + " | ".join(x.strip() for x in lines[i + 1:i + 3]))


def regrade(sub: Path, demo: Path) -> None:
    run = sorted(p for p in (sub / "runs").iterdir() if (p / "summary.json").exists())[-1]
    cmd = [str(VENV_PY), "-u", "-m", "taskfactory", "regrade", "--run", str(run),
           "--tasks", str(sub / "generated_tasks"), "--out", str(demo / "probe_container")]
    subprocess.run(cmd, cwd=FACTORY, env={**os.environ, "FACTORY_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"},
                   check=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Live demo: replayed model answers, live checks, no API spend.")
    ap.add_argument("--out", type=Path, default=ROOT / "demo")
    ap.add_argument("--skip-generate", action="store_true", help="only show the last demo run")
    ap.add_argument("--regrade", action="store_true",
                    help="grade the jury's Haiku attempts again inside the task containers (Docker in WSL)")
    a = ap.parse_args()
    os.system("")                       # switches ANSI colours on in a Windows console
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    t0 = time.time()
    if (not a.skip_generate or a.regrade) and not VENV_PY.exists():
        # Checked before anything is deleted, so a failed start never wipes the last demo run.
        print(f"{VENV_PY} is missing: run `python -m taskfactory prepare` in factory/ first; the replay also "
              "needs the response cache (factory/.cache/llm_cache) of the machine that made the recorded run.")
        return 1
    if not a.skip_generate:
        shutil.rmtree(a.out, ignore_errors=True)
        banner("1. generate and self-check five packages (model answers replayed, every check live)")
        generate(a.out)
        print(f"\n(generation and checks took {time.time() - t0:.0f} s)")
    show_fresh(a.out)
    banner("3. measured difficulty of the submitted tasks")
    show_measured(SUB)
    if a.regrade:
        banner("3b. the same Haiku attempts, graded again inside the task containers")
        regrade(SUB, a.out)
    print(f"\ntotal {time.time() - t0:.0f} s, API calls made: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
