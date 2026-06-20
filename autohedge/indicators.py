"""
AutoHedge Bot - Indicadores Técnicos REAIS.
Calcula RSI, MACD, Bollinger, EMA, ATR, Support/Resistance
a partir de dados históricos do CoinGecko (7 dias, 1h).
"""

import statistics
from typing import Dict, List, Optional, Tuple

import httpx

COINGECKO = "https://api.coingecko.com/api/v3"


def fetch_hourly_closes(days: int = 7) -> List[float]:
    """Busca preços de fechamento horários via CoinGecko."""
    try:
        resp = httpx.get(
            f"{COINGECKO}/coins/solana/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "hourly"},
            timeout=15,
        )
        data = resp.json()
        prices = data.get("prices", [])
        # Each item is [timestamp_ms, price]
        return [p[1] for p in prices if p[1] > 0]
    except Exception:
        return []


def fetch_volumes(days: int = 7) -> List[float]:
    """Busca volumes horários via CoinGecko."""
    try:
        resp = httpx.get(
            f"{COINGECKO}/coins/solana/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "hourly"},
            timeout=15,
        )
        data = resp.json()
        volumes = data.get("total_volumes", [])
        return [v[1] for v in volumes if v[1] > 0]
    except Exception:
        return []


# ── RSI ──────────────────────────────────────────────────────────────

def calc_rsi(closes: List[float], period: int = 14) -> float:
    """Calcula RSI (Relative Strength Index)."""
    if len(closes) < period + 1:
        return 50.0  # neutro se não tem dados suficientes

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    # Média exponencial (Wilder's smoothing)
    avg_gain = statistics.mean(gains[:period])
    avg_loss = statistics.mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ── MACD ─────────────────────────────────────────────────────────────

def _ema(data: List[float], period: int) -> List[float]:
    """Calcula EMA (Exponential Moving Average)."""
    if not data:
        return []
    k = 2 / (period + 1)
    ema = [data[0]]
    for price in data[1:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def calc_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
    """Calcula MACD (line, signal, histogram)."""
    if len(closes) < slow + signal:
        return {"macd": 0, "signal": 0, "histogram": 0, "cross": "neutral"}

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = _ema(macd_line, signal)

    macd_val = round(macd_line[-1], 4)
    signal_val = round(signal_line[-1], 4)
    hist = round(macd_val - signal_val, 4)

    # Detect crossover
    prev_macd = macd_line[-2] if len(macd_line) > 1 else 0
    prev_signal = signal_line[-2] if len(signal_line) > 1 else 0
    cross = "neutral"
    if prev_macd <= prev_signal and macd_val > signal_val:
        cross = "bullish"  # golden cross
    elif prev_macd >= prev_signal and macd_val < signal_val:
        cross = "bearish"  # death cross

    return {
        "macd": macd_val,
        "signal": signal_val,
        "histogram": hist,
        "cross": cross,
    }


# ── Bollinger Bands ──────────────────────────────────────────────────

def calc_bollinger(closes: List[float], period: int = 20, std_dev: float = 2.0) -> Dict:
    """Calcula Bollinger Bands (upper, middle, lower)."""
    if len(closes) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "width": 0, "position": 0.5}

    window = closes[-period:]
    middle = statistics.mean(window)
    std = statistics.stdev(window) if len(window) > 1 else 0

    upper = round(middle + std_dev * std, 2)
    lower = round(middle - std_dev * std, 2)
    width = round((upper - lower) / middle * 100, 2) if middle > 0 else 0

    # Position within bands (0 = at lower, 1 = at upper)
    current = closes[-1]
    position = round((current - lower) / (upper - lower), 2) if upper != lower else 0.5

    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "width": width,
        "position": position,
    }


# ── EMA ──────────────────────────────────────────────────────────────

def calc_emas(closes: List[float]) -> Dict:
    """Calcula EMA 20 e EMA 50."""
    ema20 = round(_ema(closes, 20)[-1], 2) if len(closes) >= 20 else 0
    ema50 = round(_ema(closes, 50)[-1], 2) if len(closes) >= 50 else 0
    current = closes[-1] if closes else 0

    trend = "neutral"
    if ema20 > 0 and ema50 > 0:
        if ema20 > ema50 and current > ema20:
            trend = "bullish"
        elif ema20 < ema50 and current < ema20:
            trend = "bearish"

    return {
        "ema20": ema20,
        "ema50": ema50,
        "current": round(current, 2),
        "trend": trend,
    }


