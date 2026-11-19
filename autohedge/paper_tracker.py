"""
AutoHedge Bot — paper tracker.

Registra cada decisão do Execution Agent (BUY/SELL) com o preço real do momento,
liquida em 1h/4h/24h e calcula acerto, markout e p-valor contra o benchmark do
mesmo mix direcional.

Existe porque o histórico real do bot era inauditável: `portfolio/index.json`
gravava `pnl: None`, então "win rate" nunca foi calculado. Sem esse número não
se sabe — e não se vende — nada.

Uso:
    python -m autohedge.paper_tracker record     # registra decisões novas
    python -m autohedge.paper_tracker settle     # liquida horizontes vencidos
    python -m autohedge.paper_tracker stats      # recalcula portfolio/paper_stats.json
    python -m autohedge.paper_tracker run        # record + settle + stats
    python -m autohedge.paper_tracker backfill   # varre todos os outputs já existentes
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BASE = Path(os.getenv("AUTOHEDGE_HOME", Path(__file__).resolve().parent.parent))
OUTPUTS = BASE / "outputs"
PORTFOLIO = BASE / "portfolio"
RECORDS = PORTFOLIO / "paper_trades.json"
STATS = PORTFOLIO / "paper_stats.json"

HORIZONS = (1, 4, 24)          # horas
COINGECKO_MAX_CHUNK_H = 290    # Coinbase devolve até 300 candles por chamada
UA = {"User-Agent": "Mozilla/5.0"}

ACTION_RE = re.compile(r"action\s*:?\s*\*{0,2}\s*:?\s*\*{0,2}\s*(buy|sell|hold)", re.I)
ACTION_FALLBACK = re.compile(r"\b(buy|sell|hold)\b", re.I)
ENTRY_RE = re.compile(r"entry\s*price\s*:?\s*\*{0,2}\s*\$?\s*([0-9]+(?:\.[0-9]+)?)", re.I)


# ------------------------------------------------------------------ tempo/utils


def parse_ts(raw: str) -> Optional[int]:
    """ISO (naive = UTC, como o pipeline grava) -> epoch."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def iso(ts: Optional[int]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def binom_p(wins: int, n: int, p: float = 0.5) -> float:
    """p-valor exato, bicaudal, para teste binomial."""
    if n <= 0:
        return 1.0
    p = min(max(p, 1e-9), 1 - 1e-9)

    def pmf(k: int) -> float:
        return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))

    obs = pmf(wins)
    total = sum(pmf(k) for k in range(n + 1) if pmf(k) <= obs + 1e-12)
    return min(1.0, total)


# ------------------------------------------------------------------ preços


