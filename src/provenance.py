"""provenance.py — what makes a curation THE canonical one, and the gate that enforces it.

THE PROBLEM THIS EXISTS TO KILL. A published dashboard is a claim about a specific book, and the
book is a function of three inputs that live in three different places:

    corpus (data/<pool>/pool.json)  ->  curation (data/<run>/journal.json)  ->  book (the profile)

Nothing tied them together, so every one of them has drifted at least once and the page said nothing:

  * 2026-08-12  FBT's --run default pointed at a 1-year pool; the page rebuilt on 1/3 of the data.
  * 2026-08-19  `cp -R` left a stale journal inside cbt_3yr_v9. CBT and the sweep both ran on a
                curation nobody had produced. Caught only because a reader noticed SBT quoting
                $272K against CBT's $115K for what was supposed to be one run.
  * 2026-08-21  CBT's --run default still said data/cbt_1yr, ~50 commits after the published page
                had moved to the 3-year grok run. A bare rebuild silently produced a 26-event page.
  * 2026-08-21  docs/cbt.html was showing $272,336 against the profile's $112,435 -- not a bug, but
                a page built five hours BEFORE `max_watchlist: 8 -> 6` was committed, and never
                rebuilt. Diagnosing that took a full price-panel investigation to rule out.

Each was found by eye, late, and after someone had already trusted a wrong number.

THE DISTINCTION THAT DOES THE WORK. Profile knobs split cleanly by WHERE THEY ACT:

  CURATION knobs act UPSTREAM of the journal -- which articles are read, which the scout is shown,
  which events open and when they retire. Change one and the existing journal could never have been
  produced under it. The run is INVALID and must be re-curated (LLM cost, hours).

  BOOK knobs act at REPLAY time, over a fixed journal -- sizing, culling, rebalancing. Change one and
  the same curation simply produces a different book. Nothing is invalidated; the page just needs a
  rebuild (seconds, free). `max_watchlist` is the type specimen: watchlist_cap() is called ONLY in
  firehose.backtest, never in the scan path.

Getting that boundary wrong in either direction is expensive. Treat a book knob as curation and every
sizing tweak demands a re-curation. Treat a curation knob as book and you publish a page whose
settings table describes something the journal never ran under -- which is the 2026-08-19 failure.

So the canonical curation is not a path anyone has to remember. It is DERIVED: the run whose recorded
inputs match (canonical corpus, current profile's curation knobs). CANON_RUN below is a pointer for
convenience, and `verify` checks the pointer against that definition on every build.

WHY A SWEEP THAT READS THE NEWS CANNOT BE CONFUSED WITH IT. Such a sweep varies a CURATION knob --
that is what makes it need the news -- so each arm's fingerprint differs from the profile's by
construction, `verify` reports it as non-canonical, and `require_publishable` refuses to let it
overwrite docs/. No naming convention to remember and no discipline required: the arms cannot reach
the published pages even if someone points a builder straight at one.
"""
from __future__ import annotations

