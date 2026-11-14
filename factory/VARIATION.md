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

(filled in from `runs/<run_id>/summary.md` after the final run)
