"""Cheap difficulty probe: a small model attempts each task ONE-SHOT and is graded locally.

This is a proxy, not the official benchmark. The official benchmark runs the terminus-2 agent in the
task container, where the model can run tests and iterate. A one-shot solver is weaker, so the signal
is one-sided: a task the probe solves is too easy; a task it fails still needs the real benchmark.
Grading mirrors tests/grade.py: any P2P failure -> 0.0; all F2P pass -> 1.0; else the F2P fraction.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, llm, package, repo
from .runner import run_pytest

SOLVER_RULES = """You are an expert Python engineer working in the `click` repository (pallets/click). Its
full library source is below. You will receive a feature request written as a GitHub issue. Implement
it in the library source so that it satisfies every stated requirement exactly, while keeping all
existing behaviour and the existing test suite working."""

SOLVER_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "search": {"type": "string"},
                               "replace": {"type": "string"}},
                "required": ["path", "search", "replace"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["edits", "notes"],
    "additionalProperties": False,
}


def solver_context(base_dir: Path) -> str:
    parts = [f"# click source at tag {config.BASE_TAG}\n"]
    for path in sorted((base_dir / "src" / "click").glob("*.py")):
        parts.append(f"\n===== FILE: {path.relative_to(base_dir).as_posix()} =====\n{repo.read_text(path)}")
    return "\n".join(parts)


def solver_prompt(spec: str, retry_error: str = "") -> str:
    extra = (f"\n\nYour previous edits could not be applied:\n{retry_error}\nReturn the complete set of "
             f"edits again, against the original source.") if retry_error else ""
    return f"""<issue>
{spec}
</issue>

Implement this issue. Return search/replace edits to existing files under src/click/ (a `search` block
must be copied verbatim from the current file and match exactly once; to create a new module use an
empty `search`). Do not write tests.{extra}"""


def grade(task_dir: Path, edits: list[dict]) -> dict:
    p = package.load(task_dir)
    f2p = json.loads(repo.read_text(task_dir / "tests" / "fail_to_pass.json"))
    p2p = json.loads(repo.read_text(task_dir / "tests" / "pass_to_pass.json"))
    with repo.workcopy(config.BASE_SNAPSHOT) as wc:
        touched, errors = checks.apply_edits(wc, edits)
        if errors:
            return {"applied": False, "errors": errors}
        # the hidden tests always win, exactly as in grade.py
        target = wc / p["test_path"]
        if target.exists():
            target.unlink()
        ok, err = repo.apply_patch(wc, p["test_patch"])
        if not ok:
            return {"applied": False, "errors": [f"test patch failed: {err}"]}
        st, _ = run_pytest(wc, [p["test_path"], *p2p])
    f2p_pass = sum(st.get(t) == "PASSED" for t in f2p)
    p2p_pass = sum(st.get(t) == "PASSED" for t in p2p)
    if p2p and p2p_pass < len(p2p):
        reward = 0.0
    elif f2p and f2p_pass == len(f2p):
        reward = 1.0
    else:
        reward = round(f2p_pass / len(f2p), 4) if f2p else 0.0
    return {"applied": True, "reward": reward, "resolved": reward == 1.0, "f2p": f"{f2p_pass}/{len(f2p)}",
            "p2p": f"{p2p_pass}/{len(p2p)}", "touched": touched}


def attempt(task_dir: Path, k_index: int, system: list[dict], model: str, effort: str, out_dir: Path,
            tel: llm.Telemetry) -> dict:
    spec = repo.read_text(task_dir / "instruction.md")
    log_dir = out_dir / task_dir.name / f"rollout_{k_index}"
    retry_error = ""
    for try_no in (1, 2):   # one retry only when the edits do not apply (formatting, not logic)
        try:
            ans = llm.call_json(system, solver_prompt(spec, retry_error), SOLVER_SCHEMA, tel, log_dir,
                                f"solve-try{try_no}", model=model, effort=effort, reserve=0.15)
        except llm.LLMError as exc:
            return {"rollout": k_index, "infra_error": str(exc)[:300]}
        res = grade(task_dir, ans["edits"])
        if res["applied"] or try_no == 2:
            res["rollout"] = k_index
            res["tries"] = try_no
            (log_dir / "grade.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
            return res
        retry_error = "\n".join(res["errors"])[:3000]
    return {"rollout": k_index, "infra_error": "unreachable"}


def run(task_dirs: list[Path], k: int, model: str, effort: str, workers: int, out_root: Path) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = out_root / run_id
    repo.build_snapshot(config.BASE_COMMIT, config.BASE_SNAPSHOT)
    from .watchdog import key_spend, logged_spend
    llm.init_budget(max(logged_spend(config.ROOT.parent / "submission" / "runs"), key_spend()))
    system = [{"type": "text", "text": SOLVER_RULES},
              {"type": "text", "text": solver_context(config.BASE_SNAPSHOT), "cache_control": {"type": "ephemeral"}}]
    tel = llm.Telemetry(model=model)
    t0 = time.time()
    jobs = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for d in task_dirs:
            for i in range(1, k + 1):
                jobs.append((d.name, pool.submit(attempt, d, i, system, model, effort, out_dir, tel)))
        results: dict[str, list] = {}
        for name, fut in jobs:
            results.setdefault(name, []).append(fut.result())
    summary = {"run_id": run_id, "model": model, "effort": effort, "k": k, "mode": "one-shot proxy",
               "cost_usd": round(tel.cost_usd, 4), "wall_seconds": round(time.time() - t0, 1), "tasks": {}}
    for name, rs in results.items():
        graded = [r for r in rs if "infra_error" not in r]
        solved = sum(1 for r in graded if r.get("resolved"))
        summary["tasks"][name] = {"resolve": f"{solved}/{len(graded)}", "rollouts": rs}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
