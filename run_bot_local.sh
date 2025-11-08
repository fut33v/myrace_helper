#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${ENV_FILE:-.env}
COOKIES_DIR=${COOKIES_DIR:-cookies}
COOKIES_FILE=${COOKIES_FILE:-myrace_cookies.txt}
COOKIES_PATH="$COOKIES_DIR/$COOKIES_FILE"
PYTHON_BIN=${PYTHON_BIN:-python3}

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "$ENV_FILE" | xargs -0 -I{} printf '%s\0' {})
else
  echo "Файл окружения $ENV_FILE не найден. Переменные нужно указать вручную." >&2
fi

mkdir -p "$COOKIES_DIR"
if [[ ! -f "$COOKIES_PATH" ]]; then
  echo "Cookie-файл $COOKIES_PATH не найден, создаём пустой." >&2
  touch "$COOKIES_PATH"
fi

export MYRACE_COOKIES_PATH=${MYRACE_COOKIES_PATH:-$COOKIES_PATH}

exec "$PYTHON_BIN" telegram_bot.py "$@"
