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

import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# BUMP THIS WHEN THE PROMPT CHANGES. The cache key is (model, PROMPT_VERSION), so a prompt fix does
# not silently reuse answers produced by the old wording -- it starts a new cache and the old one
# stays on disk to compare against. Changing SYSTEM without bumping this is the one way to corrupt
# the cache, so they are deliberately adjacent.
PROMPT_LABEL = "v1"          # human-readable, for the filename only
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "org_tags"


def prompt_id() -> str:
    """8 hex of the ACTUAL prompt text. The cache filename carries this, so editing SYSTEM starts a
    new cache automatically and the old answers stay on disk beside it.

    It replaces a hand-maintained PROMPT_VERSION constant. That constant was the kind of thing this
    repo keeps getting bitten by: correct only while someone remembers to bump it, silent when they
    do not, and the failure -- answers from two different prompts mixed in one file -- is invisible
    at every later step. Derived, it cannot be forgotten."""
    return hashlib.sha256(SYSTEM.encode()).hexdigest()[:8]


def resolve(model: str) -> tuple[str, str]:
    """Alias -> (model_id, provider), REFUSING an alias that is not in the table.

    optimizer.resolve_curator_model falls back to mimo for any unknown name -- deliberate for the
    curator stages, actively wrong here. The cache used to be keyed by the ALIAS, so
    `org_tagger_model: deepsek4` (one letter out) would have run MIMO and stored its answers in a
    file named after the typo: a cache lying about what produced it, invisible forever after. Now
    the alias is validated AND the cache is keyed by the RESOLVED id, so the filename is a fact
    about what ran rather than about what someone meant to type."""
    import optimizer as _op
    key = str(model).strip().lower()
    if key not in _op.CURATOR_MODELS:
        raise ValueError(
            f"org_tagger_model={model!r} is not a known alias. Known: "
            f"{', '.join(sorted(_op.CURATOR_MODELS))}. Refusing rather than falling back to mimo, "
            f"which is what optimizer.resolve_curator_model would do.")
    return _op.CURATOR_MODELS[key]


def cache_path(model: str) -> Path:
    """Keyed by the RESOLVED model id + a hash of the prompt: both facts about what produced the
    answers, neither maintained by hand."""
    mid = resolve(model)[0]
    return CACHE_DIR / f"{mid.replace('/', '_')}.{PROMPT_LABEL}-{prompt_id()}.jsonl"


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


def attach(articles: list[dict], model: str, canon: dict | None = None,
           force: bool = False) -> int:
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
        if u in cache and (force or not _o.article_orgs(a, canon, None)):
            a["orgs"] = cache[u]
            a["orgs_tagger"] = f"{resolve(model)[0]}.{PROMPT_LABEL}-{prompt_id()}"
            n += 1
    return n


def coverage(articles: list[dict], canon: dict | None = None, model: str | None = None) -> dict:
    """How many articles STILL have no org key after attaching -- the number nobody was told.

    attach() deliberately never calls an LLM, so an article the cache has not seen stays untagged
    and the curation reads it exactly as it did before the tagger existed. That is the right
    behaviour (a curation must not depend on a provider being reachable, and a re-curation must
    cost nothing), but on its own it is SILENT: a cron that missed a week, or a backfill nobody
    ran, degrades bundling with nothing on screen to say so. This is what the caller prints."""
    import orgs as _o
    cache = load_cache(model) if model else {}
    # "NO COMPANY KEY" IS TWO DIFFERENT FACTS AND ONLY ONE IS A PROBLEM:
    #   unseen     -- the cache has never been asked about this article. A real gap; tag it.
    #   no_company -- the tagger WAS asked and said the article has no subject firm. That is the
    #                 correct answer for a macro, policy, commodity or roundup story, and roughly
    #                 half of them are. It is not a gap and must never be reported as one.
    # Reported together, the warning cried wolf permanently: 3,629 "untagged" on a corpus where the
    # tagger had already answered for every article it was asked about.
    unseen, nocomp = [], []
    for a in articles:
        if _o.article_orgs(a, canon, None):
            continue
        (nocomp if a.get("url") in cache else unseen).append(a)
    return {"n": len(articles), "unseen": len(unseen), "no_company": len(nocomp),
            "pct_unseen": round(100 * len(unseen) / max(len(articles), 1), 1)}

