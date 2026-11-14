"""Assemble the submission folder and zip (TeamName_Track1.zip) from the pipeline's outputs.

What goes in: pipeline source, the chosen task packages, pipeline_run.json, README + variation writeup,
reports, and the raw run logs. Raw logs are stored as .tar.gz per run: they contain generated code such
as `token = ...`, which the kit's secret scanner reports as a possible key when it reads plain .json.
What stays out: .env, caches, the hand-written smoke fixture, and any second set of packages (a second
set would be counted as submitted packages), which is archived under extras/ instead.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from . import config

_SKIP_DIRS = {".cache", "smoke", "__pycache__", ".git", ".pytest_cache"}
_SKIP_FILES = {".env", ".DS_Store"}


def _copy_tree(src: Path, dst: Path) -> None:
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if any(part in _SKIP_DIRS for part in rel.parts) or p.name in _SKIP_FILES or p.suffix == ".pyc":
            continue
        if p.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst / rel)


def _tar_dirs(parent: Path, dest_dir: Path) -> int:
    """One .tar.gz per sub-directory of `parent`; loose .md files are copied as they are."""
    n = 0
    if not parent.exists():
        return 0
    dest_dir.mkdir(parents=True, exist_ok=True)
    for d in sorted(p for p in parent.iterdir() if p.is_dir()):
        with tarfile.open(dest_dir / f"{d.name}.tar.gz", "w:gz") as tar:
            tar.add(d, arcname=d.name)
        for md in d.glob("*.md"):
            shutil.copyfile(md, dest_dir / f"{d.name}.{md.name}")
        n += 1
    return n


def build(team: str, tasks_dir: Path, telemetry: Path, out_root: Path, extras: list[Path]) -> Path:
    sub = config.ROOT.parent / "submission"
    dest = out_root / f"{team}_Track1"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    _copy_tree(config.ROOT, dest / "factory")
    _copy_tree(tasks_dir, dest / "generated_tasks")
    shutil.copyfile(telemetry, dest / "pipeline_run.json")
    for name in ("README.md", "VARIATION.md"):                    # also at top level, where reviewers look first
        shutil.copyfile(config.ROOT / name, dest / name)
    # Evidence comes from the folder the packages were generated into and, when that is a later run that
    # reused earlier paid-for calls, from the first run's folder too.
    own = tasks_dir.resolve().parent
    roots = list(dict.fromkeys([own, sub.resolve()]))
    runs = probes = 0
    for root in roots:
        label = root.name
        runs += _tar_dirs(root / "runs", dest / "evidence" / label / "runs")
        probes += _tar_dirs(root / "probe", dest / "evidence" / label / "probe")
        for report in sorted(root.glob("*.md")):
            (dest / "evidence" / label).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(report, dest / "evidence" / label / report.name)
    for name in ("selfcheck", "sc"):                              # Harbor oracle/nop output, if it was produced
        if (own / name).exists():
            _copy_tree(own / name, dest / "evidence" / "selfcheck")
    if (own / "trajectory").exists():                             # benchmark rollouts belong at top level
        _copy_tree(own / "trajectory", dest / "trajectory")
    for extra in extras:
        extra = extra.resolve()
        (dest / "extras").mkdir(exist_ok=True)
        with tarfile.open(dest / "extras" / f"{extra.parent.name}__{extra.name}.tar.gz", "w:gz") as tar:
            tar.add(extra, arcname=extra.name)
    for slides in sorted(config.ROOT.parent.glob("*Track1*.pptx")) + sorted(sub.glob("*.pptx")):
        shutil.copyfile(slides, dest / slides.name)
    zpath = out_root / f"{team}_Track1.zip"
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(dest.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(out_root).as_posix())
    print(f"bundle: {dest}  ({runs} run log archive(s), {probes} probe archive(s), {len(extras)} extra archive(s))")
    print(f"zip:    {zpath}  ({zpath.stat().st_size / 1e6:.1f} MB)")
    validator = config.ROOT.parent / "Track 1 - Coding" / "harness" / "validate_submission.py"
    if validator.exists():
        proc = subprocess.run([sys.executable, str(validator), str(zpath)], capture_output=True)
        print(proc.stdout.decode("utf-8", errors="replace").strip())
    else:
        print("kit validator not found; run harness/validate_submission.py on the zip before sending")
    return zpath
