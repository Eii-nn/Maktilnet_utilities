#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[WSL] Working directory: $SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python3 was not found in WSL."
  echo "Install it with: sudo apt update && sudo apt install -y python3 python3-venv"
  exit 1
fi

if ! command -v mactelnet >/dev/null 2>&1 || ! command -v sshpass >/dev/null 2>&1; then
  echo "[WSL] Installing required Linux packages..."
  sudo apt-get update
  sudo apt-get install -y mactelnet-client sshpass
fi

if [ -n "${VIRTUAL_ENV:-}" ]; then
  echo "[WSL] Using active virtual environment: $VIRTUAL_ENV"
elif [ -d .venv ]; then
  echo "[WSL] Activating .venv"
  source .venv/bin/activate
elif [ -d mikrotik-venv ]; then
  echo "[WSL] Activating mikrotik-venv"
  source mikrotik-venv/bin/activate
else
  echo "[WSL] Creating virtual environment..."
  python3 -m venv .venv
  source .venv/bin/activate
fi

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "[WSL] Starting firmware downgrade tool..."
exec python preflight_integrated2.py