import hashlib
import collections
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- the canonical inputs
# ONE place. Promoting a new corpus, curation or sweep is an edit here and nothing else; every
# builder default derives from these, and every publish is checked against them.
CANON_CORPUS = "data/backtest_3yr_v5"
# Promoted 2026-08-22 to v18, the first curation at the current config (min_bundle_articles 1,
# max_events 0 = uncapped, max_event_scans 6). Curated with --decisions, as mb2rep was and
# as mb1/mb2/mb3 were not.
# Earlier note, on why --decisions is not optional:
# mb1/mb2/mb3 were not. Proposed-and-culled candidates are persisted nowhere else, so
# without it CBT's funnel loses three bars and panels 12/14/16/17 render empty.
# Earlier note, still true of why v9 was dropped: Same config, but v9 was curated when the corpus held
# 56.9% archived lede; the wayback backfill completed 2026-08-21 and mb1 read ~72%. v9 also
# predates provenance stamping, so 16 of its 24 curation knobs were never recorded -- it passes
# `verify` only because unrecorded knobs cannot be checked. mb1 stamped all 25 at creation.
# NOTE the gap this exposes: corpus_id is path + article count, and enrichment changes NEITHER,
# so nothing here could have told you v9 was stale. That wants a text-state digest.
CANON_RUN = "data/cbt_3yr_v24_wirelede"   # v23 -> v24 on 2026-08-30: the first curation run under
                                          # the WIRE-DATELINE lede rule (a paid-wire dateline is not a
                                          # lede, so the article is headline-only). PROMOTED ON
                                          # PROVENANCE, NOT ON RESULTS: v23 was produced by code that
                                          # no longer exists, and the lede rule is a CURATION-path
                                          # change -- it alters what the scout reads.
                                          # WHAT IT IS EVIDENCE FOR, and what it is not. The
                                          # attribution rests on a CONTROLLED paired A/B, not on this
                                          # run: same window (2026-03-28), same scout, old vs new lede
                                          # -- GRDX proposed 1/1 under the old rule (verbatim the
                                          # historical thesis and peers) and 0/3 under the new one.
                                          # This curation is only CONSISTENT with that: it has no
                                          # GRDX and 1 wire-promoted vehicle against v23's 4, but it
                                          # also has 265 events against 347 on a 41-article input
                                          # delta, which is matcher variance, not the fix. The
                                          # discovery gate is byte-identical across both runs (it
                                          # matches on titles, which did not change) and the scout
                                          # actually proposed MORE (1,280 vs 1,219) and admitted MORE
                                          # (1,073 vs 1,050) -- so the event-count drop is clustering
                                          # noise. Do NOT read any book-value difference as the fix.
                                          # PRIOR ENTRY, v22 -> v23 on 2026-08-29: the first curation run
                                          # under the SILENCE CAP (max_silent_scans=8) and the
                                          # first-read retire guard. Unlike the v21->v22 promotion
                                          # the fingerprint DOES move (624c6c2e211c -> a25e2c1839d1):
                                          # max_silent_scans is a new CURATION knob, so v22 could
                                          # not have been produced under this profile.
                                          # BUT check_canon still reports v21/v22/v9 as "matching",
                                          # because a knob absent from an OLD run's stamp is skipped
                                          # rather than counted as a mismatch. Adding a curation knob
                                          # therefore does NOT retroactively invalidate the runs that
                                          # predate it -- they are grandfathered by omission, and only
                                          # the published-page hash catches the drift.
                                          # MECHANISM, the reason it was promoted (never the P&L,
                                          # non-negotiable #6): the quiet-run tail is truncated
                                          # dead at 8. v22 had 32 events running 9-12 consecutive
                                          # ZERO-SOURCE scans -- ev222 held PCG through nine reads
                                          # of "No news on the $1B TMI loan" -- and v23 has none
                                          # past 8. The retire guard stopped 174 events that
                                          # resolve on their OPENING read from banning vehicles
                                          # they never chased.
                                          # COST, measured and NOT a saving: 347 events / 1,372
                                          # agent-scans against v22's 284 / 1,199. The cap alone
                                          # skips ~8% of the budget, but the retire guard hands
                                          # the scout back ~130 tickers it had been barred from
                                          # re-proposing, and those open more events than the cap
                                          # closes. Net +14% scans.
CANON_SWEEP = "data/sweep_v26.json"   # v25 -> v26 on 2026-08-30, swept over the promoted
                                      # cbt_3yr_v24_wirelede curation. Same 7,200-cell BOOK-knob
                                      # grid; no CURATION knob varies, so SBT still describes the
                                      # one canonical curation.
                                      # PRIOR: v24 -> v25 on 2026-08-30, swept over
                                      # cbt_3yr_v23_silence curation (the silence cap + first-read
                                      # retire guard). Same 7,200-cell BOOK-knob grid as v24, which
                                      # swept cbt_3yr_v22_resolver and is kept on disk for the
                                      # before/after. The grid touches no CURATION knob, so SBT
                                      # still describes the one canonical curation.

