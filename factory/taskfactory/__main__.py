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
    c = sub.add_parser("compose", help="build multi-feature tasks from validated atoms (no model calls)")
    c.add_argument("atoms", nargs="+", type=Path)
    c.add_argument("--span", type=int, default=3)
    c.add_argument("--n", type=int, default=5)
    c.add_argument("--first-number", type=int, default=201)
    c.add_argument("--out", type=Path, default=config.ROOT.parent / "submission" / "generated_tasks_composite")
    c.add_argument("--atom-summary", type=Path, default=None,
                   help="summary.json of the run that generated the atoms (for cost attribution)")
    pr = sub.add_parser("probe", help="cheap one-shot difficulty proxy with a small model (not the benchmark)")
    pr.add_argument("tasks", nargs="+", type=Path)
    pr.add_argument("--k", type=int, default=3)
    pr.add_argument("--model", default="anthropic/claude-haiku-5.5")
    pr.add_argument("--effort", default="high")
    pr.add_argument("--workers", type=int, default=8)
    pr.add_argument("--out", type=Path, default=config.ROOT.parent / "submission" / "probe")
    u = sub.add_parser("usage", help="API tokens and estimated cost from the run logs (live)")
    u.add_argument("--runs", type=Path, default=config.ROOT.parent / "submission" / "runs")
    a = ap.parse_args(argv)

    if a.cmd == "compose":
        from .compose import run as compose_run
        from .pipeline import prepare
        prepare()
        s = compose_run(a.atoms, a.span, a.n, a.out, a.first_number, a.atom_summary)
        print(f"composed {s['valid']}/{s['requested']} tasks of span {s['span']} in {s['wall_seconds']:.0f}s")
        for r in s["candidates"]:
            g = r.get("gates", {})
            print(f"  {r['status']:12s} {' + '.join(x.split('-', 2)[-1] for x in r['atoms'])}"
                  + (f"  F2P={r['n_f2p']} P2P={r['n_p2p']} mutants={r['mutation']['killed']}/{r['mutation']['n']}"
                     if r["status"] == "valid" else f"  {r.get('error') or [k for k, v in g.get('gates', {}).items() if not v]}"))
        (a.out.parent / "compose_summary.json").write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
        return 0 if s["valid"] else 1
    if a.cmd == "probe":
        from .probe import run
        s = run(a.tasks, a.k, a.model, a.effort, a.workers, a.out)
        print(f"probe {s['run_id']}: {s['model']} ({s['mode']}, effort {s['effort']}), cost ${s['cost_usd']:.2f}, "
              f"{s['wall_seconds']:.0f}s")
        for name, t in s["tasks"].items():
            rewards = [r.get("reward", "infra") for r in t["rollouts"]]
            print(f"  {name:55s} resolved {t['resolve']}  rewards {rewards}")
        return 0
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
