#!/usr/bin/env bash
# GitHub Radar — per-run setup.
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

# --- gh CLI bootstrap ---------------------------------------------------------
# The sandbox doesn't ship gh, so we keep a copy in $SCRIPTS_DIR/bin and add it
# to PATH via .load-env.sh. Idempotent: only downloads if missing or broken.
GH_VERSION="2.62.0"
GH_BIN_DIR="$SCRIPTS_DIR/bin"
GH_BIN="$GH_BIN_DIR/gh"
mkdir -p "$GH_BIN_DIR"

needs_install=1
if [ -x "$GH_BIN" ] && "$GH_BIN" --version >/dev/null 2>&1; then
  needs_install=0
fi

if [ "$needs_install" = "1" ]; then
  arch="$(uname -m)"
  case "$arch" in
    aarch64|arm64) gh_arch="arm64" ;;
    x86_64|amd64)  gh_arch="amd64" ;;
    *) echo "WARN: unknown arch '$arch'; skipping gh bootstrap" >&2; gh_arch="" ;;
  esac

  if [ -n "$gh_arch" ]; then
    tarball="$GH_BIN_DIR/gh.tgz"
    url="https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${gh_arch}.tar.gz"
    echo "Downloading gh ${GH_VERSION} (${gh_arch})..." >&2
    if curl -sSL --fail -o "$tarball" "$url"; then
      tar -xzf "$tarball" -C "$GH_BIN_DIR"
      mv "$GH_BIN_DIR/gh_${GH_VERSION}_linux_${gh_arch}/bin/gh" "$GH_BIN"
      rm -rf "$GH_BIN_DIR/gh_${GH_VERSION}_linux_${gh_arch}" "$tarball"
      chmod +x "$GH_BIN"
    else
      echo "WARN: gh download failed; agents will need to fall back to curl+python" >&2
    fi
  fi
fi

if [ -x "$GH_BIN" ]; then
  gh_status="installed ($("$GH_BIN" --version | head -n1))"
else
  gh_status="NOT installed (stop and ask user how to proceed)"
fi
# -----------------------------------------------------------------------------

# Write the env file with restrictive perms so the token isn't world-readable.
# gh respects GH_TOKEN automatically, so no extra config is needed.
umask 077
cat > "$SCRIPTS_DIR/.load-env.sh" <<EOF
export SCRIPTS_DIR='$SCRIPTS_DIR'
export WORK_DIR='$WORK_DIR'
export GH_TOKEN='$GH_TOKEN'
export PATH="$GH_BIN_DIR:\$PATH"
EOF

# Confirmation (token is intentionally not printed).
echo "SCRIPTS_DIR=$SCRIPTS_DIR"
echo "WORK_DIR=$WORK_DIR"
echo "GH_TOKEN: set (length ${#GH_TOKEN})"
echo "gh: $gh_status"
