"""Write a validated candidate as a Harbor task package."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import config, repo

DOCKERFILE = """\
# Harbor environment for {iid}: pallets/click at {tag} ({sha}).
# The source is fetched at the pinned commit and re-committed as a fresh one-commit git tree, so
# solve.sh and the grader can `git apply` and no later upstream history is visible in /app.
FROM {image}
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates bash \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN git init -q /app \\
    && git -C /app remote add origin {url} \\
    && git -C /app fetch -q --depth 1 origin {sha} \\
    && git -C /app checkout -q FETCH_HEAD \\
    && rm -rf /app/.git \\
    && git init -q /app \\
    && git -C /app config user.email bench@local && git -C /app config user.name bench \\
    && git -C /app config core.autocrlf false \\
    && git -C /app add -A && git -C /app commit -q -m "base state" \\
    && git config --global --add safe.directory /app
RUN python -m venv /opt/venv \\
    && /opt/venv/bin/pip install -e /app "{pytest}" \\
    && /opt/venv/bin/python -c "import click, sys; print('click', click.__file__); sys.exit(0 if click.__file__.startswith('/app/') else 1)"
ENV PATH="/opt/venv/bin:$PATH"
CMD ["/bin/bash"]
"""

RUN_SCRIPT = """\
#!/usr/bin/env bash
# Run the graded tests. -rA feeds parser.py; only files present in the tree are passed so the
# feature-absent (nop) state still runs the regression files. NOT `set -e` (pytest exits non-zero
# when the feature is absent).
set -uo pipefail
cd /app
PY=/opt/venv/bin/python
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
ALL=(
{files}
)
PRESENT=()
for f in "${{ALL[@]}}"; do [ -e "$f" ] && PRESENT+=("$f"); done
[ "${{#PRESENT[@]}}" -eq 0 ] && {{ echo "no graded test files present"; exit 0; }}
"$PY" -m pytest "${{PRESENT[@]}}" -rA --tb=short -p no:cacheprovider --color=no --continue-on-collection-errors
"""

TASK_TOML = """\
[metadata]
name = "{iid}"
instance_id = "{iid}"
task = "swe-feature-implementation"
source_repo = "{repo}"
base_commit = "{sha}"
license = "{license}"
language = "en"
programming_language = "{lang}"

[environment]
docker_image = ""
build_timeout_sec = 3600.0
cpus = 2
workdir = "/app"
memory_mb = 4096
network_mode = "{network}"

[agent]
timeout_sec = 3600.0

[verifier]
environment_mode = "shared"
timeout_sec = 900.0

[rollout_baseline.oracle]
reward = 1.0
[rollout_baseline.nop]
reward = 0.0
"""


def emit(out_root: Path, iid: str, spec: str, gold_patch: str, test_patch: str, test_path: str | list[str],
         f2p: list[str], p2p: list[str]) -> Path:
    test_paths = [test_path] if isinstance(test_path, str) else list(test_path)
    dest = out_root / iid
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "solution").mkdir(parents=True)
    (dest / "tests").mkdir()
    (dest / "environment").mkdir()

    repo.write_text(dest / "task.toml", TASK_TOML.format(
        iid=iid, repo=config.SOURCE_REPO, sha=config.BASE_COMMIT, license=config.LICENSE,
        lang=config.LANGUAGE, network=config.NETWORK_MODE))
    repo.write_text(dest / "instruction.md", spec.strip() + "\n")
    repo.write_text(dest / "solution" / "patch.diff", gold_patch)
    shutil.copyfile(config.TEMPLATES / "solve.sh", dest / "solution" / "solve.sh")
    for name in ("test.sh", "grade.py", "parser.py"):
        shutil.copyfile(config.TEMPLATES / name, dest / "tests" / name)
    repo.write_text(dest / "tests" / "test_patch.diff", test_patch)
    repo.write_text(dest / "tests" / "fail_to_pass.json", json.dumps(sorted(f2p), indent=1) + "\n")
    repo.write_text(dest / "tests" / "pass_to_pass.json", json.dumps(sorted(p2p), indent=1) + "\n")
    files = test_paths + sorted({t.split("::")[0] for t in p2p} - set(test_paths))
    repo.write_text(dest / "tests" / "run_script.sh",
                    RUN_SCRIPT.format(files="\n".join(f'  "{f}"' for f in files)))
    repo.write_text(dest / "environment" / "Dockerfile", DOCKERFILE.format(
        iid=iid, tag=config.BASE_TAG, sha=config.BASE_COMMIT, image=config.PY_IMAGE,
        url=config.UPSTREAM_URL, pytest=config.PYTEST_PIN))
    # normalise line endings of copied scripts (Windows checkouts may carry CRLF)
    for p in dest.rglob("*"):
        if p.is_file() and p.suffix in (".sh", ".py", ".toml", ".md", ".json", ".diff", ""):
            raw = p.read_bytes()
            if b"\r\n" in raw:
                p.write_bytes(raw.replace(b"\r\n", b"\n"))
    return dest


def load(task_dir: Path) -> dict:
    """Read an emitted package back (for re-checking)."""
    tp = repo.read_text(task_dir / "tests" / "test_patch.diff")
    paths = [l[6:].strip() for l in tp.splitlines() if l.startswith("+++ b/")]
    content = "\n".join(l[1:] for l in tp.splitlines()
                        if l.startswith("+") and not l.startswith("+++")) + "\n"
    return {"spec": repo.read_text(task_dir / "instruction.md"),
            "gold_patch": repo.read_text(task_dir / "solution" / "patch.diff"),
            "test_patch": tp, "test_path": paths[0], "test_content": content,
            "extra_test_paths": paths[1:]}
