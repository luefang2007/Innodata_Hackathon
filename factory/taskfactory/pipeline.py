"""Orchestration: plan cells -> design -> implement -> gates -> repair loop -> emit package."""
from __future__ import annotations

import json
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, covmap, jury, llm, matrix, package, prompts, repo


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


def _to_valid(impl: dict, spec: str, test_path: str, system: list[dict], tel: llm.Telemetry, log_dir: Path,
              record: dict, tag: str, max_attempts: int, label: str = "") -> tuple | None:
    """Build -> gates -> repair loop. Returns (candidate, report, answer, spec) for the first candidate
    that clears every gate, or None when the attempts run out."""
    attempts = 0
    while True:
        attempts += 1
        record["attempts"] += 1
        if impl.get("instruction_md", "").strip():
            spec = impl["instruction_md"]
        cand, err = checks.build_candidate(impl["edits"], impl["test_file_content"], test_path, spec)
        if cand is None:
            failures = f"[build] the answer could not be applied:\n{err}"
            record["reports"].append({"attempt": record["attempts"], "stage": label.strip(),
                                      "build_error": err[:2000]})
            _say(tag, f"{label}attempt {record['attempts']}: edits did not apply")
        else:
            rep = checks.run_gates(cand)
            record["reports"].append({"attempt": record["attempts"], "stage": label.strip(), **rep.summary()})
            _say(tag, f"{label}attempt {record['attempts']}: " + ("VALID" if rep.ok else "failed " + ", ".join(
                k for k, v in rep.gates.items() if not v["ok"])) + f"  (${tel.cost_usd:.2f} so far)")
            if rep.ok:
                return cand, rep, impl, spec
            failures = rep.failures_text()
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"failures-attempt{record['attempts']}.txt").write_text(failures, encoding="utf-8")
        if attempts >= max_attempts:
            record["last_failures"] = failures[:3000]
            return None
        impl = llm.call_json(system, prompts.repair(spec, impl, failures, test_path), prompts.IMPL_SCHEMA,
                             tel, log_dir, f"repair{record['attempts']}")


def _jury_rounds(valid: tuple, test_path: str, system: list[dict], tel: llm.Telemetry, jtel: llm.Telemetry,
                 log_dir: Path, record: dict, tag: str, shape: str = "") -> tuple:
    """Measure difficulty with the weak juror and deepen the task while it is too easy.

    Returns the hardest valid (candidate, report, probe) seen. A valid task is never lost here: if
    deepening fails to validate, or a model call fails, the best candidate so far is kept.
    """
    cand, rep, impl, spec = valid
    best = None
    for rnd in range(config.JURY_MAX_ESCALATIONS + 1):
        probe = jury.weak_probe(cand, rep.f2p, rep.p2p, log_dir / f"jury-round{rnd}", jtel)
        record["jury"].append({"round": rnd, **jury.summarize(probe)})
        if probe["rate"] is None:
            _say(tag, f"jury round {rnd}: no graded attempt ({probe['not_graded']} not graded)")
            return best or (cand, rep, probe)
        _say(tag, f"jury round {rnd}: weak solver solved {probe['solved_strict']}/{probe['k']}, "
                  f"{len(probe['traps'])} regression traps  (jury ${jtel.cost_usd:.2f})")
        if best is None or probe["rate"] < best[2]["rate"]:
            best = (cand, rep, probe)
        if probe["rate"] <= config.JURY_MAX_WEAK_RATE or rnd == config.JURY_MAX_ESCALATIONS:
            break
        mech_name, mech_text = matrix.depth_for(shape, rnd + 1)
        record["jury"][-1]["deepen_with"] = mech_name
        try:
            impl = llm.call_json(system, prompts.deepen(cand.spec, cand.gold_patch, cand.test_content, probe,
                                                        test_path, mech_text),
                                 prompts.IMPL_SCHEMA, tel, log_dir, f"deepen{rnd + 1}")
        except llm.LLMError as exc:
            record["jury_note"] = f"deepening stopped: {exc}"[:300]
            break
        nxt = _to_valid(impl, spec, test_path, system, tel, log_dir, record, tag, max_attempts=2,
                        label=f"deepen{rnd + 1} ")
        if nxt is None:
            record["jury_note"] = "the deepened candidate did not validate; the previous one was kept"
            break
        cand, rep, impl, spec = nxt
    return best


