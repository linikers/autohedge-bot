"""
AutoHedge Bot - Fetcher de dados reais de mercado.
Busca preço, variação, volume e Fear & Greed Index antes de cada ciclo.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

WALLET = "GnVxCDFsqHx681WqKeKS97qiYGsB7M9Rf4C58WcgiLmQ"
SOL_MINT = "So11111111111111111111111111111111111111112"
BTC_MINT = "qzfc6mBpV8Z6BNeSwrC6f9Dy7KqGpB9Xj7bY3VkZ8Po"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

JUPITER_PRICE = "https://api.jup.ag/price/v3"
COINGECKO = "https://api.coingecko.com/api/v3"


def _get_prices() -> Dict[str, Any]:
    """Busca preços reais via Jupiter Price API V3."""
    try:
        resp = httpx.get(
            f"{JUPITER_PRICE}?ids={SOL_MINT},{USDC_MINT}",
            timeout=10,
        )
        data = resp.json()
        sol_price = float(data.get(SOL_MINT, {}).get("usdPrice", 0))
        return {"sol_usd": sol_price}
    except Exception as e:
        return {"sol_usd": 0, "error": str(e)}


def _get_sol_change() -> Dict[str, Any]:
    """Busca variação 24h do SOL via CoinGecko (gratuito)."""
    try:
        resp = httpx.get(
            f"{COINGECKO}/coins/solana",
            params={
                "localization": "false",
                "tickers": "false",
                "community_data": "false",
                "developer_data": "false",
            },
            timeout=10,
        )
        data = resp.json()
        md = data.get("market_data", {})
        return {
            "sol_change_24h": round(md.get("price_change_percentage_24h", 0), 2),
            "sol_change_7d": round(md.get("price_change_percentage_7d", 0), 2),
            "sol_change_30d": round(md.get("price_change_percentage_30d", 0), 2),
            "sol_high_24h": md.get("high_24h", {}).get("usd", 0),
            "sol_low_24h": md.get("low_24h", {}).get("usd", 0),
            "sol_market_cap": md.get("market_cap", {}).get("usd", 0),
            "sol_volume_24h": md.get("total_volume", {}).get("usd", 0),
        }
    except Exception:
        return {}


def _get_fear_greed() -> Dict[str, Any]:
    """Busca Fear & Greed Index (alternative.me, gratuito)."""
    try:
        resp = httpx.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10,
        )
        data = resp.json().get("data", [{}])[0]
        return {
            "fear_greed_value": int(data.get("value", 50)),
            "fear_greed_label": data.get("value_classification", "Neutral"),
        }
    except Exception:
        return {}


def _get_wallet_balance() -> Dict[str, Any]:
    """Busca saldo real da wallet via RPC + Jupiter."""
    result = {"sol": 0, "usdc": 0, "total_usd": 0}

    try:
        resp = httpx.post(
            "https://api.mainnet-beta.solana.com",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [WALLET],
            },
            timeout=10,
        )
        lamports = resp.json().get("result", {}).get("value", 0)
        result["sol"] = round(lamports / 1e9, 6)
    except Exception:
        pass

    try:
        resp = httpx.get(
            f"https://api.jup.ag/ultra/v1/holdings/{WALLET}",
            timeout=10,
        )
        data = resp.json()
        for token in data.get("tokens", {}).get(USDC_MINT, []):
            result["usdc"] = round(token.get("uiAmount", 0), 4)
    except Exception:
        pass

    return result


def _get_trade_history() -> list:
    """Lê histórico de trades do portfolio/index.json."""
    index_path = os.path.join(os.path.dirname(__file__), "..", "portfolio", "index.json")
    try:
        with open(index_path) as f:
            idx = json.load(f)
        trades = idx.get("trades", [])
        # Retorna últimos 5 trades resumidos
        return [
            {
                "date": t.get("timestamp", "?")[:16],
                "type": t.get("type", "?"),
                "amount": t.get("amount_usd", 0),
                "out": t.get("out_usdc", 0),
                "status": t.get("status", "?"),
            }
            for t in trades[-5:]
        ]
    except Exception:
        return []


def fetch_all_market_data() -> str:
    """
    Busca todos os dados reais e retorna formatado pro prompt do Director.
    Esta string é injetada no task do pipeline.
    """
    prices = _get_prices()
    changes = _get_sol_change()
    fg = _get_fear_greed()
    wallet = _get_wallet_balance()
    trades = _get_trade_history()

    sol_price = prices.get("sol_usd", 0)

    lines = []
    lines.append("=" * 50)
    lines.append("DADOS REAIS DE MERCADO (fetchado agora)")
    lines.append("=" * 50)
    lines.append("")

    # Preços reais
    lines.append("## Preços Atuais")
    lines.append(f"- SOL: ${sol_price:.2f}")
    if changes.get("sol_high_24h"):
        lines.append(f"- SOL 24h High: ${changes['sol_high_24h']:.2f}")
    if changes.get("sol_low_24h"):
        lines.append(f"- SOL 24h Low: ${changes['sol_low_24h']:.2f}")
    lines.append("")

    # Variações
    lines.append("## Variações")
    if changes.get("sol_change_24h") is not None:
        lines.append(f"- SOL 24h: {changes['sol_change_24h']:+.2f}%")
    if changes.get("sol_change_7d") is not None:
        lines.append(f"- SOL 7d: {changes['sol_change_7d']:+.2f}%")
    if changes.get("sol_change_30d") is not None:
        lines.append(f"- SOL 30d: {changes['sol_change_30d']:+.2f}%")
    lines.append("")

    # Volume e market cap
    if changes.get("sol_volume_24h"):
        vol = changes["sol_volume_24h"]
        lines.append(f"- Volume 24h: ${vol:,.0f}")
    if changes.get("sol_market_cap"):
        mc = changes["sol_market_cap"]
        lines.append(f"- Market Cap: ${mc:,.0f}")
    lines.append("")

    # Fear & Greed
    if fg.get("fear_greed_value"):
        val = fg["fear_greed_value"]
        label = fg["fear_greed_label"]
        lines.append(f"## Sentimento do Mercado")
        lines.append(f"- Fear & Greed Index: {val}/100 ({label})")
        lines.append("")

    # Wallet
    lines.append("## Estado da Carteira")
    lines.append(f"- SOL: {wallet['sol']:.6f} (~${wallet['sol'] * sol_price:.2f})")
    lines.append(f"- USDC: ${wallet['usdc']:.4f}")
    total = wallet['sol'] * sol_price + wallet['usdc']
    lines.append(f"- Total: ~${total:.2f}")
    lines.append("")

    # Histórico de trades
    if trades:
        lines.append("## Histórico de Trades (últimos 5)")
        for t in trades:
            lines.append(
                f"- {t['date']} | {t['type']} | ${t['amount']} → ${t['out']:.4f} USDC | {t['status']}"
            )
        lines.append("")

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"## Hora atual: {now}")
    lines.append("")
    lines.append("IMPORTANTE: Use os dados ACIMA (preços reais, variações, volume)")
    lines.append("na sua análise. NÃO invente preços — os dados acima são reais.")
    lines.append("Se precisar de dados adicionais, mencione o que falta.")

    return "\n".join(lines)


if __name__ == "__main__":
    print(fetch_all_market_data())
