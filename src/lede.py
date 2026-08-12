"""lede.py — TWO-SPEED article text: fast-and-biased by day, clean-and-slow overnight.

THE PROBLEM THIS SOLVES. GKG discovery gives a URL, a date and a headline, but the headline names the
theme, not the ticker — the ticker lives in the lede. The only look-ahead-CLEAN way to get that lede
is the Wayback Machine, and on this repo's own measurements (`data/windows/retrieval_stats_forward.json`)
Wayback runs **3.2 items/min, ~6 hours per window, at a 45.4% join rate**. So more than half the pool
reaches the curator as a bare headline, and every prototype iteration costs a night. That single fact
is what made the backtest un-iterable, and the reason `TODO.md` recorded the GKG migration as "not a
clean-backtest speed win".

THE FIX (PWR's, adapted). Stop conflating "the lede" with "a clean lede" — keep two, tagged:

  lede        the look-ahead-CLEAN lede: the article as archived AT-OR-BEFORE the decision date.
              Fetched from archive.org by `wayback.enrich(..., field="lede")`. Slow, throttled,
              partial coverage. `lede_source="wayback"`.
  lede_live   the FAST lede: the page as it exists TODAY, fetched directly, in parallel, no archive.
              Look-ahead-BIASED — today's page may postdate (or be an edit of) the as-of article, so
              it must NEVER be used for a number anyone quotes. `lede_source="live"` only when it
              filled a Wayback MISS; a Wayback hit keeps `lede_source="wayback"`.

The loop that buys back: run `--live` in the morning (minutes) and iterate on prompts, filters and
dashboards against a nearly-full pool; run `--wayback --gentle` overnight (hours, ~1 req/s so
archive.org doesn't throttle) and re-render in the morning against clean text. Which arm a given
render used is decided by `apply()` AT RENDER TIME and recorded per-article, so a dashboard can draw
the clean and biased bands separately instead of silently mixing them.

WHY THE TITLE GATE MATTERS. URLs get recycled: today's page at a 2026-03 URL is sometimes a different
article entirely. `title_consistent()` requires real word overlap between the GKG headline and the
live text before accepting it, so a topic-swapped recycle is rejected rather than quietly attributed
to the old date. PWR learned this one the hard way.

Both arms share `wayback._extract_lede`, deliberately: the same parser on both sides means a
difference between `lede` and `lede_live` reflects the SOURCE, not the extractor, which is what makes
the two comparable at all.

    python src/lede.py --live    data/backtest/pool.json          # minutes  (biased, prototype)
    python src/lede.py --wayback data/backtest/pool.json --gentle # hours    (clean, overnight)
    python src/lede.py --report  data/backtest/pool.json          # coverage by arm, no fetching
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

import wayback

REPO_ROOT = Path(__file__).resolve().parent.parent

# A real browser UA. The default python-urllib UA is what rate-limiters and bot-walls clamp FIRST;
# PWR measured plain 503s on live fetches that succeeded immediately with a browser UA.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")
# Tuned against PWR's live fetcher (LIVE_WORKERS=25, LIVE_TIMEOUT=8, no retries) after a first cut at
# 12 workers / 20s / 3 retries / 0.75s-per-host measured only ~3 articles/s. Live fetching is NOT like
# archive.org: it hits thousands of DIVERSE hosts, so there is no single rate limiter to respect and the
# right posture is high concurrency + a short timeout + FAIL FAST. Retries were the worst offender -- a
# hanging host held a worker for 20+2+20+4+20 = 66s, and a dead link is dead however many times you ask.
# Stragglers are better recovered by a later repass over the misses than by blocking the first pass.
_LIVE_WORKERS = 24
_LIVE_TIMEOUT = 8           # dead links / paywalls / bot-walls fail fast instead of parking a worker
_LIVE_TRIES = 1             # no retry: fail fast, repass the misses later
_HOST_INTERVAL = 0.35       # still space same-host starts (politeness), but 0.75s made a heavy domain
                            # the bottleneck -- yahoo.com alone is ~11% of the pool, so 37k articles
                            # would have serialized ~50 minutes behind one host.

_GENTLE_INTERVAL = 1.0      # overnight Wayback pace (~1 req/s). archive.org tightened its limits after
_GENTLE_WORKERS = 4         # the Oct-2024 outage; the daytime 1.5s/6-worker default trips it on a cold
                            # bulk pull, and a tripped run wastes the whole night.

_STAT = {"requests": 0, "ok": 0, "http_4xx": 0, "http_5xx": 0, "timeout": 0,
         "no_lede": 0, "title_reject": 0, "author": 0, "wall_s": 0.0}

_host_last: dict[str, float] = {}
_host_lock = threading.Lock()


def _reset_stat() -> None:
    for k in _STAT:
        _STAT[k] = 0.0 if isinstance(_STAT[k], float) else 0


def _host_throttle(url: str) -> None:
    """Space request STARTS to the same host by _HOST_INTERVAL. Reserve the slot under the lock and
    sleep outside it, so a burst at one host serializes while other hosts stay fully parallel."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return
    with _host_lock:
        now = time.monotonic()
        slot = max(now, _host_last.get(host, 0.0) + _HOST_INTERVAL)
        _host_last[host] = slot
    wait = slot - now
    if wait > 0:
        time.sleep(wait)


