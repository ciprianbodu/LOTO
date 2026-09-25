#!/usr/bin/env bash
# Cloud Agent bootstrap for the Loto Enterprise app.
# CPU-only stack targeting the latest stable Python 3.14.x (see CLAUDE.md).
# Idempotent: safe to re-run on cached/partial state.
set -euo pipefail

# --- System build tools -------------------------------------------------------
# hmmlearn has no cp314 wheel yet and compiles from source; it needs a working
# C/C++ toolchain with libstdc++ headers.
if ! command -v g++ >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends build-essential
fi

# --- uv (Python toolchain + package manager) ----------------------------------
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# --- Python 3.14 --------------------------------------------------------------
uv python install 3.14

# --- Project virtualenv -------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  uv venv --python 3.14 .venv
fi

# --- Dependencies (CPU-only) --------------------------------------------------
# Force the system GCC: uv's standalone CPython ships a bundled clang that
# cannot find the system libstdc++ headers when building hmmlearn.
CC=gcc CXX=g++ uv pip install --python .venv/bin/python -r requirements_base.txt

echo "[install] Loto Enterprise environment ready (Python 3.14 + CPU stack)."
