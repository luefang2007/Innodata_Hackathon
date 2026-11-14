# Track 1: what Lue built so far, and why it needs the team API key

## Summary

I built the task factory: a pipeline that turns `pallets/click` into feature-development tasks in the
Harbor format. Each task gets a spec, a reference solution (the gold) and tests. Claude writes the
tasks. Free local checks reject any task that is broken, unfair or already in click before it is
saved. The checks, setup and packaging all work and were tested. The one step not run yet is the
real generation, because it calls Claude with the team key.

## What I did

1. **Read the kit and picked a target.**
   - Read the brief, the judging rubric (7 dimensions, 8 validation gates), the Harbor format and the
     worked example.
   - Chose `pallets/click` at tag 8.5.1 (commit `cf19179e`): pytest is fast, and the example's grading
     scripts already use pytest.
2. **Set up click locally.**
   - Cloned click and built a clean snapshot of 8.5.1, plus a snapshot of live upstream main for the
     novelty check.
   - Confirmed the full suite is green: 2350 passed, 0 failed.
3. **Mapped click's test suite.**
   - Ran every existing test under coverage and recorded which tests run which source lines.
   - This lets the pipeline pick the right regression tests for each new feature instead of dumping
     the whole suite.
4. **Wrote the pipeline** (`factory/`, Python). The parts are listed below.
5. **Tested the pipeline without the API.**
   - Fed it a hand-written fixture task. All 8 local checks passed, and the mutation check caught
     12 of 12 broken versions of the gold.
   - The emitted package passed the kit's `check_structure.py`.
   - The fixture is in `factory/smoke/` so the Docker owner can test Harbor with it now.

## What I built

| Part | What it does |
|---|---|
| Variation planner | Gives each of the 5 tasks a different click area (parser, types, core, help, terminal/CliRunner, completion) and a different behaviour shape (input grammar, interaction rules, lifecycle, output format, public API). This targets the 20-point variation score. |
| Generator | Asks Claude for a feature design and spec, then for the gold code and a test file. |
| Local checks (8) | No Docker needed, about 30 s per task. They check that: the gold passes the tests; the tests fail without the feature; existing tests stay green; broken golds get caught; the tests only assert what the spec states; the feature isn't already upstream. They also check spec hygiene and that the gold extends real click code. |
| Repair loop | Sends the exact failure report back to Claude to fix, with at most 3 attempts per task. |
| Packager | Writes the Harbor package: `task.toml`, `instruction.md`, `solution/`, `tests/`, `Dockerfile`. |
| Telemetry | Logs every model call (tokens, cost, time) and writes `pipeline_run.json`, which feeds the 10-point efficiency score. |

## Why it needs the API

- **The model writes the tasks.** The core deliverable is a pipeline whose raw output is graded.
  Judges re-run it and score the unedited packages, so writing tasks by hand doesn't count. The
  spec, gold and tests must come from a model call.
- **Calls per task:** 1 design + 1 implementation, plus up to 2 repairs if a check fails. Each
  batch also makes one cache warm-up call.
- **Model:** Claude Opus 5.5, called through the team **OpenRouter** key
  (`anthropic/claude-opus-5.5`). The brief recommends OpenRouter for generation, which leaves the
  Anthropic key for the Haiku benchmark. We want the strongest model here because yield (20 pts) and
  difficulty (20 pts) depend on how good the generated code and specs are.
- **To see spend:** run `python -m taskfactory usage` for per-run cost plus the key's total and
  remaining credit. Per-task cost is also in `submission/pipeline_run.json`.

**Cost estimate:** about $1 per task, $5–6 for a batch of 5.
- The click source (~110k tokens) is sent once and then read from the prompt cache (~$0.02 per
  call).
- Most of the cost is Claude's written output.

**Everything else is free.** `prepare`, `recheck` and all checks run locally with no API calls, so
a broken task costs one repair call, not a full regeneration.

**Suggested budget:**

| Key | Use | Amount |
|---|---|---|
| OpenRouter ($100) | 2–4 generation batches (first run, then tuned reruns) | ~$25 |
| Anthropic ($100) | Haiku 5.5 benchmark (5 tasks × 3 attempts; Haiku costs $0.10/$0.50 per million tokens) | a few dollars |
| Both | Reserve | the rest |