# ── ATR ──────────────────────────────────────────────────────────────

def calc_atr(closes: List[float], period: int = 14) -> float:
    """Calcula ATR (Average True Range) usando variações de preço."""
    if len(closes) < period + 1:
        return 0

    trs = []
    for i in range(1, len(closes)):
        tr = abs(closes[i] - closes[i - 1])
        trs.append(tr)

    if len(trs) < period:
        return 0
    return round(statistics.mean(trs[-period:]), 2)


# ── Support/Resistance ───────────────────────────────────────────────

def calc_support_resistance(closes: List[float], current_price: float) -> Dict:
    """Identifica suporte e resistência baseado em pivôs recentes."""
    if len(closes) < 10:
        return {"support": 0, "resistance": 0, "pivot": 0}

    # Encontra pivôs (mínimos e máxios locais)
    highs = []
    lows = []
    for i in range(2, len(closes) - 2):
        if closes[i] > closes[i-1] and closes[i] > closes[i-2] and \
           closes[i] > closes[i+1] and closes[i] > closes[i+2]:
            highs.append(closes[i])
        if closes[i] < closes[i-1] and closes[i] < closes[i-2] and \
           closes[i] < closes[i+1] and closes[i] < closes[i+2]:
            lows.append(closes[i])

    # Suporte = maior mínimo abaixo do preço atual
    supports = [l for l in lows if l < current_price]
    support = max(supports) if supports else min(closes)

    # Resistência = menor máximo acima do preço atual
    resistances = [h for h in highs if h > current_price]
    resistance = min(resistances) if resistances else max(closes)

    # Pivot clássico
    h7 = max(closes[-168:]) if len(closes) >= 168 else max(closes)
    l7 = min(closes[-168:]) if len(closes) >= 168 else min(closes)
    c7 = closes[-1]
    pivot = round((h7 + l7 + c7) / 3, 2)

    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "pivot": pivot,
    }


# ── Fibonacci ────────────────────────────────────────────────────────

def calc_fibonacci(closes: List[float], current_price: float) -> Dict:
    """Calcula níveis de Fibonacci baseado no range recente."""
    if len(closes) < 20:
        return {}

    recent = closes[-168:] if len(closes) >= 168 else closes  # 7 dias
    high = max(recent)
    low = min(recent)
    diff = high - low

    if diff <= 0:
        return {}

    return {
        "fib_0": round(high, 2),
        "fib_236": round(high - diff * 0.236, 2),
        "fib_382": round(high - diff * 0.382, 2),
        "fib_500": round(high - diff * 0.500, 2),
        "fib_618": round(high - diff * 0.618, 2),
        "fib_786": round(high - diff * 0.786, 2),
        "fib_100": round(low, 2),
    }


# ── Volume Analysis ──────────────────────────────────────────────────

def calc_volume_analysis(volumes: List[float]) -> Dict:
    """Analisa tendência de volume."""
    if len(volumes) < 24:
        return {"avg_24h": 0, "trend": "neutral", "ratio": 1.0}

    avg_24h = statistics.mean(volumes[-24:])
    avg_7d = statistics.mean(volumes) if volumes else avg_24h
    ratio = round(avg_24h / avg_7d, 2) if avg_7d > 0 else 1.0

    trend = "neutral"
    if ratio > 1.2:
        trend = "above_average"
    elif ratio < 0.8:
        trend = "below_average"

    return {
        "avg_24h": round(avg_24h, 0),
        "avg_7d": round(avg_7d, 0),
        "ratio": ratio,
        "trend": trend,
    }


# ── All indicators ───────────────────────────────────────────────────

