"""
AutoHedge Bot - Agentes adaptados pra rodar com DeepSeek via OpenCode API.
Substitui a dependência da library `swarms` (OpenAI) por chamadas diretas
a API compatível com OpenAI.
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI

from autohedge.prompts import (
    DIRECTOR_PROMPT,
    EXECUTION_PROMPT,
    QUANT_PROMPT,
    RISK_PROMPT,
    SENTIMENT_PROMPT,
)

# ── Config ───────────────────────────────────────────────────────────

OPENCODE_BASE_URL = os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1")
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "")
MODEL = os.getenv("AUTOHEDGE_MODEL", "deepseek-v4-flash")

_NOW = datetime.now()
_DATE_TIME_LINE = _NOW.strftime("%A, %B %d, %Y at %H:%M")
_SYSTEM_SUFFIX = f"\n\nCurrent date and time (use this as now): {_DATE_TIME_LINE.strip()}"


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


# ── Agentes especializados ───────────────────────────────────────────

sentiment_agent = Agent(
    name="Sentiment-Agent",
    system_prompt=SENTIMENT_PROMPT,
)

risk_agent = Agent(
    name="Risk-Manager",
    system_prompt=RISK_PROMPT.strip()
    + "\n\nWhen you receive a message, it will contain:\n"
    "Stock, Thesis, Quant Analysis.\n\n"
    "Provide risk assessment including:\n"
    "1. Recommended position size\n"
    "2. Maximum drawdown risk\n"
    "3. Market risk exposure\n"
    "4. Overall risk score",
)

execution_agent = Agent(
    name="Execution-Agent",
    system_prompt=EXECUTION_PROMPT.strip()
    + "\n\nWhen you receive a message, it will contain:\n"
    "Stock, Thesis, Risk Assessment.\n\n"
    "Generate trade order including:\n"
    "1. Order type (market/limit)\n"
    "2. Quantity\n"
    "3. Entry price\n"
    "4. Stop loss\n"
    "5. Take profit\n"
    "6. Time in force",
)

quant_agent = Agent(
    name="Quant-Analyst",
    system_prompt=QUANT_PROMPT.strip()
    + "\n\nWhen you receive a message, it will contain:\n"
    "Stock and Thesis from your Director.\n\n"
    "Generate quantitative analysis with: ticker, technical_score (0-1), "
    "volume_score (0-1), trend_strength (0-1), volatility, "
    "probability_score (0-1), key_levels (support, resistance, pivot).",
)

ALL_AGENTS = [sentiment_agent, risk_agent, execution_agent, quant_agent]

director_agent = Agent(
    name="Trading-Director",
    system_prompt=DIRECTOR_PROMPT,
)


# ── Pipeline runner ──────────────────────────────────────────────────

def run_full_cycle(task: str) -> Dict[str, Any]:
    """
    Executa o pipeline completo: Director → Quant → Risk → Execution.
    Retorna dict com resultados de cada etapa.
    """
    from loguru import logger

    results: Dict[str, Any] = {
        "task": task,
        "timestamp": _NOW.isoformat(),
        "steps": {},
    }

    # Step 1: Director gera tese
    logger.info("Director Agent: gerando tese de mercado...")
    director_output = director_agent.run(task)
    results["steps"]["director"] = director_output
    logger.success("Director concluído")

    # Step 2: Quant analisa
    logger.info("Quant Agent: analisando...")
    quant_output = quant_agent.run(f"Task: {task}\n\nThesis from Director:\n{director_output}")
    results["steps"]["quant"] = quant_output
    logger.success("Quant concluído")

    # Step 3: Risk avalia
    logger.info("Risk Manager: avaliando risco...")
    risk_output = risk_agent.run(
        f"Stock: General Market\nThesis: {director_output}\nQuant Analysis: {quant_output}"
    )
    results["steps"]["risk"] = risk_output
    logger.success("Risk Manager concluído")

    # Step 4: Execution gera ordem (apenas recomendação, sem executar)
    logger.info("Execution Agent: gerando recomendação...")
    exec_output = execution_agent.run(
        f"Stock: General Market\nThesis: {director_output}\nRisk Assessment: {risk_output}"
    )
    results["steps"]["execution"] = exec_output
    logger.success("Execution concluído")

    results["status"] = "complete"
    return results


if __name__ == "__main__":
    result = run_full_cycle("Analyze the crypto market and provide a thesis on current trends and opportunities.")
    print(json.dumps(result, indent=2, ensure_ascii=False))