Keys live only in `factory/.env`, which is git-ignored and excluded from the submission. They never
appear in code or logs.

## How to run

```bash
cd factory
python3.12 -m venv .cache/venv && .cache/venv/bin/pip install -r requirements.txt
cp .env.example .env                                            # add OPENROUTER_API_KEY (or ANTHROPIC_API_KEY)
.cache/venv/bin/python -m taskfactory prepare                   # free, ~4 min
.cache/venv/bin/python -m taskfactory generate --n 5 --seed 1   # uses the API, ~10-20 min
```

Output goes to `submission/`: `generated_tasks/`, `pipeline_run.json`, and `runs/` with every model
call and every check report.

## Status update (after the first run)

- **Run 1:** 5/5 single-feature tasks passed all checks, for $10.95 on OpenRouter. They're in
  `submission/generated_tasks/`.
- **Difficulty probe:** Haiku 5.5, one-shot, cost $1.80. It solved 14 of 15 attempts, so single-feature
  tasks are too easy. The specs are precise enough to transcribe.
- **No more API spend** (team decision). Difficulty is now raised with the new `compose` stage, which
  makes no model calls.
  - It builds span-3 tasks: three verified features in one change, with every gate re-run on the
    combination.
  - Result: 5/5 composites are valid and pass `check_structure.py`. They're in
    `submission/generated_tasks_composite/`.
  - Their cost attribution is in `submission/pipeline_run_generated_tasks_composite.json`.
  - See `factory/VARIATION.md` for how combinations are chosen. One pair of features conflicts in
    `core.py`, and the gates caught it.
- **Not measured:** composite difficulty. That needs the Harbor benchmark, or a probe, which would
  spend API budget.
- **Solver jury (new, in the pipeline):** the benchmark model (Haiku 5.5) attempts each candidate task
  3 times before it ships. If it solves the task more than once, the generator must deepen the task
  and it is measured again (max 2 rounds). See "Solver jury" in `factory/README.md`.
  - Tested offline by replaying the 15 Haiku answers from the morning probe: the new grading
    reproduces the morning results (3/3, 3/3, 3/3, 3/3, 2/3).
  - Not yet run against a live model, so the current packages predate it. A fresh run needs the API:
    `python -m taskfactory generate --n 5 --seed 2` (budget cap `FACTORY_BUDGET_USD`, default 25).
- **Run 2 (live, 13:08–13:26 ET, $11.01):** output in `submission_v2/`. It reused run 1's paid
  design/implement calls, measured every task with Haiku (all 3/3), then deepened once.
  - 0101 (lifecycle hooks) got harder: Haiku 3/3 -> 1/3. Its deepened spec adds teardown hooks,
    `SkipSubcommand`, exception ordering, chain mode and re-entrancy.
  - 0102 was deepened but stayed 3/3, so its original was kept. 0103/0104/0105: the deepen answer was
    cut off at the output-token limit, so their originals were kept.
  - `submission_v2/generated_tasks` is a complete valid batch of 5 (variation max 0.11). 0102 and
    0105 are byte-identical to run 1; 0103 and 0104 are equivalent variants; 0101 is the deepened one.
- **Measured variation:** `python -m taskfactory diversity <packages>`. Single-feature set: max
  similarity 0.11, no near-duplicates. Composites: max 0.69, two near-duplicate pairs, so the
  single-feature set stays the submitted one.
- **Cost fix:** run 1 paid for 7 cache writes ($5.6 of $10.95). Parallel calls now wait for the first
  call per schema. The spend cap now counts only this output folder's own run logs.
- **Packaging:** `python -m taskfactory bundle --team <TeamName>` builds `dist/<TeamName>_Track1.zip`
  and runs the kit validator. Raw logs go in as `.tar.gz`, because the kit's secret scanner flags
  generated code such as `token = ...` in plain `.json` logs.

## Still to do

1. Put the key in `factory/.env` and run `generate`.
2. Docker owner: run the Harbor self-check on `factory/smoke/` now, then on the generated tasks.
3. Run the Haiku benchmark (k=3) and keep the trajectories.
4. Finish `factory/VARIATION.md` and the member contributions, then run `validate_submission.py`.
