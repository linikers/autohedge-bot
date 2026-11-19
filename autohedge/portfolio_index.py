#!/usr/bin/env python3
"""
Update portfolio index.json after each analysis cycle.
Reads all analysis files and creates a summary index for the dashboard.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

PORTFOLIO_DIR = Path(__file__).parent.parent / "portfolio"
OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
INDEX_PATH = PORTFOLIO_DIR / "index.json"


def update_index(trade_result: dict | None = None, analysis_path: str | None = None):
    PORTFOLIO_DIR.mkdir(exist_ok=True)

    # Load existing index
    index = {"trades": [], "analyses": []}
    if INDEX_PATH.exists():
        try:
            index = json.loads(INDEX_PATH.read_text())
        except:
            pass

    # Add this analysis to the list
    if analysis_path:
        analysis_file = Path(analysis_path)
        if analysis_file.exists():
            try:
                data = json.loads(analysis_file.read_text())
                timestamp = data.get("timestamp", datetime.now().isoformat())
                steps = data.get("steps", {})
                director = steps.get("director", "")[:200]
                index["analyses"].append({
                    "timestamp": timestamp,
                    "file": analysis_file.name,
                    "director_preview": director,
                    "trade_status": trade_result.get("status", "skipped") if trade_result else "skipped",
                })
            except:
                pass

    # Add trade if executed
    if trade_result and trade_result.get("status") == "executed":
        trade_entry = {
            "timestamp": datetime.now().isoformat(),
            "status": "executed",
            "amount_usd": trade_result.get("amount_usd", 0),
            "out_usdc": trade_result.get("out_usdc", 0),
            "out_sol": trade_result.get("out_sol", 0),
            "signature": trade_result.get("signature", ""),
            "sol_price": trade_result.get("sol_price", 0),
            "direction": trade_result.get("direction", ""),
            "type": trade_result.get("type", ""),
        }
        index["trades"].append(trade_entry)
    elif trade_result and trade_result.get("status") == "skipped":
        index["analyses"][-1]["trade_status"] = "skipped"

    # Keep only last 100 entries
    index["analyses"] = index["analyses"][-100:]
    index["trades"] = index["trades"][-100:]

    INDEX_PATH.write_text(json.dumps(index, indent=2))
    return index


if __name__ == "__main__":
    # CLI mode: pass trade result as JSON string and analysis path
    import sys
    trade_json = sys.argv[1] if len(sys.argv) > 1 else None
    analysis_path = sys.argv[2] if len(sys.argv) > 2 else None
    trade = json.loads(trade_json) if trade_json else None
    update_index(trade, analysis_path)
    print("Index updated")
