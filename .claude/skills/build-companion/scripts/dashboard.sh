#!/usr/bin/env bash
# Serves the current project folder and opens the Build Companion board.
# Usage: bash dashboard.sh [port]   (run from your project root)
set -e
PORT="${1:-4321}"

if [ ! -f "build-status.json" ]; then
  echo "No build-status.json here yet — ask Claude to initialize the Build Companion in this project first."
  exit 1
fi

if [ ! -f "dashboard.html" ]; then
  # Copy the dashboard from the skill folder (this script's ../assets)
  SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  cp "$SKILL_DIR/assets/dashboard.html" ./dashboard.html
  echo "Copied the board into this project."
fi

URL="http://localhost:${PORT}/dashboard.html"
echo "Build Companion → ${URL}   (open it in a full browser window; Ctrl-C stops it)"

# Open the browser (best effort, cross-platform)
( sleep 1
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  elif command -v start >/dev/null 2>&1; then start "$URL"
  fi ) &

python3 -m http.server "$PORT"
