#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../scenarios/conflict/solution.sh"
rm src/parser/pairs.py
