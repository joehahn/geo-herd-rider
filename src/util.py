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


# PWR's cadence vocabulary, adopted 2026-08-09 so the two repos name the same thing the same way.
# A NAMED period, not a raw day count: "biweekly" says what it means and reads the same in both
# projects' profiles. It REPLACED the old numeric `rebalance_days` knob, which is retired.
REBALANCE_PERIODS = {"weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 91}


def resolve_cadence(fm: dict) -> int:
    """Scan/rebalance cadence in DAYS, from `rebalance_period`. THE only way to read the cadence.

    FAILS LOUD rather than falling back. The retired `rebalance_days` used to be the fallback here, and
    because optimizer's defaults injected 7 for it, any caller reading the raw key silently got 7 --
    including for a monthly run whose scans are 30 days apart. That produced two wrong numbers in the
    CBT dashboard before anyone noticed (2026-08-14), because 7 is a plausible cadence and nothing
    complained. A missing cadence is a config error worth stopping for, not worth guessing at."""
    per = str(fm.get("rebalance_period") or "").strip().lower()
    if not per:
        raise ValueError("rebalance_period is not set. It replaced the retired numeric rebalance_days; "
                         f"set one of {sorted(REBALANCE_PERIODS)} in the investor profile.")
    if per not in REBALANCE_PERIODS:
        raise ValueError(f"rebalance_period={per!r}; expected one of {sorted(REBALANCE_PERIODS)}")
    return REBALANCE_PERIODS[per]


def scan_anchors(start: str, end: str, period_days: int = 7) -> list[pd.Timestamp]:
    """Rebalance/scan decision points spanning the window, at 16:30 ET (after-close cron).

    `period_days` is the cadence in days (see resolve_cadence): the gap between scans AND the
    natural trailing news window each scan reads (see firehose). 7 and 14 anchor on FRIDAYS -- the
    canonical after-close cron, and it keeps a biweekly series on the same weekday as a weekly one
    so the two are directly comparable. Other cadences step every N days from `start`."""
    freq = {7: "W-FRI", 14: "2W-FRI"}.get(period_days, f"{period_days}D")
    pts = pd.date_range(start, end, freq=freq, tz="America/New_York")
    return [p.normalize() + pd.Timedelta(hours=CRON_HOUR, minutes=CRON_MIN) for p in pts]


def news_domains() -> list[str]:
    """Domains the news search prefers — parsed from news_sources.md (user-managed)."""
    f = REPO_ROOT / "news_sources.md"
    if not f.exists():
        return []
    doms = re.findall(r"https?://(?:www\.)?([a-z0-9.\-]+\.[a-z]{2,})", f.read_text())
    return sorted(set(doms))
