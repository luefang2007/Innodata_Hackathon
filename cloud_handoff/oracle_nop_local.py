"""Emulate the Harbor oracle and nop runs for every emitted package without Docker.

For each package it applies the package's own patches to the base snapshot, runs the test files its
run_script.sh lists, and scores the result with the package's own fail_to_pass.json and
pass_to_pass.json exactly like tests/grade.py does. Expected: oracle 1.0 and nop 0.0 for every task.
It also compares the package's P2P list with what the coverage map selects on this machine.

Needs the factory environment (`python -m taskfactory prepare` must have run once). No model calls.

Usage (from the repo root, with the factory venv's python):
    python cloud_handoff/oracle_nop_local.py [submission/generated_tasks]
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "factory"))

from taskfactory import config, covmap, repo  # noqa: E402
from taskfactory.runner import run_pytest  # noqa: E402


def reward(status: dict, f2p: list, p2p: list) -> tuple[float, int, int]:
    f_ok = sum(status.get(t) == "PASSED" for t in f2p)
    p_ok = sum(status.get(t) == "PASSED" for t in p2p)
    if p2p and p_ok < len(p2p):
        r = 0.0
    elif f2p and f_ok >= len(f2p):
        r = 1.0
    else:
        r = round(f_ok / len(f2p), 4) if f2p else 0.0
    return r, f_ok, p_ok


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "submission" / "generated_tasks"
    bad = 0
    for d in sorted(p for p in root.iterdir() if (p / "task.toml").exists()):
        f2p = json.loads((d / "tests" / "fail_to_pass.json").read_text())
        p2p = json.loads((d / "tests" / "pass_to_pass.json").read_text())
        gold = repo.read_text(d / "solution" / "patch.diff")
        test_patch = repo.read_text(d / "tests" / "test_patch.diff")
        files = re.findall(r'^\s+"(tests/\S+)"$', repo.read_text(d / "tests" / "run_script.sh"), re.M)
        out = {}
        for mode in ("oracle", "nop"):
            with repo.workcopy(config.BASE_SNAPSHOT) as wc:
                if mode == "oracle":
                    ok, err = repo.apply_patch(wc, gold)
                    assert ok, err
                ok, err = repo.apply_patch(wc, test_patch)
                assert ok, err
                status, _ = run_pytest(wc, files)
            out[mode] = reward(status, f2p, p2p)
        fresh = set(covmap.select_p2p(gold, config.BASE_SNAPSHOT))
        jac = len(fresh & set(p2p)) / len(fresh | set(p2p)) if fresh | set(p2p) else 1.0
        ok = out["oracle"][0] == 1.0 and out["nop"][0] == 0.0
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {d.name}\n"
              f"      oracle reward={out['oracle'][0]} (F2P {out['oracle'][1]}/{len(f2p)}, P2P {out['oracle'][2]}/{len(p2p)})"
              f"  nop reward={out['nop'][0]} (F2P {out['nop'][1]}/{len(f2p)}, P2P {out['nop'][2]}/{len(p2p)})"
              f"  P2P list vs reselection here: jaccard={jac:.2f}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
