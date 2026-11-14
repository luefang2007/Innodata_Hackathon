#!/usr/bin/env bash
# VERIFIER ENTRY — Harbor runs this. Create the reward dir and hand off to grade.py.
set -euo pipefail
mkdir -p /logs/verifier
python3 "$(dirname "$0")/grade.py"
