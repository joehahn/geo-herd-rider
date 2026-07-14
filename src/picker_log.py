"""picker_log.py — OPTIONAL decision logging for the two culls, so we can AUDIT whether they choose wisely.

OFF by default (zero cost, no file touched). A CLI flag on the backtest/forward harness calls enable(path);
then every cull decision is appended as one JSONL record capturing FULL inputs + outputs:
  - kind="scout": the inflow cull — all candidates the scout proposed vs which were admitted (max_events).
  - kind="agent": the portfolio cull — every live event's metadata (catalyst/milestones/exit/weeks-alive/
    cum-P&L) the picker saw, plus the ordered keep-list and what got culled.
Read the JSONL back to confirm the picker favored building catalysts, demoted crested winners, etc.
"""
from __future__ import annotations
import json
import threading
from pathlib import Path

_state: dict = {"path": None}
_lock = threading.Lock()


def enable(path) -> None:
    """Turn logging ON, writing to `path` (JSONL, appended). Called from a CLI flag; no-op targets stay off."""
    _state["path"] = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def enabled() -> bool:
    return _state["path"] is not None


def log(kind: str, record: dict) -> None:
    """Append one decision record. Silent no-op when logging is disabled."""
    if not _state["path"]:
        return
    with _lock, open(_state["path"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, **record}, default=str) + "\n")
