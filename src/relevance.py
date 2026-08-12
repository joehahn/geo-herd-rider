"""relevance.py — the backtest's stand-in for the forward's search-engine relevance ranker.

WHY THIS EXISTS. The forward and the backtest run byte-identical curator code (agent.process_week),
but they hand it pools built by fundamentally different processes:

  forward   web_search (24 uses x 2 passes) -> the search INDEX has already judged every article
            relevant to a beat query. Observed pools: 3-27 articles/day, 445 on a weekly gather.
  backtest  a regex over GKG title+URL -> EVERY atom match is admitted, relevance never assessed.
            Observed pools: 808 articles/scan.

So `_filter_event`'s 20-slot slice was being drawn from relevance-ranked candidates forward and from
unranked keyword matches in the backtest. Same code, materially different input -- which breaks the
requirement (CLAUDE.md) that the backtest stay a valid proxy for the forward.

This module supplies the missing stage. It sits where the forward's filter sits: at POOL ASSEMBLY,
before the scout -- NOT inside `_filter_event` -- so the scout and the event agents both see a
relevance-filtered pool exactly as they do live.

WHAT IT IS NOT. It does not score, rank or forecast anything about a TICKER (CLAUDE.md #1). It
answers one question per article -- "would a search engine have returned this for one of our beat
queries?" -- which is retrieval, the same class of work the scout tier already does.

AUDITABILITY. Brave's ranker is a black box; this one must not be. `rank_pool` records every
DROPPED article to `dropped_path` so the discard set can be re-read by a blind judge -- the same
method that caught the spam filter discarding 5.5% real catalyst reporting. A filter that cannot be
audited is a filter that silently sets the ceiling on everything downstream.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Structured output. Without it llama4 returned prose around the JSON and 3 of 7 batches failed to
# parse on the first smoke test -- which fail-open turned into "keep the newest N", i.e. silently
# back to recency selection, the exact thing this stage exists to replace.
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["keep"],
          "properties": {"keep": {"type": "array", "items": {"type": "integer"}}}}

SYSTEM = """You are a news-retrieval relevance filter standing in for a web-search index.

You are given a numbered list of article headlines gathered by keyword matching. Your job is to keep
the ones a financial news search engine would have returned for queries about market-moving
catalysts affecting publicly listed companies.

KEEP an article when it reports something that could move a listed company or sector:
  - a concrete catalyst (deal, approval, contract, export ban, sanction, shortage, outage, strike,
    ruling, launch, capacity expansion, supply shock, price move in a traded commodity)
  - a named company or sector with a reason attached
  - policy or geopolitics with an identifiable industrial consequence

DROP an article when it is:
  - machine-generated market plumbing (13F changes, holdings churn, "shares gap up", moving-average
    crosses, short-interest updates, quote/screener pages)
  - a listicle or ranking with no news in it ("N stocks to buy now")
  - general news, sport, lifestyle, crime, or politics with no industrial consequence
  - coverage of a foreign-listed company with no US-listed vehicle involved
  - duplicate coverage of a story already represented earlier in the list

Judge the STORY TYPE, never the publisher, and judge each article ON ITS MERITS -- there is NO quota.
Keep every article that qualifies even if that is most of the list, and keep none if none qualify. A
quiet week SHOULD return a short list; a week with a real supply shock SHOULD return a long one.
When uncertain, KEEP -- a downstream agent can ignore a weak article, but it can never recover one
you dropped.

Output ONLY JSON: {"keep":[<indices>]} using the integer indices shown."""


def _key(arts: list[dict], keep: int) -> str:
    h = hashlib.md5(("|".join(a.get("url", "") for a in arts) + f"#{keep}").encode()).hexdigest()[:12]
    return h


def rank_pool(client, arts: list[dict], keep: int, *, anchor=None, cache_path: str | None = None,
              dropped_path: str | None = None, batch: int = 200, enabled: bool = True) -> list[dict]:
    """Drop the articles a financial-news search index would not have returned. NO QUOTA.

    `enabled=False` returns `arts` untouched, so the pre-2026-08-10 raw-keyword behaviour stays
    reachable for an A/B.

    `keep` is a SAFETY CEILING, not a target: 0 = no ceiling (the intended setting). An earlier
    version made it a quota -- "keep at most N" -- which is wrong in both directions: it forces good
    articles out of a busy week and drags weak ones into a quiet one, and it makes pool size a
    constant when the forward's pool size floats with how much news there was. The model now judges
    each article on its merits and the pool size follows the week.

    `client` is the CHEAP scout-tier client (this is extraction/routing work, not judgment).

    Batched at `batch` headlines per call so a large pool doesn't blow the context. Cached by pool
    urls -- a re-run of the same week is free, which matters because this runs on every scan of
    every sweep."""
    if not enabled or not arts:
        return arts

    cache: dict = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = json.loads(Path(cache_path).read_text())
        except Exception:  # noqa: BLE001 - a truncated cache is not a reason to fail the run
            cache = {}
    ck = _key(arts, keep)
    if ck in cache:
        want = set(cache[ck])
        return [a for a in arts if a.get("url", "") in want]

    kept: list[dict] = []
    for i in range(0, len(arts), batch):
        chunk = arts[i:i + batch]
        listing = "\n".join(f"{n}. [{a.get('source','?')}] {a.get('title','')[:180]}"
                            for n, a in enumerate(chunk))
        try:
            txt = client.complete(SYSTEM,
                                  f"Week ending {anchor.date() if anchor is not None else '?'}. "
                                  f"Articles:\n\n{listing}\n\nOutput the JSON.",
                                  use_web_search=False, stage="agent", json_schema=SCHEMA,
                                  label=f"relevance-{anchor.date() if anchor is not None else 'na'}-{i//batch}")
            idx = json.loads(txt[txt.index("{"):txt.rindex("}") + 1]).get("keep", [])
            picked = [chunk[j] for j in idx if isinstance(j, int) and 0 <= j < len(chunk)]
        except Exception as e:  # noqa: BLE001
            # FAIL OPEN. A ranker outage must not silently shrink the corpus -- that would look like a
            # retrieval finding rather than an infrastructure failure. Keep the batch's newest `share`.
            # FAIL OPEN means keep the WHOLE batch: a filter outage must never look like a quiet week.
            print(f"  relevance batch failed ({type(e).__name__}); keeping all {len(chunk)} unfiltered",
                  flush=True)
            picked = chunk
        kept.extend(picked)

    if keep and len(kept) > keep:          # safety ceiling only; 0 = off
        kept = kept[:keep]
    kept_urls = {a.get("url", "") for a in kept}
    if dropped_path:                       # the audit trail: what a black-box ranker would never give us
        try:
            with open(dropped_path, "a") as fh:
                for a in arts:
                    if a.get("url", "") not in kept_urls:
                        fh.write(json.dumps({"anchor": str(anchor), "source": a.get("source"),
                                             "title": a.get("title"), "url": a.get("url")}) + "\n")
        except Exception:  # noqa: BLE001
            pass
    if cache_path:
        cache[ck] = sorted(kept_urls)
        Path(cache_path).write_text(json.dumps(cache))
    return [a for a in arts if a.get("url", "") in kept_urls]
