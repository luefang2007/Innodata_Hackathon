"""Feature-lattice composition: harder tasks from already-verified features, with no model calls.

A generated task ("atom") adds one feature. A composite task of span k asks the solver to land k atoms
in ONE change: one merged spec, the atoms' gold patches applied together as one gold, all their feature
tests, and a regression slice re-selected for the combined change. Difficulty becomes a dial (span),
and the factory can produce tasks harder than any single generation call. Every composite is
re-validated with the same local gates as an atom: compatibility of the golds, cross-feature
regressions and test strength are checked again on the combination, not assumed.
"""
from __future__ import annotations

import itertools
import json
import math
import re
import time
from pathlib import Path

from . import checks, config, package, repo


def _short(iid: str) -> str:
    slug = re.sub(r"^.*?-\d{4}-", "", iid)
    return "-".join(slug.split("-")[-2:])


def load_atom(d: Path) -> dict:
    p = package.load(d)
    title = next((l[2:].strip() for l in p["spec"].splitlines() if l.startswith("# ")), d.name)
    files = sorted(set(re.findall(r"^\+\+\+ b/(\S+)", p["gold_patch"], re.M)))
    return {"dir": d, "iid": d.name, "short": _short(d.name), "title": title, "files": files, **p,
            "paths": [p["test_path"], *p["extra_test_paths"]]}


def merge_spec(atoms: list[dict]) -> str:
    titles = "; ".join(a["title"] for a in atoms)
    out = [f"# Feature bundle: {titles}", "",
           "## Overview", "",
           f"This change request bundles {len(atoms)} improvements to click that must land together in a "
           f"single change. Implement all of them. Each feature section below is a complete specification "
           f"of that feature. All features must work in the same build without interfering with each other, "
           f"and any existing behaviour that a section does not mention stays exactly as it is.", ""]
    for i, a in enumerate(atoms, 1):
        out.append(f"## Feature {i}: {a['title']}")
        out.append("")
        for line in a["spec"].splitlines():
            if line.startswith("# "):
                continue                      # replaced by the feature heading
            out.append("#" + line if line.startswith("#") else line)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build(atoms: list[dict]) -> tuple[checks.Candidate | None, str]:
    with repo.workcopy(config.BASE_SNAPSHOT) as wc:
        for a in atoms:
            ok, err = repo.apply_patch(wc, a["gold_patch"])
            if not ok:
                tmp = wc.parent / f".compose-{a['short']}.diff"
                tmp.write_bytes(a["gold_patch"].encode("utf-8"))
                res = repo.git(["apply", "--3way", "--whitespace=nowarn", str(tmp)], wc, check=False)
                tmp.unlink(missing_ok=True)
                if res.returncode != 0:
                    return None, f"golds do not combine at {a['iid']}: {err.strip()[:300]}"
        gold = repo.diff_against_head(wc)
    test_patch = "".join(a["test_patch"] if a["test_patch"].endswith("\n") else a["test_patch"] + "\n"
                         for a in atoms)
    paths = [p for a in atoms for p in a["paths"]]
    content = "\n".join(a["test_content"] for a in atoms)
    return checks.Candidate(spec=merge_spec(atoms), gold_patch=gold, test_patch=test_patch, test_path=paths[0],
                            test_content=content, extra_test_paths=paths[1:],
                            p2p_limit=min(config.MAX_P2P_TESTS * 2, 160)), ""


def integration_score(combo: list[dict]) -> int:
    """How much the atoms' golds overlap in source files: overlapping features are harder to land together."""
    return sum(len(set(a["files"]) & set(b["files"])) for a, b in itertools.combinations(combo, 2))


def plan(atoms: list[dict], span: int, n: int) -> list[tuple[int, ...]]:
    """All span-subsets, most-integrated first; ties broken by index for determinism."""
    combos = list(itertools.combinations(range(len(atoms)), span))
    return sorted(combos, key=lambda c: (-integration_score([atoms[i] for i in c]), c))


