#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

mode="--dry-run"
if [[ "${1:-}" == "--apply" ]]; then
  mode="--apply"
elif [[ "${1:-}" == "--dry-run" || $# -eq 0 ]]; then
  mode="--dry-run"
else
  echo "Usage: $0 [--dry-run|--apply]" >&2
  exit 2
fi

PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 -m hipson.cli install codex "${mode}"
