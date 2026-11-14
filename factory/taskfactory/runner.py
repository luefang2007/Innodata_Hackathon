"""Run pytest inside a working copy and parse results with the *same* parser the container uses."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from . import config

_spec = importlib.util.spec_from_file_location("_kit_parser", config.TEMPLATES / "parser.py")
_kit_parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_kit_parser)


def _metadata_dir() -> Path:
    """A dist-info stub so `importlib.metadata.version("click")` works without installing click."""
    meta = config.CACHE / "meta"
    info = meta / f"click-{config.BASE_TAG}.dist-info"
    if not (info / "METADATA").exists():
        info.mkdir(parents=True, exist_ok=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: click\nVersion: {config.BASE_TAG}\n")
    return meta


def pytest_env(workdir: Path) -> dict:
    env = {**os.environ}
    env["PYTHONPATH"] = os.pathsep.join([str(workdir / "src"), str(_metadata_dir())])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    return env


def run_pytest(workdir: Path, targets: list[str], extra: list[str] | None = None,
               timeout: int = config.TEST_TIMEOUT_SEC) -> tuple[dict[str, str], str]:
    """Run pytest on `targets` (files or node ids); return ({nodeid: STATUS}, combined log)."""
    present = [t for t in targets if (workdir / t.split("::")[0]).exists()]
    if not present:
        return {}, "no test targets present"
    cmd = [sys.executable, "-m", "pytest", *present, "-rA", "--tb=short", "-p", "no:cacheprovider",
           "--color=no", "--continue-on-collection-errors", *(extra or [])]
    try:
        proc = subprocess.run(cmd, cwd=workdir, env=pytest_env(workdir), capture_output=True,
                              timeout=timeout)
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", errors="replace")
        err = "TIMEOUT"
    results = _kit_parser.parse_test_output(out, err)
    status = {r.name: r.status.name for r in results}
    return status, out + "\n" + err


def tail(log: str, n: int = 60) -> str:
    lines = log.strip().splitlines()
    return "\n".join(lines[-n:])
