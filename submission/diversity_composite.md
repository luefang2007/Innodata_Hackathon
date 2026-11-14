# Measured variation

5 packages, 10 pairs. Similarity = mean Jaccard overlap of changed symbols, regression tests and spec terms (0 = nothing shared, 1 = identical). Near-duplicate threshold: 0.5.

| Task | Files changed | Symbols changed | P2P tests | Spec terms |
|---|---|---|---|---|
| 0201 `combo-lifecycle-hooks-value-origin-shorthand-expansions` | __init__.py, core.py, decorators.py, parser.py, types.py | 38 | 160 | 223 |
| 0202 `combo-lifecycle-hooks-shorthand-expansions-choice-prompts` | __init__.py, core.py, decorators.py, parser.py, termui.py | 39 | 160 | 225 |
| 0203 `combo-lifecycle-hooks-value-origin-completion-protocol` | core.py, shell_completion.py, types.py | 19 | 160 | 230 |
| 0204 `combo-value-origin-completion-protocol-shorthand-expansions` | __init__.py, core.py, decorators.py, parser.py, shell_completion.py, types.py | 34 | 160 | 267 |
| 0205 `combo-lifecycle-hooks-completion-protocol-choice-prompts` | core.py, shell_completion.py, termui.py | 19 | 160 | 233 |

| Pair | Symbols | P2P | Terms | Similarity |
|---|---|---|---|---|
| 0201 / 0204 | 0.71 | 0.78 | 0.58 | 0.69  **near-duplicate** |
| 0201 / 0202 | 0.83 | 0.41 | 0.51 | 0.58  **near-duplicate** |
| 0203 / 0205 | 0.65 | 0.27 | 0.52 | 0.48 |
| 0201 / 0203 | 0.36 | 0.57 | 0.46 | 0.46 |
| 0202 / 0205 | 0.35 | 0.58 | 0.46 | 0.46 |
| 0203 / 0204 | 0.26 | 0.45 | 0.60 | 0.44 |
| 0202 / 0204 | 0.59 | 0.32 | 0.28 | 0.40 |
| 0202 / 0203 | 0.26 | 0.32 | 0.19 | 0.26 |
| 0201 / 0205 | 0.24 | 0.19 | 0.19 | 0.21 |
| 0204 / 0205 | 0.15 | 0.12 | 0.31 | 0.19 |

Max similarity 0.69, mean 0.42; 2 pair(s) above the threshold.
