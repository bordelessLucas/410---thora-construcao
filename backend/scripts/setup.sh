#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "[backend] Instalando uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [[ ! -x "$ROOT_DIR/venv/bin/python" ]]; then
  echo "[backend] Criando ambiente virtual (Python 3.11)..."
  uv python install 3.11
  uv venv --python 3.11 venv
fi

echo "[backend] Instalando dependências Python..."
# uv 0.11+ não detecta automaticamente o diretório `venv` (só `.venv`)
uv pip install --python "$ROOT_DIR/venv/bin/python" -r requirements.txt

echo "[backend] Setup concluído."
