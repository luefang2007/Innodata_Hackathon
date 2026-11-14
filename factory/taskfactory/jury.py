"""Solver jury: measure how hard a candidate task is before it leaves the factory.

The weak juror is the benchmark model itself. It sees only instruction.md and the click source, writes
its patch in one shot, and is graded locally with the same rule as tests/grade.py (any regression
failure -> 0; all feature tests pass -> resolved). k attempts give a solve rate. This is a proxy for the
official agent benchmark, used inside the pipeline as a difficulty signal:

  solve rate <= JURY_MAX_WEAK_RATE   -> hard enough, the task ships
  otherwise                          -> the generator must deepen the task, and it is measured again

The juror's wrong patches are also used as evidence: existing tests that a plausible wrong patch
breaks (while they pass at base and with the gold) join the regression slice, and the feature tests it
fails form the task's failure fingerprint.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import checks, config, covmap, llm, repo
from .runner import run_pytest

SOLVER_RULES = """You are an expert Python engineer working in the `click` repository (pallets/click). The
library source is below. You will receive a feature request written as a GitHub issue. Implement
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

# Files most features need come first; the rest are added while the context budget allows.
_PRIORITY = ["core.py", "__init__.py", "types.py", "decorators.py", "parser.py", "exceptions.py",
             "formatting.py", "utils.py", "termui.py", "shell_completion.py", "globals.py", "_utils.py",
             "testing.py", "_termui_impl.py", "_textwrap.py", "_compat.py", "_winconsole.py"]


def solver_context(gold_patch: str, budget_chars: int | None = None) -> str:
    """click source for a juror, trimmed to stay under the 100k-token price tier.

    Files the gold touches are always included, so a correct solution is always expressible. That
    hints at where to work, which makes the juror stronger, never weaker: the measured solve rate
    errs towards "too easy".
    """
    budget = config.JURY_CONTEXT_CHARS if budget_chars is None else budget_chars
    base = config.BASE_SNAPSHOT / "src" / "click"
    touched = [Path(p).name for p in re.findall(r"^(?:---|\+\+\+) [ab]/(src/click/\S+)", gold_patch, re.M)]
    order = list(dict.fromkeys([*touched, *_PRIORITY, *sorted(p.name for p in base.glob("*.py"))]))
    parts, omitted, used = [], [], 0
    for name in order:
        path = base / name
        if not path.exists():
            continue
        text = repo.read_text(path)
        block = f"\n===== FILE: src/click/{name} =====\n{text}"
        if name in touched or used + len(block) <= budget:
            parts.append(block)
            used += len(block)
        else:
            omitted.append(f"src/click/{name} ({text.count(chr(10))} lines)")
    header = f"# click source at tag {config.BASE_TAG}\n"
    if omitted:
        header += "Not shown (unchanged support modules): " + ", ".join(omitted) + "\n"
    return header + "".join(parts)


def solver_prompt(spec: str, retry_error: str = "") -> str:
    extra = (f"\n\nYour previous edits could not be applied:\n{retry_error}\nReturn the complete set of "
             f"edits again, against the original source.") if retry_error else ""
    return f"""<issue>
{spec}
</issue>

Implement this issue. Return search/replace edits to existing files under src/click/ (a `search` block
must be copied verbatim from the current file and match exactly once; to create a new module use an
empty `search`). Do not write tests.{extra}"""


def wide_slice(cand: checks.Candidate) -> list[str]:
    """Existing tests that execute the code the gold touches, and still pass with the gold applied."""
    wide = covmap.select_p2p(cand.gold_patch, config.BASE_SNAPSHOT, limit=400)
    if not wide:
        return []
    with repo.workcopy(config.BASE_SNAPSHOT) as wc:
        repo.apply_patch(wc, cand.gold_patch)
        st, _ = run_pytest(wc, sorted({t.split("::")[0] for t in wide}))
    return [t for t in wide if st.get(t) == "PASSED"]


def grade_edits(cand: checks.Candidate, f2p: list[str], p2p: list[str], edits: list[dict],
                wide: list[str] | None = None) -> dict:
    """Apply a solver's edits to the base repo and grade them like tests/grade.py does."""
    wide = wide or []
    with repo.workcopy(config.BASE_SNAPSHOT) as wc:
        touched, errors = checks.apply_edits(wc, edits)
        if errors:
            return {"applied": False, "errors": errors}
        for path in cand.paths:                       # the held-out tests always win
            target = wc / path
            if target.exists():
                target.unlink()
        ok, err = repo.apply_patch(wc, cand.test_patch)
        if not ok:
            return {"applied": False, "errors": [f"test patch failed: {err}"]}
        files = list(dict.fromkeys([*cand.paths, *sorted({t.split("::")[0] for t in [*p2p, *wide]})]))
        st, _ = run_pytest(wc, files)
    f2p_failed = sorted(t for t in f2p if st.get(t) != "PASSED")
    p2p_failed = sorted(t for t in p2p if st.get(t) != "PASSED")
    wide_failed = sorted(t for t in wide if t not in p2p and st.get(t) != "PASSED")
    if p2p_failed:
        reward = 0.0
    elif not f2p_failed:
        reward = 1.0
    else:
        reward = round(1 - len(f2p_failed) / len(f2p), 4) if f2p else 0.0
    return {"applied": True, "reward": reward, "resolved": reward == 1.0, "f2p_failed": f2p_failed,
            "p2p_failed": p2p_failed, "wide_failed": wide_failed, "touched": touched}


