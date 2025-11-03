#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${ENV_FILE:-.env}
COOKIES_FILE=${COOKIES_FILE:-myrace_cookies.txt}
PYTHON_BIN=${PYTHON_BIN:-python3}

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "$ENV_FILE" | xargs -0 -I{} printf '%s\0' {})
else
  echo "Файл окружения $ENV_FILE не найден. Переменные нужно указать вручную." >&2
fi

if [[ ! -f "$COOKIES_FILE" ]]; then
  echo "Cookie-файл $COOKIES_FILE не найден, создаём пустой." >&2
  touch "$COOKIES_FILE"
fi

exec "$PYTHON_BIN" telegram_bot.py "$@"
