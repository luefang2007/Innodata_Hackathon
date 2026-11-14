"""Stop a running `generate` when API spend reaches a threshold (safety net for runs started without
the in-process budget guard). Usage: python -m taskfactory.watchdog <threshold_usd> [<runs_dir>]"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .usage import _cost


def logged_spend(runs_dir: Path) -> float:
    total = 0.0
    for f in runs_dir.rglob("*.json"):
        if f.name == "summary.json":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if "usage" in d:
            total += _cost(d.get("model") or config.MODEL, d["usage"])
    return total


def key_spend() -> float:
    if config.BACKEND != "openrouter":
        return 0.0
    try:
        from .llm import _or_request
        return float(_or_request("/key", timeout=30).get("data", {}).get("usage", 0.0))
    except Exception:
        return 0.0


def generate_pids() -> list[int]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*taskfactory generate*' "
         "-and $_.Name -like 'python*' } | Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True) if sys.platform == "win32" else \
        subprocess.run(["pgrep", "-f", "taskfactory generate"], capture_output=True, text=True)
    return [int(x) for x in out.stdout.split() if x.strip().isdigit()]


def main() -> int:
    threshold = float(sys.argv[1])
    runs_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else config.ROOT.parent / "submission" / "runs"
    while True:
        pids = generate_pids()
        spend = max(logged_spend(runs_dir), key_spend())
        print(f"{time.strftime('%H:%M:%S')} spend ${spend:.2f} / stop at ${threshold:.2f}; generate pids {pids}",
              flush=True)
        if not pids:
            return 0
        if spend >= threshold:
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)] if sys.platform == "win32"
                               else ["kill", str(pid)], capture_output=True)
            print(f"STOPPED generate at ${spend:.2f}", flush=True)
            return 1
        time.sleep(15)


if __name__ == "__main__":
    sys.exit(main())
