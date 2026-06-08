# AutoHedge Bot 🤖📈

Bot de análise de mercado autônomo usando agentes de IA (DeepSeek via OpenCode).

Adaptado do [AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge) original para rodar com DeepSeek em vez de GPT-4.

## Arquitetura

```
Director Agent → Quant Agent → Risk Manager → Execution Agent
     │               │              │               │
     └── tese de    └── análise    └── avalia     └── recomendação
         mercado       quantitativa    risco           de ordem
```

## Setup

```bash
# 1. Clone
git clone https://github.com/linikers/autohedge-bot.git
cd autohedge-bot

# 2. Ambiente virtual
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure a API key
cp .env.example .env
# Edite .env com sua OPENCODE_API_KEY

# 4. Rode
python run.py
```

## Análise Periódica

O bot pode rodar automaticamente via cron job a cada N horas:

```bash
# Editar crontab
crontab -e

# Adicionar: roda a cada 4h
0 */4 * * * cd /caminho/para/autohedge-bot && .venv/bin/python run.py --notify >> logs/cron.log 2>&1
```

Ou usar o Hermes Cron (recomendado):

```bash
hermes cron create \
  --name "autohedge-analysis" \
  --schedule "0 */4 * * *" \
  --prompt "Run: cd /path && .venv/bin/python run.py --notify" \
  --deliver origin
```

## Comandos

```bash
# Análise completa (mercado crypto)
python run.py

# Com notificação
python run.py --notify

# Tarefa personalizada
python run.py --task "Analyze Solana ecosystem tokens..."

# Salvar em arquivo específico
python run.py --output ~/analise.json
```

## Resultados

Os outputs salvam em `outputs/analise_<data>.json` com:
- `task` - Tarefa solicitada
- `timestamp` - Data/hora
- `steps.director` - Tese de mercado do Director Agent
- `steps.quant` - Análise quantitativa
- `steps.risk` - Avaliação de risco
- `steps.execution` - Recomendação de trade

## Stack

- **Modelo:** DeepSeek via OpenCode API (compatível OpenAI)
- **Preços:** Jupiter API (Solana)
- **Busca:** Jupiter Tokens API
- **Swap:** Jupiter Ultra (quando conectado wallet)

Feito com ☕ por [linikers](https://github.com/linikers)
