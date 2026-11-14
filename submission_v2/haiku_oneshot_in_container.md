# One-shot Haiku attempts, re-graded in the task containers

The solver jury's logged attempts on the shipped version of each task were turned into patches,
applied in the task image that Harbor built from the package's Dockerfile, and graded by the package's
own `tests/test.sh`. No model was called for this. It is not the official benchmark, which runs the
terminus-2 agent.

| Task | Attempts graded | Solved in the container | Solved on the host | Rewards in the container | Same as host |
|---|---|---|---|---|---|
| `0101-group-subcommand-lifecycle-hooks` | 3 | 1/3 | 1/3 | 1.0, 0.9545, 0.9545 | yes |
| `0102-help-effective-value-origin` | 3 | 3/3 | 3/3 | 1.0, 1.0, 1.0 | yes |
| `0103-line-completion-protocol` | 3 | 3/3 | 3/3 | 1.0, 1.0, 1.0 | yes |
| `0104-option-shorthand-expansions` | 3 | 3/3 | 3/3 | 1.0, 1.0, 1.0 | yes |
| `0105-numbered-choice-prompts` | 3 | 3/3 | 3/3 | 1.0, 1.0, 1.0 | yes |
