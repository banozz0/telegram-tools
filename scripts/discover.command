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

read -r -p "Export JSON too? [y/N]: " EXPORT_JSON
read -r -p "Show all chats, including non-admin chats? [y/N]: " SHOW_ALL

ARGS=(discover)
if [[ "$SHOW_ALL" =~ ^[Yy]$ ]]; then
  ARGS+=(--all)
fi

if [[ "$EXPORT_JSON" =~ ^[Yy]$ ]]; then
  mkdir -p exports
  telegram-tools "${ARGS[@]}" --json exports/discovery.json
  echo "Discovery JSON written to exports/discovery.json"
  echo
fi

telegram-tools "${ARGS[@]}"
