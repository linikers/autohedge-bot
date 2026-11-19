"""Estratégias rule-based para o loop de pesquisa.

Cada função devolve uma lista de posição-alvo por barra (0 = fora, 1 = comprado),
calculada **somente com dados até aquela barra**. O motor desloca 1 barra antes de
executar, então não existe lookahead.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence


def sma(values: Sequence[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= n:
            acc -= values[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def rsi(values: Sequence[float], n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _stateful(n: int) -> List[int]:
    return [0] * n


def ma_cross(closes: Sequence[float], fast: int = 20, slow: int = 100) -> List[int]:
    f, s = sma(closes, fast), sma(closes, slow)
    out: List[int] = []
    for i in range(len(closes)):
        fv, sv = f[i], s[i]
        out.append(1 if (fv is not None and sv is not None and fv > sv) else 0)
    return out


def rsi_meanrev(closes: Sequence[float], period: int = 14, buy_below: float = 30,
                sell_above: float = 65) -> List[int]:
    r = rsi(closes, period)
    pos = _stateful(len(closes))
    holding = 0
    for i in range(len(closes)):
        rv = r[i]
        if rv is None:
            pos[i] = 0
            continue
        if not holding and rv <= buy_below:
            holding = 1
        elif holding and rv >= sell_above:
            holding = 0
        pos[i] = holding
    return pos


def donchian(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
             entry_n: int = 55, exit_n: int = 20) -> List[int]:
    pos = _stateful(len(closes))
    holding = 0
    for i in range(len(closes)):
        if i < entry_n:
            pos[i] = 0
            continue
        upper = max(highs[i - entry_n:i])
        lower = min(lows[i - exit_n:i])
        if not holding and closes[i] > upper:
            holding = 1
        elif holding and closes[i] < lower:
            holding = 0
        pos[i] = holding
    return pos


def fear_trend(closes: Sequence[float], fear: Sequence[Optional[int]], buy_below: float = 25,
               sell_above: float = 55, trend_ma: int = 200) -> List[int]:
    """Compra em pânico, mas só acima da média (ou sem filtro se trend_ma = 0)."""
    ma = sma(closes, trend_ma) if trend_ma else [None] * len(closes)
    pos = _stateful(len(closes))
    holding = 0
    for i in range(len(closes)):
        f = fear[i]
        if f is None:
            pos[i] = holding
            continue
        mav = ma[i]
        trend_ok = True if not trend_ma else (mav is not None and closes[i] > mav)
        if not holding and f <= buy_below and trend_ok:
            holding = 1
        elif holding and f >= sell_above:
            holding = 0
        pos[i] = holding
    return pos


STRATEGIES: Dict[str, dict] = {
    "ma_cross": {
        "fn": ma_cross,
        "needs": ("closes",),
        "grid": {"fast": [5, 10, 20, 50], "slow": [50, 100, 200]},
    },
    "rsi_meanrev": {
        "fn": rsi_meanrev,
        "needs": ("closes",),
        "grid": {"period": [7, 14], "buy_below": [25, 30, 35], "sell_above": [55, 65, 70]},
    },
    "donchian": {
        "fn": donchian,
        "needs": ("highs", "lows", "closes"),
        "grid": {"entry_n": [20, 55, 100], "exit_n": [10, 20, 40]},
    },
    "fear_trend": {
        "fn": fear_trend,
        "needs": ("closes", "fear"),
        "grid": {"buy_below": [20, 25, 30], "sell_above": [50, 60, 70], "trend_ma": [0, 200]},
    },
}


def expand_grid(name: str) -> List[dict]:
    """Produto cartesiano do grid de uma estratégia, já filtrando combinações inválidas."""
    spec = STRATEGIES[name]
    keys = list(spec["grid"])
    combos: List[dict] = [{}]
    for k in keys:
        combos = [{**c, k: v} for c in combos for v in spec["grid"][k]]
    out = []
    for c in combos:
        if name == "ma_cross" and c["fast"] >= c["slow"]:
            continue
        if name == "donchian" and c["exit_n"] >= c["entry_n"]:
            continue
        out.append(c)
    return out


def build_positions(name: str, params: dict, bars, fear: Sequence[Optional[int]]) -> List[int]:
    spec = STRATEGIES[name]
    kwargs = {}
    if "closes" in spec["needs"]:
        kwargs["closes"] = [b.close for b in bars]
    if "highs" in spec["needs"]:
        kwargs["highs"] = [b.high for b in bars]
    if "lows" in spec["needs"]:
        kwargs["lows"] = [b.low for b in bars]
    if "fear" in spec["needs"]:
        kwargs["fear"] = fear
    return spec["fn"](**kwargs, **params)
