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
read -r -p "Topic IDs to clear messages from (space or comma separated): " TOPICS_INPUT
read -r -p "Actually clear messages now? Type DELETE to pass --execute, or press Enter for dry-run: " EXECUTE_CONFIRM

if [ -z "$CHAT" ] || [ -z "$TOPICS_INPUT" ]; then
  echo "Chat and at least one topic ID are required."
  exit 2
fi

ARGS=(clear-messages --chat "$CHAT")
IFS=', ' read -r -a TOPICS <<< "$TOPICS_INPUT"
for TOPIC in "${TOPICS[@]}"; do
  if [ -n "$TOPIC" ]; then
    ARGS+=(--topic "$TOPIC")
  fi
done

if [ "$EXECUTE_CONFIRM" = "DELETE" ]; then
  ARGS+=(--execute)
else
  echo "Dry-run mode. No messages will be cleared."
fi

telegram-tools "${ARGS[@]}"
