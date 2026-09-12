#!/usr/bin/env bash
# Idempotent bootstrap for the LOTO app (NiceGUI UI + worker daemon), CPU-only.
#
# The app targets Python 3.14 (it uses PEP 750 t-strings, `string.templatelib`
# and `compression.zstd`, so it will not even parse on <3.14). We install a
# 3.14 toolchain via `uv`, then create a project venv and install the CPU-only
# dependency set from requirements_base.txt.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --------------------------------------------------------------------------- #
# 1) System C/C++ toolchain.
#    Several deps (hmmlearn, lightgbm, catboost, ...) have no cp314 wheels yet
#    and compile from source, so a working C++ toolchain is required.
# --------------------------------------------------------------------------- #
if ! dpkg -s build-essential >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq build-essential g++ gcc cmake pkg-config
fi

# On the default image the `c++`/`cc` alternatives point at clang, which here
# cannot locate the libstdc++ headers (fatal error: 'cstddef' file not found).
# Point them at GCC so C++ extension builds succeed.
if command -v update-alternatives >/dev/null 2>&1; then
  sudo update-alternatives --set c++ /usr/bin/g++ >/dev/null 2>&1 || true
  sudo update-alternatives --set cc  /usr/bin/gcc >/dev/null 2>&1 || true
fi

# --------------------------------------------------------------------------- #
# 2) uv (Python toolchain manager) + Python 3.14
# --------------------------------------------------------------------------- #
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv python install 3.14

# --------------------------------------------------------------------------- #
# 3) Project venv + CPU-only dependencies (idempotent)
# --------------------------------------------------------------------------- #
uv venv --python 3.14 --allow-existing .venv
VIRTUAL_ENV="$REPO_ROOT/.venv" uv pip install -r requirements_base.txt

echo "[install] ready: $(.venv/bin/python --version) at $REPO_ROOT/.venv"
