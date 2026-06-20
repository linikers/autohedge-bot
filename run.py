#!/usr/bin/env python3
"""
AutoHedge Bot - Runner com execução real.
Uso: python run.py [--trade] [--max-usd 0.50] [--notify]
"""
import argparse
import json
import os
import sys
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from autohedge.agents import run_full_cycle


def extract_signal(execution_output: str, risk_output: str) -> dict | None:
    """
    Analisa a saída dos agentes e decide se deve executar trade.
    Retorna dict com ação ou None se não for o momento.
    """
    text = execution_output.lower() + " " + risk_output.lower()

    # Verifica se o Risk Agent aprovou
    risk_approved = any(w in text for w in ["approved", "favorável", "favoravel", "positivo", "positive", "executar", "go ahead", "proceed with"])
    if not risk_approved:
        return None

    # Detecta direção
    is_bullish = any(w in text for w in ["buy", "long", "comprar", "accumulate", "entrada", "alta", "bullish", "optimistic", "otimista"])
    is_bearish = any(w in text for w in ["sell", "short", "vender", "saída", "baixa", "bearish", "pessimistic", "cautela"])

    if not is_bullish and not is_bearish:
        return None  # Sinal neutro, não executa

    if is_bullish:
        return {"action": "buy", "reason": "Sinal de alta detectado"}
    else:
        return {"action": "sell", "reason": "Sinal de baixa detectado"}


def main():
    parser = argparse.ArgumentParser(description="AutoHedge Bot - Ciclo completo")
    parser.add_argument("--task", default="Analyze the crypto market and provide a thesis on current trends and opportunities. Include analysis of Bitcoin, Ethereum, and Solana.")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--trade", action="store_true", help="Executa swap real após análise")
    parser.add_argument("--max-usd", type=float, default=None, help="Max USD por trade (default: MAX_TRADE_USD do .env ou 0.50)")
    args = parser.parse_args()

    api_key = os.getenv("OPENCODE_API_KEY", "")
    if not api_key or api_key == "sk-Qw...SuaChaveAqui...ZJW":
        print("ERRO: OPENCODE_API_KEY não configurada!")
        sys.exit(1)

    max_trade = args.max_usd or float(os.getenv("MAX_TRADE_USD", "0.50"))

    print("=" * 60)
    print("🚀 AutoHedge Bot - Ciclo completo")
    print(f"   Trade: {'SIM (max $' + str(max_trade) + ')' if args.trade else 'NÃO (análise apenas)'}")
    print("=" * 60)
    print()

    # 1. Roda análise
    result = run_full_cycle(args.task)
    steps = result.get("steps", {})

    # 2. Salva output
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(__file__).parent / "outputs" / f"analise_{ts}.json"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n📄 Análise salva: {output_path}")

    # 3. Executa trade se solicitado
    trade_result = None
    if args.trade:
        print("\n" + "=" * 60)
        print("💰 ANALISANDO SINAL PARA EXECUÇÃO")
        print("=" * 60)
        signal = extract_signal(
            steps.get("execution", ""),
            steps.get("risk", "")
        )
        if signal:
            print(f"   ✅ Sinal: {signal['reason']}")
            try:
                from solders.keypair import Keypair
                from autohedge.swap_ultra import swap_sol_to_usdc, swap_usdc_to_sol
                privkey = os.getenv("SOLANA_PRIVATE_KEY", "").strip()
                if not privkey:
                    print("   ❌ SOLANA_PRIVATE_KEY não configurada no .env")
                else:
                    kp = Keypair.from_base58_string(privkey)
                    direction = signal.get("action", "buy")
                    if direction == "sell":
                        print(f"   💱 Executando swap SOL → USDC (${max_trade})...")
                        trade_result = swap_sol_to_usdc(amount_usd=max_trade, keypair=kp)
                        trade_result["direction"] = "sell"
                        trade_result["type"] = "directional"
                    else:
                        print(f"   💱 Executando swap USDC → SOL (${max_trade})...")
                        trade_result = swap_usdc_to_sol(amount_usd=max_trade, keypair=kp)
                        trade_result["direction"] = "buy"
                        trade_result["type"] = "directional"
                    print(f"   Resultado: {trade_result.get('status', 'unknown')}")
                    if trade_result.get("signature"):
                        print(f"   TX: https://solscan.io/tx/{trade_result['signature']}")
            except Exception as e:
                trade_result = {"status": "error", "reason": str(e)}
                print(f"   ❌ Erro: {e}")
        else:
            print("   ⏸️  Sinal neutro ou desfavorável — sem trade esse ciclo")
            trade_result = {"status": "skipped", "reason": "Sinal neutro ou desfavorável"}

    print()
    print("=" * 60)
    print("✅ Ciclo completo!")
    print("=" * 60)
    print(f"\n📊 Resumo:")
    print(f"  Director:  {len(steps.get('director', ''))} chars")
    print(f"  Quant:     {len(steps.get('quant', ''))} chars")
    print(f"  Risk:      {len(steps.get('risk', ''))} chars")
    print(f"  Execution: {len(steps.get('execution', ''))} chars")
    if trade_result:
        print(f"  Trade:     {trade_result.get('status', 'N/A')}")
        if trade_result.get("signature"):
            print(f"  TX:        {trade_result['signature'][:20]}...")

    # Atualiza portfolio index
    try:
        from autohedge.portfolio_index import update_index
        update_index(trade_result, str(output_path))
    except Exception as e:
        print(f"  ⚠️  Portfolio index: {e}")
    
    # Atualiza previsão no dashboard
    try:
        from autohedge.prediction_updater import update_predictions
        update_predictions()
    except Exception as e:
        print(f"  ⚠️  Prediction: {e}")

    if args.notify:
        print("\n--- NOTIFY ---")
        print(json.dumps({
            "status": result.get("status"),
            "timestamp": result.get("timestamp"),
            "trade": trade_result,
            "output_file": str(output_path),
        }))


if __name__ == "__main__":
    main()
