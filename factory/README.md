# click task factory

A pipeline that turns `pallets/click` (pinned at tag 8.5.1, commit `cf19179e8e2f`) into
feature-development tasks in the Harbor package format, and only emits a package after it has passed
every validation gate locally.

## Setup

Python 3.12, git and network access to GitHub and the Anthropic API.

```bash
python -m venv .cache/venv            # or: uv venv .cache/venv --python 3.12
.cache/venv/bin/pip install -r requirements.txt     # Windows: .cache\venv\Scripts\pip
cp .env.example .env                  # then put the team ANTHROPIC_API_KEY in .env
```

```bash
.cache/venv/bin/python -m taskfactory prepare        # clone, snapshots, coverage map (~4 min, no model calls)
```

## Run

```bash
.cache/venv/bin/python -m taskfactory generate --n 5 --seed 1 --workers 5
```

Outputs (default `../submission/`):

| Path | What |
|---|---|
| `generated_tasks/<instance_id>/` | Harbor packages that passed every gate, unedited |
| `pipeline_run.json` | per-task wall time, model calls, attempts, tokens, cost |
| `runs/<run_id>/summary.md` | batch table: cell, attempts, F2P/P2P counts, mutants killed, cost |
| `runs/<run_id>/summary.json` | the same plus every gate result per attempt |
| `runs/<run_id>/<task>/` | every model request/response and every failure report (audit trail) |

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
emit       Harbor package: task.toml, instruction.md, solution/, tests/, environment/Dockerfile
```

Model: Claude Opus 5.5 at effort `high` with structured JSON output. Two interchangeable backends:
OpenRouter (`OPENROUTER_API_KEY`, model `anthropic/claude-opus-5.5`) or the Anthropic API
(`ANTHROPIC_API_KEY`, model `claude-opus-5-5`); `FACTORY_BACKEND` picks one explicitly. The whole click
source (~110k tokens) sits in a cached system prompt shared by every call, so retries and parallel
tasks read it from the cache.

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

## Reused components (disclosure)

- `taskfactory/templates/test.sh`, `grade.py`, `parser.py`, `solve.sh` are copied unchanged from the
  organizers' worked example (`examples/toy__stringkit-0001-truncate`).
- The Dockerfile and `run_script.sh` follow the same example's structure.

## Limitations

- Local gates run on the host OS; the container is Linux. Tests with OS-dependent markers are excluded from
  the regression slice, and the Harbor oracle/nop self-check is the final authority.
- The fairness gate is lexical: it catches unstated wording, not unstated logic.
- Novelty is checked against upstream main and the commit log, not against open pull requests.