def run(atom_dirs: list[Path], span: int, n: int, out_dir: Path, first_number: int, atom_summary: Path | None) -> dict:
    t0 = time.time()
    atoms = [load_atom(d) for d in sorted(atom_dirs)]
    max_uses = math.ceil(n * span / len(atoms))
    uses = {i: 0 for i in range(len(atoms))}
    out_dir.mkdir(parents=True, exist_ok=True)
    records, emitted = [], []
    tried: set[tuple[int, ...]] = set()

    def candidates():
        # phase 1: most-integrated first, each atom used at most `max_uses` times (balanced coverage)
        for combo in plan(atoms, span, n):
            if not any(uses[i] >= max_uses for i in combo):
                yield combo
        # phase 2: if incompatible golds left gaps, relax the cap, least-used atoms first
        while True:
            rest = [c for c in plan(atoms, span, n) if c not in tried]
            if not rest:
                return
            yield min(rest, key=lambda c: (sum(uses[i] for i in c), -integration_score([atoms[i] for i in c]), c))

    for combo in candidates():
        if len(emitted) >= n:
            break
        if combo in tried:
            continue
        tried.add(combo)
        group = [atoms[i] for i in combo]
        c0 = time.time()
        cand, err = build(group)
        rec = {"atoms": [a["iid"] for a in group], "integration": integration_score(group)}
        if cand is None:
            rec.update({"status": "incompatible", "error": err})
            records.append(rec)
            continue
        rep = checks.run_gates(cand)
        rec.update({"gates": rep.summary(), "check_seconds": round(time.time() - c0, 1)})
        if not rep.ok:
            rec.update({"status": "failed", "failures": rep.failures_text()[:2000]})
            records.append(rec)
            continue
        number = first_number + len(emitted)
        iid = f"{config.ORG_REPO_SLUG}-{number:04d}-combo-" + "-".join(a["short"] for a in group)
        package.emit(out_dir, iid, cand.spec, cand.gold_patch, cand.test_patch, cand.paths, rep.f2p, rep.p2p)
        rec.update({"status": "valid", "instance_id": iid, "n_f2p": len(rep.f2p), "n_p2p": len(rep.p2p),
                    "mutation": rep.mutation, "wall_seconds": round(time.time() - c0, 1)})
        records.append(rec)
        emitted.append(rec)
        for i in combo:
            uses[i] += 1
    summary = {"span": span, "requested": n, "valid": len(emitted), "atoms": [a["iid"] for a in atoms],
               "wall_seconds": round(time.time() - t0, 1), "candidates": records}
    if atom_summary and atom_summary.exists():
        write_pipeline_run(emitted, atoms, uses, json.loads(atom_summary.read_text(encoding="utf-8")),
                           out_dir.parent / f"pipeline_run_{out_dir.name}.json")
    return summary


def write_pipeline_run(emitted: list[dict], atoms: list[dict], uses: dict, atom_run: dict, path: Path) -> None:
    """Attribute each atom's real generation cost to the composites that use it (share = 1/uses)."""
    tel = {t["instance_id"]: t for t in atom_run["tasks"] if t.get("status") == "valid"}
    idx = {a["iid"]: i for i, a in enumerate(atoms)}
    prewarm = atom_run.get("prewarm", {}).get("cost_usd", 0.0)
    rows = []
    for rec in emitted:
        row = {"instance_id": rec["instance_id"], "model": atom_run.get("model", config.MODEL),
               "wall_seconds": rec["wall_seconds"], "model_calls": 0.0, "attempts": 1,
               "n_input_tokens": 0.0, "n_output_tokens": 0.0, "n_cache_tokens": 0.0, "cost_usd": 0.0,
               "composed_from": rec["atoms"]}
        for iid in rec["atoms"]:
            t = tel.get(iid)
            share = 1 / max(1, uses[idx[iid]])
            if not t:
                continue
            m = t["telemetry"]
            row["wall_seconds"] += t["wall_seconds"] * share
            row["model_calls"] += m["model_calls"] * share
            row["attempts"] += t["attempts"] * share
            row["n_input_tokens"] += (m["n_input_tokens"] + m["n_cache_read_tokens"] + m["n_cache_write_tokens"]) * share
            row["n_output_tokens"] += m["n_output_tokens"] * share
            row["n_cache_tokens"] += m["n_cache_read_tokens"] * share
            row["cost_usd"] += m["cost_usd"] * share
        row["cost_usd"] += prewarm / max(1, len(emitted))
        for k in ("model_calls", "n_input_tokens", "n_output_tokens", "n_cache_tokens"):
            row[k] = int(round(row[k]))
        row["attempts"] = int(math.ceil(row["attempts"]))
        row["wall_seconds"] = round(row["wall_seconds"], 1)
        row["cost_usd"] = round(row["cost_usd"], 4)
        rows.append(row)
    payload = {"pipeline": "click-task-factory/0.2 (generate + compose)",
               "note": "Composite tasks reuse generated atoms; each atom's real generation cost is split "
                       "evenly across the composites that use it. Composition itself makes no model calls.",
               "tasks": rows}
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
