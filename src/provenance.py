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
CANON_RUN = "data/cbt_3yr_v18"
CANON_SWEEP = "data/sweep_mb2rep.json"

# --------------------------------------------------------------------------- the knob partition
# UPSTREAM of the journal. Changing any of these invalidates an existing curation.
CURATION_KNOBS = frozenset({
    "model", "scout_model", "event_agent_model", "event_agent_effort",
    "picker_model", "picker_effort",           # backtest_gdelt uses the picker for the max_events cap
    "retrieval_engine", "discovery_filter",
    "news_cap", "news_lookback_days", "event_news_cap",
    "relevance_filter", "relevance_keep",
    "scout_articles_per_call", "max_article_chars",
    "min_bundle_articles",
    "max_events", "max_new_events",
    "curator_memory_weeks", "exit_patience_scans", "max_stale_scans", "max_event_scans",
    "rebalance_period",                        # sets the scan cadence -> which weeks exist at all
    "specialty_allow", "mill_block",           # source filters applied as the corpus is read
})

# REPLAY-time only. Free to change; the page just needs a rebuild.
BOOK_KNOBS = frozenset({
    "max_watchlist", "max_agents",             # max_agents is the legacy alias (firehose.watchlist_cap)
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


def curation_key(fm: dict, corpus: str | Path, arm: str = "fuller") -> dict:
    """The inputs a curation is a function of. Two runs with equal keys are the same experiment."""
    knobs = {k: _norm(fm.get(k)) for k in sorted(CURATION_KNOBS)}
    key = {"corpus": corpus_id(corpus), "arm": arm, "knobs": knobs}
    key["hash"] = hashlib.sha256(
        json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return key


def stamp(run_dir: str | Path, fm: dict, corpus: str | Path, arm: str = "fuller",
          argv: list[str] | None = None, note: str = "") -> Path:
    """Write run_dir/provenance.json. Called by the curation producer as the run is created."""
    run = REPO_ROOT / run_dir if not Path(run_dir).is_absolute() else Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    rec = curation_key(fm, corpus, arm)
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
