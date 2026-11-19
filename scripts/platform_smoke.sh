#!/usr/bin/env bash
# Keep the macOS entry point and use the checkout venv's Python.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
export HIPSON_SMOKE_BASH_VERSION="$BASH_VERSION"

for arg in "$@"; do
  case "$arg" in
    --skip-sync|-h|--help) exec python3 "$ROOT/scripts/platform_smoke.py" "$@" ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi
uv sync -q --all-extras --project "$ROOT"
export PATH="$ROOT/.venv/bin:$PATH"
exec python3 "$ROOT/scripts/platform_smoke.py" --skip-sync "$@"