# --------------------------------------------------------------------------- the knob partition
# UPSTREAM of the journal. Changing any of these invalidates an existing curation.
CURATION_KNOBS = frozenset({
    "model", "scout_model", "event_agent_model", "event_agent_effort",
    "org_tagger_model",                        # fills `orgs` at ingest -> changes which company
                                               # bundles exist -> changes what the scout is shown

    "picker_model", "picker_effort",           # backtest_gdelt uses the picker for the max_events cap
    "retrieval_engine", "discovery_filter",
    "news_cap", "news_lookback_days", "event_news_cap",
    "relevance_filter", "relevance_keep",
    "scout_articles_per_call", "max_article_chars",
    "min_bundle_articles",
    "max_events", "max_new_events",
    "curator_memory_weeks", "max_event_scans", "max_silent_scans",
    # exit_patience_scans / max_stale_scans WERE here until 2026-08-22 and were MISFILED. The test
    # is not what a knob sounds like, it is WHERE IT IS CALLED: both are read only by
    # firehose._watch_clocks, which is called only by firehose._stateful_watch, which is called only
    # inside firehose.backtest() -- the replay path. Proven empirically, not by reading: holding the
    # v18 journal FIXED and varying only those two produced books from $95,170 to $345,968. A
    # curation knob cannot do that, because the journal never changes. Misfiling them meant any
    # change to exit behaviour would have demanded an unnecessary ~$7 re-curation, and it hid two
    # free levers. They are BOOK_KNOBS; see the max_watchlist type specimen.
    "rebalance_period",                        # sets the scan cadence -> which weeks exist at all
    "specialty_allow", "mill_block",           # source filters applied as the corpus is read
})

# REPLAY-time only. Free to change; the page just needs a rebuild.
BOOK_KNOBS = frozenset({
    "max_watchlist", "max_agents",             # max_agents is the legacy alias (firehose.watchlist_cap)
    "exit_patience_scans", "max_stale_scans",  # firehose._watch_clocks, reached only from backtest()
    "concentration_cap", "risk_aversion",
    "lookback_period_days", "optimizer_lookback_days",
    "min_trade_size", "t_update_days", "risk_free_rate",
    "initial_investment_usd",
    "always_include", "starter_watchlist", "defensive_ticker",
    "cull_rank", "cull_fresh_slots", "cull_fresh_scans",
    "drop_unfunded_weeks", "unfunded_reentry_on_new_catalyst", "unfunded_cooldown_weeks",
})

# FORWARD-only, and inert in the backtest (CLAUDE.md: gather_model is the live web-search stage).
# Excluded from the fingerprint so a forward retrieval change does not falsely invalidate a backtest
# curation -- but still classified, so it counts toward the completeness check below.
FORWARD_ONLY_KNOBS = frozenset({"gather_model"})


def check_interpreter() -> str:
    """Refuse to publish from an interpreter other than the project venv.

    CLAUDE.md specifies Python 3.12 + .venv, but nothing enforced it, and every dashboard built on
    2026-08-21 used the SYSTEM python (3.9.6, numpy 2.0.2, pandas 2.3.3) while every sweep ran under
    .venv (3.12.13, numpy 2.4.6, pandas 3.0.3). Same journal, same profile, same frozen price panel,
    byte-identical inputs -- and the mean-variance optimiser returned $54,960 on one stack and
    $40,498 on the other, a 36% gap. CBT and SBT therefore disagreed all day about the same book for
    a reason no amount of checking the DATA could ever have found; it took hashing every input,
    proving them equal, and only then looking at the interpreter.

    Numerical libraries are part of the provenance. Returns "" when fine, else the complaint.
    """
    import sys
    venv = REPO_ROOT / ".venv"
    if not venv.exists():
        return ""                       # no venv in this checkout; nothing to enforce against
    try:
        inside = Path(sys.prefix).resolve() == venv.resolve()
    except Exception:  # noqa: BLE001
        return ""
    if inside:
        return ""
    return (f"running under {sys.executable} (Python {sys.version.split()[0]}), not the project venv. "
            f"Numerical results DIFFER between stacks -- rebuild with .venv/bin/python.")


def check_partition_covers_profile() -> list[str]:
    """Every profile knob must be classified. Returns the unclassified ones.

    This is the part that keeps the mechanism honest a year from now. A knob nobody has classified is
    a knob whose blast radius nobody has decided, and the failure mode is silent: it would simply be
    left out of the fingerprint, so changing it would invalidate a curation without anything noticing.
    Builders call this and refuse to publish while it is non-empty, which turns "add a knob" into
    "decide where the knob acts" -- the question CLAUDE.md already says to ask before adding one.
    """
    import optimizer
    known = CURATION_KNOBS | BOOK_KNOBS | FORWARD_ONLY_KNOBS
    return sorted(set(optimizer._FINANCIAL_MODEL_DEFAULTS) - known)


