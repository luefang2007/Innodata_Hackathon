# Harbor smoke test (not a submission task)

`pallets__click-9999-intrange-step` is a hand-written fixture used only to test the container path
(Dockerfile build, `solve.sh`, grading scripts) before the generated tasks exist. Do not submit it.

On a machine with Docker and Harbor:

```bash
python "../../Track 1 - Coding/harness/check_structure.py" pallets__click-9999-intrange-step
SELFCHECK_OUT=./sc "../../Track 1 - Coding/harness/selfcheck.sh" pallets__click-9999-intrange-step
```

Expected: oracle reward 1.0 (F2P 7/7, P2P 16/16) and nop reward 0.0 (F2P 0/7, P2P 16/16).
If this passes, every generated package will build the same way: they share the Dockerfile,
`run_script.sh` layout and grading scripts.
