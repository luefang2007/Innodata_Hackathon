# click task factory

A pipeline that turns `pallets/click` (pinned at tag 8.5.1, commit `cf19179e8e2f`) into
feature-development tasks in the Harbor package format. It only emits a package after the package has
passed every validation gate locally, and it measures each task's difficulty with the benchmark model
before the task ships.

## What is in this submission

| Path | What |
|---|---|
| `generated_tasks/` | The 5 submitted packages: the raw output of run `20261114T180829Z-s1` (solver jury on) |
| `pipeline_run.json` | Per-task telemetry for those 5 packages |
| `factory/` | The pipeline source, this README and `VARIATION.md` (the variation writeup) |
| `evidence/submission_v2/` | Logs of the submitted run, its summary and the measured-variation report |
| `evidence/submission/` | Logs of run 1 (`20261114T162521Z-s1`), whose design and implementation calls the submitted run reused, plus the stand-alone probe |
| `extras/` | Earlier package sets that are **not** submitted: run 1's five packages and the span-3 composites |

Raw logs are `.tar.gz` archives: they contain generated code such as `token = ...`, which the kit's
secret scanner reports when it reads plain `.json`.

**Results in one place.** Both runs produced 5 valid packages out of 5. On the submitted set the
one-shot Haiku 5.5 juror solves one task 1 time in 3 and the other four 3 times in 3, so four of the
five are still easy by that proxy. Pairwise similarity of the five is at most 0.11. The kit's
`selfcheck.sh` (Harbor 0.26.0, Docker 29.9.3, images built from each package's own Dockerfile) reports
oracle 1.0 and nop 0.0 for all five (`evidence/selfcheck/SUMMARY.txt`), and inside the built task image
click's full suite is green (2434 passed, 0 failed). A second Linux machine reproduced the local gates
and the oracle/nop results. The juror's 15 logged attempts on the shipped tasks were also re-graded
inside the task containers by each package's own `tests/test.sh` (`python -m taskfactory regrade`, no
model calls): the container agrees with the host on every attempt, 1 of 3 solved for 0101 and 3 of 3 for
the others (`evidence/submission_v2/haiku_oneshot_in_container.md`). No official resolve@k benchmark
(terminus-2 agent) has been run. Total API spend was $23.76
(run 1 $10.95, probe $1.80, submitted run $11.01).

## Setup

Python 3.12, git, and network access to GitHub and a model API.

```bash
python -m venv .cache/venv            # or: uv venv .cache/venv --python 3.12
.cache/venv/bin/pip install -r requirements.txt     # Windows: .cache\venv\Scripts\pip
cp .env.example .env                  # then put OPENROUTER_API_KEY (the backend we ran) in .env
```

```bash
.cache/venv/bin/python -m taskfactory prepare        # clone, snapshots, coverage map (about 1 min, no model calls)
```

## Run

```bash
.cache/venv/bin/python -m taskfactory generate --n 5 --seed 1 --workers 5
```

A fresh run of 5 tasks with the jury takes about 20 to 30 minutes and $10 to $20; `FACTORY_BUDGET_USD`
(default 25) is a hard cap. A model writes the tasks, so a re-run produces different tasks of the same
kind, not the same five.

Outputs (default `../submission/`):

| Path | What |
|---|---|
| `generated_tasks/<instance_id>/` | Harbor packages that passed every gate, unedited |
| `pipeline_run.json` | per-task wall time, model calls, attempts, tokens, cost |
| `runs/<run_id>/summary.md` | batch table: cell, attempts, F2P/P2P counts, mutants killed, cost |
| `runs/<run_id>/summary.json` | the same plus every gate result per attempt |
| `runs/<run_id>/<task>/` | every model request/response and every failure report (audit trail) |

Compose harder multi-feature tasks from the validated packages (no model calls, ~3 min):

```bash
.cache/venv/bin/python -m taskfactory compose ../submission/generated_tasks/* --span 3 --n 5 \
    --atom-summary ../submission/runs/<run_id>/summary.json
```

This writes `generated_tasks_composite/`, `pipeline_run_generated_tasks_composite.json` (each feature's
real generation cost split across the composites that use it) and `compose_summary.json`.

Re-check emitted packages without model calls:

```bash
.cache/venv/bin/python -m taskfactory recheck ../submission/generated_tasks/*
```

