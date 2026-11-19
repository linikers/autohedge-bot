"""
AutoHedge Bot — guarda de ordem.

Duas barreiras, uma antes e uma depois da assinatura:

    guard_order()        simula a transação devolvida pela Jupiter ANTES de assinar.
    verify_settlement()  confere a transação confirmada DEPOIS de executar.

Motivo (incidente 18/08/2026): uma transação contendo apenas `create ATA` +
`transferChecked` mandou 10,37552 USDC da wallet para o ATA de um endereço sem SOL
e sem histórico. Nenhum swap aconteceu e o dinheiro não voltou — a carteira caiu de
US$ 12,40 para US$ 0,21.

Regra central: o que sai tem que voltar como o que entra. Se a simulação mostra a
moeda de entrada saindo e a de saída não entrando, a ordem é bloqueada antes da
assinatura. Nunca assinar uma tx que não fecha o balanço.
"""
from __future__ import annotations

import base64
import os
from typing import Any, Callable, Dict, Optional

import httpx

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

DEFAULT_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
MAX_LOSS_PCT = float(os.getenv("GUARD_MAX_LOSS_PCT", "0.03"))  # perda máxima aceita (slippage+fee)
HARD_CAP_USD = float(os.getenv("MAX_TRADE_USD", "2.00"))
DUST_USD = 0.01


