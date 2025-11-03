#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME=${IMAGE_NAME:-myrace-helper}
CONTAINER_NAME=${CONTAINER_NAME:-myrace-helper-bot}
ENV_FILE=${ENV_FILE:-.env}
COOKIES_FILE=${COOKIES_FILE:-myrace_cookies.txt}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Файл окружения $ENV_FILE не найден. Создайте его или задайте ENV_FILE=..." >&2
  exit 1
fi

if [[ ! -f "$COOKIES_FILE" ]]; then
  echo "Cookie-файл $COOKIES_FILE не найден, создаём пустой." >&2
  touch "$COOKIES_FILE"
fi

echo ">>> Сборка образа $IMAGE_NAME ..."
docker build -t "$IMAGE_NAME" .

echo ">>> Запуск контейнера $CONTAINER_NAME ..."
docker run --rm -it \
  --name "$CONTAINER_NAME" \
  --env-file "$ENV_FILE" \
  -v "$(pwd)/$COOKIES_FILE:/app/$COOKIES_FILE" \
  "$IMAGE_NAME" \
  python3 telegram_bot.py "$@"
