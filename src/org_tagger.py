"""org_tagger.py — fill `orgs` on articles that arrive without it.

WHY. GKG stamps `orgs` from V2Organizations on 82% of articles; the websearch gather stamps none.
`agent._scout_batches` seeds COMPANY BUNDLES from `orgs.article_orgs()`, so post-handoff 90% of
what the live system reads falls to the beat/orphan path and company bundling -- the thing built so
the scout sees a ticker's whole news window in one block -- is effectively OFF in production.
Measured 2026-08-27: articles reaching a company bundle of 2+ run at 62.3% in the backtest era and
9.2% post-handoff. This stage exists to close that, and it is FORWARD-ONLY for that reason: the
backtest already has GKG's orgs and must stay at the level it was validated at.

WHY AN LLM AND NOT STRING MATCHING. Both were measured. Matching canon company names against the
article text gets 82% recall at unusable precision -- `nuclear`, `energy`, `investment`, `capital`,
`global`, plus `technology editor` (a byline) and `anthony` (a person). Bundling on those rebuilds
exactly the meaningless mega-bundle agent.py already warns about. A cheap LLM was measured at 83%
right-or-tied against GKG's 28% on the articles that reach a scout call, adjudicated blind by two
different judge families that agreed within two points. See scripts/validate_org_tagger.py.

WHAT IT IS NOT ALLOWED TO DO. Return a company for an article that has none. A macro, policy,
commodity or roundup story SHOULD tag empty and stay on the beat path; forcing a subject on it is
how you get the `United States` bundle holding 5,228 articles. Empty is a correct answer here.

CHEAP BY CONSTRUCTION: only articles that already lack a usable org key are sent, so on the GKG
corpus this touches 19% and on the websearch era 90%.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# BUMP THIS WHEN THE PROMPT CHANGES. The cache key is (model, PROMPT_VERSION), so a prompt fix does
# not silently reuse answers produced by the old wording -- it starts a new cache and the old one
# stays on disk to compare against. Changing SYSTEM without bumping this is the one way to corrupt
# the cache, so they are deliberately adjacent.
PROMPT_VERSION = "v1"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "org_tags"


def cache_path(model: str) -> Path:
    return CACHE_DIR / f"{model.replace('/', '_')}.{PROMPT_VERSION}.jsonl"


def load_cache(model: str) -> dict[str, list]:
    f = cache_path(model)
    if not f.exists():
        return {}
    out: dict[str, list] = {}
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            out[r["u"]] = r["o"]
        except Exception:  # noqa: BLE001 -- one bad line never voids the cache
            continue
    return out


def attach(articles: list[dict], model: str, canon: dict | None = None) -> int:
    """Fill `orgs` from the CACHE only. No LLM, no network, free.

    This is what the curator and the dashboards call. Tagging (below) is a separate, occasional
    job that fills the cache; reading it is instant, so a curation never pays for tagging and a
    re-curation on the same corpus pays nothing at all."""
    import orgs as _o
    cache = load_cache(model)
    if not cache:
        return 0
    n = 0
    for a in articles:
        u = a.get("url")
        if u in cache and not _o.article_orgs(a, canon, None):
            a["orgs"] = cache[u]
            a["orgs_tagger"] = f"{model}.{PROMPT_VERSION}"
            n += 1
    return n

SYSTEM = (
    "You extract company names from financial news. For each numbered article you are given a "
    "headline and the opening of the body -- exactly what a reader would see in a search result.\n\n"
    "Return the COMPANIES the article is ABOUT: its subject, the firm whose business or stock the "
    "article concerns. Listed or private both count -- the answer is used to group articles by "
    "subject firm, and a private company is as groupable as a listed one.\n\n"
    "Rules:\n"
    "- A passing mention is not a subject. 'shares fell alongside the S&P' does not make it about "
    "the S&P.\n"
    "- Never return a country, a government body, an exchange, an index, a regulator, a sector, a "
    "commodity, or a person. 'United States', 'NYSE', 'FDA', 'semiconductors', 'copper' are all "
    "wrong answers.\n"
    "- Never return the publisher, the wire service, or the byline.\n"
    "- Return the company's common name, not its ticker: 'Amgen', not 'AMGN'.\n"
    "- MOST ARTICLES HAVE ONE SUBJECT OR NONE. Returning an empty list is the correct answer for a "
    "policy, macro, commodity or market-roundup story. Do not guess to fill the field."
)

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
          "properties": {"items": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["i", "companies"],
              "properties": {"i": {"type": "integer"},
                             "companies": {"type": "array", "items": {"type": "string"}}}}}}}


def _json_from(text: str) -> dict:
    """Parse whether or not the provider honoured the schema. The Anthropic path ignores
    json_schema outright and some OpenRouter models wrap the object in prose."""
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise
        return json.loads(text[i:j + 1])


def _block(i: int, a: dict, max_chars: int) -> str:
    import lede as _l
    return (f"[{i}] HEADLINE: {(a.get('title') or '').strip()}\n"
            f"BODY: {_l.scout_text(a)[:max_chars].strip()}")


def tag(articles: list[dict], model: str, provider: str = "openrouter", *,
        max_chars: int = 800, batch: int = 12, workers: int = 24,
        canon: dict | None = None, verbose: bool = True) -> dict:
    """Stamp `orgs` on every article in `articles` that has none. Returns a summary dict.

    Mutates in place and marks each tagged article with `orgs_tagger` so a corpus can always say
    WHICH stage produced a key -- GKG's extraction and this one must never be confusable.

    A DEAD BATCH IS A MISSING ANSWER, NOT AN EMPTY ONE. It splits and retries; anything still
    unanswered keeps whatever it had (usually nothing) and is COUNTED, never silently recorded as
    'this article has no company'. That distinction cost a measurement run 17 points on 2026-08-27.
    """
    import llm as _llm, orgs as _o
    cache = load_cache(model)
    todo = [a for a in articles
            if not _o.article_orgs(a, canon, None) and a.get("url") not in cache]
    if not model:
        return {"sent": 0, "tagged": 0, "unanswered": 0, "cached": len(cache)}
    if not todo:
        attach(articles, model, canon)
        return {"sent": 0, "tagged": 0, "unanswered": 0, "cached": len(cache),
                "note": "everything already cached"}
    cli = _llm.make_client(provider, model)
    done: dict[int, list] = {}

    def run(lo: int, chunk: list) -> None:
        stack = [(lo, chunk)]
        for _ in range(3):
            nxt = []
            for off, ch in stack:
                user = ("\n\n".join(_block(k, a, max_chars) for k, a in enumerate(ch)) +
                        f"\n\nReturn one entry per article, i = 0..{len(ch)-1}.")
                try:
                    r = cli.complete(SYSTEM, user, use_web_search=False, label="org-tagger",
                                     stage="scout", json_schema=SCHEMA, effort="low")
                    for it in (_json_from(r).get("items") or []):
                        k = int(it.get("i", -1))
                        if 0 <= k < len(ch):
                            done[off + k] = [c for c in (it.get("companies") or []) if c]
                except Exception as e:  # noqa: BLE001
                    if len(ch) > 1:
                        h = len(ch) // 2
                        nxt += [(off, ch[:h]), (off + h, ch[h:])]
                    elif verbose:
                        print(f"  org_tagger: dropped 1 article ({type(e).__name__})",
                              file=sys.stderr)
            if not nxt:
                return
            stack = nxt

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda s: run(s, todo[s:s + batch]), range(0, len(todo), batch)))

    # APPEND-ONLY. A cache line is (url -> companies) for this (model, prompt) and can never be
    # invalidated by anything except a prompt bump, which changes the filename. Nothing is
    # overwritten, so a re-tag is always additive and a bad run can be diffed rather than mourned.
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with cache_path(model).open("a") as fh:
        for i, a in enumerate(todo):
            if i in done and a.get("url"):
                fh.write(json.dumps({"u": a["url"], "o": done[i]}) + "\n")
    n = attach(articles, model, canon)
    out = {"sent": len(todo), "tagged": n, "unanswered": len(todo) - len(done),
           "model": model, "cache": str(cache_path(model))}
    if verbose:
        print(f"  org_tagger[{model}]: {n:,}/{len(todo):,} articles tagged"
              + (f", {out['unanswered']:,} unanswered (left untagged, NOT recorded as empty)"
                 if out["unanswered"] else ""), file=sys.stderr, flush=True)
    return out