class OrderBlocked(Exception):
    """Ordem recusada pela guarda. Nunca deve chegar a assinatura."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _rpc(rpc_url: str, method: str, params: list, timeout: int = 25) -> Any:
    resp = httpx.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{method}: {body['error']}")
    return body["result"]


# ---------------------------------------------------------------- helpers puros


def token_deltas(balance_list_pre, balance_list_post, wallet: str) -> Dict[str, float]:
    """Delta de saldo por mint para a wallet, a partir de pre/postTokenBalances."""
    pre: Dict[str, float] = {}
    post: Dict[str, float] = {}
    for b in balance_list_pre or []:
        if b.get("owner") == wallet:
            pre[b["mint"]] = float(b.get("uiTokenAmount", {}).get("uiAmount") or 0)
    for b in balance_list_post or []:
        if b.get("owner") == wallet:
            post[b["mint"]] = float(b.get("uiTokenAmount", {}).get("uiAmount") or 0)
    mints = set(pre) | set(post)
    return {m: round(post.get(m, 0.0) - pre.get(m, 0.0), 9) for m in mints}


def evaluate_fill(
    side: str,
    d_sol: Optional[float],
    d_usdc: Optional[float],
    sol_price: float,
    amount_usd: float,
    expected_out: float,
    extra_deltas: Optional[Dict[str, float]] = None,
    max_loss_pct: float = MAX_LOSS_PCT,
    slippage_bps: int = 100,
    hard_cap_usd: float = HARD_CAP_USD,
) -> Dict[str, Any]:
    """
    Avalia o preenchimento (real ou simulado) e diz se a ordem pode passar.

    d_sol: delta em SOL da wallet (negativo = saiu SOL)
    d_usdc: delta em USDC da wallet (negativo = saiu USDC)
    expected_out: quanto a cotação prometeu entregar (SOL no buy, USDC no sell)
    """
    if sol_price is None or sol_price <= 0:
        return {"ok": False, "code": "price_unavailable", "reason": "preço do SOL indisponível"}
    if d_sol is None or d_usdc is None:
        return {"ok": False, "code": "legs_unverified", "reason": "não foi possível medir as duas pernas da ordem"}

    slip = slippage_bps / 10_000.0
    tol = max(max_loss_pct, slip + 0.005) + DUST_USD

    if side == "buy":
        if d_usdc >= -DUST_USD:
            return {"ok": False, "code": "no_input_leg",
                    "reason": f"nada de USDC saiu (delta {d_usdc:+.6f}) — ordem não é a compra esperada"}
        if d_sol <= DUST_USD / max(sol_price, 1e-9):
            return {"ok": False, "code": "no_output_leg",
                    "reason": f"saiu USDC ({d_usdc:+.4f}) mas não entrou SOL ({d_sol:+.9f}) — "
                              f"padrão do incidente de 18/08"}
        spent = -d_usdc
        received = d_sol * sol_price
        if spent > amount_usd * 1.005:
            return {"ok": False, "code": "over_budget",
                    "reason": f"ordem gasta US$ {spent:.4f}, acima do teto de US$ {amount_usd:.2f}"}
        if expected_out > 0:
            floor = expected_out * sol_price * (1 - max(max_loss_pct, slip) - 0.005)
            if received < floor:
                return {"ok": False, "code": "below_quote",
                        "reason": f"recebido US$ {received:.4f} < piso de US$ {floor:.4f} da cotação"}
        if received < spent * (1 - tol):
            return {"ok": False, "code": "value_leak",
                    "reason": f"entrou US$ {received:.4f} para US$ {spent:.4f} que saíram "
                              f"({(1 - received / spent) * 100:.2f}% de perda)"}
        return {"ok": True, "spent_usd": spent, "received_usd": received,
                "loss_pct": (1 - received / spent) * 100, "d_sol": d_sol, "d_usdc": d_usdc}

    if side == "sell":
        if d_sol >= 0:
            return {"ok": False, "code": "no_input_leg",
                    "reason": f"nada de SOL saiu (delta {d_sol:+.9f}) — ordem não é a venda esperada"}
        if d_usdc <= DUST_USD:
            return {"ok": False, "code": "no_output_leg",
                    "reason": f"saiu SOL ({d_sol:+.9f}) mas não entrou USDC ({d_usdc:+.4f})"}
        spent = -d_sol * sol_price
        received = d_usdc
        if spent > amount_usd * 1.005:
            return {"ok": False, "code": "over_budget",
                    "reason": f"ordem vende US$ {spent:.4f}, acima do teto de US$ {amount_usd:.2f}"}
        if expected_out > 0:
            floor = expected_out * (1 - max(max_loss_pct, slip) - 0.005)
            if received < floor:
                return {"ok": False, "code": "below_quote",
                        "reason": f"recebido US$ {received:.4f} < piso de US$ {floor:.4f} da cotação"}
        if received < spent * (1 - tol):
            return {"ok": False, "code": "value_leak",
                    "reason": f"entrou US$ {received:.4f} para US$ {spent:.4f} que saíram "
                              f"({(1 - received / spent) * 100:.2f}% de perda)"}
        return {"ok": True, "spent_usd": spent, "received_usd": received,
                "loss_pct": (1 - received / spent) * 100, "d_sol": d_sol, "d_usdc": d_usdc}

    return {"ok": False, "code": "bad_side", "reason": f"lado inválido: {side}"}


def unexpected_outflows(extra_deltas: Optional[Dict[str, float]], input_mint: str,
                        tolerance_usd: float = 0.05) -> list:
    """Mints que saíram da wallet sem serem a moeda de entrada da ordem."""
    bad = []
    for mint, delta in (extra_deltas or {}).items():
        if mint == input_mint:
            continue
        if delta < -tolerance_usd:
            bad.append((mint, delta))
    return bad


# ---------------------------------------------------------------- barreira 1


def guard_order(
    wallet: str,
    tx_b64: str,
    side: str,
    amount_usd: float,
    expected_out: float,
    sol_price: float,
    slippage_bps: int = 100,
    rpc_url: str = DEFAULT_RPC,
    rpc_call: Optional[Callable] = None,
    max_loss_pct: float = MAX_LOSS_PCT,
    hard_cap_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Simula a transação antes da assinatura. Retorna {'ok': bool, ...}.

    Não levanta exceção: devolve code/reason para o chamador decidir.
    """
    hard_cap = HARD_CAP_USD if hard_cap_usd is None else hard_cap_usd
    if not isinstance(wallet, str) or not (32 <= len(wallet) <= 44):
        return {"ok": False, "code": "bad_wallet", "reason": "endereço de wallet inválido"}
    if amount_usd <= 0:
        return {"ok": False, "code": "bad_amount", "reason": "valor da ordem <= 0"}
    if amount_usd > hard_cap * 1.005:
        return {"ok": False, "code": "over_cap",
                "reason": f"US$ {amount_usd:.2f} acima do teto por ordem (US$ {hard_cap:.2f})"}

    call = rpc_call or (lambda method, params: _rpc(rpc_url, method, params))

    try:
        pre_sol = call("getBalance", [wallet])["value"] / 1e9
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "rpc_error", "reason": f"saldo indisponível: {exc}"}

    if pre_sol <= 0:
        return {"ok": False, "code": "empty_wallet", "reason": "wallet sem SOL para taxa"}

    try:
        sim = call("simulateTransaction", [
            tx_b64,
            {
                "encoding": "base64",
                "sigVerify": False,
                "replaceRecentBlockhash": True,
                "commitment": "confirmed",
                "accounts": {"encoding": "base64", "addresses": [wallet]},
            },
        ])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "simulation_error", "reason": f"simulação falhou: {exc}"}

    value = (sim or {}).get("value") or {}
    if value.get("err") is not None:
        return {"ok": False, "code": "simulation_failed",
                "reason": f"tx falharia on-chain: {value.get('err')}",
                "logs": (value.get("logs") or [])[-5:]}

    d_usdc = token_deltas(value.get("preTokenBalances"), value.get("postTokenBalances"), wallet).get(USDC_MINT, 0.0)

    d_sol = None
    accounts = value.get("accounts") or []
    if accounts and accounts[0] and accounts[0].get("lamports") is not None:
        d_sol = accounts[0]["lamports"] / 1e9 - pre_sol

    extra = token_deltas(value.get("preTokenBalances"), value.get("postTokenBalances"), wallet)
    input_mint = USDC_MINT if side == "buy" else SOL_MINT
    drains = unexpected_outflows(extra, input_mint)
    if drains:
        return {"ok": False, "code": "unexpected_outflow",
                "reason": f"outros tokens saindo da wallet na mesma tx: {drains}"}

    return evaluate_fill(side, d_sol, d_usdc, sol_price, amount_usd, expected_out,
                         max_loss_pct=max_loss_pct, slippage_bps=slippage_bps,
                         hard_cap_usd=hard_cap)


