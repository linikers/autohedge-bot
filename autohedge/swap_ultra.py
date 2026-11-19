"""
AutoHedge Bot - Swap via Jupiter Ultra API (confirmed working).
"""
import base64
import json
import os
from typing import Any, Dict

import httpx
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from .guard import guard_order, verify_settlement

JUPITER_ULTRA = "https://api.jup.ag/ultra/v1"
JUPITER_PRICE = "https://api.jup.ag/price/v3"

def _headers() -> Dict[str, str]:
    h = {}
    key = os.getenv("JUPITER_API_KEY", "")
    if key:
        h["x-api-key"] = key
    return h

def get_price(mint: str) -> float:
    """Get USD price for a token mint (V3 API)."""
    resp = httpx.get(f"{JUPITER_PRICE}?ids={mint}", timeout=10)
    data = resp.json()
    return float(data.get(mint, {}).get("usdPrice", 0))

def swap_usdc_to_sol(amount_usd: float, keypair: Keypair, slippage_bps: int = 100) -> dict:
    """
    Swap USDC to SOL via Jupiter Ultra.
    amount_usd: max USD value of USDC to trade.
    """
    wallet = str(keypair.pubkey())
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    SOL_MINT = "So11111111111111111111111111111111111111112"

    # 1. Cap at MAX_TRADE_USD env var (default $1.00)
    _max = float(os.getenv("MAX_TRADE_USD", "1.00"))
    amount_usd = min(amount_usd, _max)

    # 2. USDC has 6 decimals
    amount_raw = int(amount_usd * 1_000_000)
    if amount_raw <= 0:
        return {"status": "error", "reason": "Amount too small"}

    print(f"🔄 USDC → SOL")
    print(f"   Wallet: {wallet}")
    print(f"   Amount: ${amount_usd} = {amount_raw} raw USDC")

    # 3. Get Ultra order
    params = {
        "inputMint": USDC_MINT,
        "outputMint": SOL_MINT,
        "amount": str(amount_raw),
        "taker": wallet,
        "slippageBps": str(slippage_bps),
        "computeUnitPriceMicroLamports": "1000",
    }
    print(f"   Getting order...")
    resp = httpx.get(f"{JUPITER_ULTRA}/order", params=params, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return {"status": "error", "reason": f"Order API: {resp.status_code} {resp.text[:200]}"}

    order = resp.json()
    tx_b64 = order.get("transaction")
    request_id = order.get("requestId")
    if not tx_b64 or not request_id:
        return {"status": "error", "reason": "No transaction in order response"}

    out_amount = float(order.get("outAmount", 0)) / 1_000_000_000
    print(f"   Quote: → {out_amount:.6f} SOL")

    # 3.5 GUARDA: simula a tx antes de assinar. Bloqueia se o balanço não fechar
    # (incidente 18/08/2026: USDC saiu, SOL não entrou, US$ 10,37 perdidos).
    sol_price = get_price(SOL_MINT)
    if os.getenv("GUARD_ENABLED", "1") != "0":
        guard = guard_order(wallet=wallet, tx_b64=tx_b64, side="buy",
                            amount_usd=amount_usd, expected_out=out_amount,
                            sol_price=sol_price, slippage_bps=slippage_bps)
        if not guard.get("ok"):
            print(f"   ⛔ BLOQUEADO pela guarda: {guard.get('code')} — {guard.get('reason')}")
            return {
                "status": "blocked",
                "amount_usd": amount_usd,
                "reason": f"{guard.get('code')}: {guard.get('reason')}",
                "guard": guard,
            }
        print(f"   🛡️  Guarda ok: sai US$ {guard['spent_usd']:.4f} → entra US$ {guard['received_usd']:.4f}")

    # 4. Sign transaction
    print(f"   Signing...")
    tx_bytes = base64.b64decode(tx_b64)
    raw_tx = VersionedTransaction.from_bytes(tx_bytes)
    msg_bytes = to_bytes_versioned(raw_tx.message)
    sig = keypair.sign_message(msg_bytes)

    n_sigs = raw_tx.message.header.num_required_signatures
    from solders.signature import Signature
    wallet_str = str(keypair.pubkey())
    sigs = [Signature.default()] * n_sigs
    for i in range(n_sigs):
        if str(raw_tx.message.account_keys[i]) == wallet_str:
            sigs[i] = sig
            break
    else:
        sigs[0] = sig

    signed_tx = VersionedTransaction.populate(raw_tx.message, sigs)
    signed_b64 = base64.b64encode(bytes(signed_tx)).decode()

    # 5. Execute via Ultra
    print(f"   Executing...")
    exec_payload = {
        "signedTransaction": signed_b64,
        "requestId": request_id,
    }
    exec_resp = httpx.post(f"{JUPITER_ULTRA}/execute", json=exec_payload, headers=_headers(), timeout=60)
    if exec_resp.status_code != 200:
        return {"status": "error", "reason": f"Execute API: {exec_resp.status_code} {exec_resp.text[:200]}"}

    result = exec_resp.json()
    sig_str = result.get("signature", "unknown")

    # ⚠️ A API Ultra retorna HTTP 200 + status "Failed" quando a TX falha on-chain.
    # Antes: reportava "executed" cegamente -> portfolio registrava trade falso.
    if str(result.get("status", "")).strip().lower() == "failed":
        print(f"   ❌ TX FALHOU on-chain: {result.get('error', 'sem detalhe')[:200]}")
        return {
            "status": "error",
            "signature": sig_str,
            "amount_usd": amount_usd,
            "out_sol": out_amount,
            "reason": f"Execute retornou Failed on-chain: {result.get('error', '')[:200]}",
            "full_result": result,
        }

    print(f"   ✅ TX: {sig_str[:25]}...")

    return {
        "status": "executed",
        "signature": sig_str,
        "amount_usd": amount_usd,
        "out_sol": out_amount,
        "full_result": result,
    }

    settlement = verify_settlement(sig_str, wallet, "buy", amount_usd, out_amount, sol_price,
                                   slippage_bps=slippage_bps)
    if not settlement.get("ok"):
        print(f"   ⛔ Settlement não fechou: {settlement.get('code')} — {settlement.get('reason')}")
        return {
            "status": "error",
            "signature": sig_str,
            "amount_usd": amount_usd,
            "out_sol": out_amount,
            "reason": f"settlement: {settlement.get('code')} — {settlement.get('reason')}",
            "settlement": settlement,
            "full_result": result,
        }
    return {
        "status": "executed",
        "signature": sig_str,
        "amount_usd": amount_usd,
        "out_sol": out_amount,
        "settlement": settlement,
        "full_result": result,
    }


def swap_sol_to_usdc(amount_usd: float, keypair: Keypair, slippage_bps: int = 100) -> dict:
    """
    Swap SOL to USDC via Jupiter Ultra.
    amount_usd: max USD value to trade.
    """
    wallet = str(keypair.pubkey())

    # 1. Get SOL price
    sol_price = get_price("So11111111111111111111111111111111111111112")
    if sol_price <= 0:
        return {"status": "error", "reason": "Could not get SOL price"}

    # 2. Cap at MAX_TRADE_USD env var (default $1.00)
    _max = float(os.getenv("MAX_TRADE_USD", "1.00"))
    amount_usd = min(amount_usd, _max)

    # 3. Calculate lamports
    amount_lamports = int(amount_usd / sol_price * 1_000_000_000)
    if amount_lamports <= 0:
        return {"status": "error", "reason": f"Amount too small"}

    print(f"🔄 SOL → USDC")
    print(f"   Wallet: {wallet}")
    print(f"   Amount: ${amount_usd} = {amount_lamports} lamports")

    # 4. Get Ultra order (w/out receiver — causes 400 if same as taker)
    params = {
        "inputMint": "So11111111111111111111111111111111111111112",
        "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "amount": str(amount_lamports),
        "taker": wallet,
        "slippageBps": str(slippage_bps),
        "computeUnitPriceMicroLamports": "1000",  # priority fee
    }
    print(f"   Getting order...")
    resp = httpx.get(f"{JUPITER_ULTRA}/order", params=params, headers=_headers(), timeout=30)
    if resp.status_code != 200:
        return {"status": "error", "reason": f"Order API: {resp.status_code} {resp.text[:200]}"}
    
    order = resp.json()
    tx_b64 = order.get("transaction")
    request_id = order.get("requestId")
    if not tx_b64 or not request_id:
        return {"status": "error", "reason": "No transaction in order response"}
    
    out_amount = float(order.get("outAmount", 0)) / 1_000_000
    print(f"   Quote: → {out_amount:.4f} USDC")

    # 4.5 GUARDA: simula a tx antes de assinar (ver incidente de 18/08/2026)
    if os.getenv("GUARD_ENABLED", "1") != "0":
        guard = guard_order(wallet=wallet, tx_b64=tx_b64, side="sell",
                            amount_usd=amount_usd, expected_out=out_amount,
                            sol_price=sol_price, slippage_bps=slippage_bps)
        if not guard.get("ok"):
            print(f"   ⛔ BLOQUEADO pela guarda: {guard.get('code')} — {guard.get('reason')}")
            return {
                "status": "blocked",
                "amount_usd": amount_usd,
                "reason": f"{guard.get('code')}: {guard.get('reason')}",
                "guard": guard,
            }
        print(f"   🛡️  Guarda ok: sai US$ {guard['spent_usd']:.4f} → entra US$ {guard['received_usd']:.4f}")

    # 5. Sign transaction
    print(f"   Signing...")
    tx_bytes = base64.b64decode(tx_b64)
    raw_tx = VersionedTransaction.from_bytes(tx_bytes)
    msg_bytes = to_bytes_versioned(raw_tx.message)
    sig = keypair.sign_message(msg_bytes)

    # For gasless transactions, Jupiter's fee payer is account[0] requiring sig[0].
    # Our wallet may be at a different slot. Find ours and sign only ours.
    n_sigs = raw_tx.message.header.num_required_signatures
    from solders.signature import Signature
    wallet_str = str(keypair.pubkey())
    sigs = [Signature.default()] * n_sigs
    for i in range(n_sigs):
        if str(raw_tx.message.account_keys[i]) == wallet_str:
            sigs[i] = sig
            break
    else:
        # Fallback: assume we're first signer
        sigs[0] = sig

    signed_tx = VersionedTransaction.populate(raw_tx.message, sigs)
    signed_b64 = base64.b64encode(bytes(signed_tx)).decode()

    # 6. Execute via Ultra
    print(f"   Executing...")
    exec_payload = {
        "signedTransaction": signed_b64,
        "requestId": request_id,
    }
    exec_resp = httpx.post(f"{JUPITER_ULTRA}/execute", json=exec_payload, headers=_headers(), timeout=60)
    if exec_resp.status_code != 200:
        return {"status": "error", "reason": f"Execute API: {exec_resp.status_code} {exec_resp.text[:200]}"}

    result = exec_resp.json()
    sig_str = result.get("signature", "unknown")

    # ⚠️ A API Ultra retorna HTTP 200 + status "Failed" quando a TX falha on-chain.
    # Antes: reportava "executed" cegamente -> portfolio registrava trade falso.
    if str(result.get("status", "")).strip().lower() == "failed":
        print(f"   ❌ TX FALHOU on-chain: {result.get('error', 'sem detalhe')[:200]}")
        return {
            "status": "error",
            "signature": sig_str,
            "amount_usd": amount_usd,
            "out_usdc": out_amount,
            "reason": f"Execute retornou Failed on-chain: {result.get('error', '')[:200]}",
            "full_result": result,
        }

    print(f"   ✅ TX: {sig_str[:25]}...")

    settlement = verify_settlement(sig_str, wallet, "sell", amount_usd, out_amount, sol_price,
                                   slippage_bps=slippage_bps)
    if not settlement.get("ok"):
        print(f"   ⛔ Settlement não fechou: {settlement.get('code')} — {settlement.get('reason')}")
        return {
            "status": "error",
            "signature": sig_str,
            "amount_usd": amount_usd,
            "out_usdc": out_amount,
            "reason": f"settlement: {settlement.get('code')} — {settlement.get('reason')}",
            "settlement": settlement,
            "full_result": result,
        }
    return {
        "status": "executed",
        "signature": sig_str,
        "amount_usd": amount_usd,
        "out_usdc": out_amount,
        "settlement": settlement,
        "full_result": result,
    }
