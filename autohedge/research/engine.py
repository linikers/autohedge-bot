"""Motor de backtest, walk-forward e Monte Carlo.

Regras que o motor impõe (é o que impede o auto-engano):

- posição do dia i vem do sinal do dia i-1 (sem lookahead);
- custo em toda transição flat↔long (fee de compra e de venda);
- resultado só vale se sobreviver ao bloco out-of-sample.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_FEE_BUY = 0.001    # 0,10% (medido na Jupiter: +0,06% na compra)
DEFAULT_FEE_SELL = 0.003   # 0,30% (medido na venda: +0,47% com slippage)
MIN_TRADES_OOS = 30
N_FOLDS = 4
MC_RUNS = 2000


@dataclass
class Metrics:
    trades: int = 0
    wins: int = 0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    exposure: float = 0.0
    equity: List[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    def as_dict(self) -> dict:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "win_rate": round(self.win_rate * 100, 2),
            "total_return": round(self.total_return * 100, 2),
            "max_drawdown": round(self.max_drawdown * 100, 2),
            "profit_factor": round(self.profit_factor, 2),
            "exposure": round(self.exposure * 100, 2),
        }


def simulate(closes: Sequence[float], positions: Sequence[int],
             fee_buy: float = DEFAULT_FEE_BUY, fee_sell: float = DEFAULT_FEE_SELL) -> Metrics:
    """Compra/vende 100% do capital. positions[i] vale para o retorno de i+1."""
    equity = 1.0
    curve = [1.0]
    trades = wins = 0
    in_pos = False
    entry_equity = 1.0
    gains = losses = 0.0
    days_in = 0

    for i in range(1, len(closes)):
        pos = positions[i - 1]
        ret = closes[i] / closes[i - 1] - 1.0

        if pos == 1 and not in_pos:
            equity *= (1 - fee_buy)
            in_pos = True
            entry_equity = equity
        elif pos == 0 and in_pos:
            equity *= (1 - fee_sell)
            pnl = equity / entry_equity - 1
            trades += 1
            if pnl > 0:
                wins += 1
                gains += pnl
            else:
                losses += -pnl
            in_pos = False

        if in_pos:
            equity *= (1 + ret)
            days_in += 1

        curve.append(equity)

    if in_pos:  # marca a mercado no fim
        pnl = equity / entry_equity - 1
        trades += 1
        wins += pnl > 0
        if pnl > 0:
            gains += pnl
        else:
            losses += -pnl

    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, 1 - v / peak)

    return Metrics(
        trades=trades,
        wins=wins,
        total_return=equity - 1.0,
        max_drawdown=max_dd,
        profit_factor=(gains / losses) if losses > 0 else (999.0 if gains > 0 else 0.0),
        exposure=days_in / max(len(closes) - 1, 1),
        equity=curve,
    )


# ------------------------------------------------------------------ walk-forward


def split_folds(n: int, folds: int = N_FOLDS) -> List[Tuple[int, int]]:
    """Divide o índice em blocos contíguos [(start, end), ...]."""
    size = n // folds
    out = []
    for k in range(folds):
        start = k * size
        end = n if k == folds - 1 else (k + 1) * size
        out.append((start, end))
    return out


def walk_forward_selection(bars, make_positions, configs: List[dict], folds: int = N_FOLDS,
                           fee_buy: float = DEFAULT_FEE_BUY, fee_sell: float = DEFAULT_FEE_SELL,
                           min_train_trades: int = 10) -> dict:
    """
    Walk-forward de verdade: em cada fold o parâmetro é escolhido no treino
    (janela expansível) e aplicado no bloco seguinte, que ele nunca viu.

    Devolve o resultado OOS agregado, os blocos usados e os parâmetros escolhidos.
    """
    closes = [b.close for b in bars]
    blocks = split_folds(len(closes), folds)
    oos_closes: List[float] = []
    oos_pos: List[int] = []
    oos_bars = []
    per_fold = []
    chosen = []
    trades = wins = 0
    equity = 1.0

    for k in range(folds - 1):
        train_end = blocks[k][1]
        test_start, test_end = blocks[k + 1]
        if train_end < 60 or (test_end - test_start) < 20:
            continue

        best_cfg = None
        best_ret = float("-inf")
        qualified = 0
        for cfg in configs:
            pos_train = make_positions(cfg, bars[:train_end])
            m = simulate(closes[:train_end], pos_train, fee_buy, fee_sell)
            if m.trades < min_train_trades:
                continue
            qualified += 1
            if m.total_return > best_ret:
                best_cfg, best_ret = cfg, m.total_return
        if best_cfg is None:
            continue

        # parâmetro congelado; posições do teste calculadas só com o passado
        pos_test = make_positions(best_cfg, bars[:test_end])[test_start - 1:test_end]
        seg_closes = closes[test_start - 1:test_end]
        seg_bars = bars[test_start - 1:test_end]
        m_test = simulate(seg_closes, pos_test, fee_buy, fee_sell)

        chosen.append({"fold": k + 1, "params": best_cfg,
                       "train_return": round(best_ret * 100, 2),
                       "test_return": round(m_test.total_return * 100, 2),
                       "configs_com_amostra": qualified})
        per_fold.append({"fold": k + 1, **m_test.as_dict()})
        trades += m_test.trades
        wins += m_test.wins
        equity *= (1 + m_test.total_return)
        oos_closes += seg_closes
        oos_pos += pos_test
        oos_bars += seg_bars

    peak = 1.0
    max_dd = 0.0
    curve = [1.0]
    running = 1.0
    for i in range(1, len(oos_closes)):
        ret = oos_closes[i] / oos_closes[i - 1] - 1
        if oos_pos[i - 1] == 1:
            running *= (1 + ret)
        curve.append(running)
        peak = max(peak, running)
        max_dd = max(max_dd, 1 - running / peak)

    exposures = sum(oos_pos) / max(len(oos_pos), 1)
    oos = Metrics(trades=trades, wins=wins, total_return=equity - 1.0,
                  max_drawdown=max_dd, exposure=exposures, equity=curve)
    return {"oos": oos, "per_fold": per_fold, "chosen": chosen,
            "oos_bars": oos_bars, "oos_positions": oos_pos}


# ------------------------------------------------------------------ Monte Carlo


def monte_carlo_p(bars, positions: Sequence[int], observed_return: float,
                  runs: int = MC_RUNS, seed: int = 7,
                  fee_buy: float = DEFAULT_FEE_BUY, fee_sell: float = DEFAULT_FEE_SELL) -> Optional[float]:
    """
    p-valor: em que fração de runs com entradas aleatórias (mesmo nº de trades e
    mesmo tempo de exposição) o retorno bate ou supera o da estratégia.
    """
    closes = [b.close for b in bars]
    n = len(closes)
    if n < 60:
        return None

    # quantos dias a estratégia fica exposta e quantas transições faz
    blocks = []
    start_idx = None
    for i, p in enumerate(positions):
        if p == 1 and start_idx is None:
            start_idx = i
        elif p == 0 and start_idx is not None:
            blocks.append(i - start_idx)
            start_idx = None
    if start_idx is not None:
        blocks.append(len(positions) - start_idx)
    if not blocks:
        return None

    rng = random.Random(seed)
    beats = 0
    for _ in range(runs):
        pos = [0] * n
        for length in blocks:
            max_start = n - length - 1
            if max_start <= 1:
                continue
            s = rng.randint(1, max_start)
            for j in range(s, s + length):
                pos[j] = 1
        m = simulate(closes, pos, fee_buy, fee_sell)
        if m.total_return >= observed_return:
            beats += 1
    return beats / runs


def verdict(oos: Metrics, per_fold: List[dict], mc_p: Optional[float],
            min_trades: int = MIN_TRADES_OOS, max_p: float = 0.10) -> Tuple[str, str]:
    """Aceita só com amostra, retorno OOS positivo, consistência e significância."""
    if oos.trades < min_trades:
        return "reprovado", f"amostra: {oos.trades} trades no OOS (mínimo {min_trades})"
    if oos.total_return <= 0:
        return "reprovado", f"OOS negativo ({oos.total_return * 100:.2f}%)"
    if mc_p is None:
        return "reprovado", "Monte Carlo indisponível"
    if mc_p > max_p:
        return "reprovado", f"Monte Carlo p={mc_p:.3f} (máx {max_p}) — indistinguível de entrada aleatória"
    positives = sum(1 for f in per_fold if f["total_return"] > 0)
    if positives < 2:
        return "reprovado", f"consistência: {positives}/{len(per_fold)} folds positivos"
    return "aprovado", ""
