#!/bin/bash
# setup.sh - Configura o AutoHedge Bot
# Uso: source setup.sh

echo "🚀 AutoHedge Bot Setup"
echo "======================"

# Verifica Python
python3 --version || { echo "❌ Python 3 necessário"; exit 1; }

# Cria venv
if [ ! -d ".venv" ]; then
    echo "📦 Criando ambiente virtual..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Instala dependências
echo "📥 Instalando dependências..."
pip install -q -r requirements.txt

# Verifica .env
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  .env não encontrado!"
    echo "   Copie .env.example para .env:"
    echo "   cp .env.example .env"
    echo "   E edite com sua OPENCODE_API_KEY"
    echo ""
else
    echo "✅ .env encontrado"
fi

# Verifica API key
API_KEY=$(grep OPENCODE_API_KEY .env 2>/dev/null | cut -d'=' -f2 | tr -d '"')
if [ -n "$API_KEY" ] && [ "$API_KEY" != "sk-Qww..." ]; then
    echo "✅ OPENCODE_API_KEY configurada"
else
    echo "⚠️  OPENCODE_API_KEY não configurada"
fi

echo ""
echo "✅ Setup concluído!"
echo ""
echo "▶️  Para rodar: source .venv/bin/activate && python run.py"
echo ""

# Teste rápido
python -c "from openai import OpenAI; print('✅ OpenAI client OK')" 2>/dev/null