class CoinbaseFeed:
    """Candles 1h de SOL-USD (Coinbase). Gratuito, sem chave."""

    def __init__(self, product: str = "SOL-USD", granularity: int = 3600):
        self.product = product
        self.granularity = granularity
        self.cache: Dict[int, float] = {}

    def _fetch(self, start: int, end: int) -> Dict[int, float]:
        url = (f"https://api.exchange.coinbase.com/products/{self.product}/candles"
               f"?granularity={self.granularity}"
               f"&start={datetime.fromtimestamp(start, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
               f"&end={datetime.fromtimestamp(end, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as resp:
            rows = json.loads(resp.read().decode())
        return {int(r[0]): float(r[4]) for r in rows}

    def warm(self, start: int, end: int) -> None:
        cur = start
        while cur < end:
            nxt = min(cur + COINGECKO_MAX_CHUNK_H * 3600, end)
            try:
                self.cache.update(self._fetch(cur, nxt))
            except Exception:
                pass
            cur = nxt
            time.sleep(0.3)

    def at(self, ts: int) -> Optional[float]:
        """Close do candle da hora <= ts."""
        if ts in self.cache:
            return self.cache[ts]
        hour = ts - (ts % 3600)
        if hour in self.cache:
            return self.cache[hour]
        try:
            self.cache.update(self._fetch(hour - 3600, hour + 3600))
        except Exception:
            return None
        keys = sorted(self.cache)
        i = bisect.bisect_right(keys, ts)
        return self.cache[keys[i - 1]] if i else None


class JupiterSpot:
    """Preço spot atual (para registrar a decisão no momento em que ela nasce)."""

    MINT = "So11111111111111111111111111111111111111112"

    def now(self) -> Optional[float]:
        try:
            req = urllib.request.Request(f"https://api.jup.ag/price/v3?ids={self.MINT}", headers=UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            return float(data[self.MINT]["usdPrice"])
        except Exception:
            return None


# ------------------------------------------------------------------ sinais


def extract_action(execution_text: str) -> Optional[str]:
    """Action: BUY/SELL/HOLD tolerante a markdown (`**Action:** HOLD`)."""
    if not execution_text:
        return None
    m = ACTION_RE.search(execution_text)
    if m:
        return m.group(1).lower()
    m = ACTION_FALLBACK.search(execution_text)
    return m.group(1).lower() if m else None


def extract_entry(execution_text: str) -> Optional[float]:
    m = ENTRY_RE.search(execution_text or "")
    return float(m.group(1)) if m else None


def scan_outputs(order: str = "asc") -> List[dict]:
    """Lê todos os JSON de análise e devolve [{file, ts, action, entry, mtime}]."""
    if not OUTPUTS.exists():
        return []
    out = []
    for path in sorted(OUTPUTS.glob("analise_*.json"), reverse=(order == "desc")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        ts = parse_ts(data.get("timestamp", ""))
        if ts is None:
            continue
        execution = str((data.get("steps") or {}).get("execution") or "")
        out.append({
            "file": path.name,
            "ts": ts,
            "action": extract_action(execution),
            "entry": extract_entry(execution),
            "mtime": path.stat().st_mtime,
        })
    return out


def corpus_summary(scanned: Optional[List[dict]] = None) -> dict:
    scanned = scanned if scanned is not None else scan_outputs()
    counts = {"buy": 0, "sell": 0, "hold": 0, "none": 0}
    for s in scanned:
        key = s["action"] if s["action"] in ("buy", "sell", "hold") else "none"
        counts[key] += 1
    return {"analyses": len(scanned), **counts}


# ------------------------------------------------------------------ registro


def _load_records() -> dict:
    if RECORDS.exists():
        try:
            data = json.loads(RECORDS.read_text())
            data.setdefault("records", [])
            return data
        except Exception:
            pass
    return {"version": 1, "records": []}


def _save_records(data: dict) -> None:
    PORTFOLIO.mkdir(parents=True, exist_ok=True)
    RECORDS.write_text(json.dumps(data, indent=1))


def record(scanned: Optional[List[dict]] = None, feed: Optional[Any] = None,
           spot: Optional[Any] = None, now: Optional[int] = None) -> int:
    """Cria registro para cada decisão BUY/SELL ainda não registrada."""
    data = _load_records()
    known = {r["source"] for r in data["records"]}
    scanned = scanned if scanned is not None else scan_outputs()
    spot_feed = spot if spot is not None else JupiterSpot()
    feed_ref = feed
    now = now or int(time.time())
    added = 0

    for item in scanned:
        if item["action"] not in ("buy", "sell") or item["file"] in known:
            continue
        price = spot_feed.now() if abs(now - item["ts"]) < 3600 else None
        if price is None:
            if feed_ref is None:
                feed_ref = CoinbaseFeed()
            price = feed_ref.at(item["ts"])
        if not price:
            continue
        data["records"].append({
            "source": item["file"],
            "ts": item["ts"],
            "action": item["action"],
            "entry_agent": item["entry"],
            "price": round(float(price), 4),
            "settled": {},
        })
        known.add(item["file"])
        added += 1

    if added:
        data["records"].sort(key=lambda r: r["ts"])
        _save_records(data)
    return added


def settle(feed: Optional[Any] = None, now: Optional[int] = None) -> int:
    """Liquida horizontes vencidos. Retorna quantos pontos foram preenchidos."""
    data = _load_records()
    feed_ref = feed if feed is not None else CoinbaseFeed()
    now = now or int(time.time())
    filled = 0

    for rec in data["records"]:
        for h in HORIZONS:
            key = f"{h}h"
            if key in rec["settled"]:
                continue
            due = rec["ts"] + h * 3600
            if due > now - 120:          # margem para o candle fechar
                continue
            p0 = rec["price"]
            p1 = feed_ref.at(due)
            if not p0 or not p1:
                continue
            raw = p1 / p0 - 1.0
            direction = 1 if rec["action"] == "buy" else -1
            rec["settled"][key] = {
                "price": round(p1, 4),
                "ret_pct": round(raw * 100, 4),
                "markout_pct": round(raw * direction * 100, 4),
                "win": raw * direction > 0,
            }
            filled += 1

    if filled:
        _save_records(data)
    return filled


# ------------------------------------------------------------------ estatística


def _horizon_stats(records: Iterable[dict], h: int, non_overlapping: bool = False) -> Optional[dict]:
    key = f"{h}h"
    usable = [r for r in records if key in r.get("settled", {})]
    if not usable:
        return None

    if non_overlapping:
        seen, picked = set(), []
        for r in usable:
            bucket = r["ts"] // (h * 3600)
            if bucket in seen:
                continue
            seen.add(bucket)
            picked.append(r)
        usable = picked

    n = len(usable)
    wins = sum(1 for r in usable if r["settled"][key]["win"])
    markout = sum(r["settled"][key]["markout_pct"] for r in usable) / n
    raw = [r["settled"][key]["ret_pct"] for r in usable]
    up = sum(1 for x in raw if x > 0) / n
    longs = sum(1 for r in usable if r["action"] == "buy")
    mix_long = longs / n
    expected = up * mix_long + (1 - up) * (1 - mix_long)

    return {
        "n": n,
        "wins": wins,
        "hit_pct": round(wins / n * 100, 2),
        "expected_pct": round(expected * 100, 2),
        "edge_pp": round((wins / n - expected) * 100, 2),
        "markout_pct": round(markout, 4),
        "base_up_pct": round(up * 100, 2),
        "p_value": round(binom_p(wins, n), 4),
    }


def summarize(records: Optional[List[dict]] = None) -> dict:
    data = _load_records()
    records = records if records is not None else data["records"]
    by_h = {}
    for h in HORIZONS:
        full = _horizon_stats(records, h)
        if full:
            full["non_overlapping"] = _horizon_stats(records, h, non_overlapping=True)
            by_h[f"{h}h"] = full

    recent = []
    for r in sorted(records, key=lambda x: x["ts"], reverse=True)[:12]:
        row = {"ts": iso(r["ts"]), "action": r["action"], "price": r["price"],
               "entry_agent": r.get("entry_agent")}
        for h in HORIZONS:
            s = r.get("settled", {}).get(f"{h}h")
            if s:
                row[f"markout_{h}h"] = s["markout_pct"]
        recent.append(row)

    return {
        "generated_at": iso(int(time.time())),
        "corpus": corpus_summary(),
        "records": {
            "total": len(records),
            "settled_24h": sum(1 for r in records if "24h" in r.get("settled", {})),
        },
        "by_horizon": by_h,
        "recent": recent,
        "note": "markout = retorno na direção apostada, bruto de custo. "
                "expected = acerto esperado se a direção fosse aleatória com o mesmo mix long/short.",
    }


def write_stats() -> dict:
    stats = summarize()
    PORTFOLIO.mkdir(parents=True, exist_ok=True)
    STATS.write_text(json.dumps(stats, indent=1))
    return stats


# ------------------------------------------------------------------ CLI


def backfill(now: Optional[int] = None) -> dict:
    scanned = scan_outputs()
    feed = CoinbaseFeed()
    if scanned:
        feed.warm(min(s["ts"] for s in scanned) - 7200, (now or int(time.time())) + 3600)
    added = record(scanned, feed=feed, now=now)
    filled = settle(feed=feed, now=now)
    stats = write_stats()
    return {"added": added, "settled": filled, "stats": stats}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Paper tracker do AutoHedge")
    ap.add_argument("cmd", choices=["record", "settle", "stats", "run", "backfill"])
    args = ap.parse_args(argv)

    if args.cmd == "record":
        added = record()
        print(f"registros novos: {added}")
    elif args.cmd == "settle":
        print(f"pontos liquidados: {settle()}")
    elif args.cmd == "stats":
        s = write_stats()
        print(json.dumps({"records": s["records"], "by_horizon": s["by_horizon"]}, indent=1))
    elif args.cmd == "run":
        added = record()
        filled = settle()
        s = write_stats()
        print(f"novos={added} liquidados={filled} total={s['records']['total']}")
    else:
        r = backfill()
        print(f"backfill: +{r['added']} registros, {r['settled']} pontos liquidados")
        print(json.dumps(r["stats"]["by_horizon"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
