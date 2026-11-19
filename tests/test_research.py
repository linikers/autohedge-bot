"""Testes do loop de pesquisa (motor, walk-forward, travas)."""
import math
import unittest

from autohedge.research.data import Bar
from autohedge.research.engine import (
    Metrics,
    monte_carlo_p,
    simulate,
    split_folds,
    verdict,
    walk_forward_selection,
)
from autohedge.research.strategies import (
    build_positions,
    donchian,
    expand_grid,
    ma_cross,
    rsi,
    rsi_meanrev,
    sma,
)


def bars_from(closes, start_ts=1700000000, step=86400):
    return [Bar(start_ts + i * step, c, c, c, c, 1.0) for i, c in enumerate(closes)]


class TestIndicators(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4], 2)[1], 1.5)
        self.assertIsNone(sma([1, 2], 3)[1])

    def test_rsi_sobe_quando_tudo_sobe(self):
        closes = [float(i) for i in range(1, 40)]
        r = rsi(closes, 14)
        self.assertIsNotNone(r[-1])
        assert r[-1] is not None
        self.assertGreater(r[-1], 95)

    def test_ma_cross_sem_lookahead(self):
        # 100 barras planas + rampa de alta: só pode ficar long depois da rampa
        closes = [100.0] * 100 + [100.0 + i for i in range(1, 101)]
        pos = ma_cross(closes, fast=5, slow=20)
        self.assertEqual(pos[0], 0)
        self.assertEqual(pos[-1], 1)
        self.assertGreater(pos.index(1), 99)

    def test_rsi_meanrev_estado(self):
        closes = [100 - i for i in range(40)] + [100] * 5
        pos = rsi_meanrev(closes, period=14, buy_below=30, sell_above=70)
        self.assertEqual(pos[0], 0)
        self.assertIn(1, pos)

    def test_donchian_precisa_de_janela(self):
        closes = [float(i) for i in range(1, 80)]
        pos = donchian(closes, closes, closes, entry_n=20, exit_n=10)
        self.assertEqual(pos[0], 0)
        self.assertEqual(pos[-1], 1)

    def test_expand_grid_filtra_invalidos(self):
        grids = expand_grid("ma_cross")
        self.assertTrue(all(c["fast"] < c["slow"] for c in grids))
        self.assertEqual(len(grids), 11)

    def test_build_positions_respeita_tamanho(self):
        bars = bars_from([100 + i for i in range(80)])
        pos = build_positions("ma_cross", {"fast": 5, "slow": 20}, bars, [None] * 80)
        self.assertEqual(len(pos), 80)


class TestSimulate(unittest.TestCase):
    def test_sem_posicao_nao_muda_nada(self):
        closes = [100, 110, 120, 130]
        m = simulate(closes, [0, 0, 0, 0])
        self.assertEqual(m.trades, 0)
        self.assertAlmostEqual(m.total_return, 0.0)

    def test_uma_operacao_com_custos(self):
        closes = [100, 100, 110, 110]
        # entra no bar 2 (pos do bar 1), sai no bar 4
        m = simulate(closes, [1, 1, 0, 0], fee_buy=0.001, fee_sell=0.003)
        self.assertEqual(m.trades, 1)
        esperado = (1 - 0.001) * 1.10 * (1 - 0.003) - 1
        self.assertAlmostEqual(m.total_return, esperado, places=6)

    def test_perda_conta_como_derrota(self):
        closes = [100, 100, 90, 90]
        m = simulate(closes, [1, 1, 0, 0])
        self.assertEqual(m.wins, 0)
        self.assertLess(m.total_return, 0)

    def test_drawdown(self):
        closes = [100, 120, 60, 80]
        m = simulate(closes, [1, 1, 1, 1])
        self.assertGreater(m.max_drawdown, 0.4)


