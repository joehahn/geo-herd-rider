"""article_contract.py — the ONE shape every corpus hands the curator.

THE PROBLEM THIS SOLVES. There are two ingest sources feeding three corpora, read by one curator:

    gkg + wayback ------.
                         >--- backtest / bootstrap / forward corpus ---> ONE curator
    anthropic + tavily -'

The curator can only be source-agnostic if both ingests produce the same article shape. They do not,
natively: GKG stamps `orgs` from V2Organizations and tags beats with their bare names, while the
websearch gather stamps no `orgs` at all and Anthropic appends `before:<date>` to every query. Left
unreconciled, each difference becomes an `if` in shared code -- and every one of those is a place the
reconciliation can be FORGOTTEN. That is not hypothetical: forgetting it at one of nine read sites
silently stripped the gem-score bonus from 38.6% of gem-scored articles while the fingerprint matched
and check_canon reported ALL CONSISTENT.

So the reconciliation happens ONCE, here, at the boundary where a pool is loaded -- never scattered
across the readers.

THE CONTRACT
    url, title, published_date, source   identity + provenance of the article itself
    snippet                              the text the scout reads
    queries[]                            CANONICAL beat names (see gkg.canon_beat)
    orgs[]                               subject companies; [] means "looked, found none"
  optional, source-specific, never required by the curator:
    queries_raw[]  the tags exactly as the engine wrote them -- the immutable record of which query
                   fired. `engine` is inferred from their shape, so they are never overwritten.
    era, engine, pull_date, score

WHAT THIS IS NOT. It does not fetch, filter, rank or drop anything. `normalise_pool` only fills in
fields that are missing and canonicalises ones that exist; it never removes an article. Filtering
belongs to the ingests (each owns its own quality control) and selection belongs to the curator.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED = ("url", "title", "published_date", "source")
CURATOR_FACING = ("snippet", "queries", "orgs")


def normalise_pool(arts: list[dict], canon: dict | None = None, attach_orgs: bool = True) -> list[dict]:
    """Bring `arts` to the contract, IN PLACE, and return it.

    Idempotent: running it twice changes nothing, because canonicalisation is idempotent and an
    article that already carries `orgs` is left alone. That matters -- a pool may be normalised by
    its loader and then again by a builder, and neither should have to know whether the other did.

    `canon` is the company vocabulary for org attachment (orgs.build_canon). Without it, articles
    that have no `orgs` get an empty list rather than a guess. `attach_orgs=False` skips company
    attribution entirely, for callers that only want the beat tags reconciled."""
    try:
        import gkg as _g
        canon_beat = _g.canon_beat
    except Exception:  # noqa: BLE001 -- a checkout without the config still loads its corpus
        canon_beat = None

    for a in arts:
        if canon_beat is not None:
            raw = list(a.get("queries") or [])
            if raw:
                canonical = sorted({c for c in (canon_beat(q) for q in raw) if c})
                if canonical != raw:
                    a.setdefault("queries_raw", raw)      # keep the record of what actually fired
                a["queries"] = canonical
        a.setdefault("snippet", "")
        a.setdefault("queries", [])

    if attach_orgs and canon and any("orgs" not in a for a in arts):
        try:
            import websearch_orgs as _wo
            _wo.attach_orgs(arts, canon)
        except Exception as e:  # noqa: BLE001 -- attribution is enrichment; never block a load
            print(f"  contract: org attribution unavailable ({type(e).__name__}: {e})", file=sys.stderr)
    for a in arts:
        a.setdefault("orgs", [])
    return arts


def check_contract(arts: list[dict], sample: int = 0) -> list[str]:
    """Contract violations in `arts`, as complaints. Empty list = conforming.

    Reports COUNTS rather than the first offender: "3,441 articles have no orgs" is a corpus
    property worth knowing, while "article 17 has no orgs" is noise."""
    out: list[str] = []
    if not arts:
        return ["pool is empty"]
    pool = arts[:sample] if sample else arts
    n = len(pool)
    for f in REQUIRED:
        missing = sum(1 for a in pool if not a.get(f))
        if missing:
            out.append(f"{missing:,} of {n:,} articles have no `{f}`")
    for f in CURATOR_FACING:
        absent = sum(1 for a in pool if f not in a)
        if absent:
            out.append(f"{absent:,} of {n:,} articles are missing the `{f}` KEY "
                       f"(an empty value is fine; an absent key means no stage ever set it)")
    try:
        import gkg as _g
        stale = {q for a in pool for q in (a.get("queries") or []) if _g.canon_beat(q) != q}
        if stale:
            out.append(f"{len(stale)} beat tag(s) are not canonical, e.g. {sorted(stale)[:3]} "
                       f"-- normalise_pool was not run on this pool")
    except Exception:  # noqa: BLE001
        pass
    return out


# --------------------------------------------------------------------------- ingest provenance
def ingest_stamp(source: str, **extra) -> dict:
    """What produced a corpus, recorded INTO the corpus. Returns a dict for pool.json's meta.

    THE GAP THIS CLOSES. A pool.json held `start`, `end`, `chunk_days`, `articles` and nothing else --
    no record of the vocabulary or the ingest settings that built it. So "which beats produced this
    corpus?" was unanswerable from the corpus, and the only link between a curation and its ingest
    config was the profile fingerprint, which covers the PROFILE and not retrieval_config.json.

    That matters for more than tidiness. Ingest parameters belong with the ingest, not in the
    investor profile -- but they can only move once the corpus itself records what it was built
    with, or the link is lost entirely rather than merely indirect.

    `beat_vocab` is a HASH, not the beat list: the list is long, it changes for reasons unrelated to
    the corpus, and what a reader needs is "same or different", answerable by comparison. The names
    are in retrieval_config.json and git history if the difference needs explaining.
    """
    import hashlib
    out = {"source": source, "stamped_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    try:
        cfg = json.loads((REPO_ROOT / "retrieval_config.json").read_text())
        beats = [b["query"] for b in (cfg.get("gem_beats") or [])] + \
                [b["query"] for b in (cfg.get("coverage_beats") or [])]
        out["n_beats"] = len(beats)
        out["beat_vocab"] = hashlib.sha256("\n".join(sorted(beats)).encode()).hexdigest()[:12]
    except Exception as e:  # noqa: BLE001 -- a stamp is provenance; never block an ingest
        out["beat_vocab_error"] = f"{type(e).__name__}: {e}"
    out.update(extra)
    return out


def check_stamp(meta: dict) -> list[str]:
    """Complaints about a corpus's ingest stamp: absent, or built on a different beat vocabulary."""
    out: list[str] = []
    st = (meta or {}).get("ingest")
    if not st:
        return ["corpus has no `ingest` stamp -- it predates ingest_stamp(), so what built it is "
                "unrecorded. Harmless for an existing corpus; every NEW ingest should carry one."]
    now = ingest_stamp(st.get("source", "?"))
    if st.get("beat_vocab") and now.get("beat_vocab") and st["beat_vocab"] != now["beat_vocab"]:
        out.append(f"corpus was ingested on beat vocabulary {st['beat_vocab']} "
                   f"({st.get('n_beats','?')} beats); retrieval_config.json is now "
                   f"{now['beat_vocab']} ({now.get('n_beats','?')}). Re-ingesting would give a "
                   f"DIFFERENT corpus; a replay of this one is unaffected.")
    return out
