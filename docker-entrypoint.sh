#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "python3" ]]; then
  shift
  exec python3 "$@"
fi

exec "$@"
