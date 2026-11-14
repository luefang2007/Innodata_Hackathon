# Measured variation

5 packages, 10 pairs. Similarity = mean Jaccard overlap of changed symbols, regression tests and spec terms (0 = nothing shared, 1 = identical). Near-duplicate threshold: 0.5.

| Task | Files changed | Symbols changed | P2P tests | Spec terms |
|---|---|---|---|---|
| 0101 `group-subcommand-lifecycle-hooks` | core.py | 9 | 80 | 56 |
| 0102 `help-effective-value-origin` | core.py, types.py | 7 | 80 | 91 |
| 0103 `line-completion-protocol` | shell_completion.py | 4 | 19 | 92 |
| 0104 `option-shorthand-expansions` | __init__.py, core.py, decorators.py, parser.py | 25 | 80 | 97 |
| 0105 `numbered-choice-prompts` | core.py, termui.py | 7 | 80 | 88 |

| Pair | Symbols | P2P | Terms | Similarity |
|---|---|---|---|---|
| 0102 / 0104 | 0.07 | 0.21 | 0.05 | 0.11 |
| 0102 / 0105 | 0.27 | 0.00 | 0.07 | 0.11 |
| 0101 / 0102 | 0.07 | 0.08 | 0.05 | 0.07 |
| 0101 / 0104 | 0.03 | 0.07 | 0.06 | 0.05 |
| 0101 / 0103 | 0.00 | 0.12 | 0.00 | 0.04 |
| 0101 / 0105 | 0.07 | 0.00 | 0.01 | 0.03 |
| 0104 / 0105 | 0.03 | 0.00 | 0.04 | 0.02 |
| 0102 / 0103 | 0.00 | 0.00 | 0.01 | 0.00 |
| 0103 / 0104 | 0.00 | 0.00 | 0.01 | 0.00 |
| 0103 / 0105 | 0.00 | 0.00 | 0.01 | 0.00 |

Max similarity 0.11, mean 0.04; 0 pair(s) above the threshold.
