#!/usr/bin/env python3
"""check_pull_depth.py — is the latest pull THIN? (the sibling of check_pull_gaps)

check_pull_gaps asks whether a day is MISSING. This asks whether a day arrived EMPTY-ISH, which is
the failure it cannot see: on 2026-09-08 Tavily's median article fell from 1,083 characters to 316
and the gap check reported the sequence complete and exited 0. The pull was present and thin, the
curator was handed blurbs instead of paragraphs, and it took three days and a look at FBS panel 11
to notice. Nothing in the daily chain counts characters.

PER ENGINE, NEVER BLENDED, and that is the whole design. The two post-handoff engines differ ~7.8x
in delivered text -- Anthropic returns URLs and we fetch the body ourselves, which the best desks
block (median 150 chars); Tavily returns the body (median 1,168). A blended median therefore moves
with the MIX rather than with depth, and `_ANTHROPIC_LOOKBACK = 7` shifts that mix every day of the
week. Read blended, the same signal says weekends carry shorter news, which is false -- per engine,
length is flat on every weekday. Any check built on the blend would fire on Tuesdays and miss a real
outage. See the FBS panel 11 note.

RELATIVE, NOT ABSOLUTE. Each engine is compared to its OWN trailing median, so the thresholds do not
need re-tuning as the corpus, the query set or the provider mix changes -- and so the check cannot
quietly become a number nobody remembers choosing.

IT ALSO WATCHES THE HEADLINE-ONLY SHARE, because since 2026-09-11 the pull backfills unreadable
articles through Tavily's extract endpoint (search.extract). A rise there is a DIFFERENT fault --
extract failing -- with the same symptom, and separating the two at 06:30 is worth a line of output.

    scripts/check_pull_depth.py           # exit 1 if an engine is materially thin
    scripts/check_pull_depth.py --day 2026-09-08
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "forward" / "daily"
sys.path.insert(0, str(ROOT / "src"))

WINDOW = 14          # trailing days the baseline is taken over
DROP = 0.50          # an engine is THIN below this share of its own trailing median
MIN_N = 5            # fewer articles than this is a sample, not a measurement
HEADLINE_JUMP = 0.30 # headline-only share above this is called out on its own


def _engine(a: dict) -> str:
    """anthropic / tavily, inferred from the query form.

    The DAILY FILES are raw -- they keep the query exactly as issued, and only Anthropic's gather is
    told to put `before:<date>` in every one (forward_gather.py:192). bootstrap_corpus.load() stamps
    an explicit `engine` field, but that runs over the ASSEMBLED corpus and normalises queries away,
    so it is not available here. Inferring from the raw marker is correct at this layer and wrong at
    that one -- which is the mistake FBS panel 11 made in its first version."""
    qs = [str(q) for q in (a.get("queries") or [])]
    return "anthropic" if any("before:" in q for q in qs) else "tavily"


def _lens(pool: list) -> dict:
    out: dict = {"anthropic": [], "tavily": []}
    for a in pool:
        out[_engine(a)].append(len((a.get("snippet") or "")))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None, help="check this pull instead of the latest")
    a = ap.parse_args(argv)
    files = sorted(glob.glob(str(DAILY / "*.json")))
    if not files:
        print("  pull depth: no daily files at all")
        return 1
    days = [Path(f).stem for f in files]
    day = a.day or days[-1]
    if day not in days:
        print(f"  pull depth: no pull file for {day}")
        return 1
    prior = [d for d in days if d < day][-WINDOW:]

    def med(d: str, eng: str):
        v = [x for x in _lens(json.loads((DAILY / f"{d}.json").read_text()).get("pool") or [])[eng]
             if x > 0]
        return (st.median(v), len(v)) if len(v) >= MIN_N else (None, len(v))

    pool = json.loads((DAILY / f"{day}.json").read_text()).get("pool") or []
    n_head = sum(1 for x in pool if not (x.get("snippet") or "").strip())
    head_share = n_head / max(len(pool), 1)
    bad = 0
    parts = []
    for eng in ("tavily", "anthropic"):
        cur, n = med(day, eng)
        base = [m for m, _ in (med(d, eng) for d in prior) if m is not None]
        ref = st.median(base) if base else None
        if cur is None:
            parts.append(f"{eng} n={n} (too few to judge)")
            continue
        parts.append(f"{eng} {cur:.0f}" + (f" (14d {ref:.0f})" if ref else ""))
        if ref and cur < DROP * ref:
            bad += 1
            print(f"  !! {eng.upper()} DEPTH DOWN: median {cur:.0f} chars against a {len(base)}-day "
                  f"median of {ref:.0f} ({100 * (cur / ref - 1):+.0f}%). The pull is PRESENT but "
                  f"THIN -- the curator is being handed blurbs, and check_pull_gaps cannot see it. "
                  f"Look at FBS panel 11's {eng} line before trusting a curation run on this window.",
                  file=sys.stderr)
    if head_share > HEADLINE_JUMP:
        bad += 1
        print(f"  !! HEADLINE-ONLY SHARE {100 * head_share:.0f}% ({n_head} of {len(pool)}). Since "
              f"2026-09-11 the pull backfills unreadable articles via tavily extract, so a high "
              f"share here points at EXTRACT failing rather than at the search engines.",
              file=sys.stderr)
    print(f"  pull depth {day}: " + " · ".join(parts)
          + f" · headline-only {n_head} of {len(pool)}"
          + ("" if bad else "  ok"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
