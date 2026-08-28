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
HISTORY_DAYS = 92               # ~3 months of GKG before the handoff -> day 0 (the FIRST SCAN)
# WARM-UP: extra GKG kept BEFORE day zero so the first scan has a full news window to read. It is
# corpus, never scanned -- day_zero() is unchanged and no anchor lands in it.
#
# Without it the first anchor sits ON the corpus's first day, so its `news_lookback_days` window
# extends into nothing and it reads a fraction of a normal scan. Measured on the weekly curation:
# 509 articles at 2026-05-01 against ~3,100 settled, tracking days-available almost exactly
# (509 ~ 5 days x ~101/day) -- a window artefact, NOT missing news; the corpus's own first day
# already carries 101 articles. It costs one MONTHLY scan out of five (20% of the run) versus one
# weekly scan out of seventeen, so it matters far more at the cadence the profile now uses.
#
# FREE: the pre-handoff half is a date SLICE of the canonical GKG corpus, which already runs back
# to 2023-08-11 -- widening the slice fetches nothing. Text coverage in the extra month is
# comparable (92.7% with a lede, against 95.3% in the scanned era), so it is real reading material.
WARMUP_DAYS = 30                # >= news_lookback_days; see corpus_start()
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


def corpus_start(handoff: str = HANDOFF, history_days: int = HISTORY_DAYS,
                 warmup_days: int = WARMUP_DAYS) -> str:
    """First day of CORPUS, which is `warmup_days` BEFORE the first scan. Read, never scanned."""
    return (_dt.date.fromisoformat(day_zero(handoff, history_days))
            - _dt.timedelta(days=warmup_days)).isoformat()


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


def profile_org_tagger() -> str | None:
    """`org_tagger_model` from the FORWARD profile, or None when off.

    ONE reader, because the bootstrap corpus has three independent entry points -- the curation
    (backtest_gdelt --bootstrap), CBS and FBS -- and all three must see the SAME corpus or the page
    describes a corpus the curation never read. Each having its own copy of this lookup is exactly
    how `news_lookback_days` came to be implemented in one place and not the other."""
    try:
        import optimizer as _op
        return _op.load_financial_model("investor_profile.forward.md").get("org_tagger_model") or None
    except Exception:  # noqa: BLE001 -- never block a corpus load on reading a knob
        return None


