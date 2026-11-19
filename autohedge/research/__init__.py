"""
AutoHedge — loop de pesquisa.

O ponto deste módulo não é achar uma estratégia: é **descartar hipótese rápido e
sem se enganar**. Três travas contra auto-engano:

1. Out-of-sample de verdade (walk-forward): parâmetro é escolhido no treino e
   avaliado no bloco seguinte, que ele nunca viu.
2. Amostra mínima: menos de 30 trades no out-of-sample é reprovado, ponto.
3. Monte Carlo: o resultado tem que bater entradas aleatórias com o mesmo número
   de trades e o mesmo tempo de exposição. Se não bater, é sorte.

Uso:
    python -m autohedge.research.run --days 400
    python -m autohedge.research.run --days 400 --json reports/research.json
"""
