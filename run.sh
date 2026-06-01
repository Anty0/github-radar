#!/usr/bin/env bash
# GitHub Radar — non-AI runner.
#
# Mirrors SKILL.md without the agent reasoning step. Produces the same
# report.html that the scheduled task would, but without "Critical" or
# "Top picks" sections (since those are picked by the agent). You can
# open the HTML directly in a browser or pipe it anywhere you like.
#
# Usage:
#   bash run.sh              # generate report and print the HTML path
#   bash run.sh --open       # also open the HTML in the default browser (macOS)
#
# Exit codes:
#   0 — success
#   1 — setup failed (missing .token, network, etc.)
#   2 — gh_report.py failed (see timings.txt in the run's WORK_DIR)
#   3 — render_report.py failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- STEP 0: setup -----------------------------------------------------------
bash "$SCRIPT_DIR/setup.sh" || {
  echo "ERROR: setup.sh failed — check that .token exists and contains a valid PAT" >&2
  exit 1
}

# shellcheck disable=SC1091
source "$SCRIPT_DIR/.load-env.sh"

echo "WORK_DIR: $WORK_DIR"

# --- STEP 1: fetch GitHub data ----------------------------------------------
echo "Fetching GitHub data..."
if ! python3 "$SCRIPTS_DIR/gh_report.py" \
       > "$WORK_DIR/report.json" 2> "$WORK_DIR/timings.txt"; then
  echo "ERROR: gh_report.py failed. stderr:" >&2
  cat "$WORK_DIR/timings.txt" >&2
  exit 2
fi
echo "--- timings ---"
cat "$WORK_DIR/timings.txt"

# --- STEP 2: write an empty picks.json --------------------------------------
# The agent normally writes this with its top picks / critical items. Without
# the agent we just feed empty pools so render_report.py emits the report
# without the "Critical" and "Top picks" sections.
cat > "$WORK_DIR/picks.json" <<'JSON'
{
  "critical": [],
  "top_picks": []
}
JSON

# --- STEP 3: render the HTML report -----------------------------------------
echo "Rendering report..."
if ! python3 "$SCRIPTS_DIR/render_report.py" \
       "$WORK_DIR/picks.json" \
       < "$WORK_DIR/report.json" \
       > "$WORK_DIR/report.html"; then
  echo "ERROR: render_report.py failed" >&2
  exit 3
fi

bytes=$(wc -c < "$WORK_DIR/report.html" | tr -d ' ')
echo "Wrote $bytes bytes to $WORK_DIR/report.html"

# --- STEP 4: check for repo updates -----------------------------------------
echo
echo "--- repo update check ---"
if git -C "$SCRIPT_DIR" remote | grep -q .; then
  git -C "$SCRIPT_DIR" fetch --all 2>&1 || echo "(fetch failed — network or auth issue)"
  git -C "$SCRIPT_DIR" status --short --branch || true
else
  echo "no remote configured, skipping"
fi

echo
echo "Report: $WORK_DIR/report.html"

# Optional convenience flag.
if [ "${1:-}" = "--open" ]; then
  if command -v open >/dev/null 2>&1; then
    open "$WORK_DIR/report.html"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$WORK_DIR/report.html"
  else
    echo "(no 'open' or 'xdg-open' available; open the path above manually)"
  fi
fi
