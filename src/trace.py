"""trace.py — OPTIONAL full-transcript logging for a playtest/audit run.

Off by default (zero overhead). When a `--trace <path>` CLI flag calls `enable(path)`, every LLM call
(system prompt + user prompt + response + the web-searches the model issued) and every deterministic
search query (GDELT / Tavily) is appended as one JSON record per line to a JSONL file. Read it back with
`json.loads` per line, or `jq`. The file can be a few MB per multi-week run — it's local + gitignored.

    import trace
    trace.enable("data/backtest_gdelt/transcript.jsonl")   # from the CLI flag
    trace.log("llm", stage=..., system=..., user=..., response=...)
    ...
    trace.disable()
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

_F = None          # open file handle when tracing is active, else None
_PATH: str | None = None


def enable(path: str) -> None:
    """Start appending trace records to `path` (JSONL). Idempotent-ish: re-enabling reopens."""
    global _F, _PATH
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    _F = open(path, "a", encoding="utf-8")
    _PATH = path


def active() -> bool:
    return _F is not None


def path() -> str | None:
    return _PATH


def log(kind: str, **fields) -> None:
    """Append one record: {ts, kind, **fields}. No-op when tracing is off (cheap guard)."""
    if _F is None:
        return
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
    _F.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
    _F.flush()


def disable() -> None:
    global _F, _PATH
    if _F is not None:
        _F.close()
    _F = None
    _PATH = None
