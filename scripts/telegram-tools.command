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

telegram-tools
