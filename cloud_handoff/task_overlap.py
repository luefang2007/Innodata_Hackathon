"""Measure how different the emitted task packages are from each other (no model calls, no Docker).

For every pair of packages it reports the overlap of their regression slices (P2P test ids) and of
the click classes/functions their gold patches edit. Low overlap is evidence for the variation claim.

Usage (from the repo root):  python cloud_handoff/task_overlap.py [submission/generated_tasks]
"""
import itertools
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "submission/generated_tasks")
tasks = {}
for d in sorted(p for p in root.iterdir() if (p / "task.toml").exists()):
    patch = (d / "solution" / "patch.diff").read_text(encoding="utf-8")
    added = [l[1:] for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++")]
    tasks[re.sub(r"^.*?-(\d{4})-.*$", r"\1", d.name)] = {
        "files": re.findall(r"^\+\+\+ b/(\S+)", patch, re.M),
        "scopes": {h.strip() for h in re.findall(r"^@@ .* @@ ?(.*)$", patch, re.M) if h.strip()},
        "added": len(added),
        "f2p": json.loads((d / "tests" / "fail_to_pass.json").read_text()),
        "p2p": set(json.loads((d / "tests" / "pass_to_pass.json").read_text())),
    }

print("| task | files edited by the gold | hunk scopes | added lines | F2P | P2P |")
print("|---|---|---|---|---|---|")
for n, t in tasks.items():
    scopes = ", ".join(sorted(s.split("(")[0].replace("class ", "").replace("def ", "").rstrip(":") for s in t["scopes"]))
    print(f"| {n} | {', '.join(f.split('/')[-1] for f in t['files'])} | {scopes} | {t['added']} | {len(t['f2p'])} | {len(t['p2p'])} |")

print("\n| pair | P2P Jaccard | shared P2P tests | shared hunk scopes |")
print("|---|---|---|---|")
vals = []
for a, b in itertools.combinations(tasks, 2):
    A, B = tasks[a]["p2p"], tasks[b]["p2p"]
    j = len(A & B) / len(A | B) if A | B else 0.0
    vals.append(j)
    shared = sorted(tasks[a]["scopes"] & tasks[b]["scopes"])
    print(f"| {a}-{b} | {j:.2f} | {len(A & B)} | {', '.join(shared) or '-'} |")
print(f"\nmean pairwise P2P Jaccard = {sum(vals) / len(vals):.3f}, max = {max(vals):.2f}")
