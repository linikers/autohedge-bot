#!/usr/bin/env python3
"""
AutoHedge Bot - Runner.
Executa um ciclo completo de análise e loga o resultado.
Uso: python run.py [--notify]
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Carrega .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from autohedge.agents import run_full_cycle


def main():
    parser = argparse.ArgumentParser(description="AutoHedge Bot - Ciclo de análise")
    parser.add_argument(
        "--task",
        default="Analyze the crypto market and provide a thesis on current trends and opportunities. Include analysis of Bitcoin, Ethereum, and Solana.",
        help="Tarefa para o Director Agent",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Envia resultado via stdout para integração com notificações",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Caminho do arquivo de saída (default: outputs/analise_<data>.json)",
    )
    args = parser.parse_args()

    # Verifica se tem API key
    api_key = os.getenv("OPENCODE_API_KEY", "")
    if not api_key or api_key.startswith("sk-Qww"):
        print("ERRO: OPENCODE_API_KEY não configurada!")
        print("Copie .env.example para .env e preencha a chave.")
        sys.exit(1)

    print("=" * 60)
    print("🚀 AutoHedge Bot - Iniciando ciclo de análise")
    print("=" * 60)
    print()

    result = run_full_cycle(args.task)

    # Salva output
    if args.output:
        output_path = Path(args.output)
    else:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(__file__).parent / "outputs" / f"analise_{ts}.json"

    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 60)
    print(f"✅ Análise concluída! Salva em: {output_path}")
    print("=" * 60)

    # Resumo rápido
    steps = result.get("steps", {})
    print(f"\n📊 Resumo:")
    print(f"  Director:  {len(steps.get('director', ''))} chars")
    print(f"  Quant:     {len(steps.get('quant', ''))} chars")
    print(f"  Risk:      {len(steps.get('risk', ''))} chars")
    print(f"  Execution: {len(steps.get('execution', ''))} chars")

    if args.notify:
        # Saída formatada pra webhook/cron
        print("\n--- NOTIFY ---")
        print(json.dumps({
            "status": result.get("status"),
            "timestamp": result.get("timestamp"),
            "director_preview": steps.get("director", "")[:300],
            "output_file": str(output_path),
        }))


if __name__ == "__main__":
    main()
