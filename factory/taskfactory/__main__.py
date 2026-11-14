"""CLI: python -m taskfactory {prepare,generate,recheck}"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, pipeline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="taskfactory", description="click feature-task factory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="snapshots, coverage map, base health (no model calls)")
    g = sub.add_parser("generate", help="generate and self-check task packages")
    g.add_argument("--n", type=int, default=5)
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--out", type=Path, default=config.ROOT.parent / "submission" / "generated_tasks")
    g.add_argument("--workers", type=int, default=5)
    r = sub.add_parser("recheck", help="re-run the local gates on emitted packages")
    r.add_argument("tasks", nargs="+", type=Path)
    u = sub.add_parser("usage", help="API tokens and estimated cost from the run logs (live)")
    u.add_argument("--runs", type=Path, default=config.ROOT.parent / "submission" / "runs")
    a = ap.parse_args(argv)

    if a.cmd == "usage":
        from .usage import report
        print(report(a.runs))
        return 0

    if a.cmd == "prepare":
        print(json.dumps({k: v for k, v in pipeline.prepare().items() if k != "upstream_log"}, indent=1))
        return 0
    if a.cmd == "generate":
        s = pipeline.generate(a.n, a.seed, a.out, a.workers)
        print(pipeline.render_summary(s))
        return 0 if s["valid"] else 1
    if a.cmd == "recheck":
        res = pipeline.recheck(a.tasks)
        for row in res:
            flag = "PASS" if row["ok"] else "FAIL"
            print(f"{flag}  {row['task']}  F2P={row['n_f2p']} P2P={row['n_p2p']} "
                  f"mutation={row['mutation'].get('killed')}/{row['mutation'].get('n')}  gates={row['gates']}")
            if row["failures"]:
                print(row["failures"])
        return 0 if all(r["ok"] for r in res) else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
