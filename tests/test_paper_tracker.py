"""Testes do paper tracker. Roda sem rede: `python -m unittest tests.test_paper_tracker`."""
import unittest

from autohedge.paper_tracker import (
    _horizon_stats,
    binom_p,
    corpus_summary,
    extract_action,
    extract_entry,
    record,
    settle,
    summarize,
)

H = 3600


class FakeFeed:
    """Preço por hora, determinístico."""

    def __init__(self, series):
        self.series = series  # {epoch: preco}

    def at(self, ts):
        hour = ts - (ts % H)
        return self.series.get(hour)


class FakeSpot:
    def __init__(self, price):
        self.price = price

    def now(self):
        return self.price


class TestExtract(unittest.TestCase):
    def test_action_markdown(self):
        self.assertEqual(extract_action("- **Action:** HOLD"), "hold")
        self.assertEqual(extract_action("Action: BUY | Amount"), "buy")
        self.assertEqual(extract_action("AÇÃO:\nSELL"), "sell")

    def test_action_ausente(self):
        self.assertIsNone(extract_action("sem decisão aqui"))

    def test_entry_price(self):
        self.assertEqual(extract_entry("- Entry price: $75.20"), 75.20)


class TestBinom(unittest.TestCase):
    def test_metade_da_amostra(self):
        self.assertGreater(binom_p(50, 100), 0.9)

    def test_tudo_certo(self):
        self.assertLess(binom_p(10, 10), 0.01)

    def test_amostra_vazia(self):
        self.assertEqual(binom_p(0, 0), 1.0)


def rec(ts, action, price, settled=None):
    return {"source": f"a{ts}.json", "ts": ts, "action": action, "price": price,
            "settled": settled or {}}


def settled(h, ret_pct, action="buy"):
    """ret_pct = retorno do mercado; markout segue a direção da aposta."""
    direction = 1 if action == "buy" else -1
    markout = ret_pct * direction
    return {f"{h}h": {"price": 1.0, "ret_pct": ret_pct, "markout_pct": markout,
                      "win": markout > 0}}


class TestHorizonStats(unittest.TestCase):
    def test_conta_acerto_e_markout(self):
        records = [rec(0, "buy", 10, settled(4, 2.0)),
                   rec(0, "buy", 10, settled(4, -1.0)),
                   rec(0, "sell", 10, settled(4, -3.0, "sell"))]
        s = _horizon_stats(records, 4)
        self.assertIsNotNone(s)
        self.assertEqual(s["n"], 3)
        self.assertEqual(s["wins"], 2)
        self.assertAlmostEqual(s["hit_pct"], 66.67, places=2)
        self.assertAlmostEqual(s["markout_pct"], (2 - 1 + 3) / 3, places=3)

    def test_benchmark_mesmo_mix(self):
        # 4 registros, 3 long / 1 short; mercado subiu em metade das janelas
        # mercado subiu em 3 das 4 janelas (3 compras ganham, a venda perde)
        records = [rec(0, "buy", 10, settled(4, 1.0)),
                   rec(0, "buy", 10, settled(4, 1.0)),
                   rec(0, "buy", 10, settled(4, 1.0)),
                   rec(0, "sell", 10, settled(4, 1.0, "sell"))]
        s = _horizon_stats(records, 4)
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["base_up_pct"], 100.0, places=2)
        # mix 3 long / 1 short com mercado 100% de alta => esperado 75%
        self.assertAlmostEqual(s["expected_pct"], 75.0, places=2)
        self.assertAlmostEqual(s["hit_pct"], 75.0, places=2)

    def test_sem_dados_retorna_none(self):
        self.assertIsNone(_horizon_stats([rec(0, "buy", 10)], 4))

    def test_nao_sobreposto_pega_um_por_janela(self):
        records = [rec(0, "buy", 10, settled(24, 1.0)),
                   rec(2 * H, "buy", 10, settled(24, 1.0)),
                   rec(30 * H, "buy", 10, settled(24, 1.0))]
        full = _horizon_stats(records, 24)
        nonov = _horizon_stats(records, 24, non_overlapping=True)
        self.assertIsNotNone(full)
        self.assertIsNotNone(nonov)
        self.assertEqual(full["n"], 3)
        self.assertEqual(nonov["n"], 2)


