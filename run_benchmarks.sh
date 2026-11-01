#!/usr/bin/env bash
# Lanza la matriz del benchmark (D-006): rondas de 38 celdas, una corrida a la vez.
# Envoltura delgada: toda la lógica está en runner/orquestador.py (Python del .venv).
#
# Ejemplos (desde Git Bash, en la raíz del repositorio):
#   ./run_benchmarks.sh --dry-run                                   # orden y estado, sin medir
#   ./run_benchmarks.sh --rondas 3 --hora-limite "2026-11-01 20:00" --respaldo /d/respaldo_benchmark
#   ./run_benchmarks.sh --results-dir results/piloto --rondas 1 --celdas postgres:C:Q1:50
#
# Pausar entre corridas:   touch results/PAUSAR     (reanudar: rm results/PAUSAR)
# Detener entre corridas:  touch results/DETENER
# Retomar tras un corte:   volver a lanzar el mismo comando.
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"          # Windows (Git Bash)
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"                  # Linux / macOS
else
  echo "No existe .venv. Créalo con: python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt" >&2
  exit 1
fi

export PYTHONIOENCODING=utf-8
exec "$PY" runner/orquestador.py "$@"
