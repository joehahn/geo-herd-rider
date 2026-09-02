#!/usr/bin/env python3
"""check_pull_gaps.py — is any day MISSING from the daily news pull?

The pull is unrepeatable, so a skipped day is a permanent hole in the corpus. It is also silent:
2026-08-15's cron crashed on an Anthropic 400 and the gap was not noticed for eighteen days, because
nothing ever compared the files on disk to the calendar. The traceback was in cron.log the whole time.

Prints one line on a healthy sequence and a loud block on a gap, so the daily cron log carries the
answer every morning rather than the evidence for it.

    scripts/check_pull_gaps.py            # exit 1 if a day is missing
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "forward" / "daily"


def main() -> int:
    files = sorted(glob.glob(str(DAILY / "*.json")))
    if not files:
        print("  pull gaps: no daily files at all"); return 1
    days = [dt.date.fromisoformat(Path(f).stem) for f in files]
    have = set(days)
    want = {days[0] + dt.timedelta(days=i) for i in range((days[-1] - days[0]).days + 1)}
    missing = sorted(want - have)
    # a day whose file exists but holds nothing is a gap too, just a quieter one
    empty = []
    for f in files:
        try:
            if not (json.loads(Path(f).read_text()).get("pool") or []):
                empty.append(Path(f).stem)
        except Exception:  # noqa: BLE001
            empty.append(Path(f).stem + " (unreadable)")
    back = [Path(f).stem for f in files
            if json.loads(Path(f).read_text()).get("backfilled")]
    # ONLY POST-HANDOFF GAPS COST THE CORPUS ANYTHING. Before the handoff the bootstrap reads GKG,
    # not the daily pull, so a missing pre-handoff day is a hole in this directory and in nothing
    # else. Saying so keeps the loud line loud: 2026-07-11/12 have been missing since the pull was
    # first stood up and are not worth recovering, 2026-08-15 is.
    try:
        import sys as _s; _s.path.insert(0, str(ROOT / "src"))
        import bootstrap_corpus as _bc
        ho = dt.date.fromisoformat(_bc.HANDOFF)
    except Exception:  # noqa: BLE001
        ho = days[0]
    live_missing = [d for d in missing if d >= ho]
    live_empty = [e for e in empty if e[:10] >= ho.isoformat()]
    span = f"{days[0]} .. {days[-1]} ({len(have)} of {len(want)} days)"
    if not live_missing and not live_empty:
        print(f"  pull sequence complete in the corpus window (>= handoff {ho}): {span}"
              + (f"; backfilled {', '.join(back)}" if back else ""))
        return 0
    print(f"  !! PULL GAPS in {span}  (handoff {ho}; only days >= it are in the corpus)")
    if live_missing:
        print(f"  !! MISSING {len(live_missing)} corpus day(s): {', '.join(str(d) for d in live_missing)}")
        print(f"  !! recover with: .venv/bin/python src/forward.py --pull --anchor {live_missing[0]}")
    if live_empty:
        print(f"  !! EMPTY {len(live_empty)} corpus day(s): {', '.join(live_empty)}")
    _pre = [str(d) for d in missing if d < ho] + [e for e in empty if e[:10] < ho.isoformat()]
    if _pre:
        print(f"     (pre-handoff, not in the corpus, not worth recovering: {', '.join(_pre)})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
