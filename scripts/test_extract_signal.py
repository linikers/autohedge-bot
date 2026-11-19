#!/usr/bin/env python3
"""Testa o extract_signal real contra o output salvo do Execution/Risk."""
import json
import sys

sys.path.insert(0, '/root/autohedge-bot')
from run import extract_signal

with open('/root/autohedge-bot/outputs/analise_20260809_060635.json') as f:
    a = json.load(f)

exec_out = a['steps'].get('execution', '')
risk_out = a['steps'].get('risk', '')

print('=== TESTE extract_signal ===')
print('execução:', extract_signal(exec_out, risk_out))

# Testar variações do regex pra diagnosticar o bug do markdown
import re
m1 = re.search(r'action:\s*(buy|sell|hold)', exec_out, re.IGNORECASE)
print('regex atual casa?', m1.group(1) if m1 else 'NÃO')
m2 = re.search(r'action:\s*\**\s*(buy|sell|hold)', exec_out, re.IGNORECASE)
print('regex com \\** (markdown) casa?', m2.group(1) if m2 else 'NÃO')

# De onde vem o is_bullish no fallback?
full_text = exec_out.lower() + ' ' + risk_out.lower()
bull_words = [w for w in ["buy", "long", "comprar", "accumulate", "entrada", "bullish", "optimistic", "otimista"] if w in full_text]
bear_words = [w for w in ["sell", "short", "vender", "saída", "baixa", "bearish", "pessimistic", "cautela"] if w in full_text]
print('palavras bullish no full_text:', bull_words)
print('palavras bearish no full_text:', bear_words)
print()
print('=== EXECUTION (completo) ===')
print(exec_out)