def _attempt(index: int, system: list[dict], cand: checks.Candidate, f2p: list[str], p2p: list[str],
             wide: list[str], model: str, effort: str, log_dir: Path, tel: llm.Telemetry) -> dict:
    retry_error = ""
    out_dir = log_dir / f"rollout_{index}"
    for try_no in (1, 2):      # one retry only when the edits do not apply (a format slip, not logic)
        try:
            ans = llm.call_json(system, solver_prompt(cand.spec, retry_error), SOLVER_SCHEMA, tel, out_dir,
                                f"solve-try{try_no}", model=model, effort=effort, reserve=0.15)
        except llm.LLMError as exc:
            return {"rollout": index, "infra_error": str(exc)[:300]}
        res = grade_edits(cand, f2p, p2p, ans.get("edits", []), wide)
        if res["applied"] or try_no == 2:
            res.update({"rollout": index, "tries": try_no})
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "grade.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
            return res
        retry_error = "\n".join(res["errors"])[:3000]
    return {"rollout": index, "infra_error": "unreachable"}


def weak_probe(cand: checks.Candidate, f2p: list[str], p2p: list[str], log_dir: Path, tel: llm.Telemetry,
               k: int | None = None, model: str | None = None, effort: str | None = None) -> dict:
    """k one-shot attempts by the weak juror. Returns solve counts, regression traps and a fingerprint."""
    k = config.JURY_K if k is None else k
    model = model or config.JURY_WEAK_MODEL
    effort = effort or config.JURY_WEAK_EFFORT
    system = [{"type": "text", "text": SOLVER_RULES},
              {"type": "text", "text": solver_context(cand.gold_patch), "cache_control": {"type": "ephemeral"}}]
    wide = wide_slice(cand)
    with ThreadPoolExecutor(max_workers=max(1, k)) as pool:
        rollouts = list(pool.map(lambda i: _attempt(i, system, cand, f2p, p2p, wide, model, effort, log_dir, tel),
                                 range(1, k + 1)))
    graded = [r for r in rollouts if r.get("applied")]          # format slips and API errors are not attempts
    # A patch that breaks most of the touched code's tests is simply broken; only plausible patches
    # nominate regression traps.
    limit = max(10, len(wide) // 5)
    traps = Counter(t for r in graded if len(r["wide_failed"]) <= limit for t in r["wide_failed"])
    trap_ids = [t for t, _ in traps.most_common(config.JURY_MAX_TRAPS)]
    solved = sum(1 for r in graded if r["resolved"])
    # strict: also counts a break of a trap test as a miss, exactly as the shipped package will grade it
    solved_strict = sum(1 for r in graded if r["resolved"] and not (set(r["wide_failed"]) & set(trap_ids)))
    failed = Counter(t for r in graded for t in r["f2p_failed"])
    return {"model": model, "k": len(graded), "solved": solved, "solved_strict": solved_strict,
            "rate": (solved_strict / len(graded)) if graded else None,
            "rewards": [r.get("reward") for r in graded], "not_graded": len(rollouts) - len(graded),
            "traps": trap_ids, "failed_f2p": dict(failed.most_common()),
            "regressions": dict(Counter(t for r in graded for t in r["p2p_failed"]).most_common(10))}


def strong_check(cand: checks.Candidate, f2p: list[str], p2p: list[str], log_dir: Path, tel: llm.Telemetry) -> dict:
    """One blind attempt by a strong juror: evidence that the spec alone is enough to solve the task."""
    system = [{"type": "text", "text": SOLVER_RULES},
              {"type": "text", "text": solver_context(cand.gold_patch), "cache_control": {"type": "ephemeral"}}]
    res = _attempt(1, system, cand, f2p, p2p, [], config.JURY_STRONG_MODEL, "high", log_dir, tel)
    return {"model": config.JURY_STRONG_MODEL, "resolved": bool(res.get("resolved")), "reward": res.get("reward"),
            "f2p_failed": res.get("f2p_failed", []), "p2p_failed": res.get("p2p_failed", []),
            "error": res.get("infra_error") or ("edits did not apply" if res.get("applied") is False else "")}


def summarize(probe: dict) -> dict:
    """The part of a probe result that goes into run summaries and task cards."""
    return {k: probe[k] for k in ("model", "k", "solved", "solved_strict", "rate", "rewards", "not_graded")} | {
        "n_traps": len(probe["traps"]), "failed_f2p": probe["failed_f2p"], "regressions": probe["regressions"]}