def fetch_all_indicators() -> str:
    """
    Busca todos os indicadores técnicos reais e retorna formatado pro prompt.
    """
    closes = fetch_hourly_closes(days=7)
    volumes = fetch_volumes(days=7)

    if not closes:
        return "⚠️ Não foi possível buscar dados históricos de preço."

    current_price = closes[-1]

    rsi = calc_rsi(closes)
    macd = calc_macd(closes)
    bollinger = calc_bollinger(closes)
    emas = calc_emas(closes)
    atr = calc_atr(closes)
    sr = calc_support_resistance(closes, current_price)
    fib = calc_fibonacci(closes, current_price)
    vol = calc_volume_analysis(volumes)

    lines = []
    lines.append("=" * 50)
    lines.append("INDICADORES TÉCNICOS REAIS (calculados agora)")
    lines.append("=" * 50)
    lines.append("")

    # Price context
    lines.append(f"## Preço Atual: ${current_price:.2f}")
    lines.append(f"## Dados: {len(closes)} candles de 1h (7 dias)")
    lines.append("")

    # RSI
    rsi_signal = "sobrecomprado" if rsi > 70 else "sobrevendido" if rsi < 30 else "neutro"
    lines.append(f"## RSI (14): {rsi} — {rsi_signal}")
    if rsi > 70:
        lines.append("   ⚠️ Acima de 70 = possível reversão para baixo")
    elif rsi < 30:
        lines.append("   ⚠️ Abaixo de 30 = possível reversão para cima")
    lines.append("")

    # MACD
    cross_emoji = "🟢" if macd["cross"] == "bullish" else "🔴" if macd["cross"] == "bearish" else "⚪"
    lines.append(f"## MACD (12,26,9):")
    lines.append(f"   MACD: {macd['macd']}")
    lines.append(f"   Signal: {macd['signal']}")
    lines.append(f"   Histogram: {macd['histogram']}")
    lines.append(f"   Cruzamento: {cross_emoji} {macd['cross']}")
    lines.append("")

    # Bollinger
    bb_pos = "acima das bandas" if bollinger["position"] > 1 else "abaixo das bandas" if bollinger["position"] < 0 else "dentro das bandas"
    lines.append(f"## Bollinger Bands (20,2):")
    lines.append(f"   Upper: ${bollinger['upper']}")
    lines.append(f"   Middle: ${bollinger['middle']}")
    lines.append(f"   Lower: ${bollinger['lower']}")
    lines.append(f"   Largura: {bollinger['width']}%")
    lines.append(f"   Posição: {bollinger['position']} ({bb_pos})")
    lines.append("")

    # EMAs
    trend_emoji = "🟢" if emas["trend"] == "bullish" else "🔴" if emas["trend"] == "bearish" else "⚪"
    lines.append(f"## Médias Móveis:")
    lines.append(f"   EMA 20: ${emas['ema20']}")
    lines.append(f"   EMA 50: ${emas['ema50']}")
    lines.append(f"   Tendência: {trend_emoji} {emas['trend']}")
    lines.append("")

    # ATR
    atr_pct = round(atr / current_price * 100, 2) if current_price > 0 else 0
    lines.append(f"## ATR (14): ${atr} ({atr_pct}% do preço)")
    lines.append("")

    # Support/Resistance
    lines.append(f"## Suporte / Resistência:")
    lines.append(f"   Suporte: ${sr['support']}")
    lines.append(f"   Resistência: ${sr['resistance']}")
    lines.append(f"   Pivot: ${sr['pivot']}")
    lines.append("")

    # Fibonacci
    if fib:
        lines.append(f"## Fibonacci (7d range):")
        lines.append(f"   0%: ${fib['fib_0']} (topo)")
        lines.append(f"   23.6%: ${fib['fib_236']}")
        lines.append(f"   38.2%: ${fib['fib_382']}")
        lines.append(f"   50%: ${fib['fib_500']}")
        lines.append(f"   61.8%: ${fib['fib_618']}")
        lines.append(f"   78.6%: ${fib['fib_786']}")
        lines.append(f"   100%: ${fib['fib_100']} (fundo)")
        lines.append("")

    # Volume
    vol_emoji = "📈" if vol["trend"] == "above_average" else "📉" if vol["trend"] == "below_average" else "➡️"
    lines.append(f"## Volume:")
    lines.append(f"   Média 24h: ${vol['avg_24h']:,.0f}")
    lines.append(f"   Média 7d: ${vol['avg_7d']:,.0f}")
    lines.append(f"   Razão: {vol['ratio']}x {vol_emoji} {vol['trend']}")
    lines.append("")

    lines.append("IMPORTANTE: Estes são dados REAIS calculados a partir de")
    lines.append("preços históricos. Use-os na sua análise. NÃO invente valores.")

    return "\n".join(lines)


if __name__ == "__main__":
    print(fetch_all_indicators())
