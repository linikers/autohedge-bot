"""
AutoHedge Bot - Agentes adaptados pra rodar com DeepSeek via OpenCode API.
Pipeline: Director → Quant → Risk → Execution
Injeta dados reais de mercado antes de cada ciclo.
"""

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI

from autohedge.market_data import fetch_all_market_data
from autohedge.prompts import (
    DIRECTOR_PROMPT,
    EXECUTION_PROMPT,
    QUANT_PROMPT,
    RISK_PROMPT,
)

# ── Config ───────────────────────────────────────────────────────────

OPENCODE_BASE_URL = os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1")
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "")
MODEL = os.getenv("AUTOHEDGE_MODEL", "deepseek-v4-flash")

_NOW = datetime.now()
_DATE_TIME_LINE = _NOW.strftime("%A, %B %d, %Y at %H:%M")
_SYSTEM_SUFFIX = f"\n\nCurrent date and time (use this as now): {_DATE_TIME_LINE.strip()}"

# Retry config
MAX_DIRECTOR_RETRIES = 2


# ── Client ───────────────────────────────────────────────────────────

def _get_client() -> OpenAI:
    return OpenAI(base_url=OPENCODE_BASE_URL, api_key=OPENCODE_API_KEY)


# ── Agent base ───────────────────────────────────────────────────────

