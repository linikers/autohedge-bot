"""Testes da guarda de ordem. Roda sem rede: `python -m unittest tests.test_guard`."""
import unittest

from autohedge.guard import (
    USDC_MINT,
    OrderBlocked,
    assert_order_ok,
    evaluate_fill,
    guard_order,
    token_deltas,
    unexpected_outflows,
    verify_settlement,
)

WALLET = "GnVxCDFsqHx681WqKeKS97qiYGsB7M9Rf4C58WcgiLmQ"
SOL_PRICE = 76.14


class FakeRpc:
    """RPC falso: devolve exatamente o que o teste mandar."""

    def __init__(self, sim=None, balance_lamports=1_000_000_000, tx=None):
        self.sim = sim or {}
        self.balance_lamports = balance_lamports
        self.tx = tx
        self.calls = []

    def __call__(self, method, params):
        self.calls.append(method)
        if method == "getBalance":
            return {"value": self.balance_lamports}
        if method == "simulateTransaction":
            return {"value": self.sim}
        if method == "getTransaction":
            return self.tx
        raise AssertionError(f"método inesperado: {method}")


def sim_18_08():
    """Simulação idêntica ao incidente: USDC sai, SOL não entra."""
    return {
        "err": None,
        "logs": ["Program Tokenkeg... invoke [2]"],
        "preTokenBalances": [{"owner": WALLET, "mint": USDC_MINT,
                              "uiTokenAmount": {"uiAmount": 10.37552}}],
        "postTokenBalances": [{"owner": WALLET, "mint": USDC_MINT,
                               "uiTokenAmount": {"uiAmount": 0.0}}],
        "accounts": [{"lamports": 4_800_491}],
    }


class TestEvaluateFill(unittest.TestCase):
    def test_compra_saudavel_passa(self):
        r = evaluate_fill("buy", d_sol=0.02626, d_usdc=-2.0, sol_price=SOL_PRICE,
                          amount_usd=2.0, expected_out=0.02626)
        self.assertTrue(r["ok"], r)
        self.assertLess(r["loss_pct"], 0.1)

    def test_incidente_18_08_bloqueia(self):
        r = evaluate_fill("buy", d_sol=0.0, d_usdc=-10.37552, sol_price=SOL_PRICE,
                          amount_usd=2.0, expected_out=0.026)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "no_output_leg")
        self.assertIn("18/08", r["reason"])

    def test_compra_sem_usdc_saindo_bloqueia(self):
        r = evaluate_fill("buy", d_sol=0.02, d_usdc=0.0, sol_price=SOL_PRICE,
                          amount_usd=2.0, expected_out=0.026)
        self.assertEqual(r["code"], "no_input_leg")

    def test_venda_saudavel_passa(self):
        r = evaluate_fill("sell", d_sol=-0.0263, d_usdc=1.999, sol_price=SOL_PRICE,
                          amount_usd=2.0, expected_out=2.0)
        self.assertTrue(r["ok"], r)

    def test_venda_sem_usdc_entrando_bloqueia(self):
        r = evaluate_fill("sell", d_sol=-0.0263, d_usdc=0.0, sol_price=SOL_PRICE,
                          amount_usd=2.0, expected_out=2.0)
        self.assertEqual(r["code"], "no_output_leg")

    def test_acima_do_teto_bloqueia(self):
        r = evaluate_fill("buy", d_sol=0.26, d_usdc=-20.0, sol_price=SOL_PRICE,
                          amount_usd=2.0, expected_out=0.26)
        self.assertEqual(r["code"], "over_budget")

    def test_abaixo_da_cotacao_bloqueia(self):
        r = evaluate_fill("buy", d_sol=0.02626, d_usdc=-2.0, sol_price=SOL_PRICE,
                          amount_usd=2.0, expected_out=0.030)
        self.assertEqual(r["code"], "below_quote")

    def test_vazamento_de_valor_bloqueia(self):
        # saiu US$ 10 e voltou US$ 5
        r = evaluate_fill("buy", d_sol=0.0656, d_usdc=-10.0, sol_price=SOL_PRICE,
                          amount_usd=10.0, expected_out=0)
        self.assertEqual(r["code"], "value_leak")
        self.assertIn("perda", r["reason"])

    def test_perna_nao_medida_bloqueia(self):
        r = evaluate_fill("buy", d_sol=None, d_usdc=-2.0, sol_price=SOL_PRICE,
                          amount_usd=2.0, expected_out=0.026)
        self.assertEqual(r["code"], "legs_unverified")

    def test_preco_indisponivel_bloqueia(self):
        r = evaluate_fill("buy", d_sol=0.02, d_usdc=-2.0, sol_price=0,
                          amount_usd=2.0, expected_out=0.026)
        self.assertEqual(r["code"], "price_unavailable")

    def test_lado_invalido(self):
        r = evaluate_fill("short", 0, 0, SOL_PRICE, 1, 1)
        self.assertEqual(r["code"], "bad_side")


