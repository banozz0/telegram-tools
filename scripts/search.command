#!/bin/bash
set -euo pipefail

finish() {
  status=$?
  echo
  read -r -p "Press Enter to close" _
  exit "$status"
}
trap finish EXIT

cd /Users/sven/code/telegram-tools
source .venv/bin/activate

read -r -p "Chat (@username, t.me link, or numeric ID): " CHAT
read -r -p "Keyword: " KEYWORD
read -r -p "Topic ID (optional): " TOPIC

if [ -z "$CHAT" ] || [ -z "$KEYWORD" ]; then
  echo "Chat and keyword are required."
  exit 2
fi

ARGS=(search --chat "$CHAT" --contains "$KEYWORD" --format json)
if [ -n "$TOPIC" ]; then
  ARGS+=(--topic "$TOPIC")
fi

telegram-tools "${ARGS[@]}"