# --------------------------------------------------------------------------- fingerprint
def check_beat_vocabulary(corpus: str | Path | None = None) -> list[str]:
    """Every beat reference must resolve to a LIVE beat. Returns the complaints.

    THE HOLE THIS CLOSES. retrieval_config.json is not in the curation fingerprint, and it cannot
    simply be added: it is an INGEST-time input, and the corpus -- not the config -- is what a replay
    reads. But a beat name is also a JOIN KEY. Every article carries the beat name it was retrieved
    under, and the curator later intersects those tags with the CONFIGURED names
    (agent._gem_beats() & queries) and looks them up in beat_parent. Rename a beat and the join
    silently matches nothing, which is indistinguishable from a beat that found nothing.

    Measured on 2026-08-24: renaming three beats stripped the gem-score bonus from 8,077 of 20,941
    gem-scored articles (38.6%) in any re-curation over the existing corpus, and dropped one beat out
    of its bundle parent -- while the fingerprint matched, the frozen journal was untouched, every
    published number was unchanged, and check_canon reported ALL CONSISTENT. Nothing failed loudly.

    Two invariants, both pure reads:
      1. every beat_parent key and value names a live beat;
      2. every distinct query tag in the corpus resolves, via gkg.canon_beat, to a live beat.
    Invariant 2 is the one with teeth -- it counts the ORPHANED ARTICLES, so a rename shows up as
    thousands of articles the curator can no longer score rather than as a config diff nobody reads.
    The fix for a legitimate rename is an entry in retrieval_config's `beat_renames`; the fix for a
    changed QUERY is to retire the old beat and add a new one, because it is not the same search."""
    out: list[str] = []
    try:
        import gkg as _g
    except Exception as e:  # noqa: BLE001 -- never let this check crash the report
        return [f"could not import gkg to check the beat vocabulary: {e}"]
    live = {b["query"] for b in _g.beats()}

    bp = _g.beat_parent()
    for k, v in bp.items():
        if _g.canon_beat(k) not in live:
            out.append(f"beat_parent KEY does not name a live beat: {k!r}")
        if _g.canon_beat(v) not in live:
            out.append(f"beat_parent VALUE does not name a live beat: {v!r} (parent of {k!r})")

    path = Path(corpus or (REPO_ROOT / CANON_CORPUS))
    pool = path if path.suffix == ".json" else path / "pool.json"
    if not pool.exists():
        return out
    try:
        d = json.loads(pool.read_text())
        arts = d.get("articles", d) if isinstance(d, dict) else d
    except Exception as e:  # noqa: BLE001
        return out + [f"could not read {pool} to check beat tags: {e}"]
    orphan_arts: dict = collections.Counter()
    for a in arts:
        if not isinstance(a, dict):
            continue
        for q in {_g.canon_beat(x) for x in (a.get("queries") or [])}:
            if q and q not in live:
                orphan_arts[q] += 1
    if orphan_arts:
        n = sum(orphan_arts.values())
        top = ", ".join(f"{q!r} ({c:,})" for q, c in orphan_arts.most_common(3))
        out.append(f"{len(orphan_arts)} corpus beat tag(s) resolve to NO live beat, orphaning "
                   f"{n:,} article-tags in {pool.parent.name}: {top}"
                   + ("" if len(orphan_arts) <= 3 else f", +{len(orphan_arts)-3} more")
                   + ". Add a `beat_renames` entry, or retire-and-add if the query itself changed.")
    return out


def _norm(v):
    """Normalise for comparison: lists become sorted tuples so ordering is not a false difference."""
    if isinstance(v, (list, tuple)):
        return sorted(str(x) for x in v)
    return v