class TestHelpers(unittest.TestCase):
    def test_token_deltas(self):
        pre = [{"owner": WALLET, "mint": USDC_MINT, "uiTokenAmount": {"uiAmount": 10.0}}]
        post = [{"owner": WALLET, "mint": USDC_MINT, "uiTokenAmount": {"uiAmount": 8.0}}]
        self.assertAlmostEqual(token_deltas(pre, post, WALLET)[USDC_MINT], -2.0)

    def test_outflow_inesperado(self):
        drains = unexpected_outflows({USDC_MINT: -2.0, "OutroMint": -0.5}, USDC_MINT)
        self.assertEqual(drains, [("OutroMint", -0.5)])

    def test_assert_order_ok_levanta(self):
        with self.assertRaises(OrderBlocked):
            assert_order_ok({"ok": False, "code": "x", "reason": "y"})


class TestGuardOrder(unittest.TestCase):
    def test_bloqueia_incidente_18_08_antes_de_assinar(self):
        rpc = FakeRpc(sim=sim_18_08())
        r = guard_order(WALLET, "dHg=", "buy", amount_usd=2.0, expected_out=0.026,
                        sol_price=SOL_PRICE, rpc_call=rpc)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "no_output_leg")

    def test_bloqueia_erro_de_simulacao(self):
        rpc = FakeRpc(sim={"err": {"InstructionError": [5, {"Custom": 4294967295}]}})
        r = guard_order(WALLET, "dHg=", "buy", 2.0, 0.026, SOL_PRICE, rpc_call=rpc)
        self.assertEqual(r["code"], "simulation_failed")

    def test_bloqueia_acima_do_teto_antes_da_rede(self):
        rpc = FakeRpc(sim=sim_18_08())
        r = guard_order(WALLET, "dHg=", "buy", 50.0, 0.6, SOL_PRICE, rpc_call=rpc,
                        hard_cap_usd=2.0)
        self.assertEqual(r["code"], "over_cap")
        self.assertNotIn("simulateTransaction", rpc.calls)

    def test_bloqueia_wallet_invalida(self):
        r = guard_order("abc", "dHg=", "buy", 2.0, 0.026, SOL_PRICE)
        self.assertEqual(r["code"], "bad_wallet")

    def test_compra_ok_usa_simulacao(self):
        sim = {
            "err": None,
            "preTokenBalances": [{"owner": WALLET, "mint": USDC_MINT,
                                  "uiTokenAmount": {"uiAmount": 8.7563}}],
            "postTokenBalances": [{"owner": WALLET, "mint": USDC_MINT,
                                   "uiTokenAmount": {"uiAmount": 6.7563}}],
            "accounts": [{"lamports": 1_026_310_000}],
        }
        rpc = FakeRpc(sim=sim, balance_lamports=1_000_000_000)
        r = guard_order(WALLET, "dHg=", "buy", 2.0, 0.02626, SOL_PRICE, rpc_call=rpc)
        self.assertTrue(r["ok"], r)
        self.assertIn("simulateTransaction", rpc.calls)


class TestVerifySettlement(unittest.TestCase):
    def _tx(self, pre_sol, post_sol, pre_usdc, post_usdc, err=None):
        return {
            "meta": {
                "err": err,
                "preBalances": [5_000_000_000, int(pre_sol * 1e9)],
                "postBalances": [5_000_000_000, int(post_sol * 1e9)],
                "preTokenBalances": [{"owner": WALLET, "mint": USDC_MINT,
                                      "uiTokenAmount": {"uiAmount": pre_usdc}}],
                "postTokenBalances": [{"owner": WALLET, "mint": USDC_MINT,
                                       "uiTokenAmount": {"uiAmount": post_usdc}}],
            },
            "transaction": {"message": {"accountKeys": [{"pubkey": "feePayer"},
                                                        {"pubkey": WALLET}]}},
        }

    def test_settlement_ok(self):
        rpc = FakeRpc(tx=self._tx(0.0048, 0.0311, 10.3755, 8.3755))
        r = verify_settlement("sig", WALLET, "buy", 2.0, 0.02626, SOL_PRICE, rpc_call=rpc)
        self.assertTrue(r["ok"], r)

    def test_settlement_detecta_incidente(self):
        rpc = FakeRpc(tx=self._tx(0.0048, 0.00276, 10.3755, 0.0))
        r = verify_settlement("sig", WALLET, "buy", 2.0, 0.02626, SOL_PRICE, rpc_call=rpc)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "no_output_leg")

    def test_settlement_tx_falhou(self):
        rpc = FakeRpc(tx=self._tx(1, 1, 10, 10, err={"InstructionError": [0, "x"]}))
        r = verify_settlement("sig", WALLET, "buy", 2.0, 0.02626, SOL_PRICE, rpc_call=rpc)
        self.assertEqual(r["code"], "tx_failed")

    def test_settlement_nao_encontrado(self):
        rpc = FakeRpc(tx=None)
        r = verify_settlement("sig", WALLET, "buy", 2.0, 0.02626, SOL_PRICE, rpc_call=rpc)
        self.assertEqual(r["code"], "not_found")


if __name__ == "__main__":
    unittest.main()