def load(handoff: str = HANDOFF, history_days: int = HISTORY_DAYS,
         gkg_run: str = GKG_RUN, daily_dir: str = DAILY_DIR,
         spam_filter: bool = True, org_tagger: str | None = None) -> tuple[list[dict], dict]:
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
    # from CORPUS start, not scan start: the warm-up month is here to be READ by the first scan's
    # lookback window, never to be scanned itself.
    c0 = corpus_start(handoff, history_days)
    pre = [_norm(a, "gkg") for a in gkg_arts
           if c0 <= (a.get("published_date") or "")[:10] < handoff]

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
    # ONE ARTICLE SHAPE FOR BOTH SOURCES, via the shared contract -- see article_contract. This used
    # to be done inline here, which normalised the BOOTSTRAP corpus and left the backtest's own loader
    # doing it at read time. One owner, both loaders.
    try:
        import article_contract as _ac
        import orgs as _o
        _canon = _o.build_canon(arts)
        # ORG-TAGGER CACHE, attached at the SAME boundary and before normalisation so a tagged
        # article is indistinguishable downstream from one GKG stamped itself. Read-only and free:
        # attach() is a dict lookup, never an LLM call, so loading the corpus can neither cost money
        # nor block on a network. An empty or missing cache is the no-op that leaves today's
        # behaviour exactly as it was, which is what `org_tagger_model: off` means.
        if org_tagger:
            import org_tagger as _ot
            _n = _ot.attach(arts, org_tagger, _canon)
            if _n:
                import sys as _s
                print(f"  bootstrap: org-tagger cache filled `orgs` on {_n:,} articles "
                      f"({org_tagger})", file=_s.stderr)
                _canon = _o.build_canon(arts)      # the new orgs are vocabulary too
        _ac.normalise_pool(arts, _canon)
        # MEASURED AFTER normalise_pool, NOT BEFORE. The contract fills `orgs` from the canon for
        # articles the tagger left empty, so a count taken before it runs describes a state the
        # curator never sees -- it reported 221 untagged where the real figure is 76 and where
        # backfill_org_tags had 0 to send. Third time on this feature that a count and the thing
        # it counts disagreed, and the same cause each time: measuring at the wrong point.
        if org_tagger:
            _cov = _ot.coverage(
                [a for a in arts if (a.get("published_date") or "")[:10] >= handoff],
                _canon, org_tagger, _o.ticker_map(arts, _canon))
            import sys as _s
            print(f"  bootstrap: org-tagger \u2014 {_cov['no_company']:,} articles tagged 'no "
                  f"subject company' (correct for macro/policy/roundup stories)", file=_s.stderr)
            if _cov["unseen"]:
                print(f"  bootstrap: {_cov['unseen']:,} of {_cov['n']:,} articles "
                      f"({_cov['pct_unseen']}%) the tagger has never seen \u2014 run "
                      f"scripts/backfill_org_tags.py --corpus bootstrap --post-handoff-only",
                      file=_s.stderr)
    except Exception as _e:  # noqa: BLE001 -- never block corpus loading on normalisation
        import sys as _s
        print(f"  bootstrap: contract normalisation unavailable ({type(_e).__name__}: {_e})", file=_s.stderr)
    arts.sort(key=lambda a: (a.get("published_date") or ""))
    last = arts[-1]["published_date"][:10] if arts else handoff
    eng = collections.Counter(a["engine"] for a in post.values())
    # `start` stays the SCAN start -- backtest_gdelt reads it as --start and the dashboard seeds the
    # book from it, and neither should move because the corpus grew a read-only warm-up.
    meta = {"start": d0, "corpus_start": c0, "end": last, "handoff": handoff,
            "n_gkg": len(pre), "n_websearch": len(post), "spam_dropped": dropped_spam,
            # the websearch era's engine split -- exclusive buckets that sum to n_websearch
            "n_tavily": eng["tavily"], "n_anthropic": eng["anthropic"], "n_both": eng["both"],
            "pull_days": pull_days, "pull_kept": dict(pull_kept),
            "gkg_run": gkg_run, "history_days": history_days,
            "org_tagger": org_tagger or None}
    # SAME INGEST STAMP THE GKG POOL CARRIES. The bootstrap is assembled in memory rather than
    # written to disk, but it is still a corpus a curation will read, so it records what built it on
    # the same terms -- and names BOTH sources, which is the fact that distinguishes it.
    try:
        import article_contract as _ac
        meta["ingest"] = _ac.ingest_stamp("gkg+wayback | anthropic+tavily", handoff=handoff,
                                          gkg_run=gkg_run, history_days=history_days)
    except Exception as _e:  # noqa: BLE001 -- provenance, never a gate
        import sys as _s
        print(f"  bootstrap: ingest stamp unavailable ({type(_e).__name__}: {_e})", file=_s.stderr)
    return arts, meta


def describe(meta: dict) -> str:
    # THE CORPUS SPAN, not the scan span. They differ by the warm-up month, and this line is
    # describing articles -- quoting the scan start next to an article COUNT that includes the
    # warm-up would understate the span the count belongs to.
    _c0 = meta.get("corpus_start", meta["start"])
    _sp = (f"{_c0} .. {meta['end']}" if _c0 == meta["start"]
           else f"{_c0} .. {meta['end']} (scans from {meta['start']})")
    return (f"bootstrap corpus {_sp} · handoff {meta['handoff']} · "
            f"GKG {meta['n_gkg']:,} + websearch {meta['n_websearch']:,} "
            f"= {meta['n_gkg'] + meta['n_websearch']:,} articles")


if __name__ == "__main__":                          # quick sanity check: python src/bootstrap_corpus.py
    a, m = load()
    print(" ", describe(m))
    print(f"  spam-filtered out of the websearch era: {m['spam_dropped']}")