def corpus_id(corpus: str | Path) -> dict:
    """Identify a corpus by path, article count AND TEXT STATE.

    THE TEXT STATE IS NOT DECORATION, it is the part that works. Path plus article count was the
    original fingerprint and it is blind to the change that matters most: `ingest.py --wayback`
    rewrites pool.json IN PLACE, filling `lede` from archive.org without adding or removing a single
    article. Measured on 2026-08-21, the backfill moved the canonical corpus from 56.9% to 75.1%
    archived lede while the count sat at 99,117 both sides. So data/cbt_3yr_v9 -- curated against the
    thinner text -- kept verifying as canonical for as long as anyone cared to ask, and the staleness
    was found by REASONING about it rather than by any check here. That is the failure this module
    exists to make impossible, reappearing one level down.

    `clean` / `live` / `none` are the three lede provenances FBT plots, so a corpus that has been
    re-enriched fingerprints differently from the one a curation actually read.
    """
    p = REPO_ROOT / corpus if not Path(corpus).is_absolute() else Path(corpus)
    rel = str(Path(corpus)) if not Path(corpus).is_absolute() else str(p.relative_to(REPO_ROOT))
    out = {"path": rel, "articles": None, "text": None}
    pool = p / "pool.json"
    if pool.exists():
        try:
            d = json.loads(pool.read_text())
            arts = d.get("articles", d) if isinstance(d, dict) else d
            out["articles"] = len(arts)
            clean = sum(1 for a in arts if a.get("lede"))
            live = sum(1 for a in arts if a.get("lede_live") and not a.get("lede"))
            out["text"] = {"clean": clean, "live": live, "none": len(arts) - clean - live}
        except Exception:  # noqa: BLE001 -- an unreadable pool is reported as unknown, not fatal
            pass
    return out


def corpus_id_from_articles(arts: list, label: str, **extra) -> dict:
    """corpus_id() for a corpus that is ASSEMBLED IN MEMORY and has no pool.json on disk.

    The bootstrap corpus is deliberately never materialised -- "a copy of two sources is a third
    thing that can drift from both" -- so corpus_id() found no pool.json, left `articles` and `text`
    as None, and stamped the path as "(gdelt-live)". A curation whose recorded inputs do not describe
    what produced it is precisely the drift this module exists to prevent, so the bootstrap
    identifies itself by the same three things a corpus dir does -- count and text state -- plus the
    span and the ingest stamp, which are what make a bootstrap corpus what it is.

    Measures TEXT STATE identically to corpus_id, so a bootstrap curated before a wayback backfill
    fingerprints differently from one curated after."""
    clean = sum(1 for a in arts if a.get("lede"))
    live = sum(1 for a in arts if a.get("lede_live") and not a.get("lede"))
    out = {"path": label, "articles": len(arts),
           "text": {"clean": clean, "live": live, "none": len(arts) - clean - live}}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


# INGEST-OWNED knobs that no longer live in the investor profile. They are still part of what a
# curation is a function of -- moving a parameter to its proper owner must not change what the
# fingerprint MEANS -- so curation_key reads their VALUES from retrieval_config.json instead.
# Because the values were carried over unchanged, the canonical fingerprint is unchanged too, which
# is the test that the move was a relocation and not an edit.
INGEST_OWNED = frozenset({"specialty_allow", "mill_block"})


def _ingest_knob(k: str):
    try:
        return json.loads((REPO_ROOT / "retrieval_config.json").read_text()).get(k)
    except Exception:  # noqa: BLE001
        return None


