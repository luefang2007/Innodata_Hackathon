#!/bin/bash
# Oracle: apply the gold patch so the verifier scores reward 1.0.
set -e
git -C /app apply "$(dirname "$0")/patch.diff"
echo "gold applied to /app"
