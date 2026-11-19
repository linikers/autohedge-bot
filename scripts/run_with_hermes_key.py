#!/usr/bin/env python3
"""
AutoHedge Bot - Runner usando a chave API do Hermes Agent.
Lê OPENCODE_GO_API_KEY de /root/.hermes/.env e mapeia para OPENCODE_API_KEY.
Uso: python scripts/run_with_hermes_key.py [--task "minha tarefa"] [--notify]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Lê a chave do Hermes diretamente do filesystem
hermes_env = Path("/root/.hermes/.env")
if not hermes_env.exists():
    print("ERRO: /root/.hermes/.env não encontrado")
    sys.exit(1)

content = hermes_env.read_text()
match = re.search(r'^OPENCODE_GO_API_KEY=(.+)', content, re.MULTILINE)
if not match:
    print("ERRO: OPENCODE_GO_API_KEY não encontrada em /root/.hermes/.env")
    sys.exit(1)

api_key = match.group(1).strip().strip('"').strip("'")

# Seta as env vars (sem printar a chave)
os.environ["OPENCODE_API_KEY"] = api_key
os.environ["OPENCODE_BASE_URL"] = "https://opencode.ai/zen/go/v1"


# Carrega também .env local para JUPITER_API_KEY, etc.
local_env = Path(__file__).parent.parent / ".env"
if local_env.exists():
    with open(local_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # Não sobrescreve a chave API
                if k != "OPENCODE_API_KEY" and k not in os.environ:
                    os.environ[k] = v

# deepseek-v4-flash saiu do OpenCode Go em nov/2026: o modelo válido é v4.1.
# O valor do .env manda; isto é só o fallback.
os.environ.setdefault("AUTOHEDGE_MODEL", "deepseek-v4.1-flash")

# Args
parser = argparse.ArgumentParser(description="AutoHedge Bot (Hermes key bridge)")
parser.add_argument("--task", default=None, help="Tarefa para o Director Agent")
parser.add_argument("--notify", action="store_true", help="Saída JSON para notificação")
parser.add_argument("--output", default=None, help="Caminho do arquivo de saída")
args = parser.parse_args()

task = args.task or "Analyze the crypto market and provide a thesis on current trends and opportunities. Include analysis of Bitcoin, Ethereum, and Solana."

# Importa e executa
sys.path.insert(0, str(Path(__file__).parent.parent))
from autohedge.agents import run_full_cycle

print("=" * 60)
print("🚀 AutoHedge Bot - Iniciando ciclo de análise (Hermes key bridge)")
print("=" * 60)
print()

result = run_full_cycle(task)

# Salva output
if args.output:
    output_path = Path(args.output)
else:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(__file__).parent.parent / "outputs" / f"analise_{ts}.json"

output_path.parent.mkdir(exist_ok=True)
output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

print()
print("=" * 60)
print(f"✅ Análise concluída! Salva em: {output_path}")
print("=" * 60)

steps = result.get("steps", {})
print(f"\n📊 Resumo:")
for step_name in ["director", "quant", "risk", "execution"]:
    content = steps.get(step_name, "")
    print(f"  {step_name.capitalize():>10}: {len(content)} chars")

if args.notify:
    print("\n--- NOTIFY ---")
    print(json.dumps({
        "status": result.get("status"),
        "timestamp": result.get("timestamp"),
        "task": task,
        "director_preview": steps.get("director", "")[:300],
        "output_file": str(output_path),
    }))
