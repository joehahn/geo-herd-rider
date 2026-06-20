"""costs.py — LLM token + dollar accounting for the curator pipeline.

Every Anthropic call in the solution (the trigger scout, the causal-ladder mapper) routes
its usage through `record()`, which prices it and appends a row to a ledger CSV. The dashboard
reads the ledger so the running cost of the LLM stages is visible — the lever that makes the
Opus-vs-Haiku-vs-OpenRouter tradeoff legible (CLAUDE.md model-choice note).

Prices are $/token, cached from the Anthropic pricing table (2026-06). Server-side web search
is billed per search on top of tokens, and is the DOMINANT cost when laddering on a cheap
model — so we count it explicitly.
"""
from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER = REPO_ROOT / "data" / "llm_costs.csv"
_LOCK = threading.Lock()

# $ per token (input, output). Substring-matched so aliases/snapshots resolve.
PRICES = {
    "fable-5":    (10e-6, 50e-6),
    "opus":       (5e-6, 25e-6),
    "sonnet":     (3e-6, 15e-6),
    "haiku":      (1e-6, 5e-6),
}
CACHE_READ_FACTOR = 0.1          # cache reads bill ~0.1x input
WEB_SEARCH_PER_CALL = 10.0 / 1000.0   # ~$10 / 1000 searches

_COLS = ["ts", "stage", "model", "label", "input_tokens", "output_tokens",
         "cache_read_tokens", "web_searches", "cost_usd"]


def _rate(model: str) -> tuple[float, float]:
    for key, rate in PRICES.items():
        if key in model:
            return rate
    return PRICES["opus"]  # safe default (most expensive) if unknown


def extract(usage) -> dict:
    """Pull the fields we bill on out of an Anthropic `response.usage` object,
    tolerating missing attributes across model families."""
    web = 0
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        web = getattr(stu, "web_search_requests", 0) or 0
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "web_searches": web,
    }


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read_tokens: int = 0, web_searches: int = 0) -> float:
    rin, rout = _rate(model)
    return (input_tokens * rin + output_tokens * rout
            + cache_read_tokens * rin * CACHE_READ_FACTOR
            + web_searches * WEB_SEARCH_PER_CALL)


def record(stage: str, model: str, label: str, usage: dict) -> float:
    """Price one call's usage and append it to the ledger. `usage` is an extract() dict
    (or anything with the same keys). Returns the dollar cost. Thread-safe."""
    c = cost_usd(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                 usage.get("cache_read_tokens", 0), usage.get("web_searches", 0))
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "stage": stage, "model": model, "label": label,
           "cost_usd": round(c, 5), **{k: usage.get(k, 0) for k in _COLS if k in usage}}
    with _LOCK:
        new = not LEDGER.exists()
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_COLS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, 0) for k in _COLS})
    return c


def summary() -> dict:
    """Aggregate the ledger: total $ and per-stage / per-model breakdown (for the dashboard)."""
    import pandas as pd
    if not LEDGER.exists():
        return {"total_usd": 0.0, "by_stage": {}, "by_model": {}, "n_calls": 0}
    df = pd.read_csv(LEDGER)
    return {
        "total_usd": round(float(df["cost_usd"].sum()), 4),
        "n_calls": int(len(df)),
        "by_stage": df.groupby("stage")["cost_usd"].sum().round(4).to_dict(),
        "by_model": df.groupby("model")["cost_usd"].sum().round(4).to_dict(),
    }