Container validation (needs Docker + Harbor):

```bash
python "../Track 1 - Coding/harness/check_structure.py" ../submission/generated_tasks/*
SELFCHECK_OUT=./sc "../Track 1 - Coding/harness/selfcheck.sh" ../submission/generated_tasks/*
```

## How it works

```
prepare    click@8.5.1 snapshot (fresh one-commit git tree) + live upstream main snapshot
           + per-test coverage map of the whole base suite (which tests execute which lines)
plan       variation matrix: module x behaviour shape, seeded; no module or shape repeats in a batch
design     Claude writes the feature design + instruction.md for its cell
implement  Claude writes search/replace edits to src/click (gold) + a new test file
gates      local, no Docker, ~30 s per candidate:
             hygiene         no authoring tells; spec never mentions tests or the reference solution
             grounding       gold edits existing click modules
             gold_tests      every new test passes with the gold
             feature_absent  >= 6 new tests fail on the base; at most 1 in 5 pass without the feature
             regression      existing tests that execute the touched code (from the coverage map)
                             pass at base and with the gold
             mutation        >= 70% of up to 12 mutants of the gold's added lines are caught
             fairness        every word of an asserted message appears in the spec, the test's own
                             inputs or click's existing source
             novelty         the feature tests do not pass on live upstream main
repair     on any failure the exact failure report goes back to Claude (max 3 attempts in total)
jury       difficulty is measured before a task ships (see "Solver jury" below): the benchmark model
           attempts the task k times; if it solves it too often the generator must deepen the task and
           it is measured again (max 2 rounds). The hardest valid candidate is emitted.
emit       Harbor package: task.toml, instruction.md, solution/, tests/, environment/Dockerfile
compose    (optional, no model calls) span-k tasks: k validated features must land in one change.
           Merged spec, golds applied together, all feature tests, regression slice re-selected for
           the combined change, and every gate re-run on the combination. Golds that edit the same
           code and cannot coexist are detected and the combination is dropped.
```

## Solver jury (difficulty measured inside the pipeline)

A task factory with no difficulty signal only learns whether a task is hard after the benchmark has
run. Ours measures it before the task ships, with the benchmark model as a juror (`taskfactory/jury.py`):

- **Weak juror = Claude Haiku 5.5.** It sees only `instruction.md` and the click source (trimmed to the
  files the feature touches plus core modules, about 85k tokens), writes its patch in one shot, and is
  graded locally with the same rule as `tests/grade.py`. Three attempts give a solve rate.
- **Decision.** Solve rate at most 1/3: the task ships. Otherwise the generator receives the result
  (including the feature tests the juror failed) and must deepen the task: more rules that interact with
  existing click behaviour, a declarative spec instead of a procedure, tests that need several rules at
  once. The new candidate passes every validity gate again and is measured again, up to 2 rounds.
- **Why 1/3.** The grader is all-or-nothing, so a single-attempt solve rate p gives
  P(resolve@3 <= 1/3) = (1-p)^3 + 3p(1-p)^2: about 90% at p = 0.2 and 74% at p = 1/3.
- **Regression traps.** Existing tests that a juror's plausible-but-wrong patch breaks (while they pass
  at base and with the gold) are added to the regression slice.
- **Failure fingerprint.** The feature tests the juror fails are recorded per task in the run summary.
- **Safety.** The jury can only keep or improve a task: if a deepened candidate does not validate, or a
  model call fails, the best valid candidate so far is emitted.
- **Optional strong juror** (`FACTORY_JURY_STRONG=<model>`): one blind attempt by a strong model, recorded
  as evidence that the spec alone is enough to solve the task.

The jury is an adversarial filter that uses the benchmark model. We disclose that here on purpose.
It is on by default (`FACTORY_JURY=0` turns it off) and costs a few cents per round on Haiku plus one
generator call per deepening round.

**First live result (run `20261114T180829Z-s1`, in `submission_v2/`).** The run replayed run 1's design
and implementation calls from the response cache and spent $11.01 on new calls (cap $12). In round 0
Haiku solved all five tasks 3/3. One deepening round followed:

| Task | Mechanism | What happened | Haiku after |
|---|---|---|---|
| 0101 lifecycle hooks | state and re-entrancy | deepened version passed every gate first try | **1/3** |
| 0102 effective value | compatibility trap | passed after one repair but was no harder, so the original was kept | 3/3 |
| 0103, 0104, 0105 | algorithm, call sites, algorithm | the deepen answer hit the 80k-token output limit, so the original was kept | 3/3 |