def make_task(cell: dict, number: int, siblings: list[str], system: list[dict], out_dir: Path,
              run_dir: Path) -> dict:
    tel = llm.Telemetry()
    jtel = llm.Telemetry(model=config.JURY_WEAK_MODEL)
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
        valid = _to_valid(impl, spec, test_path, system, tel, log_dir, record, tag, config.MAX_ATTEMPTS)
        if valid is not None:
            cand, rep = valid[0], valid[1]
            p2p = list(rep.p2p)
            if config.JURY:
                record["jury"] = []
                try:
                    best_cand, best_rep, probe = _jury_rounds(valid, test_path, system, tel, jtel, log_dir,
                                                              record, tag, cell["shape"])
                    cand, rep = best_cand, best_rep
                    p2p = list(dict.fromkeys([*rep.p2p, *probe["traps"]]))
                    record["difficulty"] = {"weak_model": probe["model"], "weak_solved": probe["solved_strict"],
                                            "weak_k": probe["k"], "weak_rate": probe["rate"],
                                            "rounds": len(record["jury"]), "regression_traps": len(probe["traps"]),
                                            "hard_enough": probe["rate"] is not None
                                            and probe["rate"] <= config.JURY_MAX_WEAK_RATE}
                    if config.JURY_STRONG_MODEL and probe["rate"] is not None:
                        record["blind_strong"] = jury.strong_check(cand, rep.f2p, p2p, log_dir / "jury-strong",
                                                                   jtel)
                except Exception as exc:  # the jury must never cost us a valid task
                    record["jury_error"] = f"{type(exc).__name__}: {exc}"[:300]
                    log_dir.mkdir(parents=True, exist_ok=True)
                    (log_dir / "jury-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            package.emit(out_dir, iid, cand.spec, cand.gold_patch, cand.test_patch, cand.paths, rep.f2p, p2p)
            record.update({"status": "valid", "n_f2p": len(rep.f2p), "n_p2p": len(p2p),
                           "mutation": rep.mutation})
    except Exception as exc:  # keep the batch going; the record shows what happened
        record["error"] = f"{type(exc).__name__}: {exc}"
        _say(tag, f"ERROR {record['error'][:300]}")
        (log_dir).mkdir(parents=True, exist_ok=True)
        (log_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    record["wall_seconds"] = round(time.time() - t0, 1)
    record["telemetry"] = {k: v for k, v in tel.__dict__.items()}
    record["jury_telemetry"] = {k: v for k, v in jtel.__dict__.items()}
    return record


def generate(n: int, seed: int, out_dir: Path, workers: int) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-s{seed}"
    run_dir = out_dir.parent / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    prep = prepare()
    system = llm.system_blocks(prompts.RULES, prompts.repo_context(config.BASE_SNAPSHOT, prep["upstream_log"]))
    access = "offline: replaying cached calls only" if config.OFFLINE else llm.check_access()
    _say("run", f"backend {config.BACKEND}, model {config.MODEL}: {access}")
    from .watchdog import logged_spend
    runs_root = out_dir.parent / "runs"
    # The cap covers what this output directory has spent (its own run logs), not the key's lifetime
    # usage: a key that other work has already used must not stop a fresh run.
    already = logged_spend(runs_root)
    llm.init_budget(already)
    llm.reset_cache_gates()
    _say("run", f"budget: ${already:.2f} already spent here of ${config.BUDGET_USD:.2f} cap; "
                f"jury {'on (' + config.JURY_WEAK_MODEL + ')' if config.JURY else 'off'}")
    if config.RESPONSE_CACHE:
        seeded = llm.seed_cache_from_logs(runs_root, system, {"design": prompts.DESIGN_SCHEMA,
                                                              "impl": prompts.IMPL_SCHEMA})
        if seeded:
            _say("run", f"{seeded} earlier paid-for calls can be reused from the logs")
    shared = llm.Telemetry()      # no separate warm-up call: the first call per schema writes the cache
    _say("run", f"{run_id}: prepared; logs in {run_dir}")

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
        j = r.get("jury_telemetry") or {}          # the jury's calls are part of producing the task
        both = lambda key: t[key] + j.get(key, 0)  # noqa: E731
        row = {
            "instance_id": r["instance_id"], "model": t["model"], "wall_seconds": r["wall_seconds"],
            "model_calls": both("model_calls"), "attempts": r["attempts"],
            "n_input_tokens": both("n_input_tokens") + both("n_cache_read_tokens") + both("n_cache_write_tokens"),
            "n_output_tokens": both("n_output_tokens"), "n_cache_tokens": both("n_cache_read_tokens"),
            "cost_usd": round(both("cost_usd") + shared.cost_usd * share, 4),
        }
        if j.get("model_calls"):
            row["jury_model"] = j.get("model")
            row["jury_cost_usd"] = round(j.get("cost_usd", 0.0), 4)
        if r.get("difficulty"):
            row["weak_solve_rate"] = r["difficulty"]["weak_rate"]
        tasks.append(row)
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
             "| # | cell | instance_id | status | attempts | F2P | P2P | mutants killed | weak solver | cost $ |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in s["tasks"]:
        m = r.get("mutation") or {}
        d = r.get("difficulty") or {}
        weak = f"{d['weak_solved']}/{d['weak_k']} after {d['rounds']} round(s)" if d.get("weak_k") else "-"
        cost = r["telemetry"]["cost_usd"] + (r.get("jury_telemetry") or {}).get("cost_usd", 0.0)
        lines.append(f"| {r['number']} | {r['cell']['module']} x {r['cell']['shape']} | {r.get('instance_id', '-')} "
                     f"| {r['status']} | {r['attempts']} | {r.get('n_f2p', '-')} | {r.get('n_p2p', '-')} "
                     f"| {m.get('killed', '-')}/{m.get('n', '-')} | {weak} | {cost:.2f} |")
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
