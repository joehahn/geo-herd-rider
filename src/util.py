"""util.py — small shared helpers for the firehose architecture.

These were previously homed in the (now-retired) decision-tree modules map_event.py /
synthesize.py; relocated here so the firehose + forward path own them with no dependency on the
deleted code. Zero third-party deps beyond pandas.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent

CRON_HOUR, CRON_MIN = 16, 30          # weekly scan decision point: Friday 16:30 ET
MAX_TEXT = 320                        # truncate each post in a prompt


def load_dotenv() -> None:
    """Load KEY=VALUE lines from a repo-root .env into os.environ (no dependency, won't
    override anything already set). Lets a cloner just edit .env and run."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def scan_anchors(start: str, end: str, period_days: int = 7) -> list[pd.Timestamp]:
    """Rebalance/scan decision points spanning the window, at 16:30 ET (after-close cron).

    `period_days` is the single cadence knob (`rebalance_days`): the gap between scans AND the
    natural trailing news window each scan reads (see firehose). The weekly default (7) anchors
    on Fridays (the canonical after-close weekly cron); any other cadence steps every N days."""
    freq = "W-FRI" if period_days == 7 else f"{period_days}D"
    pts = pd.date_range(start, end, freq=freq, tz="America/New_York")
    return [p.normalize() + pd.Timedelta(hours=CRON_HOUR, minutes=CRON_MIN) for p in pts]


def news_domains() -> list[str]:
    """Domains the news search prefers — parsed from news_sources.md (user-managed)."""
    f = REPO_ROOT / "news_sources.md"
    if not f.exists():
        return []
    doms = re.findall(r"https?://(?:www\.)?([a-z0-9.\-]+\.[a-z]{2,})", f.read_text())
    return sorted(set(doms))
