#!/bin/bash
set -e

echo "=== finwatch setup ==="

# Verificar Python
python3 --version || { echo "Python 3 no encontrado"; exit 1; }

# Instalar dependencias
echo "Instalando dependencias..."
python3 -m pip install -r requirements.txt

# Crear directorios necesarios
mkdir -p data/cache data/raw

# Copiar .env si no existe
if [ ! -f .env ]; then
    cp env.example .env
    echo ""
    echo "=== IMPORTANTE ==="
    echo "Se creó .env desde env.example."
    echo "Editá .env y completá tus API keys antes de correr la app:"
    echo "  - FINNHUB_API_KEY    → https://finnhub.io (gratis)"
    echo "  - MARKETAUX_API_KEY  → https://www.marketaux.com (gratis)"
    echo "  - ANTHROPIC_API_KEY  → https://console.anthropic.com"
    echo "  - OPENROUTER_API_KEY → https://openrouter.ai (gratis, sin tarjeta)"
    echo ""
else
    echo ".env ya existe, no se sobreescribe."
fi

echo ""
echo "=== Setup completo ==="
echo "Para correr la app:"
echo "  streamlit run frontend/app.py"
echo ""
echo "Para correr los tests:"
echo "  pytest tests/unit/ -v"
