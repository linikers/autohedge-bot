"""Dados diários para o loop de pesquisa (Coinbase + Fear & Greed)."""
from __future__ import annotations

import csv
import json
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

UA = {"User-Agent": "Mozilla/5.0"}
CACHE = Path(__file__).resolve().parent.parent.parent / "portfolio" / "research_cache"


@dataclass
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _get(url: str, tries: int = 3) -> Any:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.2)
    return []


GRANULARITIES = {3600: "1h", 21600: "6h", 86400: "1d"}


def load_bars(product: str = "SOL-USD", days: int = 400, granularity: int = 21600,
              cache: bool = True) -> List[Bar]:
    """Candles em ordem crescente (granularidade em segundos: 3600, 21600, 86400)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    tag = GRANULARITIES.get(granularity, str(granularity))
    path = CACHE / f"{product.replace('-', '')}_{days}d_{tag}.csv"

    if cache and path.exists() and (time.time() - path.stat().st_mtime) < 6 * 3600:
        with path.open() as fh:
            return [Bar(int(r["ts"]), float(r["open"]), float(r["high"]),
                        float(r["low"]), float(r["close"]), float(r["volume"]))
                    for r in csv.DictReader(fh)]

    now = int(time.time())
    start = now - days * 86400
    rows: Dict[int, Bar] = {}
    cur = start
    while cur < now:
        nxt = min(cur + 290 * granularity, now)
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles?granularity={granularity}"
               f"&start={datetime.fromtimestamp(cur, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
               f"&end={datetime.fromtimestamp(nxt, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        try:
            for r in _get(url):
                rows[int(r[0])] = Bar(int(r[0]), float(r[3]), float(r[2]),
                                      float(r[1]), float(r[4]), float(r[5]))
        except Exception:
            pass
        cur = nxt
        time.sleep(0.35)

    bars = [rows[k] for k in sorted(rows)]
    if cache and bars:
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
            for b in bars:
                w.writerow([b.ts, b.open, b.high, b.low, b.close, b.volume])
    return bars


def load_daily(product: str = "SOL-USD", days: int = 400, cache: bool = True) -> List[Bar]:
    """Compatibilidade: candles diários."""
    return load_bars(product, days, granularity=86400, cache=cache)


def load_fear(days: int = 400, cache: bool = True) -> Dict[int, int]:
    """Fear & Greed diário, alinhado por dia (None quando não existe)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"fear_{days}d.json"
    if cache and path.exists() and (time.time() - path.stat().st_mtime) < 6 * 3600:
        data = json.loads(path.read_text())
    else:
        data = _get("https://api.alternative.me/fng/?limit=0&format=json").get("data", [])
        path.write_text(json.dumps(data))

    by_day = {}
    for d in data:
        try:
            day = int(d["timestamp"]) // 86400
            by_day[day] = int(d["value"])
        except Exception:
            continue
    return by_day
