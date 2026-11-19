"""CLI do loop de pesquisa: roda o walk-forward e imprime o veredito."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from .data import GRANULARITIES, load_bars, load_fear
from .engine import (
    DEFAULT_FEE_BUY,
    DEFAULT_FEE_SELL,
    MIN_TRADES_OOS,
    monte_carlo_p,
    simulate,
    verdict,
    walk_forward_selection,
)
from .strategies import STRATEGIES, build_positions, expand_grid

REPORTS = Path(__file__).resolve().parent.parent.parent / "reports"


def buy_and_hold(bars) -> float:
    if len(bars) < 2:
        return 0.0
    return bars[-1].close / bars[0].close - 1


def evaluate(name: str, bars, fear_map, folds: int = 4, min_train_trades: int = 10) -> dict:
    def make_positions(cfg: dict, bars_sub) -> List[int]:
        fear_sub = [fear_map.get(b.ts // 86400) for b in bars_sub]
        return build_positions(name, cfg, bars_sub, fear_sub)

    configs = expand_grid(name)
    res = walk_forward_selection(bars, make_positions, configs, folds=folds,
                                 min_train_trades=min_train_trades)
    oos = res["oos"]

    mc_p = None
    if res["oos_bars"] and res["oos_positions"]:
        mc_p = monte_carlo_p(res["oos_bars"], res["oos_positions"], oos.total_return)

    status, why = verdict(oos, res["per_fold"], mc_p)
    bh = buy_and_hold(res["oos_bars"]) if res["oos_bars"] else 0.0

    return {
        "strategy": name,
        "configs_testados": len(configs),
        "status": status,
        "motivo": why,
        "oos": oos.as_dict(),
        "monte_carlo_p": None if mc_p is None else round(mc_p, 4),
        "buy_hold_oos": round(bh * 100, 2),
        "folds": res["per_fold"],
        "escolhidos": res["chosen"],
    }


def run(days: int = 400, folds: int = 4, only: Optional[List[str]] = None,
        out: Optional[str] = None, min_train_trades: int = 10,
        granularity: int = 21600) -> dict:
    t0 = time.time()
    bars = load_bars(days=days, granularity=granularity)
    if len(bars) < 120:
        raise SystemExit(f"histórico insuficiente: {len(bars)} barras diárias")
    fear_map = load_fear(days)

    results = []
    for name in (only or list(STRATEGIES)):
        results.append(evaluate(name, bars, fear_map, folds, min_train_trades))
        r = results[-1]
        print(f"  {name:14s} {r['status']:9s} OOS {r['oos']['total_return']:>7.2f}%  "
              f"trades {r['oos']['trades']:>3d}  p={r['monte_carlo_p']}", flush=True)

    report = {
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "granularidade": GRANULARITIES.get(granularity, str(granularity)),
        "dias_de_dados": len(bars),
        "periodo": [time.strftime("%Y-%m-%d", time.gmtime(bars[0].ts)),
                    time.strftime("%Y-%m-%d", time.gmtime(bars[-1].ts))],
        "custos": {"fee_compra_pct": DEFAULT_FEE_BUY * 100, "fee_venda_pct": DEFAULT_FEE_SELL * 100},
        "min_trades_oos": MIN_TRADES_OOS,
        "resultados": results,
        "duracao_s": round(time.time() - t0, 1),
    }
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=1))
        print(f"\nrelatório: {path}")
    return report


def print_table(report: dict) -> None:
    print(f"dados: {report['dias_de_dados']} barras de {report['granularidade']} ({report['periodo'][0]} → {report['periodo'][1]})")
    print(f"custos: {report['custos']['fee_compra_pct']}% compra + {report['custos']['fee_venda_pct']}% venda")
    print(f"\n{'estratégia':14s} {'status':9s} {'OOS':>8s} {'buy&hold':>9s} {'trades':>6s} "
          f"{'acerto':>7s} {'DD':>6s} {'MC p':>6s}  motivo")
    print("-" * 108)
    for r in report["resultados"]:
        o = r["oos"]
        print(f"{r['strategy']:14s} {r['status']:9s} {o['total_return']:>7.2f}% {r['buy_hold_oos']:>8.2f}% "
              f"{o['trades']:>6d} {o['win_rate']:>6.1f}% {o['max_drawdown']:>5.1f}% "
              f"{str(r['monte_carlo_p']):>6s}  {r['motivo']}")
    aprovadas = [r for r in report["resultados"] if r["status"] == "aprovado"]
    print(f"\n{len(aprovadas)} de {len(report['resultados'])} estratégias passaram nos três filtros "
          f"(amostra ≥ {report['min_trades_oos']} trades, OOS > 0, Monte Carlo p ≤ 0,10).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Loop de pesquisa do AutoHedge")
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--min-train-trades", type=int, default=10)
    ap.add_argument("--granularity", type=int, default=21600,
                    help="3600 (1h), 21600 (6h) ou 86400 (1d)")
    args = ap.parse_args(argv)

    print(f"carregando {args.days} dias de SOL…")
    report = run(days=args.days, folds=args.folds, only=args.only, out=args.json,
                 min_train_trades=args.min_train_trades, granularity=args.granularity)
    print_table(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
