"""Re-grade logged solver attempts inside each task's own container, with the package's own verifier.

No model is called. The solver jury already paid for the benchmark model's one-shot attempts on every
shipped task and graded them on the host. This takes the same answers, turns each into a patch, applies
it in the task image that Harbor built from the package's Dockerfile, and runs the package's
`tests/test.sh` there. The result is what the official grader says about those attempts in the official
environment.

It is not the official benchmark: that runs the terminus-2 agent, which can run tests and iterate.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import checks, config, repo

_RUN_ONE = r'''run_one() {  # iid rollout image tests_dir patch
  local out
  out="$(docker run --rm -v "$4:/tests:ro" -v "$5:/tmp/solver.diff:ro" "$3" bash -c 'cd /app && if git apply --whitespace=nowarn /tmp/solver.diff 2>/dev/null; then bash /tests/test.sh >/dev/null 2>&1; cat /logs/verifier/grading_details.json; else echo "{\"apply_failed\": true}"; fi' 2>/dev/null | tr -d "\n")"
  echo "RESULT|$1|$2|${out}"
}
'''


def _host(path: Path) -> str:
    """The path as the Docker host sees it (WSL when the pipeline runs on Windows)."""
    path = path.resolve()
    if sys.platform != "win32":
        return str(path)
    return f"/mnt/{path.drive[0].lower()}" + path.as_posix()[2:]


def _shell(script: Path, timeout: int = 3600) -> str:
    cmd = ["bash", _host(script)]
    if sys.platform == "win32":
        cmd = ["wsl", "-d", os.environ.get("FACTORY_WSL_DISTRO", "Ubuntu-24.04"), "--", *cmd]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return proc.stdout.decode("utf-8", errors="replace").replace("\x00", "")


def shipped_round(record: dict) -> int | None:
    """The jury round whose candidate was emitted: the lowest solve rate, earliest on ties."""
    rounds = [j for j in record.get("jury", []) if j.get("rate") is not None]
    return min(rounds, key=lambda j: (j["rate"], j["round"]))["round"] if rounds else None


def collect(run_dir: Path, tasks_dir: Path, work: Path) -> list[dict]:
    """One entry per logged attempt on a shipped task, with the attempt written out as a patch file."""
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    attempts = []
    for rec in summary["tasks"]:
        rnd = shipped_round(rec)
        if rec.get("status") != "valid" or rnd is None:
            continue
        iid = rec["instance_id"]
        tag = f"{rec['number']:04d}-{rec['cell']['module']}-{rec['cell']['shape']}"
        for roll in sorted((run_dir / tag / f"jury-round{rnd}").glob("rollout_*")):
            grade = json.loads((roll / "grade.json").read_text(encoding="utf-8")) if (roll / "grade.json").exists() else {}
            answers = sorted(roll.glob("solve-try*.json"))
            if not answers or not grade.get("applied"):
                continue
            edits = json.loads(json.loads(answers[-1].read_text(encoding="utf-8"))["response_text"])["edits"]
            with repo.workcopy(config.BASE_SNAPSHOT) as wc:
                touched, errors = checks.apply_edits(wc, edits)
                if errors:
                    continue
                patch = repo.diff_against_head(wc, touched)
            pdir = work / iid / roll.name
            pdir.mkdir(parents=True, exist_ok=True)
            repo.write_text(pdir / "solver.diff", patch)
            attempts.append({"instance_id": iid, "rollout": roll.name, "round": rnd, "patch": pdir / "solver.diff",
                             "tests": tasks_dir / iid / "tests", "host_reward": grade.get("reward"),
                             "host_resolved": bool(grade.get("resolved"))})
    return attempts


def run(run_dir: Path, tasks_dir: Path, out: Path, scratch: Path) -> dict:
    repo.build_snapshot(config.BASE_COMMIT, config.BASE_SNAPSHOT)
    attempts = collect(run_dir, tasks_dir, out / "patches")
    lines = ["#!/usr/bin/env bash", "set -u", _RUN_ONE,
             "images=\"$(docker images --format '{{.Repository}}:{{.Tag}}')\""]
    for i, a in enumerate(attempts):
        key = a["instance_id"][:32].lower()               # Harbor truncates the task name in image tags
        lines.append(f"img=\"$(echo \"$images\" | grep -- '^{key}' | head -1)\"")
        lines.append(f"if [ -z \"$img\" ]; then echo 'RESULT|{a['instance_id']}|{a['rollout']}|{{\"no_image\": true}}'; "
                     f"else run_one '{a['instance_id']}' '{a['rollout']}' \"$img\" '{_host(a['tests'])}' "
                     f"'{_host(a['patch'])}' & fi")
        if i % 6 == 5:
            lines.append("wait")
    lines.append("wait")
    scratch.mkdir(parents=True, exist_ok=True)
    script = scratch / "regrade.sh"
    script.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    output = _shell(script)
    graded = {}
    for line in output.splitlines():
        if line.startswith("RESULT|"):
            _, iid, roll, payload = line.split("|", 3)
            try:
                graded[(iid, roll)] = json.loads(payload) if payload.strip() else {"no_output": True}
            except json.JSONDecodeError:
                graded[(iid, roll)] = {"unparsed": payload[:200]}
    tasks: dict[str, dict] = {}
    for a in attempts:
        g = graded.get((a["instance_id"], a["rollout"]), {"missing": True})
        t = tasks.setdefault(a["instance_id"], {"round": a["round"], "attempts": []})
        t["attempts"].append({"rollout": a["rollout"], "host_reward": a["host_reward"],
                              "container_reward": g.get("reward"), "container_resolved": g.get("resolved"),
                              "f2p": f"{g.get('f2p_passed')}/{g.get('f2p_total')}",
                              "p2p": f"{g.get('p2p_passed')}/{g.get('p2p_total')}",
                              "problem": next((k for k in ("apply_failed", "no_image", "no_output", "unparsed", "missing")
                                               if g.get(k)), "")})
    for t in tasks.values():
        ok = [x for x in t["attempts"] if not x["problem"]]
        t["container_resolved"] = sum(1 for x in ok if x["container_resolved"])
        t["host_resolved"] = sum(1 for x in t["attempts"] if x["host_reward"] == 1.0)
        t["graded"] = len(ok)
        t["agree"] = all(x["container_reward"] == x["host_reward"] for x in ok)
    result = {"run": run_dir.name, "what": "logged one-shot Haiku attempts on the shipped tasks, re-graded with each "
              "package's tests/test.sh inside the task image built by Harbor; no model calls", "tasks": tasks}
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    md = ["# One-shot Haiku attempts, re-graded in the task containers", "",
          "The solver jury's logged attempts on the shipped version of each task were turned into patches,",
          "applied in the task image that Harbor built from the package's Dockerfile, and graded by the package's",
          "own `tests/test.sh`. No model was called for this. It is not the official benchmark, which runs the",
          "terminus-2 agent.", "",
          "| Task | Attempts graded | Solved in the container | Solved on the host | Rewards in the container | Same as host |",
          "|---|---|---|---|---|---|"]
    for iid, t in tasks.items():
        rewards = ", ".join(str(x["container_reward"]) if not x["problem"] else x["problem"] for x in t["attempts"])
        md.append(f"| `{iid.split('-', 1)[1]}` | {t['graded']} | {t['container_resolved']}/{t['graded']} "
                  f"| {t['host_resolved']}/{len(t['attempts'])} | {rewards} | {'yes' if t['agree'] else 'NO'} |")
    (out.parent / "haiku_oneshot_in_container.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return result
