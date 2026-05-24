#!/usr/bin/env bash
# Daily GitHub TODO — per-run setup.
#
# Resolves SCRIPTS_DIR / WORK_DIR / GH_TOKEN and writes them to
# $SCRIPTS_DIR/.load-env.sh, which every subsequent shell sources via:
#
#     source "$SCRIPTS_DIR/.load-env.sh"
#
# WORK_DIR is $SCRIPTS_DIR/outputs/YYYY-MM-DD_NN, with NN starting at 01 and
# auto-incrementing so an existing folder is never overwritten.
#
# Run this once at the start of each session. Re-running creates a fresh
# WORK_DIR (NN+1) — useful if a run was aborted mid-way.

set -euo pipefail

# Self-locating: SCRIPTS_DIR is wherever this script lives.
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$SCRIPTS_DIR/outputs"

today="$(date -u +%Y-%m-%d)"
i=1
while [ -e "$SCRIPTS_DIR/outputs/${today}_$(printf '%02d' "$i")" ]; do
  i=$((i+1))
done
WORK_DIR="$SCRIPTS_DIR/outputs/${today}_$(printf '%02d' "$i")"
mkdir -p "$WORK_DIR"

GH_TOKEN="$(tr -d '[:space:]' < "$SCRIPTS_DIR/.token")"
if [ -z "$GH_TOKEN" ]; then
  echo "ERROR: $SCRIPTS_DIR/.token is empty or missing" >&2
  exit 1
fi

# Write the env file with restrictive perms so the token isn't world-readable.
umask 077
cat > "$SCRIPTS_DIR/.load-env.sh" <<EOF
export SCRIPTS_DIR='$SCRIPTS_DIR'
export WORK_DIR='$WORK_DIR'
export GH_TOKEN='$GH_TOKEN'
EOF

# Confirmation (token is intentionally not printed).
echo "SCRIPTS_DIR=$SCRIPTS_DIR"
echo "WORK_DIR=$WORK_DIR"
echo "GH_TOKEN: set (length ${#GH_TOKEN})"