The jury made one of five tasks measurably harder and did not lose a valid task. Two things it
taught us: the deepen call needs a smaller reasoning budget or a larger output limit ($5 of the $11 went
to three cut-off answers), and a deepened task that is not harder should get its second mechanism
rather than be dropped. Before this run the same code was tested offline by replaying the 15 Haiku
answers logged by the earlier probe.

The same juror is available stand-alone: `python -m taskfactory probe <packages>`. It solved 14 of 15
one-shot attempts on our single-feature tasks, which is the measurement that motivated the jury.

## Other difficulty and variation tools

- `compose` (above) is a second difficulty dial: span k asks for k validated features in one change.
  Measured on our data it costs variation (see below), so it is a tool, not the default.
- `python -m taskfactory diversity <packages>` measures pairwise similarity of packages from the code
  symbols their golds change, their regression slices and their spec terms. Single-feature set: max
  0.11, mean 0.04, no near-duplicates. Span-3 composites: max 0.69, mean 0.42, two near-duplicate pairs.
- `FACTORY_OFFLINE=1` never calls a model: identical earlier calls are replayed from the response cache.
  A task whose first attempt needed a repair cannot be replayed, because the repair prompt embeds test
  output that differs from run to run (timings, paths).

Model: Claude Opus 5.5 at effort `high` with structured JSON output. Two interchangeable backends:
OpenRouter (`OPENROUTER_API_KEY`, model `anthropic/claude-opus-5.5`) or the Anthropic API
(`ANTHROPIC_API_KEY`, model `claude-opus-5-5`); `FACTORY_BACKEND` picks one explicitly. The whole click
source (~160k tokens) sits in a cached system prompt shared by every call. The cached prefix includes
the output schema, so the first call per schema runs ahead and the parallel ones wait for it (up to
60 s) and then read the cache. Run 1 did not have this gate and paid for seven cache writes ($5.6 of
$10.95); with it a batch should need two (not measured yet).

Spend is capped per output directory (`FACTORY_BUDGET_USD`, default 25): the cap counts the calls logged
under that directory's `runs/`, and a call only starts if its worst case still fits.

API spend, live or after a run (OpenRouter also shows the key's total usage and limit):

```bash
.cache/venv/bin/python -m taskfactory usage
```

## Variation strategy

See `VARIATION.md`. In short, each task gets one cell of a matrix whose axes are *where in click* (parser,
types, core runtime, help formatting, terminal UI and CliRunner, shell completion) and *what shape of
behaviour* (input grammar, cross-feature interaction rules, lifecycle/state, exact output format, public
extension API). A batch never repeats a row or a column, so the five tasks differ in capability and in
the kind of reasoning they demand.

## Reused components and tools (disclosure)

- `taskfactory/templates/test.sh`, `grade.py`, `parser.py`, `solve.sh` are copied unchanged from the
  organizers' worked example (`examples/toy__stringkit-0001-truncate`).
- The Dockerfile and `run_script.sh` follow the same example's structure.
- The pipeline code was written with an AI coding assistant (Claude Code). Task content (specs, golds,
  tests) comes only from the pipeline's own model calls, which are logged under `runs/`; no package was
  edited by hand.
- The benchmark model (Haiku 5.5) is used inside the pipeline as a difficulty juror, and was used once
  before that as a stand-alone probe ($1.80, logged under `probe/`).

## Team contributions

(To be filled in by the team before submitting: one line per member.)

## Limitations

- Local gates run on the host OS; the container is Linux. Tests with OS-dependent markers are excluded from
  the regression slice, and the Harbor oracle/nop self-check is the final authority.
- The fairness gate is lexical: it catches unstated wording, not unstated logic.
- The juror is a one-shot proxy for the official agent benchmark (terminus-2 can run tests and
  iterate), so its solve rate needs calibrating against real resolve@k.
- Composites drawn from five features share features with each other (measured: two near-duplicate
  pairs), and by the probe's numbers three easy features at once are still likely to be solved.
- The Anthropic-SDK backend follows the SDK documentation but only the OpenRouter backend was
  exercised end to end.
- Novelty is checked against upstream main and the commit log, not against open pull requests.