# THE TASK IS EXTRACTION, NOT IDENTIFICATION. The first version of this prompt asked for the
# article's SUBJECT company and forbade passing mentions, sectors and commodities. It scored 83%
# right-or-tied against GKG under a blind judge -- and it made the curation WORSE (events opened
# 72 -> 59, distinct tickers 386 -> 315, cull-at-birth 27.8% -> 30.5%), because precision is the
# wrong objective here and the eval that blessed it asked the wrong question.
#
# WHAT BUNDLING ACTUALLY RUNS ON is REPLICATION. orgs.group(): "An article joins EVERY org it names.
# A two-company story is real evidence for both, and assigning it to one arbitrarily would discard
# signal." GKG's V2Organizations does not identify a subject at all -- it lists every organisation
# mentioned -- and that generosity is what makes bundles thick:
#
#                        memberships/article   articles in 2+ bundles   no key
#     GKG (backtest)            1.16                   24.9%             17%
#     subject-only prompt       0.59                    5.8%             49%
#
# Half the connective tissue. Thin bundles, 68% singletons, and a singleton company bundle is WORSE
# than no tag at all: with min_bundle_articles=1 it pulls the article out of its beat bundle and
# shows it alone, with less context than before tagging.
#
# The repo already learned this once. `max_article_orgs` capped orgs-per-article and was deleted the
# same day as "redundant and harmful" for deleting genuine multi-company catalyst articles. Asking a
# model for the subject only is that knob again, written as English instead of code.
#
# TARGETS, fixed before re-tagging so they cannot be fitted afterwards: >=1.0 memberships/article
# and >=18% of articles in 2+ bundles. Below that, do not spend a curation on it.
SYSTEM = (
    "You list the companies a financial news article gives information about. This is an EXTRACTION "
    "task: the output is used to group articles by company, so an article that informs a reader "
    "about three companies should list all three.\n\n"
    "List EVERY company the article says something about — the one it centres on AND the ones it "
    "compares, names as a rival, supplier, customer, acquirer, target, or beneficiary. A company "
    "does not have to be the subject to belong in the list. Listed or private both count.\n\n"
    "Also list the SECTOR OR THEME when the article is about one — 'memory chips', 'uranium', "
    "'data centers', 'gold miners'. Those group commodity and policy stories that name no single "
    "firm, and they are as useful for grouping as a company name.\n\n"
    "Rules:\n"
    "- Do NOT return a country, a government body, an exchange, an index, a regulator, or a person. "
    "'United States', 'NYSE', 'FDA', 'the Fed' are wrong answers.\n"
    "- Do NOT return the publisher, the wire service, or the byline.\n"
    "- Return common names, not tickers: 'Amgen', not 'AMGN'.\n"
    "- An article naming no company and no clear theme returns an empty list. That is a correct "
    "answer for a pure macro or market-summary story — but prefer a theme where one is genuinely "
    "present, since a themed group is more useful than nothing.\n"
    "- Typical articles yield one to three entries. Do not pad, and do not reduce to one."
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
        canon: dict | None = None, verbose: bool = True, refresh: bool = False) -> dict:
    """Stamp `orgs` on every article in `articles` that has none. Returns a summary dict.

    Mutates in place and marks each tagged article with `orgs_tagger` so a corpus can always say
    WHICH stage produced a key -- GKG's extraction and this one must never be confusable.

    A DEAD BATCH IS A MISSING ANSWER, NOT AN EMPTY ONE. It splits and retries; anything still
    unanswered keeps whatever it had (usually nothing) and is COUNTED, never silently recorded as
    'this article has no company'. That distinction cost a measurement run 17 points on 2026-08-27.
    """
    import llm as _llm, orgs as _o
    cache = {} if refresh else load_cache(model)
    # `refresh` re-sends articles ALREADY cached. Safe because the cache is append-only and
    # load_cache lets the LAST line for a url win, so a re-tag overrides without rewriting the file
    # and the superseded answer stays on disk to diff against. Without this, "fix the tagging later"
    # was only true for articles that had never been tagged -- the ones least likely to be wrong.
    todo = [a for a in articles
            if refresh or (not _o.article_orgs(a, canon, None) and a.get("url") not in cache)]
    if not model:
        return {"sent": 0, "tagged": 0, "unanswered": 0, "cached": len(cache)}
    if not todo:
        attach(articles, model, canon)
        return {"sent": 0, "tagged": 0, "unanswered": 0, "cached": len(cache),
                "note": "everything already cached"}
    # THE CACHE IS KEYED BY THE ALIAS, THE CLIENT NEEDS THE RESOLVED ID. `deepseek4` is a stable,
    # human-readable cache filename; `deepseek/deepseek-v4-flash` is what the API accepts. Passing
    # the alias straight to make_client returns a 400 "not a valid model ID" on every call, which
    # the retry loop would then swallow as 2,431 unanswered articles.
    _id, _prov = resolve(model)
    cli = _llm.make_client(provider or _prov, _id)
    done: dict[int, list] = {}

    _lock = threading.Lock()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _fh = cache_path(model).open("a")

    def _flush(off: int, ch: list, got: dict) -> None:
        """Persist a batch THE MOMENT IT LANDS, not when the pool drains.

        The first version buffered every answer and wrote once at the end, which made a killed run
        lose all of it -- and the commit message claimed the opposite. On a 2,400-article corpus
        that is 7 wasted minutes; on the 18,564-article GKG corpus it is half an hour and a real
        amount of money, lost to one Ctrl-C. Appending per batch under a lock makes the claim true:
        whatever finished is on disk, and re-running sends only what is still missing."""
        with _lock:
            for k, a in enumerate(ch):
                if k in got and a.get("url"):
                    _fh.write(json.dumps({"u": a["url"], "o": got[k]}) + "\n")
            _fh.flush()

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
                    _got = {}
                    for it in (_json_from(r).get("items") or []):
                        k = int(it.get("i", -1))
                        if 0 <= k < len(ch):
                            _got[k] = [c for c in (it.get("companies") or []) if c]
                            done[off + k] = _got[k]
                    _flush(off, ch, _got)
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
    _fh.close()
    n = attach(articles, model, canon, force=refresh)
    out = {"sent": len(todo), "answered": len(done), "unanswered": len(todo) - len(done),
           "attached": n,          # includes articles already in the cache, so it can exceed `sent`
           "model": model, "cache": str(cache_path(model))}
    if verbose:
        print(f"  org_tagger[{model}]: {len(done):,}/{len(todo):,} newly tagged, "
              f"{n:,} attached"
              + (f", {out['unanswered']:,} unanswered (left untagged, NOT recorded as empty)"
                 if out["unanswered"] else ""), file=sys.stderr, flush=True)
    return out
