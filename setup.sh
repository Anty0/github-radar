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
# Prefer whatever `gh` the host already has. If none is on PATH, fall back to a
# bundled copy in $SCRIPTS_DIR/bin downloaded for the host OS/arch. Idempotent:
# only re-downloads if the bundled copy is missing or broken.
GH_VERSION="2.62.0"
GH_BIN_DIR="$SCRIPTS_DIR/bin"
GH_BIN="$GH_BIN_DIR/gh"
mkdir -p "$GH_BIN_DIR"

# 1) System-installed gh? Use it as-is — no PATH override needed.
system_gh=""
if command -v gh >/dev/null 2>&1 && gh --version >/dev/null 2>&1; then
  system_gh="$(command -v gh)"
fi

# 2) Otherwise, see if a previous run already bootstrapped a working copy.
needs_install=1
if [ -n "$system_gh" ]; then
  needs_install=0
elif [ -x "$GH_BIN" ] && "$GH_BIN" --version >/dev/null 2>&1; then
  needs_install=0
fi

if [ "$needs_install" = "1" ]; then
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$arch" in
    aarch64|arm64) gh_arch="arm64" ;;
    x86_64|amd64)  gh_arch="amd64" ;;
    *) echo "WARN: unknown arch '$arch'; skipping gh bootstrap" >&2; gh_arch="" ;;
  esac
  case "$os" in
    linux)  gh_os="linux";  gh_ext="tar.gz" ;;
    darwin) gh_os="macOS";  gh_ext="zip" ;;
    *) echo "WARN: unsupported OS '$os'; skipping gh bootstrap (install gh manually)" >&2; gh_os="" ;;
  esac

  if [ -n "$gh_arch" ] && [ -n "$gh_os" ]; then
    archive="$GH_BIN_DIR/gh-archive"
    url="https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_${gh_os}_${gh_arch}.${gh_ext}"
    extracted_dir="$GH_BIN_DIR/gh_${GH_VERSION}_${gh_os}_${gh_arch}"
    echo "Downloading gh ${GH_VERSION} (${gh_os}/${gh_arch})..." >&2
    if curl -sSL --fail -o "$archive" "$url"; then
      case "$gh_ext" in
        tar.gz) tar -xzf "$archive" -C "$GH_BIN_DIR" ;;
        zip)
          if ! command -v unzip >/dev/null 2>&1; then
            echo "WARN: 'unzip' not found; cannot extract macOS gh release" >&2
            gh_os=""
          else
            unzip -q "$archive" -d "$GH_BIN_DIR"
          fi
          ;;
      esac
      if [ -n "$gh_os" ] && [ -x "$extracted_dir/bin/gh" ]; then
        mv "$extracted_dir/bin/gh" "$GH_BIN"
        rm -rf "$extracted_dir" "$archive"
        chmod +x "$GH_BIN"
      fi
    else
      echo "WARN: gh download failed; install gh manually (https://cli.github.com/) or check network" >&2
    fi
  fi
fi

if [ -n "$system_gh" ]; then
  gh_status="using system gh ($("$system_gh" --version | head -n1))"
elif [ -x "$GH_BIN" ]; then
  gh_status="installed ($("$GH_BIN" --version | head -n1))"
else
  gh_status="NOT installed (install gh manually: https://cli.github.com/)"
fi
# -----------------------------------------------------------------------------

# Write the env file with restrictive perms so the token isn't world-readable.
# gh respects GH_TOKEN automatically, so no extra config is needed.
# Only prepend the bundled bin/ to PATH when we're actually using a bundled gh.
umask 077
if [ -n "$system_gh" ]; then
  path_export="export PATH=\"\$PATH\""
else
  path_export="export PATH=\"$GH_BIN_DIR:\$PATH\""
fi
cat > "$SCRIPTS_DIR/.load-env.sh" <<EOF
export SCRIPTS_DIR='$SCRIPTS_DIR'
export WORK_DIR='$WORK_DIR'
export GH_TOKEN='$GH_TOKEN'
$path_export
EOF

# Confirmation (token is intentionally not printed).
echo "SCRIPTS_DIR=$SCRIPTS_DIR"
echo "WORK_DIR=$WORK_DIR"
echo "GH_TOKEN: set (length ${#GH_TOKEN})"
echo "gh: $gh_status"
