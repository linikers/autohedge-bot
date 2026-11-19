#!/bin/bash
# Paper tracker do AutoHedge — registra decisões, liquida 1h/4h/24h e atualiza o dashboard.
# Chamado pelo cron do Hermes (no_agent): roda com o python do venv, não o do sistema.
cd /root/autohedge-bot || exit 1
exec .venv/bin/python3 -m autohedge.paper_tracker run