def curation_key(fm: dict, corpus: "str | Path | dict", arm: str = "fuller") -> dict:
    """The inputs a curation is a function of. Two runs with equal keys are the same experiment.

    `corpus` may be a path OR an already-built identity dict (corpus_id_from_articles), for a corpus
    that is assembled in memory and has no pool.json to point at."""
    knobs = {k: _norm(fm.get(k) if k not in INGEST_OWNED else _ingest_knob(k))
             for k in sorted(CURATION_KNOBS)}
    key = {"corpus": corpus if isinstance(corpus, dict) else corpus_id(corpus),
           "arm": arm, "knobs": knobs}
    key["hash"] = hashlib.sha256(
        json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return key


def curator_code_id() -> dict:
    """A digest of the CURATOR CODE that produced a curation -- the prompts and the gates.

    WHY THIS EXISTS. `curation_key` hashes profile knobs + corpus + arm, and that is the right key
    for "could this curation have been produced under this profile". But a curation is also a
    function of the SCOUT PROMPT and the code-side gates, and those live in src/agent.py, which the
    fingerprint cannot see. On 2026-08-22 the retired-ticker guard was rewritten from a categorical
    ban into a raised evidentiary bar -- a change that alters which tickers the scout may propose
    while leaving every profile knob untouched. The re-curation would have fingerprinted IDENTICALLY
    to the run it was meant to be compared against, and the only thing separating them would have
    been a directory name and somebody's memory. That is the exact failure mode CLAUDE.md's
    provenance section catalogues ("Every one was caught by eye, late").

    RECORDED, NOT HASHED. This is deliberately NOT folded into the fingerprint: doing so would make
    every existing stamp mismatch on the next whitespace edit to agent.py, and would conflate "ran
    under a different config" (which must block a publish) with "ran under different code" (which
    must be visible but is often intended). It is written alongside so a comparison of two runs can
    always answer "same code?" without anybody having to remember.
    """
    import subprocess
    out = {}
    # src/org_tagger.py ADDED 2026-08-28, and it should never have been missing. cbs_v7 and cbs_v8
    # are two curations of one corpus produced from COMPLETELY DIFFERENT tag sets -- a subject-only
    # prompt and an extraction prompt, 0.59 vs 1.46 bundle memberships per article -- and they
    # stamped IDENTICAL hashes AND identical code digests. Only a directory name separated them,
    # which is verbatim the failure this function's docstring was written about. The tagger's prompt
    # is a curator input exactly as the scout prompt is; it just lives in a file nobody listed here.
    for rel in ("src/agent.py", "src/firehose.py", "src/org_tagger.py"):
        f = REPO_ROOT / rel
        if f.exists():
            out[rel] = hashlib.md5(f.read_bytes()).hexdigest()[:12]
    try:
        out["git"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                                    capture_output=True, text=True, timeout=10).stdout.strip() or "?"
        out["dirty"] = bool(subprocess.run(["git", "status", "--porcelain", "src"], cwd=REPO_ROOT,
                                           capture_output=True, text=True, timeout=10).stdout.strip())
    except Exception:  # noqa: BLE001 -- provenance must never sink a curation
        out["git"] = "?"
    return out


def stamp(run_dir: str | Path, fm: dict, corpus: "str | Path | dict", arm: str = "fuller",
          argv: list[str] | None = None, note: str = "") -> Path:
    """Write run_dir/provenance.json. Called by the curation producer as the run is created."""
    run = REPO_ROOT / run_dir if not Path(run_dir).is_absolute() else Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    rec = curation_key(fm, corpus, arm)
    rec["code"] = curator_code_id()
    # BOOK KNOBS ARE RECORDED BUT NOT FINGERPRINTED. The hash covers CURATION_KNOBS only, and that is
    # correct -- changing a book knob needs a rebuild, not a re-curation, so it must not invalidate an
    # existing journal. But the stamp claims to record "the EFFECTIVE config", and it was silently
    # omitting half of it: max_stale_scans, set per-arm on the 2026-08-26 cadence sweep, was nowhere
    # in the file, so a later reader (and sweep_optimizer, which needs it to replay an arm under the
    # settings it was curated with) had to parse argv to find out. Separate key, so nothing that reads
    # `knobs` or the hash changes behaviour.
    rec["book_knobs"] = {k: _norm(fm.get(k)) for k in sorted(BOOK_KNOBS)}
    # WHAT ACTUALLY RAN, not what was typed. `knobs` records the ALIAS ("llama4"), which is the
    # right fingerprint input -- but it cannot answer "which model produced this curation" if the
    # alias table is ever re-pointed, and before 2026-08-28 an unknown alias silently resolved to
    # mimo, so a stamp could name a model that never ran. Outside `knobs`, so the hash is unchanged
    # and no existing curation is invalidated by recording it.
    try:
        import optimizer as _op
        (_s_id, _s_p), (_e_id, _e_p) = _op.resolve_stage_models(fm)
        rec["models_resolved"] = {"scout": f"{_s_p}:{_s_id}", "event_agent": f"{_e_p}:{_e_id}"}
        _g = _op.resolve_gather_model(fm)
        rec["models_resolved"]["gather"] = f"{_g[1]}:{_g[0]}"
        _o = _op.resolve_org_tagger_model(fm)
        rec["models_resolved"]["org_tagger"] = f"{_o[1]}:{_o[0]}" if _o else None
        if _o:
            # WHICH TAGS, not just which tagger. The cache filename carries the prompt hash, so
            # this is the one string that distinguishes two curations tagged by the same model
            # under different prompts.
            import org_tagger as _ot
            rec["models_resolved"]["org_tagger_cache"] = _ot.cache_path(
                fm.get("org_tagger_model")).name
    except Exception as _e:  # noqa: BLE001 -- provenance enrichment, never a gate on writing a stamp
        rec["models_resolved"] = {"error": f"{type(_e).__name__}: {_e}"}
    rec["argv"] = argv or []
    if note:
        rec["note"] = note
    f = run / "provenance.json"
    f.write_text(json.dumps(rec, indent=1, sort_keys=True, default=str))
    return f


def verify(run_dir: str | Path, fm: dict, corpus: str | Path | None = None,
           arm: str = "fuller") -> dict:
    """Compare a run's recorded inputs against the current profile + canonical corpus.

    Returns {ok, reason, diffs, unverifiable}. `unverifiable` names knobs the run never recorded --
    only ever non-empty for runs stamped before this module existed, and reported rather than
    assumed equal, because silently passing what was never checked is how the original bug survived.
    """
    run = REPO_ROOT / run_dir if not Path(run_dir).is_absolute() else Path(run_dir)
    want = curation_key(fm, corpus or CANON_CORPUS, arm)
    f = run / "provenance.json"
    if not f.exists():
        return {"ok": False, "reason": "unstamped", "diffs": [], "unverifiable": sorted(CURATION_KNOBS),
                "detail": f"{run.name} has no provenance.json, so what it ran under is unknown."}
    got = json.loads(f.read_text())
    diffs, unver, unver_corpus = [], [], False
    gc, wc = got.get("corpus") or {}, want["corpus"]
    if gc.get("path") != wc.get("path"):
        diffs.append(("corpus", gc.get("path"), wc.get("path")))
    elif gc.get("articles") != wc.get("articles"):
        diffs.append(("corpus articles", gc.get("articles"), wc.get("articles")))
    elif gc.get("text") != wc.get("text"):
        # A run stamped before text state was recorded reports None -- unverifiable, not a mismatch.
        if gc.get("text") is None:
            unver_corpus = True
        else:
            def _pct(t):
                n = sum(t.values()) or 1
                return f"{100*t['clean']/n:.1f}% clean / {100*t['live']/n:.1f}% live"
            diffs.append(("corpus TEXT STATE (re-enriched since this run)",
                          _pct(gc["text"]), _pct(wc["text"])))
    if got.get("arm") != want["arm"]:
        diffs.append(("lede arm", got.get("arm"), want["arm"]))
    gk = got.get("knobs") or {}
    for k in sorted(CURATION_KNOBS):
        if k not in gk:                      # never recorded -> reported, never assumed equal
            unver.append(k); continue
        if _norm(gk[k]) != want["knobs"][k]:
            diffs.append((k, gk[k], want["knobs"][k]))
    ok = not diffs
    if unver_corpus:
        unver.append("corpus text state (not recorded by this run)")
    return {"ok": ok, "reason": "" if ok else "curation-knob mismatch", "diffs": diffs,
            "unverifiable": unver, "hash_run": got.get("hash"), "hash_want": want["hash"]}


# --------------------------------------------------------------------------- the publish gate
PUBLISHED = {"docs/cbt.html", "docs/fbt.html", "docs/sbt.html", "docs/fbs.html"}


def is_published(out: str | Path) -> bool:
    p = Path(out)
    try:
        rel = p.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return str(rel) in PUBLISHED


def require_publishable(out: str | Path, page: str, problems: list[str]) -> None:
    """Refuse to overwrite a PUBLISHED page when its inputs are not the canonical ones.

    A hard stop, and only for docs/. Building an off-canon page is a normal, useful thing to do --
    an old curation, a bake-off arm, a news-reading sweep -- so anything written elsewhere is waved
    through with a warning. What is not normal is that page becoming the published one, which is the
    step every incident above had in common. Redirect with --out, or fix the inputs.
    """
    import sys
    if not problems:
        return
    body = "\n".join(f"     - {p}" for p in problems)
    if not is_published(out):
        print(f"  !! {page}: NOT the canonical book --\n{body}\n"
              f"     Writing to {out} anyway (not a published page).", file=sys.stderr)
        return
    raise SystemExit(
        f"\nREFUSING to publish {out}.\n"
        f"  {page} would describe a book that is not the canonical one:\n{body}\n\n"
        f"  The canonical book is: corpus {CANON_CORPUS}, curation {CANON_RUN},\n"
        f"  curation knobs as set in investor_profile.backtest.md.\n\n"
        f"  Either re-curate/rebuild so the inputs match, or send this build somewhere else:\n"
        f"      --out docs_preview/{Path(out).name}\n")
