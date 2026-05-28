#!/bin/bash
# finwatch — arrancar con un solo comando: bash run.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Crear venv si no existe
if [ ! -d ".venv" ]; then
    echo "Creando entorno virtual..."
    python3 -m venv .venv
    echo "Instalando dependencias..."
    .venv/bin/pip install -r requirements.txt -q
fi

# Actualizar dependencias si requirements.txt cambió
if [ requirements.txt -nt .venv/updated ]; then
    echo "Actualizando dependencias..."
    .venv/bin/pip install -r requirements.txt -q
    touch .venv/updated
fi

# Crear directorios necesarios
mkdir -p data/cache data/raw

# Verificar que existe .env
if [ ! -f ".env" ]; then
    cp env.example .env
    echo ""
    echo "⚠️  Se creó .env desde env.example."
    echo "    Completá tus API keys en .env antes de continuar."
    echo ""
    exit 1
fi

echo "Iniciando finwatch..."
.venv/bin/streamlit run frontend/app.py --server.port=8501
