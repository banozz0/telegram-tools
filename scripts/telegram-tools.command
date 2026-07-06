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

telegram-tools