class TestWalkForward(unittest.TestCase):
    def test_split_folds_cobre_tudo(self):
        blocks = split_folds(100, 4)
        self.assertEqual(blocks[0][0], 0)
        self.assertEqual(blocks[-1][1], 100)
        self.assertEqual(sum(e - s for s, e in blocks), 100)

    def test_selecao_escolhe_no_treino_e_testa_no_bloco_seguinte(self):
        # série oscilante com deriva: gera várias operações (senão é filtrada por amostra)
        closes = [100 + 8 * math.sin(i / 3.5) + i * 0.1 for i in range(400)]
        bars = bars_from(closes)

        configs = [{"fast": 5, "slow": 20}, {"fast": 50, "slow": 200}]

        def make_positions(cfg, bars_sub):
            return ma_cross([b.close for b in bars_sub], **cfg)

        res = walk_forward_selection(bars, make_positions, configs, folds=4, min_train_trades=3)
        self.assertGreaterEqual(len(res["chosen"]), 1)
        for c in res["chosen"]:
            self.assertIn(c["params"], configs)
        # mecânica: um fold avaliado por bloco de teste, com resultado apurado
        self.assertEqual(len(res["per_fold"]), len(res["chosen"]))
        self.assertIsNotNone(res["oos"].total_return)
        self.assertGreater(len(res["oos_positions"]), 0)

    def test_sem_config_valida_nao_quebra(self):
        bars = bars_from([100 + i for i in range(200)])
        res = walk_forward_selection(bars, lambda cfg, sub: [0] * len(sub),
                                     [{"fast": 5, "slow": 20}], folds=4)
        self.assertEqual(res["oos"].trades, 0)
        self.assertEqual(res["chosen"], [])


class TestMonteCarlo(unittest.TestCase):
    def test_retorno_igual_ao_aleatorio_da_p_alto(self):
        closes = [100 + (i % 7) for i in range(300)]
        bars = bars_from(closes)
        pos = [1 if i % 20 < 10 else 0 for i in range(300)]
        m = simulate(closes, pos)
        p = monte_carlo_p(bars, pos, m.total_return, runs=200)
        self.assertIsNotNone(p)
        self.assertGreater(p, 0.05)

    def test_serie_curta_retorna_none(self):
        bars = bars_from([100, 101, 102])
        self.assertIsNone(monte_carlo_p(bars, [1, 1, 1], 0.0))


class TestVerdict(unittest.TestCase):
    def test_reprova_amostra_pequena(self):
        oos = Metrics(trades=8, wins=6, total_return=0.2)
        status, why = verdict(oos, [{"total_return": 5}, {"total_return": 3}], 0.01)
        self.assertEqual(status, "reprovado")
        self.assertIn("amostra", why)

    def test_reprova_oos_negativo(self):
        oos = Metrics(trades=50, wins=20, total_return=-0.1)
        status, why = verdict(oos, [{"total_return": -1}], 0.01)
        self.assertEqual(status, "reprovado")
        self.assertIn("negativo", why)

    def test_reprova_monte_carlo(self):
        oos = Metrics(trades=50, wins=30, total_return=0.1)
        status, why = verdict(oos, [{"total_return": 1}, {"total_return": 1}], 0.4)
        self.assertEqual(status, "reprovado")
        self.assertIn("Monte Carlo", why)

    def test_reprova_inconsistencia_entre_folds(self):
        oos = Metrics(trades=50, wins=30, total_return=0.1)
        status, why = verdict(oos, [{"total_return": 1}, {"total_return": -1},
                                    {"total_return": -1}], 0.01)
        self.assertEqual(status, "reprovado")
        self.assertIn("consistência", why)

    def test_aprova_quando_passa_em_tudo(self):
        oos = Metrics(trades=50, wins=30, total_return=0.1)
        status, _ = verdict(oos, [{"total_return": 1}, {"total_return": 1}], 0.01)
        self.assertEqual(status, "aprovado")


if __name__ == "__main__":
    unittest.main()