class Agent:
    """Agente simples que chama DeepSeek via API compatível com OpenAI."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: Optional[List[Callable]] = None,
        max_loops: int = 1,
    ):
        self.name = name
        self.system_prompt = system_prompt + _SYSTEM_SUFFIX
        self.tools = tools or []
        self.max_loops = max_loops

    def run(self, task: str, **kwargs) -> str:
        client = _get_client()
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]

        for attempt in range(self.max_loops):
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=8000,
            )
            content = resp.choices[0].message.content or ""

            # Se o agente tem tools e pediu tool calls, processa
            if self.tools and "<tool>" in content:
                content = self._handle_tool_calls(content, client, messages)

            messages.append({"role": "assistant", "content": content})

        return content

    def _handle_tool_calls(self, content: str, client: Any, messages: List) -> str:
        """Processa chamadas de ferramentas no formato <tool>nome(args)</tool>."""
        tool_pattern = re.compile(r"<tool>(\w+)\((.*?)\)</tool>")
        for match in tool_pattern.finditer(content):
            tool_name = match.group(1)
            tool_args = match.group(2)
            for tool_fn in self.tools:
                if tool_fn.__name__ == tool_name:
                    try:
                        result = tool_fn(tool_args)
                        messages.append({"role": "tool", "content": str(result), "name": tool_name})
                    except Exception as e:
                        messages.append({"role": "tool", "content": f"Error: {e}", "name": tool_name})

        # Segunda chamada com resultado das tools
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=4000,
        )
        return resp.choices[0].message.content or ""


# ── Agentes especializados (crypto-specific) ────────────────────────

risk_agent = Agent(
    name="Risk-Manager",
    system_prompt=RISK_PROMPT.strip()
    + "\n\nYou are evaluating RISK for a CRYPTO PORTFOLIO (Solana/SOL)."
    "\nWhen you receive a message, it will contain:"
    "\n- Real market data (prices, volume, Fear & Greed)"
    "\n- Portfolio state (SOL/USDC balances, trade history)"
    "\n- Thesis from Director + Quant Analysis"
    "\n\nProvide risk assessment including:"
    "\n1. Recommended position size (considering current wallet balance)"
    "\n2. Maximum drawdown risk"
    "\n3. Market risk exposure"
    "\n4. Overall risk score"
    "\n5. CLEAR VERDICT: 'APPROVED' or 'REJECTED' with reason",
)

execution_agent = Agent(
    name="Execution-Agent",
    system_prompt=EXECUTION_PROMPT.strip()
    + "\n\nYou are generating trade orders for CRYPTO (Solana/SOL) on Jupiter DEX."
    "\nWhen you receive a message, it will contain:"
    "\n- Real market data (current SOL price, wallet balance)"
    "\n- Thesis from Director + Risk Assessment"
    "\n\nGenerate trade order including:"
    "\n1. Action: BUY (swap USDC→SOL) or SELL (swap SOL→USDC)"
    "\n2. Amount in USD (max $0.50 per trade)"
    "\n3. Entry price (use REAL current price from market data)"
    "\n4. Stop loss"
    "\n5. Take profit"
    "\n6. Reasoning (1-2 sentences)",
)

quant_agent = Agent(
    name="Quant-Analyst",
    system_prompt=QUANT_PROMPT.strip()
    + "\n\nYou are analyzing CRYPTO assets (primarily Solana/SOL, but also BTC and ETH for context)."
    "\nWhen you receive a message, it will contain:"
    "\n- Real market data (prices, 24h/7d/30d changes, volume, market cap)"
    "\n- Director's thesis"
    "\n\nGenerate quantitative analysis with:"
    "\n- technical_score (0-1)"
    "\n- volume_score (0-1)"
    "\n- trend_strength (0-1)"
    "\n- volatility"
    "\n- probability_score (0-1)"
    "\n- key_levels (support, resistance, pivot)"
    "\n\nUse the REAL prices provided. Do not invent price data.",
)

director_agent = Agent(
    name="Trading-Director",
    system_prompt=DIRECTOR_PROMPT.strip()
    + "\n\nYou are a CRYPTO Trading Director specializing in Solana (SOL)."
    "\nYou receive REAL market data — use it. Do NOT invent prices."
    "\nYour thesis must include:"
    "\n1. Market direction (bullish/bearish/neutral)"
    "\n2. Key support/resistance levels (use REAL prices from data)"
    "\n3. Recommended action (BUY SOL / SELL SOL / HOLD)"
    "\n4. Risk factors",
)


# ── Pipeline runner ──────────────────────────────────────────────────

def run_full_cycle(task: str) -> Dict[str, Any]:
    """
    Executa o pipeline completo: Director → Quant → Risk → Execution.
    Injeta dados reais de mercado antes de cada ciclo.
    Retorna dict com resultados de cada etapa.
    """
    from loguru import logger

    # 1. Busca dados reais de mercado
    logger.info("Buscando dados reais de mercado...")
    market_data = fetch_all_market_data()
    logger.success(f"Dados obtidos: SOL ${_extract_sol_price(market_data)}")

    results: Dict[str, Any] = {
        "task": task,
        "timestamp": _NOW.isoformat(),
        "market_data_snapshot": market_data,
        "steps": {},
    }

    # 2. Director gera tese (com dados reais + retry)
    director_output = ""
    for attempt in range(MAX_DIRECTOR_RETRIES + 1):
        logger.info(f"Director Agent: gerando tese de mercado (tentativa {attempt + 1})...")
        director_prompt = (
            f"Task: {task}\n\n"
            f"## DADOS REAIS DE MERCADO:\n{market_data}\n\n"
            f"Analise os dados REAIS acima. Use os preços e variações fornecidos."
        )
        director_output = director_agent.run(director_prompt)

        if len(director_output.strip()) > 100:
            logger.success("Director concluído com tese válida")
            break
        else:
            logger.warning(f"Director retornou saída muito curta ({len(director_output)} chars), tentando novamente...")
            if attempt < MAX_DIRECTOR_RETRIES:
                time.sleep(2)

    results["steps"]["director"] = director_output

    # 3. Quant analisa (com dados reais)
    logger.info("Quant Agent: analisando...")
    quant_prompt = (
        f"## DADOS REAIS DE MERCADO:\n{market_data}\n\n"
        f"## Tese do Director:\n{director_output}\n\n"
        f"Faça análise quantitativa usando os preços REAIS fornecidos acima."
    )
    quant_output = quant_agent.run(quant_prompt)
    results["steps"]["quant"] = quant_output
    logger.success("Quant concluído")

    # 4. Risk avalia (com contexto do portfólio)
    logger.info("Risk Manager: avaliando risco...")
    risk_prompt = (
        f"## DADOS REAIS DE MERCADO:\n{market_data}\n\n"
        f"## Tese do Director:\n{director_output}\n\n"
        f"## Análise Quantitativa:\n{quant_output}\n\n"
        f"Avalie o risco considerando o saldo REAL da carteira e o histórico de trades."
    )
    risk_output = risk_agent.run(risk_prompt)
    results["steps"]["risk"] = risk_output
    logger.success("Risk Manager concluído")

    # 5. Execution gera recomendação (com preço real)
    logger.info("Execution Agent: gerando recomendação...")
    exec_prompt = (
        f"## DADOS REAIS DE MERCADO:\n{market_data}\n\n"
        f"## Tese do Director:\n{director_output}\n\n"
        f"## Avaliação de Risco:\n{risk_output}\n\n"
        f"Gere a ordem de trade usando o preço REAL atual."
    )
    exec_output = execution_agent.run(exec_prompt)
    results["steps"]["execution"] = exec_output
    logger.success("Execution concluído")

    results["status"] = "complete"
    return results


def _extract_sol_price(market_data: str) -> str:
    """Extrai o preço SOL do texto de market data."""
    match = re.search(r"SOL: \$([\d.]+)", market_data)
    return match.group(1) if match else "?"


if __name__ == "__main__":
    result = run_full_cycle("Analise o mercado de criptomoedas com foco em Solana. Forneça tese de mercado, níveis-chave e recomendação de trade.")
    print(json.dumps(result, indent=2, ensure_ascii=False))
