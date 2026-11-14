#!/usr/bin/env python3
"""Deterministic SWE-style verifier (Harbor contract). No LLM judge.

Copied verbatim into every generated task's tests/. Reads the sibling
test_patch.diff, run_script.sh, parser.py, fail_to_pass.json, pass_to_pass.json.
Writes /logs/verifier/{reward.json, reward.txt, grading_details.json}.

Reward contract:
  1.0  iff every FAIL_TO_PASS passes AND every PASS_TO_PASS passes
  0.0  if any PASS_TO_PASS regresses (regression gate)
  else fraction of FAIL_TO_PASS passed
"""
import json, os, re, subprocess
D = os.path.dirname(os.path.abspath(__file__))
APP = "/app"
REWARD_DIR = "/logs/verifier"
f2p_set = set(json.load(open(os.path.join(D, "fail_to_pass.json"))))
p2p_set = set(json.load(open(os.path.join(D, "pass_to_pass.json"))))

def main():
    os.makedirs(REWARD_DIR, exist_ok=True)
    tp = os.path.join(D, "test_patch.diff")
    if os.path.exists(tp) and os.path.getsize(tp) > 0:
        # The held-out tests must win. An agent that happened to create its own files at
        # these paths would otherwise make `git apply --check` fail, the patch would be
        # silently skipped, and the graded run would collect none of the real tests --
        # scoring 0 for every F2P *and* every P2P, which looks like total model failure
        # but is a harness fault. Clear the paths the patch creates, then apply.
        targets = re.findall(r"^\+\+\+ b/(\S+)", open(tp, encoding="utf-8",
                                                      errors="replace").read(), re.M)
        for rel in targets:
            victim = os.path.join(APP, rel)
            if os.path.exists(victim):
                os.remove(victim)
        if subprocess.run(["git", "-C", APP, "apply", tp]).returncode != 0:
            # last resort: 3-way merge against the index
            subprocess.run(["git", "-C", APP, "apply", "--3way", tp])
    so = os.path.join(REWARD_DIR, "stdout.log"); se = os.path.join(REWARD_DIR, "stderr.log")
    out = os.path.join(REWARD_DIR, "output.json")
    with open(so, "w") as o, open(se, "w") as e:
        subprocess.run(["bash", os.path.join(D, "run_script.sh")], cwd=APP, stdout=o, stderr=e)
    subprocess.run(["python3", os.path.join(D, "parser.py"), so, se, out])
    tests = json.load(open(out)).get("tests", []) if os.path.exists(out) else []
    st = {t["name"]: t["status"] for t in tests}
    f2p_pass = sum(1 for n in f2p_set if st.get(n) == "PASSED")
    p2p_pass = sum(1 for n in p2p_set if st.get(n) == "PASSED")
    ft, pt = len(f2p_set), len(p2p_set)
    if pt and p2p_pass < pt:
        reward = 0.0
    elif ft and f2p_pass >= ft:
        reward = 1.0
    else:
        reward = round(f2p_pass / ft, 4) if ft else 0.0
    details = {"reward": reward, "f2p_passed": f2p_pass, "f2p_total": ft,
               "p2p_passed": p2p_pass, "p2p_total": pt, "resolved": bool(reward == 1.0)}
    json.dump({"reward": reward}, open(os.path.join(REWARD_DIR, "reward.json"), "w"), indent=1)
    open(os.path.join(REWARD_DIR, "reward.txt"), "w").write(str(reward))
    json.dump(details, open(os.path.join(REWARD_DIR, "grading_details.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
