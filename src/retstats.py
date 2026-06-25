"""retstats.py — tiny shared accumulator for retrieval-health metrics.

gdelt.pool() and wayback.enrich() merge their sections into one JSON file (default
data/windows/retrieval_stats.json) that build_dashboard reads into the "Retrieval health"
panel. Per-RUN metrics (download times, error counts) — they describe the run that produced
the current book. Atomic write so a crashed run can't corrupt it."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PATH = "data/windows/retrieval_stats.json"


def merge(path: str | None, section: str, payload: dict) -> None:
    """Read-modify-write `path`, setting d[section] = payload. No-op if path is falsy."""
    if not path:
        return
    d: dict = {}
    if os.path.exists(path):
        try:
            d = json.loads(Path(path).read_text())
        except Exception:  # noqa: BLE001 — a corrupt stats file must not sink a run
            d = {}
    d[section] = payload
    tmp = f"{path}.tmp"
    Path(tmp).write_text(json.dumps(d, indent=2))
    os.replace(tmp, path)


def load(path: str | None) -> dict:
    if path and os.path.exists(path):
        try:
            return json.loads(Path(path).read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}
