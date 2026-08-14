"""bootstrap_corpus.py — THE definition of the bootstrap-era news corpus.

One module so FBS (the retrieval dashboard) and CBS (the curator dashboard) can never disagree about
what "the bootstrap corpus" is. Today's lesson, three times over: a definition duplicated in two places
drifts silently (the beat vocabulary hand-copied between forward_gather.py and retrieval_config.json,
`news_lookback_days` implemented in the backtest but not the forward, `cap=0` meaning "uncapped" in one
gather and "keep nothing" in the other). Anything that needs to know what the bootstrap reads imports
this; nothing re-derives it.

THE SHAPE — a CLEAN CUT at the handoff, not a blend:

    day 0 .......... HANDOFF .......... today (growing daily)
    |-- GKG + wayback --|-- websearch only --|

  BEFORE the handoff: GDELT GKG (with the wayback lede backfill) -- deep, retrospective, ~101/day.
  AFTER  the handoff: the daily websearch pull ONLY -- ~97/day and extended every morning by cron.
  GKG is NOT used after the handoff even though it has coverage there, and the two are never unioned.

WHY A CLEAN CUT RATHER THAN AN OVERLAP BLEND. The forward test this bootstrap leads to will run on
websearch alone, so every post-handoff day must look exactly like production. Blending GKG in (the way
portfolio-wave-rider's --blend-backtest-news does) would make the bootstrap richer than the thing it is
meant to predict, which is how a backtest flatters itself.

WHY 2026-07-28 IS THE HANDOFF. Measured 2026-08-14 on the accumulated pulls: before 07-28 the websearch
corpus cannot carry a corpus alone -- median 14 articles/day, TWO days with zero articles, and two days
missing entirely (07-11, 07-12, when cron did not fire). From 07-28 it runs at a median of 97/day. Since
GKG is cut off at the handoff with no fallback, an earlier handoff would put real holes in the corpus.
It also happens to make the seam nearly invisible: 101/day before, 97/day after.
CAVEAT worth remembering: the jump on 2026-07-27 (15 -> 33 -> 48 -> 85 -> 102 across 07-25..07-29) is
UNEXPLAINED -- plausibly a Tavily quota or config change. So there is a known provenance change sitting
right at the handoff. It does not invalidate the corpus, but any before/after comparison across that
date is confounded and should not be read as a retrieval improvement.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- the three knobs that define the corpus -------------------------------------------------------
HANDOFF = "2026-07-28"          # first websearch-only day (see the docstring for why this date)
HISTORY_DAYS = 92               # ~3 months of GKG before the handoff -> day 0
GKG_RUN = "data/backtest_3yr_v3"   # the GKG corpus + its wayback backfill
DAILY_DIR = "data/forward/daily"   # the accumulated websearch pulls


def day_zero(handoff: str = HANDOFF, history_days: int = HISTORY_DAYS) -> str:
    return (_dt.date.fromisoformat(handoff) - _dt.timedelta(days=history_days)).isoformat()


def _norm(a: dict, era: str) -> dict:
    """One article shape for both eras.

    `era` is stamped on every article because it is the single most important thing to be able to
    slice by downstream: a metric that moves at the handoff is a corpus change, not a signal."""
    a = dict(a)
    a["era"] = era
    a.setdefault("snippet", "")
    a.setdefault("queries", [])
    return a


def load(handoff: str = HANDOFF, history_days: int = HISTORY_DAYS,
         gkg_run: str = GKG_RUN, daily_dir: str = DAILY_DIR,
         spam_filter: bool = True) -> tuple[list[dict], dict]:
    """(articles, meta) for the bootstrap corpus, assembled IN MEMORY.

    Deliberately not materialised to a third pool.json: a copy of two sources is a third thing that can
    drift from both. Assembling on every read costs ~a second and cannot go stale.

    `spam_filter` re-applies the backtest's title-spam rules to the WEBSEARCH side. The daily pull only
    started filtering on 2026-08-14, so days pulled before that carry listicle titles the GKG side would
    have dropped -- without this the two eras are filtered to different standards and any funnel metric
    that straddles the handoff is measuring our own inconsistency."""
    d0 = day_zero(handoff, history_days)
    root = REPO_ROOT

    # --- pre-handoff: GKG + wayback -------------------------------------------------------------
    gkg_pool = json.loads((root / gkg_run / "pool.json").read_text())
    gkg_arts = gkg_pool.get("articles", gkg_pool) if isinstance(gkg_pool, dict) else gkg_pool
    pre = [_norm(a, "gkg") for a in gkg_arts
           if d0 <= (a.get("published_date") or "")[:10] < handoff]

    # --- post-handoff: websearch only ------------------------------------------------------------
    post: dict[str, dict] = {}                      # keyed by URL: the same story recurs across days
    dropped_spam = 0
    _spam = None
    if spam_filter:
        try:
            import gkg as _g
            _spam = _g._spam_title
        except Exception:  # noqa: BLE001 -- filtering is a nicety; a missing module must not block the build
            _spam = None
    for f in sorted((root / daily_dir).glob("*.json")):
        for a in (json.loads(f.read_text()).get("pool") or []):
            if not isinstance(a, dict):
                continue
            d = (a.get("published_date") or "")[:10]
            if d < handoff:
                continue                            # pre-handoff websearch is DISCARDED: GKG owns that era
            if _spam is not None and _spam(a.get("title") or ""):
                dropped_spam += 1
                continue
            u = (a.get("url") or "").split("?")[0].rstrip("/").lower()
            if u and u not in post:
                post[u] = _norm(a, "websearch")

    arts = pre + list(post.values())
    arts.sort(key=lambda a: (a.get("published_date") or ""))
    last = arts[-1]["published_date"][:10] if arts else handoff
    meta = {"start": d0, "end": last, "handoff": handoff,
            "n_gkg": len(pre), "n_websearch": len(post), "spam_dropped": dropped_spam,
            "gkg_run": gkg_run, "history_days": history_days}
    return arts, meta


def describe(meta: dict) -> str:
    return (f"bootstrap corpus {meta['start']} .. {meta['end']} · handoff {meta['handoff']} · "
            f"GKG {meta['n_gkg']:,} + websearch {meta['n_websearch']:,} "
            f"= {meta['n_gkg'] + meta['n_websearch']:,} articles")


if __name__ == "__main__":                          # quick sanity check: python src/bootstrap_corpus.py
    a, m = load()
    print(" ", describe(m))
    print(f"  spam-filtered out of the websearch era: {m['spam_dropped']}")