class TestRecordSettle(unittest.TestCase):
    def test_registra_apenas_buy_sell_e_sem_duplicar(self):
        import autohedge.paper_tracker as pt
        original = (pt._load_records, pt._save_records, pt.RECORDS)
        store = {"version": 1, "records": []}
        pt._load_records = lambda: store
        pt._save_records = lambda d: None
        try:
            scanned = [
                {"file": "a.json", "ts": 1000, "action": "buy", "entry": 1.0, "mtime": 0},
                {"file": "b.json", "ts": 2000, "action": "hold", "entry": None, "mtime": 0},
                {"file": "c.json", "ts": 3000, "action": "sell", "entry": 2.0, "mtime": 0},
            ]
            added = record(scanned, feed=FakeFeed({0: 10.0}), spot=FakeSpot(99.0), now=3000 + 7200)
            self.assertEqual(added, 2)
            self.assertEqual({r["action"] for r in store["records"]}, {"buy", "sell"})
            # segunda passada não duplica
            self.assertEqual(record(scanned, feed=FakeFeed({0: 10.0}), spot=FakeSpot(99.0),
                                    now=3000 + 7200), 0)
        finally:
            pt._load_records, pt._save_records, pt.RECORDS = original

    def test_settle_so_liquida_vencido(self):
        import autohedge.paper_tracker as pt
        original = (pt._load_records, pt._save_records)
        store = {"version": 1, "records": [
            {"source": "a.json", "ts": 0, "action": "buy", "price": 10.0, "settled": {}},
        ]}
        pt._load_records = lambda: store
        pt._save_records = lambda d: None
        try:
            # 2h depois: liquida 1h, não liquida 4h nem 24h
            feed = FakeFeed({0: 10.0, 1 * H: 10.5, 4 * H: 11.0, 24 * H: 12.0})
            filled = settle(feed=feed, now=2 * H)
            self.assertEqual(filled, 1)
            self.assertIn("1h", store["records"][0]["settled"])
            self.assertNotIn("4h", store["records"][0]["settled"])
            # 25h depois: liquida o resto
            filled = settle(feed=feed, now=25 * H)
            self.assertEqual(filled, 2)
            self.assertAlmostEqual(store["records"][0]["settled"]["24h"]["markout_pct"], 20.0, places=2)
        finally:
            pt._load_records, pt._save_records = original

    def test_summarize_estrutura(self):
        import autohedge.paper_tracker as pt
        original = (pt._load_records, pt.corpus_summary)
        pt._load_records = lambda: {"records": [rec(0, "buy", 10, settled(4, 1.0))]}
        pt.corpus_summary = lambda scanned=None: {"analyses": 1, "buy": 1, "sell": 0, "hold": 0, "none": 0}
        try:
            s = summarize()
            self.assertIn("4h", s["by_horizon"])
            self.assertIn("expected_pct", s["by_horizon"]["4h"])
            self.assertIn("p_value", s["by_horizon"]["4h"])
        finally:
            pt._load_records, pt.corpus_summary = original


class TestCorpus(unittest.TestCase):
    def test_conta_acoes(self):
        scanned = [{"action": "buy"}, {"action": "hold"}, {"action": None}, {"action": "hold"}]
        c = corpus_summary(scanned)
        self.assertEqual(c["analyses"], 4)
        self.assertEqual(c["buy"], 1)
        self.assertEqual(c["hold"], 2)
        self.assertEqual(c["none"], 1)


if __name__ == "__main__":
    unittest.main()
