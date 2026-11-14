# How the factory creates meaningful variation

## The idea

A task template produces the same task with different names. A factory has to decide *what kind of
work* each task demands. Ours makes that decision explicitly, before any model call, with a two-axis
variation matrix over click:

| Axis | Values |
|---|---|
| **Where in click** (which subsystem the feature extends) | argument parser · parameter types · core runtime (Context, Command, Group) · help formatting · terminal UI and CliRunner · shell completion |
| **Behaviour shape** (what kind of reasoning the solver needs) | input grammar · cross-feature interaction rules · lifecycle and state · exact output format · public extension API |

A seeded planner assigns each task one cell so that **no subsystem and no behaviour shape repeats
within a batch**, and only pairs that make sense for click are allowed (for example, shell completion
pairs with grammar, output and API, but not lifecycle). The same seed always produces the same plan;
a new seed produces a different, still non-overlapping plan.

## Why this gives substantive differences

- Different subsystems mean different code to read and different integration points: a parser task
  edits the token loop, a help task edits `HelpFormatter` and `Command.format_*`, a completion task edits
  the per-shell payload classes.
- Different behaviour shapes mean different failure modes for a solver: grammar tasks punish sloppy
  validation and error text, interaction tasks punish getting a precedence order wrong, lifecycle tasks
  punish wrong call ordering, output tasks punish layout mistakes, API tasks punish missing validation of
  misuse.
- The design prompt also lists the other cells of the batch and forbids overlap with them.

## How the pipeline keeps the variation honest

- Each design must touch two or more places in click and state 6 to 9 numbered rules, at least two of
  which govern interaction with existing features (environment variables, `default_map`, prompts,
  `multiple`/`nargs`, help, completion, `ParameterSource`).
- The regression slice for each task is selected from the coverage map of the code that task's gold
  actually touches, so the P2P sets themselves differ task to task.

## Batch produced for the submission

Run `20261114T162521Z-s1` (seed 1, `anthropic/claude-opus-5.5` via OpenRouter). All 5 cells produced a
valid package. F2P counts the feature tests, P2P the coverage-selected existing tests, and Mutants how
many of 12 broken versions of the gold the feature tests caught.

| Cell (area x shape) | Task | What the solver must get right | F2P | P2P | Mutants |
|---|---|---|---|---|---|
| core x lifecycle | `group-subcommand-lifecycle-hooks` | ordering of before/after hooks around nested and chained subcommands | 15 | 80 | 11/12 |
| help x interaction | `help-effective-value-origin` | precedence of env var, prompt, `default_map` and default, plus help rendering and conflict with `show_default` | 14 | 80 | 12/12 |
| completion x grammar | `line-completion-protocol` | tokenising a shell line from `COMP_LINE`/`COMP_POINT`, quoting and cursor position | 38 | 19 | 10/12 |
| parser x api | `option-shorthand-expansions` | a declarative expansion API resolved inside the parser's token loop | 27 | 80 | 11/12 |
| termui x output | `numbered-choice-prompts` | exact menu layout, numbering and input rules for `Choice` prompts | 16 | 80 | 9/12 |

The five tasks touch five different click subsystems and five different kinds of behaviour. No two
share a primary module, and their regression slices come from the coverage of different code.

## Measured variation

We do not only claim the five tasks differ; `python -m taskfactory diversity` measures it from the
packages themselves, with no model. Each package gets a fingerprint: the existing click classes and
functions its gold changes, its regression tests, and the code terms its spec uses. Similarity of two
packages is the mean Jaccard overlap of the three sets (0 = nothing shared, 1 = identical).

| Pair | Changed symbols | Regression tests | Spec terms | Similarity |
|---|---|---|---|---|
| 0102 / 0104 | 0.07 | 0.21 | 0.05 | 0.11 |
| 0102 / 0105 | 0.27 | 0.00 | 0.07 | 0.11 |
| 0101 / 0102 | 0.07 | 0.08 | 0.05 | 0.07 |
| 0101 / 0104 | 0.03 | 0.07 | 0.06 | 0.05 |
| 0101 / 0103 | 0.00 | 0.12 | 0.00 | 0.04 |
| 0101 / 0105 | 0.07 | 0.00 | 0.01 | 0.03 |
| 0104 / 0105 | 0.03 | 0.00 | 0.04 | 0.02 |
| 0102 / 0103, 0103 / 0104, 0103 / 0105 | 0.00 | 0.00 | 0.01 | 0.00 |

Across the ten pairs the maximum is 0.11 and the mean 0.04; no pair is near the 0.5 near-duplicate
threshold. The full report is `evidence/diversity_single.md`.

The same measurement is why the span-3 composites below are not our submitted set: they score a
maximum of 0.69 and a mean of 0.42, with two near-duplicate pairs (`evidence/diversity_composite.md`).

## Second axis of variation: span (difficulty as a dial)

A cheap one-shot probe (Haiku 5.5, no tools, no test runs) solved 14 of 15 attempts on the single-feature
tasks above. Rich, precise specs make each feature fair, but one feature at a time is too easy. The
pipeline now answers this in two ways. The solver jury (see the README) measures each candidate with
the benchmark model and makes the generator deepen tasks that are solved too often. The compose stage
needs no model at all: a span-k task asks for k verified features in one change, with one merged spec,
the k golds applied together, every feature test, and a regression slice re-selected for the combined
change. All eight gates run again on the combination, so compatibility is checked, not assumed.

Two rules pick the combinations:

1. **Integration first.** Combinations whose golds edit the same source files rank first, so the solver
   has to coordinate changes in shared code (`core.py` is touched by four of the five features).
2. **Balanced reuse.** Each feature is used at most ceil(n·k / features) times. If incompatible golds leave
   gaps, the cap relaxes and the least-used features fill them.

The gates found one real conflict: *help effective value* and *numbered choice prompts* edit the same
lines of `core.py`, so their golds cannot coexist and every combination containing both was dropped.

| Task | Features (span 3) | F2P | P2P | Mutants |
|---|---|---|---|---|
| 0201 | lifecycle hooks + help effective value + option shorthands | 56 | 160 | 12/12 |
| 0202 | lifecycle hooks + option shorthands + numbered choice prompts | 58 | 160 | 11/12 |
| 0203 | lifecycle hooks + help effective value + line completion protocol | 67 | 160 | 11/12 |
| 0204 | help effective value + line completion protocol + option shorthands | 79 | 160 | 11/12 |
| 0205 | lifecycle hooks + line completion protocol + numbered choice prompts | 69 | 160 | 11/12 |

Each composite differs from every other in at least one feature and in the subsystems it forces the
solver to integrate. The trade-off is explicit: five span-3 tasks drawn from five features must share
features, which costs some variation to buy difficulty. A larger atom pool (another `generate` run)
removes the overlap; the composition stage itself scales without model cost.