_SESSION = None
_SESSION_LOCK = threading.Lock()


def _session():
    """One pooled requests.Session for the whole process. urllib opened a fresh TCP+TLS connection
    per article; with keep-alive a repeat host costs one round trip instead of a full handshake,
    which matters when ~11% of the pool is a single domain."""
    global _SESSION
    if _SESSION is None:
        with _SESSION_LOCK:
            if _SESSION is None:
                s = requests.Session()
                ad = requests.adapters.HTTPAdapter(pool_connections=_LIVE_WORKERS,
                                                   pool_maxsize=_LIVE_WORKERS * 2, max_retries=0)
                s.mount("http://", ad)
                s.mount("https://", ad)
                s.headers.update({
                    "User-Agent": _UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                _SESSION = s
    return _SESSION


_LAST_MISS: dict[str, str] = {}     # url -> why the fetch failed, so the reason survives into the cache
_REJECTED_TEXT: dict[str, str] = {}  # url -> the text a title-consistency reject threw away (auditable)


def _fetch_html(url: str, tries: int = _LIVE_TRIES) -> str | None:
    """Today's page as text, or None. Fails fast by design (see the _LIVE_* constants). On failure the
    REASON is recorded in _LAST_MISS -- an aggregate counter cannot tell you which articles lost text
    or when, and "why is the text missing" is the question the dashboard has to answer."""
    for attempt in range(max(1, tries)):
        _host_throttle(url)
        t0 = time.monotonic()
        _STAT["requests"] += 1
        try:
            r = _session().get(url, timeout=_LIVE_TIMEOUT, allow_redirects=True, stream=False)
            _STAT["wall_s"] += time.monotonic() - t0
            if r.status_code >= 400:
                _STAT["http_4xx" if r.status_code < 500 else "http_5xx"] += 1
                # 404/410 = the article is genuinely GONE; 401/403 = a paywall or a bot-wall, which
                # this code cannot tell apart (both refuse an anonymous client). Kept as separate
                # labels so the dashboard reports what was measured, not a guess.
                _LAST_MISS[url] = ("removed" if r.status_code in (404, 410) else
                                   "blocked_or_paywalled" if r.status_code in (401, 403) else
                                   f"http_{r.status_code}")
                return None
            _STAT["ok"] += 1
            return r.text
        except requests.exceptions.RequestException:
            _STAT["wall_s"] += time.monotonic() - t0
            _STAT["timeout"] += 1
            _LAST_MISS[url] = "unreachable"
        if attempt < tries - 1:
            time.sleep(1.0)
    return None


_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "at", "by", "with", "from",
         "as", "is", "are", "was", "were", "be", "its", "it", "this", "that", "after", "over",
         "new", "says", "said", "amid", "how", "why", "what", "will", "has", "have"}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (s or "").lower()) if w not in _STOP}


