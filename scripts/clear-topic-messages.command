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
read -r -p "Topic ID to clear messages from: " TOPIC
read -r -p "Actually clear messages now? Type DELETE to pass --execute, or press Enter for dry-run: " EXECUTE_CONFIRM

if [ -z "$CHAT" ] || [ -z "$TOPIC" ]; then
  echo "Chat and topic ID are required."
  exit 2
fi

ARGS=(clear-messages --chat "$CHAT" --topic "$TOPIC")
if [ "$EXECUTE_CONFIRM" = "DELETE" ]; then
  ARGS+=(--execute)
else
  echo "Dry-run mode. No messages will be cleared."
fi

telegram-tools "${ARGS[@]}"
