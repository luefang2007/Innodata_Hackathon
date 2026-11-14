"""Measured variation: how similar are the emitted packages to each other?

Each package gets a fingerprint from things that can be read off the package, with no model:
  symbols   the existing click classes and functions its gold changes
  p2p       its curated regression tests (chosen from the coverage of the code it touches)
  terms     the code terms its spec defines or relies on (backticked text in instruction.md)
Two packages that change the same code, share a regression slice and talk about the same names are
the same task in disguise, whatever their wording. Similarity is the mean Jaccard overlap of the three
sets; a pair above TOO_SIMILAR is reported as a near-duplicate.
"""
from __future__ import annotations

import ast
import json
import re
from itertools import combinations
from pathlib import Path

from . import config, covmap, package, repo

TOO_SIMILAR = 0.5


def fingerprint(task_dir: Path) -> dict:
    p = package.load(task_dir)
    symbols: set[str] = set()
    for rel, lines in covmap.touched_regions(p["gold_patch"], config.BASE_SNAPSHOT).items():
        tree = ast.parse(repo.read_text(config.BASE_SNAPSHOT / rel))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if set(range(node.lineno, (node.end_lineno or node.lineno) + 1)) & lines:
                    symbols.add(f"{Path(rel).stem}.{node.name}")
    p2p = set(json.loads(repo.read_text(task_dir / "tests" / "pass_to_pass.json")))
    terms = {t.strip() for t in re.findall(r"`([^`\n]{2,60})`", p["spec"])}
    files = set(re.findall(r"^\+\+\+ b/(\S+)", p["gold_patch"], re.M))
    return {"symbols": symbols, "p2p": p2p, "terms": terms, "files": files}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def report(task_dirs: list[Path]) -> tuple[str, dict]:
    dirs = sorted(Path(d) for d in task_dirs)
    fps = {d.name: fingerprint(d) for d in dirs}
    names = list(fps)
    short = {n: re.sub(r"^.*?-(\d{4})-.*$", r"\1", n) for n in names}
    pairs = []
    for a, b in combinations(names, 2):
        parts = {k: round(jaccard(fps[a][k], fps[b][k]), 2) for k in ("symbols", "p2p", "terms")}
        pairs.append({"a": a, "b": b, **parts, "similarity": round(sum(parts.values()) / 3, 2)})
    too = [p for p in pairs if p["similarity"] > TOO_SIMILAR]
    lines = ["# Measured variation", "",
             f"{len(names)} packages, {len(pairs)} pairs. Similarity = mean Jaccard overlap of changed symbols, "
             f"regression tests and spec terms (0 = nothing shared, 1 = identical). "
             f"Near-duplicate threshold: {TOO_SIMILAR}.", "",
             "| Task | Files changed | Symbols changed | P2P tests | Spec terms |", "|---|---|---|---|---|"]
    for n in names:
        f = fps[n]
        lines.append(f"| {short[n]} `{n.split('-', 2)[-1]}` | {', '.join(sorted(Path(x).name for x in f['files']))} "
                     f"| {len(f['symbols'])} | {len(f['p2p'])} | {len(f['terms'])} |")
    lines += ["", "| Pair | Symbols | P2P | Terms | Similarity |", "|---|---|---|---|---|"]
    for p in sorted(pairs, key=lambda x: -x["similarity"]):
        flag = "  **near-duplicate**" if p["similarity"] > TOO_SIMILAR else ""
        lines.append(f"| {short[p['a']]} / {short[p['b']]} | {p['symbols']:.2f} | {p['p2p']:.2f} | {p['terms']:.2f} "
                     f"| {p['similarity']:.2f}{flag} |")
    sims = [p["similarity"] for p in pairs] or [0.0]
    lines += ["", f"Max similarity {max(sims):.2f}, mean {sum(sims) / len(sims):.2f}; "
                  f"{len(too)} pair(s) above the threshold."]
    data = {"tasks": {n: {k: sorted(v) for k, v in f.items()} for n, f in fps.items()}, "pairs": pairs,
            "max_similarity": max(sims), "mean_similarity": round(sum(sims) / len(sims), 3),
            "too_similar": [(p["a"], p["b"]) for p in too]}
    return "\n".join(lines) + "\n", data