# ---------------------------------------------------------------- barreira 2


def verify_settlement(
    signature: str,
    wallet: str,
    side: str,
    amount_usd: float,
    expected_out: float,
    sol_price: float,
    slippage_bps: int = 100,
    rpc_url: str = DEFAULT_RPC,
    rpc_call: Optional[Callable] = None,
    max_loss_pct: float = MAX_LOSS_PCT,
) -> Dict[str, Any]:
    """
    Confere a transação já confirmada. Se o balanço não fechar, o chamador deve
    registrar status 'error' e NÃO gravar o trade no histórico.
    """
    call = rpc_call or (lambda method, params: _rpc(rpc_url, method, params))
    try:
        tx = call("getTransaction", [signature, {"encoding": "jsonParsed",
                                                 "maxSupportedTransactionVersion": 0,
                                                 "commitment": "confirmed"}])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "rpc_error", "reason": f"getTransaction falhou: {exc}"}
    if not tx:
        return {"ok": False, "code": "not_found", "reason": "transação não encontrada"}

    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return {"ok": False, "code": "tx_failed", "reason": f"tx falhou: {meta.get('err')}"}

    keys = [k.get("pubkey") for k in tx["transaction"]["message"].get("accountKeys", [])]
    if wallet not in keys:
        return {"ok": False, "code": "wallet_absent", "reason": "wallet não participa da tx"}
    idx = keys.index(wallet)

    pre_sol = meta["preBalances"][idx] / 1e9
    post_sol = meta["postBalances"][idx] / 1e9
    d_sol = post_sol - pre_sol
    d_usdc = token_deltas(meta.get("preTokenBalances"), meta.get("postTokenBalances"), wallet).get(USDC_MINT, 0.0)

    return evaluate_fill(side, d_sol, d_usdc, sol_price, amount_usd, expected_out,
                         max_loss_pct=max_loss_pct, slippage_bps=slippage_bps)


def assert_order_ok(result: Dict[str, Any]) -> None:
    """Atalho para quem preferir exceção a if."""
    if not result.get("ok"):
        raise OrderBlocked(result.get("code", "blocked"), result.get("reason", "ordem bloqueada"))
