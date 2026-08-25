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

import collections
import datetime as _dt
import json
import re as _re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- the three knobs that define the corpus -------------------------------------------------------
HANDOFF = "2026-07-28"          # first websearch-only day (see the docstring for why this date)
HISTORY_DAYS = 92               # ~3 months of GKG before the handoff -> day 0
def _canon_corpus() -> str:
    """The canonical GKG corpus, DERIVED from provenance rather than named here.

    It used to be the literal "data/backtest_3yr_v3". When the canonical corpus was promoted to v5
    the bootstrap silently stayed on v3 -- the exact drift CLAUDE.md forbids ("Do NOT hard-code a run
    or corpus path in a builder again"), in the one module sitting outside the canon machinery. It
    hid well because the two are nearly the same articles: over the bootstrap window v5 is a strict
    SUBSET of v3, 9,125 of 9,179 URLs, so every count looked plausible. What differed was the field
    that matters most to the curator -- v5 carries GKG's subject-company extraction (`orgs`) on 93%
    of articles and v3 carries it on NONE, so FBS/CBS were reading a corpus from which
    orgs.article_orgs() can return nothing at all, while FBT/CBT read one where it works.

    Derived, so the NEXT promotion carries automatically instead of needing to be remembered."""
    try:
        import provenance as _p
        return _p.CANON_CORPUS
    except Exception:  # noqa: BLE001 -- a standalone checkout without provenance still loads
        return "data/backtest_3yr_v5"


GKG_RUN = _canon_corpus()          # the GKG corpus + its wayback backfill
DAILY_DIR = "data/forward/daily"   # the accumulated websearch pulls


def day_zero(handoff: str = HANDOFF, history_days: int = HISTORY_DAYS) -> str:
    return (_dt.date.fromisoformat(handoff) - _dt.timedelta(days=history_days)).isoformat()


_BEFORE = _re.compile(r"\s*\bbefore:\S+")


def _renames() -> dict:
    """OLD beat query -> current name, from retrieval_config.json (cached)."""
    global _RENAMES
    if _RENAMES is None:
        try:
            _RENAMES = json.loads((REPO_ROOT / "retrieval_config.json").read_text()).get("beat_renames") or {}
        except Exception:  # noqa: BLE001 -- a missing map must not break corpus loading
            _RENAMES = {}
    return _RENAMES


_RENAMES: dict | None = None


def beat_of(query: str) -> str:
    """A query tag reduced to the BEAT it came from, by stripping Anthropic's `before:<date>`.

    Same tagging convention `engine_of` reads, used the other way. Anthropic is told to append
    `before:<anchor>` to every search, so the SAME beat arrives tagged differently on every day it
    fires -- `technology stocks` from Tavily, `technology stocks before:2026-08-19` from Anthropic.
    Counting raw tags therefore counts a beat once per day per engine instead of once, which inflates
    "distinct beats" without bound and simultaneously UNDERCOUNTS any per-beat article total that
    looks a beat up by its bare name.

    Measured on the bootstrap corpus: 160 raw distinct tags collapse to 106 real beats, and the
    operator is a clean suffix in all 106 cases (0 inline), so the strip is unambiguous."""
    # DELEGATES to gkg.canon_beat -- one definition, because this exact reconciliation is also
    # needed by the curator (agent.py) and the bundler, and a second copy here is the duplication
    # this module's own docstring warns about.
    try:
        import gkg as _g
        return _g.canon_beat(query)
    except Exception:  # noqa: BLE001 -- fall back to the local strip if gkg is unavailable
        b = _BEFORE.sub("", query or "").strip()
        return _renames().get(b, b)


def beats_of(a: dict) -> set[str]:
    """The DISTINCT beats that surfaced one article. A set, because an article both engines found
    carries the same beat twice -- once bare, once `before:`-suffixed -- and it is still one
    article of that beat."""
    return {b for b in (beat_of(q) for q in (a.get("queries") or [])) if b}


def engine_of(a: dict) -> str:
    """Which web-search engine surfaced a POST-handoff article: "tavily" | "anthropic" | "both".

    INFERRED, not recorded. `forward.pull_day` unions the two engines through `merge_pools`, which
    dedups by URL and merges the beat tags but stamps no provenance field, so the engine has to be
    read back off the tags. The tell is mechanical and comes from the engines' different date
    handling (see forward.py's _ANTHROPIC_LOOKBACK note): Anthropic's web_search has no recency
    operator, so the gather prompt makes it write `before:<anchor>` into every query, while Tavily
    bounds dates through the API and tags the BARE beat string. So `before:` in a tag == Anthropic.

    ACCURACY. Measured over the 2,232 post-handoff articles in the corpus: every one carries at
    least one tag, so nothing falls through. The one leak is an Anthropic query the model wrote
    freely without the operator (2 seen, both article-title-shaped) — those read as Tavily. ~0.1%,
    and it can only UNDERSTATE Anthropic, which is the direction that matters least given Anthropic
    is already the small half. Stamping the engine at pull time would retire the heuristic, but only
    for days pulled after the change; the accumulated corpus would still need this.

    Pre-handoff (GKG) articles have no engine — they did not come from a web search at all."""
    qs = a.get("queries") or []
    anth = any("before:" in q for q in qs)
    tav = any("before:" not in q for q in qs)
    return "both" if anth and tav else ("anthropic" if anth else "tavily")


