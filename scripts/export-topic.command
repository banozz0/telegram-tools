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
read -r -p "Topic ID to export: " TOPIC
read -r -p "Output file [exports/topic-messages.json]: " OUTPUT
read -r -p "Format [json/csv, default json]: " FORMAT

if [ -z "$CHAT" ] || [ -z "$TOPIC" ]; then
  echo "Chat and topic ID are required."
  exit 2
fi

OUTPUT="${OUTPUT:-exports/topic-messages.json}"
FORMAT="${FORMAT:-json}"

mkdir -p "$(dirname "$OUTPUT")"
telegram-tools search --chat "$CHAT" --topic "$TOPIC" --format "$FORMAT" --output "$OUTPUT"

echo
echo "Topic messages exported to $OUTPUT"
