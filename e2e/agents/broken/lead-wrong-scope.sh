#!/usr/bin/env bash
set -euo pipefail
# Builders edit the default parser target while claiming an unrelated directory.
source <(sed 's/--scope src\/parser/--scope src\/notes/g' "$(dirname "$0")/../scenarios/lead-flow/solution.sh")
