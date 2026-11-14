#!/usr/bin/env bash
# Run the graded tests. -rA feeds parser.py; only files present in the tree are passed so the
# feature-absent (nop) state still runs the regression files. NOT `set -e` (pytest exits non-zero
# when the feature is absent).
set -uo pipefail
cd /app
PY=/opt/venv/bin/python
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
ALL=(
  "tests/test_line_completion_protocol.py"
  "tests/test_shell_completion.py"
)
PRESENT=()
for f in "${ALL[@]}"; do [ -e "$f" ] && PRESENT+=("$f"); done
[ "${#PRESENT[@]}" -eq 0 ] && { echo "no graded test files present"; exit 0; }
"$PY" -m pytest "${PRESENT[@]}" -rA --tb=short -p no:cacheprovider --color=no --continue-on-collection-errors