def title_consistent(title: str, text: str, min_overlap: int = 2) -> bool:
    """Does `text` plausibly belong to the article headlined `title`?

    URLs get recycled and CMSs re-point old paths at new stories, so today's page at a months-old URL
    is sometimes a completely different article. Accepting that text would attach NEW content to an
    OLD date — look-ahead contamination of the worst kind, because it looks like a legitimate hit.
    The test is deliberately permissive (>= `min_overlap` shared content words, or the title's own
    words being few enough that overlap can't be demanded): the goal is to reject topic swaps, not to
    demand the lede restate the headline."""
    tw = _words(title)
    if len(tw) < min_overlap:
        return True                                  # too short to judge; don't reject on no evidence
    return len(tw & _words(text)) >= min_overlap


def live_lede(url: str, title: str = "", source: str = "") -> tuple[str, str] | str:
    """Today's (lede, author) for `url`, or None. Uses the SAME extractor as the Wayback arm so the
    two arms stay comparable, and rejects a topic-swapped URL recycle via `title_consistent`.

    The byline is a second parse of html we already hold -- no extra network -- and is cleaned of
    wire/newsroom pseudo-authors so a per-author view tracks real writers, not publishers."""
    h = _fetch_html(url)
    if not h:
        return _LAST_MISS.get(url, "unreachable")   # why, not just "no"
    # RECYCLE CHECK FIRST, and headline-to-headline. The page publishes its own title; comparing that
    # with the stored GKG headline is apples-to-apples. The previous test compared the headline against
    # extracted BODY text and rejected 73.5% of it wrongly, because a body paragraph does not restate
    # its own headline. Body overlap survives only as a fallback when the page carries no title at all.
    page_title = wayback.extract_page_title(h)
    if title and page_title and not title_consistent(title, page_title):
        _STAT["title_reject"] += 1
        _LAST_MISS[url] = "url_recycled"
        _REJECTED_TEXT[url] = f"[page title] {page_title[:360]}"
        return "url_recycled"
    txt = wayback._extract_lede(h)
    if not txt:
        _STAT["no_lede"] += 1
        # A 200 with no extractable prose is overwhelmingly a paywall/consent interstitial: the page
        # loaded, it just isn't the article. This is as close as the code gets to "paywalled" -- it is
        # an INFERENCE from page shape, not a detected paywall, and is named accordingly.
        return "no_text_on_page"
    if title and not page_title and not title_consistent(title, txt):
        # fallback path only: no page title to compare, so fall back to body overlap
        _STAT["title_reject"] += 1
        # KEEP the rejected text. This gate is the single largest cause of missing body text, and its
        # threshold (>=2 shared content words) is a judgement call -- so the evidence has to survive or
        # the gate can never be checked. Stored on the miss record, never on `lede_live`, so it cannot
        # leak into what the curator reads.
        _LAST_MISS[url] = "url_recycled"
        _REJECTED_TEXT[url] = txt[:400]
        return "url_recycled"
    author = wayback.extract_author(h, publisher=source)
    if author:
        _STAT["author"] += 1
    return txt, author


