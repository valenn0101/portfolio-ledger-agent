#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
fi

if [ -z "${OPENAI_API_KEY:-}" ] && [ -z "${OPENROUTER_API_KEY:-}" ] \
  && ! grep -Eq '^(OPENAI_API_KEY|OPENROUTER_API_KEY)=.+$' .env; then
  echo "Falta OPENAI_API_KEY en $(pwd)/.env"
  echo "Creá tu clave en https://platform.openai.com/api-keys, pegala allí y volvé a iniciar."
  exit 1
fi

exec ./iniciar.sh "$@"
