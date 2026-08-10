#!/usr/bin/env python3
"""Testes sintéticos do extract_signal após fix de markdown/word-boundary."""
import sys

sys.path.insert(0, '/root/autohedge-bot')
from run import extract_signal

cases = [
    # (descrição, execution, risk, esperado_action_ou_None)
    ("HOLD com markdown (bug real)", "**Action:** HOLD\n**Reasoning:** não comprar", "Risco favorável, manter posição", None),
    ("BUY limpo sem markdown", "Action: BUY\nAmount: $2.00", "Risco favorável, aprovar execução", "buy"),
    ("SELL limpo sem markdown", "Action: SELL\nAmount: $2.00", "Risco favorável, aprovar execução", "sell"),
    ("BUY com markdown", "**Action:** BUY  \n**Amount:** $1.00", "Risco favorável, aprovar", "buy"),
    ("SELL com markdown", "**Action:** SELL  \n**Amount:** $1.00", "Risco favorável, aprovar", "sell"),
    ("HOLD sem markdown", "Action: HOLD", "Risco favorável", None),
    ("longo prazo NÃO vira buy (bug real)", "**Action:** HOLD — visão de longo prazo", "Risco favorável, aguardar", None),
    ("Risk não aprovou", "Action: BUY", "Risco: cautela, não executar", None),
    ("Risk bearish desempate", "Recomendo vender posição", "Risco favorável para venda, saída recomendada", "sell"),
    ("Neutro", "Sem ação clara", "Risco favorável", None),
    # 10/08/2026: Execution usou rótulo PT "AÇÃO" em tabela markdown (bug real)
    ("AÇÃO HOLD em tabela markdown PT", "| **AÇÃO** | **HOLD** |\n| **AMOUNT** | $0.00 USD |\nSem trade neste ciclo.", "Risco: REJECTED, cautela, não executar", None),
    ("AÇÃO SELL em tabela markdown PT", "| **AÇÃO** | **Venda** |\n| **AMOUNT** | $1.00 USD |", "Risco favorável, aprovar venda", "sell"),
    ("AÇÃO BUY com acento PT", "AÇÃO: Compra\nAmount: $1.00", "Risco favorável, aprovar", "buy"),
    ("guard HOLD no fallback", "Ordem: manutenção, hold posição, sem trade", "Risco favorável", None),
]

passed = 0
for desc, exec_out, risk_out, expected in cases:
    result = extract_signal(exec_out, risk_out)
    got = result['action'] if result else None
    status = "✅" if got == expected else "❌"
    if got == expected:
        passed += 1
    print(f"{status} {desc}: esperado={expected} → got={got} {result.get('reason','') if result else ''}")

print(f"\n{passed}/{len(cases)} passaram")
sys.exit(0 if passed == len(cases) else 1)