def enrich_live(articles: list[dict], cache_path: str | None = None, max_chars: int = 280,
                workers: int = _LIVE_WORKERS, only_wayback_misses: bool = False,
                stats_path: str | None = None) -> list[dict]:
    """Fill `lede_live` on every article that lacks one, in parallel. IDEMPOTENT: an article that
    already has a `lede_live` is skipped, so a re-run only fills gaps and costs nothing for the rest.

    `only_wayback_misses=True` restricts the fetch to articles with no clean `lede` — the cheap mode
    once an overnight Wayback pass has already covered most of the pool.

    A confirmed miss is cached as `false` so it isn't retried forever; nothing else is cached as a
    permanent failure. Sets `lede_source="live"` ONLY where there is no clean lede to displace — a
    Wayback hit keeps its own provenance and merely gains an alternative rendering."""
    cache: dict = {}
    if cache_path and os.path.exists(cache_path):
        try:
            cache = json.loads(Path(cache_path).read_text())
        except json.JSONDecodeError:
            cache = {}

    todo: list[dict] = []
    seen: set[str] = set()
    for a in articles:
        url = a.get("url", "")
        if not url or a.get("lede_live") or url in seen:
            continue
        if only_wayback_misses and a.get("lede"):
            continue
        c = cache.get(url)
        if c is False or (isinstance(c, dict) and c.get("miss")):
            continue                                 # already attempted and failed, with or without a reason
        if isinstance(c, (str, dict)):
            continue                                 # already in cache; applied in the loop below
        seen.add(url)
        todo.append(a)

    _reset_stat()
    t0 = time.monotonic()
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(live_lede, a["url"], a.get("title", ""), a.get("source", "")): a["url"]
                    for a in todo}
            done = 0
            for fut in as_completed(futs):
                url = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001 — one bad page must not sink the pass
                    res = f"error_{type(e).__name__}"
                if isinstance(res, tuple):
                    cache[url] = {"lede": res[0], "author": res[1]}
                else:
                    rec = {"miss": str(res or "unknown")}
                    if url in _REJECTED_TEXT:       # evidence for auditing the recycle gate
                        rec["rejected_text"] = _REJECTED_TEXT.pop(url)
                    cache[url] = rec
                done += 1
                if done % 200 == 0:
                    print(f"    live ledes: {done}/{len(todo)} fetched", file=sys.stderr, flush=True)
        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            tmp = f"{cache_path}.tmp"
            Path(tmp).write_text(json.dumps(cache))
            os.replace(tmp, cache_path)

    n_hit = 0
    for a in articles:
        if a.get("lede_live"):
            a.pop("text_miss", None)     # already filled on an earlier pass: no miss to explain
            continue
        v = cache.get(a.get("url", ""))
        if isinstance(v, str):                       # legacy cache entry: bare lede string
            v = {"lede": v, "author": ""}
        if isinstance(v, dict) and v.get("miss"):
            a["text_miss"] = v["miss"]               # why this article reaches the curator headline-only
        elif v is False:
            a["text_miss"] = "unknown"               # legacy cache entry, recorded before reasons existed
        if isinstance(v, dict) and v.get("lede"):
            a.pop("text_miss", None)     # it has text now; a stale reason would double-count forever
            a["lede_live"] = v["lede"][:max_chars]
            if v.get("author") and not a.get("author"):
                a["author"] = v["author"]
                a["author_source"] = "live"
            n_hit += 1
            if not a.get("lede"):
                a["lede_source"] = "live"            # a Wayback MISS filled by the biased arm
    el = time.monotonic() - t0
    if stats_path:
        import retstats
        miss = {}
        for x in articles:
            if x.get("text_miss"):
                miss[x["text_miss"]] = miss.get(x["text_miss"], 0) + 1
        # CORPUS-wide, not this-pass: n_hit counts only articles FILLED on this run, and a repass over
        # previous misses fills almost nothing by construction. Reporting n_hit here made a 30k-article
        # corpus look like it had 257 ledes.
        retstats.merge(stats_path, "lede_live", {
            "articles": len(articles),
            "with_text": sum(1 for x in articles if x.get("lede_live")),
            "filled_this_pass": n_hit,
            "with_author": sum(1 for x in articles if x.get("author")),
            "coverage_pct": round(100 * sum(1 for x in articles if x.get("lede_live"))
                                  / max(len(articles), 1), 1),
            "miss_reasons": dict(sorted(miss.items(), key=lambda kv: -kv[1])),
            "fetched_this_pass": len(todo), "elapsed_s": round(el, 1),
            "per_sec": round(len(todo) / max(el, 1e-9), 1)})
    print(f"  live enrich: {n_hit} articles now carry lede_live ({len(todo)} fetched this pass, "
          f"{_STAT['http_4xx']} dead, {_STAT['http_5xx']} 5xx, {_STAT['timeout']} timeout, "
          f"{_STAT['no_lede']} no-lede, {_STAT['title_reject']} title-rejected, "
          f"{_STAT['author']} bylines) in {el:.0f}s ({len(todo) / max(el, 1e-9):.1f}/s)",
          file=sys.stderr)
    return articles


