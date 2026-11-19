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
    Versão híbrida (25/06/2026):
    1. Risk Agent aprovou? (condição necessária)
    2. Extrair "Action:" do Execution Agent (sinal limpo, 8/10 análises)
    3. Fallback: keyword matching (método original)
    """
    exec_lower = execution_output.lower()
    risk_lower = risk_output.lower()
    full_text = exec_lower + " " + risk_lower

    # 1. CHECAGEM: Risk Agent aprovou?
    #    (09/08/2026: "não executar" contém "executar" — mesma armadilha do
    #    "do not proceed". Negação de "executar" não conta como aprovação.)
    risk_approved = any(w in full_text for w in [
        "approved", "favorável", "favoravel", "positivo", "positive",
        "executar", "go ahead", "proceed with"
    ])
    if "não executar" in full_text or "nao executar" in full_text or "sem executar" in full_text:
        risk_approved = False
    if not risk_approved:
        return None

    # 2. SINAL LIMPO: Extrair label de ação do Execution Agent.
    #    Suporta: "Action:", "AÇÃO:", "**Action:**", e célula de tabela markdown
    #    "| **AÇÃO** | **HOLD** |" (10/08/2026: Execution usou rótulo PT "AÇÃO"
    #    em tabela — o regex antigo action\s*: não casava e caía no fallback de
    #    keywords, gerando SELL falso contra HOLD explícito + Risk REJECTED).
    _ACTION_RE = re.compile(
        r'(?:action|a[çc]ã?o)\s*:\s*\*{0,2}\s*(buy|sell|hold|compra|venda)'
        r'|\|\s*\*{0,2}(?:a[çc]ã?o|action)\*{0,2}\s*\|\s*\*{0,2}(buy|sell|hold|compra|venda)',
        re.IGNORECASE,
    )
    action_match = _ACTION_RE.search(execution_output)
    if action_match:
        action = (action_match.group(1) or action_match.group(2)).lower()
        # mapeia valores PT → en
        if action in ("compra",):
            action = "buy"
        elif action in ("venda",):
            action = "sell"
        # normaliza: se cair um valor não-hold inesperado, trata como hold seguro
        if action not in ("buy", "sell"):
            return None  # HOLD = não trade
        return {
            "action": action,
            "reason": f"Execution Agent: Action: {action.upper()}"
        }

    # 2b. GUARD defensivo de HOLD: se o Execution explicitamente indicar HOLD /
    #     "sem trade" mas de forma não capturada pelo regex acima, NÃO deixa o
    #     fallback de keywords virar um trade falso (bug real 10/08/2026).
    if re.search(r'\bhold\b', exec_lower) or "sem trade" in exec_lower or "não executar" in exec_lower:
        return None

    # 3. FALLBACK: Keyword matching (método original)
    #    (09/08/2026: word boundaries — "long" casava com "longo prazo" no risk output)
    def _word_boundary(word: str) -> bool:
        return re.search(r'\b' + re.escape(word) + r'\b', full_text) is not None

    is_bullish = any(_word_boundary(w) for w in [
        "buy", "long", "comprar", "accumulate", "entrada",
        "bullish", "optimistic", "otimista"
    ])
    is_bearish = any(_word_boundary(w) for w in [
        "sell", "short", "vender", "saída", "baixa",
        "bearish", "pessimistic", "cautela"
    ])

    if is_bullish and not is_bearish:
        return {"action": "buy", "reason": "Fallback: sinal de alta detectado"}
    elif is_bearish and not is_bullish:
        return {"action": "sell", "reason": "Fallback: sinal de baixa detectado"}
    elif is_bullish and is_bearish:
        # Ambos detectados — usar Risk Agent como desempate
        risk_bullish = any(_word_boundary(w) for w in ["bullish", "buy", "comprar", "accumulate", "entrada"])
        risk_bearish = any(_word_boundary(w) for w in ["bearish", "sell", "vender", "saída", "baixa"])
        if risk_bearish and not risk_bullish:
            return {"action": "sell", "reason": "Fallback: Risk Agent indica baixa"}
        elif risk_bullish and not risk_bearish:
            return {"action": "buy", "reason": "Fallback: Risk Agent indica alta"}
        return None  # Ambíguos, não trade

    return None  # Sinal neutro


def get_wallet_balances() -> dict:
    """Busca saldos reais da wallet (SOL + USDC) via RPC."""
    import httpx
    wallet = "GnVxCDFsqHx681WqKeKS97qiYGsB7M9Rf4C58WcgiLmQ"
    rpc = os.getenv("HELIUS_RPC_URL", "https://api.mainnet-beta.solana.com")
    result = {"sol": 0.0, "usdc": 0.0, "sol_price": 0.0}

    try:
        # SOL balance
        resp = httpx.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet]}, timeout=10)
        result["sol"] = resp.json()["result"]["value"] / 1e9

        # USDC balance
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        resp2 = httpx.post(rpc, json={"jsonrpc": "2.0", "id": 2, "method": "getTokenAccountsByOwner",
            "params": [wallet, {"mint": usdc_mint}, {"encoding": "jsonParsed"}]}, timeout=10)
        for acc in resp2.json().get("result", {}).get("value", []):
            info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            result["usdc"] += float(info.get("tokenAmount", {}).get("uiAmount", 0))

        # SOL price
        price_resp = httpx.get("https://api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112", timeout=10)
        result["sol_price"] = price_resp.json()["So11111111111111111111111111111111111111112"]["usdPrice"]
    except Exception as e:
        print(f"   ⚠️  Erro ao buscar saldos: {e}")

    return result


def calc_safe_max_usd(direction: str, configured_max: float) -> float:
    """Calcula max-usd seguro baseado no saldo real da wallet."""
    balances = get_wallet_balances()
    sol_price = balances["sol_price"] or 72.0  # fallback

    if direction == "buy":
        # Comprar SOL com USDC — limitado pelo saldo USDC
        available = balances["usdc"]
        safe = max(0, available - 0.02)  # buffer pra gas
        print(f"   📊 Saldo USDC: ${available:.2f} | Max config: ${configured_max}")
    else:
        # Vender SOL por USDC — limitado pelo saldo SOL
        sol_usd = balances["sol"] * sol_price
        safe = max(0, sol_usd - 0.02)  # buffer pra rent-exempt
        print(f"   📊 Saldo SOL: {balances['sol']:.6f} (${sol_usd:.2f}) | Max config: ${configured_max}")

    final = min(configured_max, safe)
    if final < configured_max:
        print(f"   ⚠️  Max ajustado: ${configured_max} → ${final:.2f} (saldo insuficiente)")
    else:
        print(f"   ✅ Max: ${final:.2f}")

    return round(max(final, 0), 2)


def main():
    parser = argparse.ArgumentParser(description="AutoHedge Bot - Ciclo completo")
    parser.add_argument("--task", default="Analyze the crypto market and provide a thesis on current trends and opportunities. Include analysis of Bitcoin, Ethereum and Solana.")
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
                    # Calcula max-usd seguro baseado no saldo real
                    safe_max = calc_safe_max_usd(direction, max_trade)
                    if safe_max <= 0:
                        print(f"   ❌ Saldo insuficiente pra trade ({direction})")
                        trade_result = {"status": "error", "reason": "Saldo insuficiente"}
                    elif direction == "sell":
                        print(f"   💱 Executando swap SOL → USDC (${safe_max})...")
                        trade_result = swap_sol_to_usdc(amount_usd=safe_max, keypair=kp)
                        trade_result["direction"] = "sell"
                        trade_result["type"] = "directional"
                    else:
                        print(f"   💱 Executando swap USDC → SOL (${safe_max})...")
                        trade_result = swap_usdc_to_sol(amount_usd=safe_max, keypair=kp)
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
