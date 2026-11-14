"""Sum API usage from the per-call logs (works while a run is still in progress)."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from . import config


def _cost(model: str, u: dict) -> float:
    if u.get("cost") is not None:          # OpenRouter reports the billed amount directly
        return float(u["cost"])
    p = config.price_for(model or config.MODEL)
    return ((u.get("input_tokens") or 0) * p["in"] + (u.get("output_tokens") or 0) * p["out"]
            + (u.get("cache_read_input_tokens") or 0) * p["cache_read"]
            + (u.get("cache_creation_input_tokens") or 0) * p["cache_write"]) / 1e6


def report(runs_dir: Path) -> str:
    rows = []
    grand = defaultdict(float)
    for run in sorted(p for p in runs_dir.iterdir() if p.is_dir()) if runs_dir.exists() else []:
        tot = defaultdict(float)
        for f in run.rglob("*.json"):
            if f.name == "summary.json":
                continue          # prewarm calls are logged in their own prewarm*.json files
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if "usage" not in d:
                continue
            u = d["usage"]
            tot["calls"] += 1
            tot["input"] += (u.get("input_tokens") or 0)
            tot["output"] += (u.get("output_tokens") or 0)
            tot["cache_read"] += (u.get("cache_read_input_tokens") or 0)
            tot["cache_write"] += (u.get("cache_creation_input_tokens") or 0)
            tot["cost"] += _cost(d.get("model") or config.MODEL, u)
        for k, v in tot.items():
            grand[k] += v
        rows.append(f"{run.name:28s} calls={int(tot['calls']):3d}  in={int(tot['input']):>8,}  "
                    f"out={int(tot['output']):>8,}  cache_read={int(tot['cache_read']):>9,}  "
                    f"cache_write={int(tot['cache_write']):>8,}  ${tot['cost']:.2f}")
    rows.append(f"{'TOTAL':28s} calls={int(grand['calls']):3d}  ${grand['cost']:.2f} "
                f"(OpenRouter: billed cost; Anthropic: estimate at list prices)")
    if config.BACKEND == "openrouter":
        from .llm import _or_request
        try:
            info = _or_request("/key").get("data", {})
            rows.append(f"OpenRouter key total usage: ${info.get('usage', 0):.2f}"
                        + (f" of ${info['limit']:.2f} limit (${info.get('limit_remaining', 0):.2f} left)"
                           if info.get("limit") is not None else ""))
        except Exception as exc:  # informational only
            rows.append(f"OpenRouter key info unavailable: {exc}")
    return "\n".join(rows)
