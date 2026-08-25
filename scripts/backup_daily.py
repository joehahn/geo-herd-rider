#!/usr/bin/env python3
"""backup_daily.py — append-only mirror of data/forward/daily/, because the pull is UNREPEATABLE.

Measured 2026-08-24: re-querying Tavily for a window it had already served returned a SMALLER and
partly DIFFERENT set (2 where the cron got 5, one of them an article the cron never had). Tavily is
deterministic within minutes and churns over hours, so a daily file that is lost or overwritten
cannot be reconstructed -- not approximately, not at all. That is the whole reason this exists.

WHAT IT IS NOT: a way to substitute one day for another. Yesterday's articles are yesterday's NEWS;
dropping them into today's bucket would corrupt the publication-date series every panel is keyed to.
This restores a day to ITS OWN last-good copy, nothing else.

NEVER DELETES. A file whose content changed is not overwritten in place -- the previous copy is moved
to superseded/<date>.<sha8>.json first, so a re-pull that turns out worse than what it replaced is
still recoverable. (That case is not hypothetical: a manual pull on 2026-08-24 captured 29 articles
where the cron later captured 51.)

    python scripts/backup_daily.py            # mirror new/changed files
    python scripts/backup_daily.py --verify    # report only, change nothing
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "forward" / "daily"
DST = ROOT / "data" / "forward" / "daily_backup"


def _sha8(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="report only; make no changes")
    a = ap.parse_args(argv)
    if not SRC.exists():
        print(f"no source dir {SRC}", file=sys.stderr)
        return 1
    (DST / "superseded").mkdir(parents=True, exist_ok=True)
    new = changed = same = 0
    for f in sorted(SRC.glob("*.json")):
        b = DST / f.name
        if not b.exists():
            new += 1
            if not a.verify:
                shutil.copy2(f, b)
        elif _sha8(f) != _sha8(b):
            changed += 1
            if not a.verify:
                # keep the old one FIRST, then take the new -- nothing is ever lost
                shutil.move(str(b), str(DST / "superseded" / f"{f.stem}.{_sha8(b)}.json"))
                shutil.copy2(f, b)
            print(f"  CHANGED {f.name}: previous copy kept under superseded/")
        else:
            same += 1
    # a day present in the backup but MISSING from daily/ is the alarm this exists for
    lost = [b.name for b in sorted(DST.glob("*.json")) if not (SRC / b.name).exists()]
    verb = "would add" if a.verify else "added"
    print(f"  {verb} {new}, changed {changed}, unchanged {same}  -> {DST}")
    if lost:
        print(f"  !! {len(lost)} day(s) in the backup are MISSING from daily/: {lost[:5]}"
              f"{'' if len(lost) <= 5 else f' +{len(lost)-5} more'}")
        print(f"     restore with:  cp {DST}/<date>.json {SRC}/")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
