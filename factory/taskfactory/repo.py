"""Git snapshots of click and throwaway working copies.

A snapshot is the source tree at one commit with *fresh* git history (one commit), exactly like the
task container: no future commits leak to anyone who inspects the tree. Every check runs in a copy
of a snapshot, so checks never interfere with each other and can run in parallel.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import config

GIT_ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}


def git(args: list[str], cwd: Path, check: bool = True, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, input=input_bytes, check=check, env=GIT_ENV)


def ensure_clone() -> Path:
    if not (config.CLONE_DIR / ".git").exists():
        config.CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", config.UPSTREAM_URL, str(config.CLONE_DIR)], check=True)
    return config.CLONE_DIR


def fetch_upstream() -> str:
    """Refresh the clone and return the live upstream main sha."""
    clone = ensure_clone()
    git(["fetch", "--quiet", "origin", config.UPSTREAM_BRANCH], clone, check=False)
    return git(["rev-parse", f"origin/{config.UPSTREAM_BRANCH}"], clone).stdout.decode().strip()


def build_snapshot(ref: str, dest: Path, force: bool = False) -> Path:
    marker = dest / ".snapshot_ref"
    if dest.exists() and not force and marker.exists() and marker.read_text().strip() == ref:
        return dest
    if dest.exists():
        shutil.rmtree(dest, onerror=_force_remove)
    clone = ensure_clone()
    data = git(["archive", "--format=zip", ref], clone).stdout
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)
    git(["init", "--quiet"], dest)
    git(["config", "user.email", "bench@local"], dest)
    git(["config", "user.name", "bench"], dest)
    git(["config", "core.autocrlf", "false"], dest)
    git(["add", "-A"], dest)
    git(["commit", "--quiet", "-m", "base state"], dest)
    marker.write_text(ref)
    # keep the marker out of `git status` / diffs
    (dest / ".git" / "info").mkdir(parents=True, exist_ok=True)
    with open(dest / ".git" / "info" / "exclude", "a") as fh:
        fh.write("\n.snapshot_ref\n")
    return dest


def _force_remove(func, path, _exc):
    os.chmod(path, 0o700)
    func(path)


@contextmanager
def workcopy(snapshot: Path, keep: bool = False):
    """A disposable copy of a snapshot (with its one-commit git repo)."""
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix="wc-", dir=config.WORK_DIR))
    shutil.rmtree(dest)
    shutil.copytree(snapshot, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    try:
        yield dest
    finally:
        if not keep:
            shutil.rmtree(dest, onerror=_force_remove)


def apply_patch(workdir: Path, patch_text: str) -> tuple[bool, str]:
    if not patch_text.strip():
        return True, ""
    tmp = workdir.parent / f".patch-{uuid.uuid4().hex}.diff"
    tmp.write_bytes(patch_text.encode("utf-8"))
    try:
        res = git(["apply", "--whitespace=nowarn", str(tmp)], workdir, check=False)
        return res.returncode == 0, res.stderr.decode(errors="replace")
    finally:
        tmp.unlink(missing_ok=True)


def diff_against_head(workdir: Path, paths: list[str] | None = None) -> str:
    """Unified diff (with new files) of the working tree against the snapshot commit."""
    git(["add", "-A"], workdir)
    args = ["diff", "--cached", "--no-color", "--no-ext-diff", "HEAD"]
    if paths:
        args += ["--", *paths]
    out = git(args, workdir).stdout.decode("utf-8")
    git(["reset", "--quiet"], workdir)
    return out


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))
