"""Stand-alone difficulty probe for emitted packages: the solver jury's weak juror, outside the pipeline.

A proxy, not the official benchmark: the official benchmark runs the terminus-2 agent in the task
container, where the model can run tests and iterate. See jury.py for how attempts are graded.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, jury, llm, package, repo


def load_candidate(task_dir: Path) -> tuple[checks.Candidate, list[str], list[str]]:
    cand = checks.Candidate(**package.load(task_dir))
    f2p = json.loads(repo.read_text(task_dir / "tests" / "fail_to_pass.json"))
    p2p = json.loads(repo.read_text(task_dir / "tests" / "pass_to_pass.json"))
    return cand, f2p, p2p


def run(task_dirs: list[Path], k: int, model: str, effort: str, workers: int, out_root: Path) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_root / run_id
    repo.build_snapshot(config.BASE_COMMIT, config.BASE_SNAPSHOT)
    from .watchdog import logged_spend
    llm.init_budget(logged_spend(config.ROOT.parent / "submission" / "runs") + logged_spend(out_root))
    tel = llm.Telemetry(model=model)
    t0 = time.time()
    summary = {"run_id": run_id, "model": model, "effort": effort, "k": k, "mode": "one-shot proxy", "tasks": {}}
    for d in task_dirs:
        cand, f2p, p2p = load_candidate(d)
        probe = jury.weak_probe(cand, f2p, p2p, out_dir / d.name, tel, k=k, model=model, effort=effort)
        summary["tasks"][d.name] = {"resolve": f"{probe['solved_strict']}/{probe['k']}", **jury.summarize(probe),
                                    "traps": probe["traps"]}
    summary["cost_usd"] = round(tel.cost_usd, 4)
    summary["wall_seconds"] = round(time.time() - t0, 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
