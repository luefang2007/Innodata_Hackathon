"""Orchestration: plan cells -> design -> implement -> gates -> repair loop -> emit package."""
from __future__ import annotations

import json
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, covmap, llm, matrix, package, prompts, repo


def prepare() -> dict:
    """Snapshots of base and live upstream main, coverage map, base health."""
    main_sha = repo.fetch_upstream()
    repo.build_snapshot(config.BASE_COMMIT, config.BASE_SNAPSHOT)
    repo.build_snapshot(main_sha, config.MAIN_SNAPSHOT)
    health = covmap.setup()
    log = repo.git(["log", "--oneline", "--no-decorate", f"{config.BASE_COMMIT}..{main_sha}"],
                   config.CLONE_DIR).stdout.decode()
    return {"main_sha": main_sha, "upstream_log": log, "base_passed": health["n_passed"],
            "base_failed": health["n_failed"]}


def _say(tag: str, msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [{tag}] {msg}", flush=True)


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return "-".join(s.split("-")[:5])[:40] or "feature"


def make_task(cell: dict, number: int, siblings: list[str], system: list[dict], out_dir: Path,
              run_dir: Path) -> dict:
    tel = llm.Telemetry()
    t0 = time.time()
    tag = f"{number:04d}-{cell['module']}-{cell['shape']}"
    log_dir = run_dir / tag
    record = {"cell": cell, "number": number, "status": "failed", "attempts": 0, "reports": []}
    try:
        design = llm.call_json(system, prompts.design(cell, siblings), prompts.DESIGN_SCHEMA, tel,
                               log_dir, "design")
        slug = _slugify(design["slug"])
        iid = f"{config.ORG_REPO_SLUG}-{number:04d}-{slug}"
        test_path = f"tests/test_{slug.replace('-', '_')}.py"
        spec = design["instruction_md"]
        record.update({"instance_id": iid, "title": design["title"], "capability": design["capability"]})
        _say(tag, f"design: {design['title']}  (${tel.cost_usd:.2f} so far)")
        impl = llm.call_json(system, prompts.implement(design, spec, test_path), prompts.IMPL_SCHEMA, tel,
                             log_dir, "implement")
        while True:
            record["attempts"] += 1
            if impl.get("instruction_md", "").strip():
                spec = impl["instruction_md"]
            cand, err = checks.build_candidate(impl["edits"], impl["test_file_content"], test_path, spec)
            if cand is None:
                failures = f"[build] the answer could not be applied:\n{err}"
                record["reports"].append({"attempt": record["attempts"], "build_error": err[:2000]})
            else:
                rep = checks.run_gates(cand)
                record["reports"].append({"attempt": record["attempts"], **rep.summary()})
                _say(tag, f"attempt {record['attempts']}: " + ("VALID" if rep.ok else "failed " + ", ".join(
                    k for k, v in rep.gates.items() if not v["ok"])) + f"  (${tel.cost_usd:.2f} so far)")
                if rep.ok:
                    package.emit(out_dir, iid, cand.spec, cand.gold_patch, cand.test_patch, test_path,
                                 rep.f2p, rep.p2p)
                    record.update({"status": "valid", "n_f2p": len(rep.f2p), "n_p2p": len(rep.p2p),
                                   "mutation": rep.mutation})
                    break
                failures = rep.failures_text()
            (log_dir / f"failures-attempt{record['attempts']}.txt").write_text(failures, encoding="utf-8")
            if cand is None:
                _say(tag, f"attempt {record['attempts']}: edits did not apply")
            if record["attempts"] >= config.MAX_ATTEMPTS:
                record["last_failures"] = failures[:3000]
                break
            impl = llm.call_json(system, prompts.repair(spec, impl, failures, test_path), prompts.IMPL_SCHEMA,
                                 tel, log_dir, f"repair{record['attempts']}")
    except Exception as exc:  # keep the batch going; the record shows what happened
        record["error"] = f"{type(exc).__name__}: {exc}"
        _say(tag, f"ERROR {record['error'][:300]}")
        (log_dir).mkdir(parents=True, exist_ok=True)
        (log_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    record["wall_seconds"] = round(time.time() - t0, 1)
    record["telemetry"] = {k: v for k, v in tel.__dict__.items()}
    return record


def generate(n: int, seed: int, out_dir: Path, workers: int) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-s{seed}"
    run_dir = out_dir.parent / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    prep = prepare()
    system = llm.system_blocks(prompts.RULES, prompts.repo_context(config.BASE_SNAPSHOT, prep["upstream_log"]))
    _say("run", f"backend {config.BACKEND}, model {config.MODEL}: {llm.check_access()}")
    from .watchdog import key_spend, logged_spend
    runs_root = out_dir.parent / "runs"
    already = max(logged_spend(runs_root), key_spend())
    llm.init_budget(already)
    _say("run", f"budget: ${already:.2f} already spent of ${config.BUDGET_USD:.2f} cap")
    if config.RESPONSE_CACHE:
        n = llm.seed_cache_from_logs(runs_root, system, {"design": prompts.DESIGN_SCHEMA,
                                                         "impl": prompts.IMPL_SCHEMA})
        if n:
            _say("run", f"{n} earlier paid-for calls can be reused from the logs")
    shared = llm.Telemetry()
    # All tasks start with a design call at the same moment: warm the design-schema prefix once so
    # they read it instead of each writing it. Implement/repair calls are staggered and warm themselves.
    llm.prewarm(system, prompts.DESIGN_SCHEMA, shared, run_dir, "prewarm-design")
    _say("run", f"{run_id}: prepared, cache warmed (${shared.cost_usd:.2f}); logs in {run_dir}")

    cells = matrix.plan(n, seed)
    labels = [f"{c['module']} x {c['shape']}: {c['area'].split('(')[0].strip()} / {c['shape_desc'].split(':')[0]}"
              for c in cells]
    jobs = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, cell in enumerate(cells):
            siblings = [l for j, l in enumerate(labels) if j != i]
            jobs.append(pool.submit(make_task, cell, seed * 100 + i + 1, siblings, system, out_dir, run_dir))
        records = [j.result() for j in jobs]

    summary = {"run_id": run_id, "seed": seed, "model": config.MODEL, "effort": config.EFFORT,
               "base_commit": config.BASE_COMMIT, "upstream_main": prep["main_sha"],
               "requested": n, "valid": sum(r["status"] == "valid" for r in records),
               "wall_seconds": round(time.time() - t0, 1), "prewarm": shared.__dict__, "tasks": records}
    summary["first_pass_yield"] = round(summary["valid"] / n, 3) if n else 0.0
    spent, cap = llm.budget_state()
    summary["budget"] = {"cap_usd": cap, "spent_total_usd": round(spent, 4)}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    write_pipeline_run(records, out_dir.parent / "pipeline_run.json", shared)
    (run_dir / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


def write_pipeline_run(records: list[dict], path: Path, shared: llm.Telemetry) -> None:
    tasks = []
    valid = [r for r in records if r["status"] == "valid"]
    share = 1 / len(valid) if valid else 0
    for r in valid:
        t = r["telemetry"]
        tasks.append({
            "instance_id": r["instance_id"], "model": t["model"], "wall_seconds": r["wall_seconds"],
            "model_calls": t["model_calls"], "attempts": r["attempts"],
            "n_input_tokens": t["n_input_tokens"] + t["n_cache_read_tokens"] + t["n_cache_write_tokens"],
            "n_output_tokens": t["n_output_tokens"], "n_cache_tokens": t["n_cache_read_tokens"],
            "cost_usd": round(t["cost_usd"] + shared.cost_usd * share, 4),
        })
    failed = [{"cell": f"{r['cell']['module']}x{r['cell']['shape']}", "attempts": r["attempts"],
               "cost_usd": round(r["telemetry"]["cost_usd"], 4), "error": r.get("error", "")}
              for r in records if r["status"] != "valid"]
    payload = {"pipeline": "click-task-factory/0.1", "tasks": tasks, "discarded_candidates": failed}
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def render_summary(s: dict) -> str:
    lines = [f"# Run {s['run_id']}", "",
             f"- model `{s['model']}` (effort {s['effort']}), base `{s['base_commit'][:12]}`, "
             f"upstream main `{s['upstream_main'][:12]}`",
             f"- valid packages: **{s['valid']}/{s['requested']}** (first-pass yield {s['first_pass_yield']:.0%}), "
             f"wall {s['wall_seconds']:.0f}s", "",
             "| # | cell | instance_id | status | attempts | F2P | P2P | mutants killed | cost $ |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in s["tasks"]:
        m = r.get("mutation") or {}
        lines.append(f"| {r['number']} | {r['cell']['module']} x {r['cell']['shape']} | {r.get('instance_id', '-')} "
                     f"| {r['status']} | {r['attempts']} | {r.get('n_f2p', '-')} | {r.get('n_p2p', '-')} "
                     f"| {m.get('killed', '-')}/{m.get('n', '-')} | {r['telemetry']['cost_usd']:.2f} |")
    return "\n".join(lines) + "\n"


def recheck(task_dirs: list[Path]) -> list[dict]:
    prepare()
    out = []
    for d in task_dirs:
        p = package.load(d)
        cand = checks.Candidate(**p)
        rep = checks.run_gates(cand)
        out.append({"task": d.name, **rep.summary(),
                    "failures": rep.failures_text() if not rep.ok else ""})
    return out