def enrich_wayback(articles: list[dict], cutoff: str, cache_path: str | None = None,
                   max_chars: int = 280, gentle: bool = False, fetch: bool = True,
                   stats_path: str | None = None, per_article: bool = False,
                   grace_days: int = 7) -> list[dict]:
    """Fill the look-ahead-CLEAN `lede` from archive.org. Thin wrapper over `wayback.enrich` that
    targets the `lede` field instead of `snippet`.

    `per_article=True` asks archive.org for each article as of ITS OWN publish date plus `grace_days`
    -- the look-ahead-correct mode. The single-`cutoff` mode is only valid when every article shares
    one decision date (a per-week slice). A corpus-wide backfill under one late cutoff fetches pages
    as they stood months after publication, which is the contamination the archived lede exists to
    prevent. `grace_days` exists because archive.org rarely captures on the day of publication; 7 days
    matches the rebalance cadence, so it is bounded by what the curator would have seen at that
    week's scan.

    `gentle=True` selects the overnight pace (~1 req/s, 4 workers). `fetch=False` applies only
    already-cached ledes and makes no archive.org calls at all — the mode a repeat/bake-off run uses
    so its cost is zero and its inputs are byte-identical to the run it is being compared against."""
    pc = {}
    if per_article:
        import datetime as _dt
        for a in articles:
            d = (a.get("published_date") or "")[:10]
            if not d:
                continue
            try:
                pc[a.get("url", "")] = (_dt.date.fromisoformat(d)
                                        + _dt.timedelta(days=grace_days)).isoformat()
            except ValueError:
                continue
    return wayback.enrich(
        articles, cutoff, cache_path=cache_path, max_chars=max_chars, stats_path=stats_path,
        field="lede", fetch=fetch, per_cutoff=pc,
        workers=_GENTLE_WORKERS if gentle else None,
        min_interval=_GENTLE_INTERVAL if gentle else None)


# --------------------------------------------------------------------------- render-time selection
ARMS = ("clean", "fuller", "fast", "live-only")