def _norm(a: dict, era: str) -> dict:
    """One article shape for both eras.

    `era` is stamped on every article because it is the single most important thing to be able to
    slice by downstream: a metric that moves at the handoff is a corpus change, not a signal.
    `engine` is the same idea one level down, and only for the websearch era: the post-handoff side
    is a UNION of two engines with very different reach, so "which engine" is a provenance change
    exactly the way "which era" is."""
    a = dict(a)
    a["era"] = era
    a.setdefault("snippet", "")
    a.setdefault("queries", [])
    if era == "websearch":
        a["engine"] = engine_of(a)
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
                # WHICH PULL FIRST CAUGHT IT, not when it was published. The two engines have very
                # different lag: Tavily runs a 1-day window so it is same-day 100% of the time, while
                # Anthropic looks back a week (forward._ANTHROPIC_LOOKBACK) and lands a median 1 day
                # late. So a recent published-date has NOT finished collecting its Anthropic share,
                # and anything plotting engine mix against published date has to know that or it
                # reads the unsettled tail as Anthropic going dark.
                post[u]["pull_date"] = f.stem

    # PULL-SIDE PROVENANCE, keyed by COLLECTION date rather than publication date. "Did the cron
    # fire?" is a question about the pull, and answering it off published dates conflates a morning
    # the cron missed with a morning that simply had less news -- and hides the reverse, since a
    # slow engine backfills a missed day's publication bucket from later pulls. `pull_days` is every
    # daily file that exists; `pull_kept` is how many NEW post-handoff articles each contributed.
    pull_kept = collections.Counter()
    for a in post.values():
        pull_kept[a["pull_date"]] += 1
    pull_days = sorted(f.stem for f in (root / daily_dir).glob("*.json"))

    arts = pre + list(post.values())
    # ONE ARTICLE SHAPE FOR BOTH SOURCES. The GKG half arrives with `orgs` from V2Organizations; the
    # websearch half arrives with none, and the curator must not have to know which is which. Stamp
    # it here, at the ingest boundary, using the GKG half's own vocabulary -- which is why this runs
    # AFTER the two halves are joined and not inside the websearch loop. GKG articles are never
    # touched (attach_orgs skips anything that already has the key).
    # CANONICAL BEAT TAGS, at the boundary rather than at nine read sites. An article is tagged with
    # whatever the beat was CALLED and in whatever SHAPE the engine wrote it: Anthropic appends
    # `before:<date>` to every query, and beats get renamed. Measured here: the websearch half holds
    # 172 distinct raw tags of which 119 need reconciling, the GKG half 43 of which 3 do. That
    # reconciliation was happening at read time in agent.py, provenance.py, bootstrap_corpus and four
    # sites in the CBT builder -- and every one of them was a place it could be FORGOTTEN, which is
    # exactly how a rename silently stripped the gem bonus from 38.6% of gem-scored articles.
    # The RAW tags are preserved in `queries_raw`: they are the immutable record of which query
    # actually fired, and `engine` is inferred from their shape, so they are never overwritten.
    try:
        import gkg as _g
        for _a in arts:
            _raw = list(_a.get("queries") or [])
            if _raw:
                _a["queries_raw"] = _raw
                _a["queries"] = sorted({_c for _c in (_g.canon_beat(_q) for _q in _raw) if _c})
    except Exception as _e:  # noqa: BLE001 -- never block corpus loading on tag bookkeeping
        import sys as _s
        print(f"  bootstrap: beat canonicalisation unavailable ({type(_e).__name__}: {_e})", file=_s.stderr)
    try:
        import orgs as _o
        import websearch_orgs as _wo
        _canon = _o.build_canon(arts)
        _wo.attach_orgs(arts, _canon)
    except Exception as _e:  # noqa: BLE001 -- attribution is an enrichment; never block corpus loading
        import sys as _s
        print(f"  bootstrap: websearch org attribution unavailable ({type(_e).__name__}: {_e})",
              file=_s.stderr)
    arts.sort(key=lambda a: (a.get("published_date") or ""))
    last = arts[-1]["published_date"][:10] if arts else handoff
    eng = collections.Counter(a["engine"] for a in post.values())
    meta = {"start": d0, "end": last, "handoff": handoff,
            "n_gkg": len(pre), "n_websearch": len(post), "spam_dropped": dropped_spam,
            # the websearch era's engine split -- exclusive buckets that sum to n_websearch
            "n_tavily": eng["tavily"], "n_anthropic": eng["anthropic"], "n_both": eng["both"],
            "pull_days": pull_days, "pull_kept": dict(pull_kept),
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
