#!/bin/bash
set -euo pipefail

finish() {
  status=$?
  echo
  read -r -p "Press Enter to close" _
  exit "$status"
}
trap finish EXIT

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source .venv/bin/activate

read -r -p "Chat (@username, t.me link, or numeric ID): " CHAT
read -r -p "Actually clear messages from ALL topics? Type DELETE to pass --execute, or press Enter for dry-run: " EXECUTE_CONFIRM

if [ -z "$CHAT" ]; then
  echo "Chat is required."
  exit 2
fi

ARGS=(clear-messages --chat "$CHAT" --all-topics-in-chat)
if [ "$EXECUTE_CONFIRM" = "DELETE" ]; then
  ARGS+=(--execute)
else
  echo "Dry-run mode. No messages will be cleared."
fi

telegram-tools "${ARGS[@]}"