def apply(articles: list[dict], arm: str = "clean", max_chars: int = 280) -> dict:
    """Fill each article's `snippet` — the field every downstream consumer reads — from the chosen
    arm, and return a coverage tally. This is the ONE place the clean/biased tradeoff is made, and it
    is made at RENDER time, not at fetch time, so the same pool can be re-rendered either way without
    re-fetching anything.

      clean      lede only. Look-ahead-safe. The ONLY arm whose numbers may be reported.
      fuller     lede, falling back to lede_live. Mostly clean, partially biased -- for iterating
                 when coverage matters more than a quotable number.
      fast       lede_live, falling back to lede. Maximum coverage, mostly biased. Prototype only.
      live-only  lede_live only. The "do we even need Wayback" control.

    Articles with nothing for the chosen arm keep `snippet = title` (headline-only), exactly as
    before, and are counted in the returned tally so a dashboard can show the headline-only band."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
    tally = {"arm": arm, "total": len(articles), "wayback": 0, "live": 0, "headline_only": 0}
    for a in articles:
        clean, live = a.get("lede"), a.get("lede_live")
        if arm == "clean":
            pick, src = clean, "wayback"
        elif arm == "fuller":
            pick, src = (clean, "wayback") if clean else (live, "live")
        elif arm == "fast":
            pick, src = (live, "live") if live else (clean, "wayback")
        else:
            pick, src = live, "live"
        if pick:
            a["snippet"] = pick[:max_chars]
            a["snippet_source"] = src
            tally[src] += 1
        else:
            a["snippet"] = a.get("title", "")
            a["snippet_source"] = "headline"
            tally["headline_only"] += 1
    tally["coverage_pct"] = round(100 * (tally["wayback"] + tally["live"]) / max(len(articles), 1), 1)
    return tally


def coverage(articles: list[dict]) -> dict:
    """Per-arm coverage of a pool, computed without fetching anything — the number that says whether
    tonight's Wayback pass is still worth running."""
    n = len(articles)
    n_clean = sum(1 for a in articles if a.get("lede"))
    n_live = sum(1 for a in articles if a.get("lede_live"))
    n_either = sum(1 for a in articles if a.get("lede") or a.get("lede_live"))
    n_both = sum(1 for a in articles if a.get("lede") and a.get("lede_live"))
    pct = lambda k: round(100 * k / n, 1) if n else 0.0   # noqa: E731
    return {"articles": n,
            "clean_wayback": n_clean, "clean_pct": pct(n_clean),
            "live": n_live, "live_pct": pct(n_live),
            "either": n_either, "either_pct": pct(n_either),
            "both": n_both,
            "live_only": n_either - n_clean, "headline_only": n - n_either,
            "headline_only_pct": pct(n - n_either)}


# --------------------------------------------------------------------------- CLI
def _load(path: Path) -> tuple[list[dict], dict]:
    d = json.loads(path.read_text())
    if isinstance(d, list):
        return d, {}
    return d.get("articles", d.get("pool", [])), d


def _save(path: Path, arts: list[dict], envelope: dict) -> None:
    out = dict(envelope)
    key = "articles" if "articles" in envelope or not envelope else ("pool" if "pool" in envelope else "articles")
    out[key] = arts
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(out, default=str))
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pool", help="pool json (a list of articles, or {articles|pool: [...]})")
    ap.add_argument("--live", action="store_true", help="fast biased pass: fill lede_live")
    ap.add_argument("--wayback", action="store_true", help="clean pass: fill lede from archive.org")
    ap.add_argument("--report", action="store_true", help="print per-arm coverage; fetch nothing")
    ap.add_argument("--gentle", action="store_true", help="overnight Wayback pace (~1 req/s, 4 workers)")
    ap.add_argument("--cutoff", default=None, help="as-of date for the Wayback arm (default: pool max date)")
    ap.add_argument("--misses-only", action="store_true", help="live pass: only articles with no clean lede")
    ap.add_argument("--cache-dir", default=None, help="where the lede caches live (default: alongside the pool)")
    a = ap.parse_args(argv)

    path = Path(a.pool)
    arts, env = _load(path)
    if not arts:
        print(f"{path}: no articles", file=sys.stderr)
        return 1
    cdir = Path(a.cache_dir) if a.cache_dir else path.parent
    cdir.mkdir(parents=True, exist_ok=True)

    if a.report or not (a.live or a.wayback):
        print(json.dumps(coverage(arts), indent=2))
        return 0

    if a.live:
        enrich_live(arts, cache_path=str(cdir / "lede_live_cache.json"),
                    only_wayback_misses=a.misses_only)
    if a.wayback:
        cutoff = a.cutoff or max((x.get("published_date", "") for x in arts), default="")[:10]
        if not cutoff:
            print("no cutoff and no dated articles; pass --cutoff", file=sys.stderr)
            return 1
        enrich_wayback(arts, cutoff, cache_path=str(cdir / "wayback_cache.json"), gentle=a.gentle,
                       stats_path=str(REPO_ROOT / "data" / "windows" / "retrieval_stats.json"))

    _save(path, arts, env)
    print(json.dumps(coverage(arts), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
