"""gkg.py — look-ahead-clean historical news DISCOVERY via GDELT's GKG table on BigQuery.

The fast, reliable replacement for `gdelt.py`'s DOC-API discovery, with the SAME output contract, so
it is a drop-in wherever `gdelt.pool(...)` is called. Three reasons it exists, all measured on this
repo's own runs (`data/windows/retrieval_stats_forward.json`, `data/backtest_bwet_v2/`):

  1. RELIABILITY. The DOC API rate-limits hard: 130 requests -> 87 HTTP 429s (67% failure), and it
     has gone fully dark for 10h+ (2026-06-30/07-01, which blocked the GDX cold scan). One
     date-partitioned SQL query has no per-request throttle and no burst behaviour to lose to.
  2. SPEED. The same runs measured 28 items/min and ~3400s wall for ONE window; the 114-week full
     run is unworkable on the DOC API. BigQuery scans a window in seconds-to-minutes.
  3. RECALL. The DOC API is lexical, so every sector beat had to be ANDed with
     `_VEHICLE = (stock OR ETF OR shares OR equities)` just to ask "is this about a listed company",
     and a Boolean AND can only SHRINK recall -- an "overlooked automation ETF quietly doubled"
     slips past `robotics stocks` (TODO.md, "the keyword ceiling"). GKG answers that question
     semantically instead, via TWO tests that don't require the headline to say "stock":
     `V2Themes` (GDELT's own topic classification -- is this market news at all) AND
     `V2Organizations` (which companies the article is ABOUT, by character offset).
     NOTE, measured the hard way: the org test ALONE is NOT sufficient. A first cut relied on it
     without the theme gate and returned 320,798 articles for 56 days -- general news that merely
     named an organization, topped by a radio network. "Names an org" is not "is about a listed
     company". Both tests are load-bearing.

RETRIEVAL SURFACE -- the one real capability loss vs the DOC API. GDELT's DOC API searches article
FULL TEXT; GKG's `Extras` gives only the page TITLE, so we match on title + URL. An article whose
keyword appears only in its body is structurally unreachable here. Measured overlap with a cached
DOC pool over the same window was 14.5%, and that is NOT a bug to tune away -- the two engines
select on different surfaces, so "superset of the DOC pool" can never be the acceptance test.

WHAT IT DOES NOT FIX (recorded so it isn't over-claimed): GKG indexes what GDELT crawls, and GDELT
does not crawl the Cloudflare-walled niche gem press (etf.com and friends). The coverage ceiling on
early under-the-radar coverage is UNCHANGED -- this buys a faster, more reliable dev loop, not a
better edge. The forward paper trade is still the only clean scoreboard (CLAUDE.md #4/#6).

LOOK-AHEAD HYGIENE: rows are bounded server-side by `_PARTITIONTIME`, which is the GKG ingest time,
so a query for a past window returns only what GDELT had actually seen by then. This is real
point-in-time retrieval, the same guarantee `gdelt.py` relied on and the reason neither Tavily nor
Anthropic web search can be used for a clean backtest.

CONTENT: GKG carries no article body -- it gives URL, publish date, source, page title (from the
`Extras` column) and tone. The title-level record is exactly what the DOC API gave, so nothing is
lost; ledes still come from `wayback.py` (clean, slow) or a live fetch (fast, biased). See Stage 2.

Vocabulary comes from `retrieval_config.json`, the ONE source of truth shared with the forward
gather -- see that file's `_how_it_is_rendered`.

    python src/gkg.py --validate --start 2026-05-01 --end 2026-05-31   # smoke-test one window
    python src/gkg.py --cost                                           # BigQuery spend so far
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import functools
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

import retstats
import trace

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "retrieval_config.json"
CACHE_DIR = REPO_ROOT / "data" / "gkg_cache"          # raw BigQuery rows (gitignored)
COST_LOG = REPO_ROOT / "data" / "gkg_cache" / "bigquery_cost.jsonl"

# GKG columns we pull. Kept as narrow as possible: BigQuery bills on BYTES SCANNED, and the wide
# theme/tone columns are most of the table's volume, so every column added here costs real money on
# every query. V2Themes is REFERENCED in the WHERE clause but never SELECTed -- that still bills for
# it (measured: 3.6 -> 8.7 GB per 14 days), and it is worth it: without that gate the pool is 28x
# larger and mostly not financial news.
_FIELDS = ("DATE", "SourceCommonName", "DocumentIdentifier", "V2Organizations", "V2Tone", "Extras")

# Corporate suffixes stripped before de-duplicating org names ("Cipher Mining Inc" == "Cipher Mining").
_ORG_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|lp|plc|sa|nv|ag|holdings?|"
    r"group|technologies|technology)\b\.?\s*$", re.IGNORECASE)

# process-cumulative retrieval-health counters; pool() snapshots them per run (mirrors gdelt._STAT)
_STAT_ZERO = {"queries": 0, "rows": 0, "scanned_gb": 0.0, "billed_gb": 0.0,
              "dropped_blocklist": 0, "dropped_spam": 0, "dropped_no_beat": 0, "dropped_no_org": 0, "dropped_syndicated": 0,
              "rescued_named_ticker": 0}
_STAT = dict(_STAT_ZERO)


def _reset_stat() -> None:
    _STAT.update(_STAT_ZERO)


# --------------------------------------------------------------------------- config
_CFG: dict | None = None


def config() -> dict:
    """retrieval_config.json, parsed once."""
    global _CFG
    if _CFG is None:
        _CFG = json.loads(CONFIG_FILE.read_text())
    return _CFG


def beats() -> list[dict]:
    """Every beat (gem + coverage) as {query, origin, keywords}. The ORDER is gem-then-coverage,
    matching forward_gather's two-pass sweep, so beat attribution reads the same on both paths."""
    c = config()
    return list(c["gem_beats"]) + list(c["coverage_beats"])


def _mill_block(profile: str | None = None) -> list[str]:
    """Domains to drop, read from the investor profile's `mill_block` -- the SAME list the forward
    gather passes to web_search as blocked_domains, so backtest and forward exclude the same mills.
    Deliberately NOT duplicated into retrieval_config.json: one home per knob (CLAUDE.md)."""
    from optimizer import load_financial_model
    p = profile or str(REPO_ROOT / "investor_profile.backtest.md")
    return list(load_financial_model(p).get("mill_block") or [])


def _theme_regex() -> str:
    """Alternation of `engine.market_themes` for the V2Themes gate -- GDELT's own topic classification
    of the article. This is what asks "is this financial news at all", replacing the DOC API's lexical
    `(stock OR ETF OR shares)` AND-clause with a semantic test that does NOT require the headline to
    use the word 'stock'. Measured on 2026-03-03..03-17: it cuts the pool 6.7x (320,555 -> 47,511 rows)
    at 2.9x the bytes scanned, because V2Themes is a wide column and BigQuery bills every column a
    query REFERENCES, not just those it SELECTs."""
    return "(" + "|".join(re.escape(t) for t in config()["engine"]["market_themes"]) + ")"


# Optional plural suffix on an atom's LAST word. Measured need: the \b anchor made the matcher blind
# to plurals, so the atom `rare earth` missed "Trump administration is investing in US rare EARTHS" --
# an article squarely on the rare-earth beat. Replaying the dropped set showed 2,954 articles (+9.6%
# corpus) lost this way, led by rare earth (493), gold price (437), record high (245), stablecoin (187).
# NO possessive form here: a literal apostrophe would terminate the BigQuery r'...' string the regex is
# interpolated into. Measured as costing nothing -- possessives recovered zero extra articles.
_PLURAL = "(?:e?s)?"


def _atom_regex(atom: str) -> str:
    """One atom as a regex fragment: space matches space/hyphen/underscore (so 'rare earth' hits the
    URL slug 'rare-earth'), \\b anchors keep 'ai' from matching inside 'said', and a trailing optional
    plural catches the inflected form."""
    return rf"\b{re.escape(atom.lower()).replace(chr(92) + ' ', '[ _-]')}{_PLURAL}\b"


def _keyword_regex() -> str:
    """Case-insensitive alternation of every beat keyword, for the BigQuery REGEXP_CONTAINS."""
    atoms = sorted({k.lower() for b in beats() for k in b["keywords"]})
    return "(?i)(" + "|".join(_atom_regex(a) for a in atoms) + ")"


def _beat_matchers() -> list[tuple[str, re.Pattern]]:
    """(beat query, compiled any-keyword pattern) per beat, for attributing an article back to the
    beat(s) that would have surfaced it -- the `queries` field gdelt.pool() also produces, which the
    dashboard uses to attribute $ gain to beats."""
    out = []
    for b in beats():
        # SAME fragment builder as the SQL gate: attribution must agree with inclusion, or an article
        # can enter the pool and then belong to no beat.
        out.append((b["query"],
                    re.compile("|".join(_atom_regex(k) for k in b["keywords"]), re.IGNORECASE)))
    return out


_SPAM_RE: tuple | None = None


def _spam_title(title: str) -> bool:
    """Machine-generated / boilerplate headlines, dropped before they reach the scout. Two tiers:

      HARD  -- pure market plumbing (13F churn, Form-4, listicles, transcripts, quote pages). Never
               salvageable; always dropped.
      SOFT  -- an ANALYST-ACTION or TECHNICAL-SIGNAL template ("Price Target Raised to $X", "Hits New
               52-Week High"). Boilerplate only when it is the WHOLE story. A soft match is SPARED
               when the headline also names a catalyst, because the combination is real reporting.

    The soft tier exists because an LLM audit of the drops (scripts/judge_dropped.py, blind to the
    filter) confirmed 5.5% of dropped headlines were genuine catalyst coverage -- "NVIDIA Price Target
    Raised to $225 After $100B OpenAI Deal", "Regeneron Scores FDA Approval, JP Morgan Reaffirms $800
    Price Target". A flat pattern list was discarding ~240 real articles a year, and they were exactly
    the class this strategy hunts. Patterns live in retrieval_config.json, editable without code."""
    global _SPAM_RE
    if _SPAM_RE is None:
        c = config()
        _SPAM_RE = ([re.compile(p) for p in c.get("spam_title_patterns", [])],
                    [re.compile(p) for p in c.get("spam_title_patterns_soft", [])],
                    re.compile(c["catalyst_exemption"]) if c.get("catalyst_exemption") else None)
    hard, soft, exempt = _SPAM_RE
    t = title or ""
    if any(p.search(t) for p in hard):
        return True
    if any(p.search(t) for p in soft):
        return not (exempt and exempt.search(t))    # spared when a catalyst is named alongside
    return False


def _domain_in(src: str, domains) -> bool:
    """Boundary-aware domain match: finance.yahoo.com matches yahoo.com, but proactiveinvestors.com
    does NOT match investors.com."""
    d = (src or "").lower()
    return any(d == x or d.endswith("." + str(x).lower()) for x in domains)


# --------------------------------------------------------------------------- BigQuery
def _client():
    """Authenticated BigQuery client. The key path is `engine.key_path` in retrieval_config.json,
    relative to the repo root; GDELT itself is a PUBLIC dataset, so `engine.project` only names the
    project that gets BILLED for bytes scanned."""
    from google.cloud import bigquery
    from google.oauth2 import service_account
    eng = config()["engine"]
    key = REPO_ROOT / eng["key_path"]
    if not key.exists():
        sys.exit(f"missing {key} (BigQuery service-account key) -- see README 'Setup'")
    creds = service_account.Credentials.from_service_account_file(str(key))
    return bigquery.Client(credentials=creds, project=eng["project"])


def _log_cost(window: str, scanned_gb: float, billed_gb: float) -> None:
    """Append a real (non-cached) query's cost so spend is auditable rather than a surprise."""
    COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with COST_LOG.open("a") as fh:
        fh.write(json.dumps({"window": window, "scanned_gb": round(scanned_gb, 2),
                             "billed_gb": round(billed_gb, 2),
                             "ts": datetime.now().isoformat(timespec="seconds")}) + "\n")


def cost_summary() -> dict:
    """Cumulative BigQuery spend. On-demand pricing is $6.25/TB with the first 1 TB/month free --
    note the free tier is per BILLING ACCOUNT, so it is shared with any other project on the same
    account."""
    if not COST_LOG.exists():
        return {"queries": 0, "billed_gb": 0.0, "usd_over_free_tier": 0.0}
    es = [json.loads(l) for l in COST_LOG.read_text().splitlines() if l.strip()]
    billed = sum(e["billed_gb"] for e in es)
    return {"queries": len(es), "billed_gb": round(billed, 1),
            "usd_over_free_tier": round(max(0.0, billed - 1000) / 1000 * 6.25, 2)}


def _query_rows(client, start: str, end: str) -> list[dict]:
    """Raw GKG rows for [start, end], cached on disk by (window, keyword-hash).

    The cache is keyed on the KEYWORDS, not on the downstream filters, so iterating on the Python
    filters (blocklist, spam, subject-org) is free and only a retrieval_config.json keyword edit
    re-queries BigQuery. `max_scan_gb` is a hard cost guard checked via a dry run BEFORE the real
    query -- a fat-fingered date range aborts instead of billing."""
    from google.cloud import bigquery
    eng = config()["engine"]
    kw, th = _keyword_regex(), _theme_regex()
    # English-ORIGIN only, per engine.english_only. GDELT machine-translates foreign coverage and
    # flags it in TranslationInfo; this is the largest filter in the pipeline (88.5M rows/yr) and is
    # part of the cache key, so flipping it re-queries rather than silently serving the old corpus.
    _english = "AND TranslationInfo IS NULL" if eng.get("english_only", True) else ""
    # Cache key: the DEFAULT (english-only) hashes exactly as before this knob existed, so promoting
    # it to config did not orphan ~235 GB of already-paid-for rows. Only the non-default case perturbs
    # the key -- which is the case that genuinely returns different rows.
    khash = hashlib.md5((kw + th + ("" if eng.get("english_only", True) else "|all_langs"))
                        .encode()).hexdigest()[:8]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"rows-{start}-{end}-{khash}.json"
    if cache.exists():
        return json.loads(cache.read_text())

    sql = f"""
    SELECT {', '.join(_FIELDS)}
    FROM `{eng['table']}`
    WHERE _PARTITIONTIME BETWEEN TIMESTAMP('{start}') AND TIMESTAMP('{end}')
      {_english}
      AND REGEXP_CONTAINS(V2Themes, r'{th}')
      AND (REGEXP_CONTAINS(DocumentIdentifier, r'{kw}') OR REGEXP_CONTAINS(Extras, r'{kw}'))
    """
    dry = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True))
    gb = dry.total_bytes_processed / 1e9
    if gb > eng["max_scan_gb"]:
        sys.exit(f"cost guard: {start}..{end} would scan {gb:.1f} GB > max_scan_gb "
                 f"({eng['max_scan_gb']} GB); narrow the window or raise the guard deliberately")
    print(f"  [gkg] {start}..{end}: scanning {gb:.1f} GB ...", file=sys.stderr, flush=True)
    job = client.query(sql)
    rows = [{f: r[f] for f in _FIELDS} for r in job.result()]
    billed = (job.total_bytes_billed or 0) / 1e9
    _STAT["queries"] += 1
    _STAT["scanned_gb"] += gb
    _STAT["billed_gb"] += billed
    _log_cost(f"{start}..{end}", gb, billed)
    cache.write_text(json.dumps(rows))
    print(f"  [gkg] {start}..{end}: {len(rows)} rows ({billed:.1f} GB billed)", file=sys.stderr)
    return rows


# --------------------------------------------------------------------------- row -> article
def _page_title(extras: str) -> str:
    m = re.search(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", extras or "", re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _slug_title(url: str) -> str:
    """Headline reconstructed from the URL slug, for the minority of rows with no <PAGE_TITLE>.
    Better than an empty title: the slug usually carries the actual headline words."""
    seg = re.sub(r"[?#].*$", "", (url or "").rstrip("/")).split("/")[-1]
    seg = re.sub(r"\.(html?|php|aspx?)$", "", seg)
    seg = re.sub(r"\b\d{5,}\b", "", seg)                     # strip article-id digits
    words = re.split(r"[-_]+", seg)
    return " ".join(w.capitalize() for w in words if w).strip()


def _gkg_date(d) -> str:
    s = str(d)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else ""


def _tone(v2tone: str) -> float:
    try:
        return float((v2tone or "0").split(",")[0])
    except (ValueError, IndexError):
        return 0.0


# A headline that NAMES a listed vehicle: an exchange-qualified ticker, or a bare parenthesised
# symbol that is not a crypto ticker. Crypto symbols are excluded because "(BTC)" marks a coin-price
# story, not a listed company.
_CRYPTO_SYMS = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "BNB", "USDT", "USDC", "LTC", "TRX",
                "SHIB", "AVAX", "DOT", "LINK", "MATIC", "PEPE", "BCH", "XLM", "ETC"}
_EXCH_TICKER = re.compile(r"\((?:NYSE|NASDAQ|NYSEARCA|NYSEAMERICAN|AMEX|OTCMKTS)\s*:\s*[A-Z.]{1,6}\)", re.I)
_BARE_TICKER = re.compile(r"\(([A-Z]{2,5})\)")


def _names_ticker(title: str) -> bool:
    """True when the HEADLINE itself names a US-listed vehicle.

    This is the rescue for the subject-org gate, and it follows straight from the thesis: the bet is
    on coverage where the press NAMES the ticker, so a headline that does exactly that must never be
    discarded because GDELT's entity extractor happened to miss the company. Measured on the 1-year
    corpus: the org gate was dropping 3,980 such articles -- "Kura Sushi (NASDAQ:KRUS) Beats Q2 Sales
    Targets", "Amneal Pharmaceuticals (AMRX) Announces FDA Approval" -- because the only organisations
    GDELT extracted were a law firm or a wire service buried deep in the page.

    Deliberately NOT solved by raising ontopic_offset: the entities sitting beyond the cutoff are
    mostly noise (Bloomberg@4280, Twitter@2089, Reuters@976), so a looser offset admits junk and still
    misses the named ticker."""
    s = title or ""
    if _EXCH_TICKER.search(s):
        return True
    m = _BARE_TICKER.search(s)
    return bool(m and m.group(1) not in _CRYPTO_SYMS)


@functools.lru_cache(maxsize=4)
def _stoplist_re(entries: tuple) -> "re.Pattern":
    """Stoplist as a WORD-BOUNDARY alternation, not a substring test.

    The substring form (`any(s in low for s in stoplist)`) silently discarded real listed companies
    whose names merely CONTAIN a short stop token: `sec` killed SecureWorks / Secure Energy Services /
    Second Sight Medical, `epa` killed EPAM Systems, `russell` killed Russell Metals. Those articles
    were dropped from the pool with no counter and no trace. Lookarounds rather than \b so tokens
    with non-word edges ("s&p", "investing.com") still anchor correctly."""
    alts = "|".join(re.escape(s) for s in sorted(entries, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.IGNORECASE)


def _subject_orgs(orgs: str, offset: int, stoplist: set) -> list[str]:
    """Organizations the article is ABOUT -- those GDELT found within `offset` characters of the
    start, minus non-company entities (wires, exchanges, agencies). This is what replaces the DOC
    API's `(stock OR ETF OR shares)` AND-clause: an article with a subject company is about a
    company, whether or not its headline uses the word "stock"."""
    best: dict[str, str] = {}
    for part in (orgs or "").split(";"):
        if "," not in part:
            continue
        name, off = part.rsplit(",", 1)
        if not off.strip().isdigit() or int(off) > offset:
            continue
        norm = _ORG_SUFFIX_RE.sub("", name.strip()).strip()
        low = norm.lower()
        if len(norm) < 4 or _stoplist_re(tuple(sorted(stoplist))).search(low):
            continue
        best[low] = norm
    return list(best.values())


def _specialty(profile: str | None = None) -> list[str]:
    """Specialty-desk domains from the profile's `specialty_allow` -- the same allowlist the forward
    gather's GEM pass uses. Here it only breaks ties when picking a syndicated story's representative
    copy, never to filter."""
    from optimizer import load_financial_model
    p = profile or str(REPO_ROOT / "investor_profile.backtest.md")
    return list(load_financial_model(p).get("specialty_allow") or [])


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def collapse_syndication(arts: list[dict], specialty: list[str]) -> list[dict]:
    """Collapse syndicated copies of one story into a single record, keeping the highest-authority
    copy as the representative and recording how many outlets carried it in `syndication`.

    Measured need: in a 14-day sample, "Crude oil prices surpass $100 a barrel" appeared 5 times
    across local TV affiliates and "Trump seeks help to keep Strait of Hormuz open" 4 times across UK
    local papers -- one story each, eating nine slots of the scout's budget.

    DELIBERATE DIVERGENCE FROM PWR. PWR's rank_stories() also RANKS by salience (how many recognized
    outlets carried the story) and caps to a top-K. That is right for PWR, whose curator wants the
    week's biggest developments -- and wrong here. GHR's whole bet (CLAUDE.md #2) is the EARLY,
    still-under-the-radar call, which BY DEFINITION is carried by few outlets; ranking by syndication
    would systematically demote exactly what this repo hunts. So we collapse duplicates and keep the
    count as metadata, and leave selection to the scout (news_cap), as today.

    Authority is used ONLY to pick which copy represents the story: a specialty desk over the long
    tail, because those pages are better archived and so give Wayback a better shot at a lede."""
    spec = [s.lower() for s in specialty]
    best: dict[str, dict] = {}
    for a in arts:
        nt = _norm_title(a.get("title", ""))
        if not nt:
            continue
        cur = best.get(nt)
        if cur is None:
            best[nt] = dict(a, syndication=1, syndicated_sources=[a.get("source", "")])
            continue
        cur["syndication"] += 1
        if a.get("source") not in cur["syndicated_sources"]:
            cur["syndicated_sources"].append(a.get("source", ""))
        # promote a specialty-desk copy over a long-tail one; earlier publish date wins ties
        cur_auth = _domain_in(cur.get("source", ""), spec)
        new_auth = _domain_in(a.get("source", ""), spec)
        if (new_auth and not cur_auth) or (new_auth == cur_auth
                                           and a.get("published_date", "") < cur.get("published_date", "")):
            merged = dict(a, syndication=cur["syndication"],
                          syndicated_sources=cur["syndicated_sources"], queries=cur["queries"])
            best[nt] = merged
    return list(best.values())


# --------------------------------------------------------------------------- public API
def pool(start, end, queries: list[str] | None = None, cache_path: str | None = None,
         stats_path: str | None = None, profile: str | None = None,
         chunk_days: int = 30) -> list[dict]:
    """Deduped article pool over [start, end] -- the drop-in replacement for `gdelt.pool()`.

    Returns the same record shape, so callers need no other change:
        {published_date, source, title, snippet, url, language, queries: [beat, ...]}
    `snippet` is the title (GKG carries no body), exactly as the DOC API path behaved; `src/lede.py`
    then fills the real lede. `queries` lists the beat(s) whose keywords matched, so per-beat
    attribution in the dashboard keeps working.

    `queries` (arg): optional subset of beat query strings to restrict attribution+filtering to; None
    = every beat in retrieval_config.json. `chunk_days` splits a long span into several BigQuery
    queries so each stays under the cost guard and caches independently (a re-run with a longer end
    date reuses the earlier chunks).

    Unlike the DOC-API pool there is no per-(query, chunk) resume checkpoint -- a chunk is one SQL
    call that either completes or doesn't, and completed chunks are cached, so a killed run resumes
    at chunk granularity for free."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    _reset_stat()
    t0 = time.monotonic()

    # VOCABULARY-KEY THE POOL CACHE. The raw-row cache is already keyed on (window, keyword+theme
    # hash), but this pool-level cache used to sit in FRONT of it under a fixed filename -- so editing
    # retrieval_config.json and re-running returned the OLD pool in 0 seconds while reporting success.
    # A stale result that looks correct is worse than an error, and it silently broke the one property
    # this design exists for: that retrieval can be iterated on for free. Same key here means a
    # vocabulary edit naturally lands in a new file, and the previous pool stays for comparison.
    if cache_path:
        _p = Path(cache_path)
        _h = hashlib.md5((_keyword_regex() + _theme_regex()).encode()).hexdigest()[:8]
        cache_path = str(_p.with_name(f"{_p.stem}-{_h}{_p.suffix}"))

    if cache_path and os.path.exists(cache_path):
        arts = json.loads(Path(cache_path).read_text())
        arts = arts.get("articles", arts) if isinstance(arts, dict) else arts
        _write_stats(stats_path, arts, 0.0, from_cache=True)
        print(f"  [gkg] pool: {len(arts)} articles (from {cache_path})", file=sys.stderr)
        return arts

    eng = config()["engine"]
    stoplist = {s.lower() for s in config()["org_stoplist"]}
    blocked = _mill_block(profile)
    matchers = _beat_matchers()
    if queries is not None:
        want = set(queries)
        matchers = [(q, p) for q, p in matchers if q in want]

    client = _client()
    edges = list(pd.date_range(start, end, freq=f"{chunk_days}D"))
    if not edges or edges[-1] < end:
        edges.append(end)

    seen: dict[str, dict] = {}
    for i in range(len(edges) - 1):
        lo, hi = edges[i].date().isoformat(), edges[i + 1].date().isoformat()
        if lo == hi:
            continue
        for r in _query_rows(client, lo, hi):
            _STAT["rows"] += 1
            url = r["DocumentIdentifier"] or ""
            if not url:
                continue
            src = (r["SourceCommonName"] or "").lower()
            if _domain_in(src, blocked):
                _STAT["dropped_blocklist"] += 1
                continue
            title = _page_title(r["Extras"]) or _slug_title(url)
            if not title or _spam_title(title):
                _STAT["dropped_spam"] += 1
                continue
            hay = f"{title} {url}"
            hits = [q for q, p in matchers if p.search(hay)]
            if not hits:
                # the regex matched somewhere in Extras (an embedded link, a related-stories block)
                # but not in this article's own headline or URL -- not actually about the beat
                _STAT["dropped_no_beat"] += 1
                continue
            # PERSIST the subject orgs, do not just test them. This list is the ENTITY KEY the
            # curator needs to group a ticker's coverage together -- "RKLB is skyrocketing" and
            # "$5.6B Neutron win" are the same story only if something says both are about Rocket
            # Lab. It was computed here and thrown away, so every downstream stage had to re-derive
            # it from the headline (measured: an exchange tag appears in only 14% of gate-passers,
            # which groups almost nothing). Free: the column is already SELECTed and scanned.
            _orgs = _subject_orgs(r["V2Organizations"], eng["ontopic_offset"], stoplist)
            if not _orgs:
                if _names_ticker(title):
                    _STAT["rescued_named_ticker"] += 1   # headline names the vehicle: keep regardless
                else:
                    _STAT["dropped_no_org"] += 1
                    continue
            pub = _gkg_date(r["DATE"])
            if not pub:
                continue
            ex = seen.get(url)
            if ex is None:
                seen[url] = {"published_date": pub, "source": r["SourceCommonName"] or "",
                             "title": title, "snippet": title, "url": url, "language": "English",
                             "tone": round(_tone(r["V2Tone"]), 2), "queries": list(hits),
                             # ENTITY KEY for ticker-grouping. Stored generously (40) rather than
                             # tightly, because the LISTICLE THRESHOLD is a curation-time decision
                             # (max_article_orgs) and truncating here would make a 40-org listicle
                             # indistinguishable from a 4-org story -- the 40 is only a corpus-size
                             # bound, not a filter.
                             "orgs": _orgs[:40]}
            else:
                for q in hits:                       # same URL re-surfaced by another beat
                    if q not in ex["queries"]:
                        ex["queries"].append(q)

    arts = list(seen.values())
    n_raw = len(arts)
    arts = collapse_syndication(arts, _specialty(profile))
    _STAT["dropped_syndicated"] = n_raw - len(arts)
    if cache_path:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{cache_path}.tmp"
        Path(tmp).write_text(json.dumps(arts))
        os.replace(tmp, cache_path)
    _write_stats(stats_path, arts, time.monotonic() - t0, from_cache=False)
    trace.log("search", engine="gkg", query=f"{len(matchers)} beats",
              start=str(start)[:10], end=str(end)[:10], n_results=len(arts))
    return arts


def _write_stats(stats_path: str | None, arts: list[dict], elapsed: float, from_cache: bool) -> None:
    """Retrieval-health section for the dashboard, mirroring gdelt._write_stats so the two engines
    render in the same panel. The `dropped_*` counters are the filter audit: they say WHY rows were
    discarded, so a recall regression is diagnosable instead of invisible."""
    if not stats_path:
        return
    n = len(arts)
    retstats.merge(stats_path, "gkg", {
        "items": n, "rows_scanned": _STAT["rows"], "queries": _STAT["queries"],
        "scanned_gb": round(_STAT["scanned_gb"], 2), "billed_gb": round(_STAT["billed_gb"], 2),
        "dropped_blocklist": _STAT["dropped_blocklist"], "dropped_spam": _STAT["dropped_spam"],
        "dropped_no_beat": _STAT["dropped_no_beat"], "dropped_no_org": _STAT["dropped_no_org"],
        "rescued_named_ticker": _STAT["rescued_named_ticker"],
        "dropped_syndicated": _STAT["dropped_syndicated"],
        "keep_rate_pct": round(100 * n / _STAT["rows"], 1) if _STAT["rows"] else 0.0,
        "elapsed_s": round(elapsed, 1),
        "items_per_min": round(60 * n / elapsed, 1) if elapsed > 0 else None,
        "from_cache": from_cache,
    })


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GKG/BigQuery news discovery")
    ap.add_argument("--validate", action="store_true", help="fetch one window and print a breakdown")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--chunk-days", type=int, default=30)
    ap.add_argument("--cost", action="store_true", help="print cumulative BigQuery spend")
    ap.add_argument("--regex", action="store_true", help="print the keyword regex and exit")
    a = ap.parse_args(argv)

    if a.regex:
        print(_keyword_regex())
        return 0
    if a.cost:
        print(json.dumps(cost_summary(), indent=2))
        return 0
    if not a.validate:
        ap.print_help()
        return 1
    if not (a.start and a.end):
        return ap.error("--validate needs --start and --end")

    arts = pool(a.start, a.end, chunk_days=a.chunk_days)
    print(f"\n{len(arts)} articles  {a.start}..{a.end}")
    print(f"  rows scanned      {_STAT['rows']}")
    print(f"  dropped blocklist {_STAT['dropped_blocklist']}")
    print(f"  dropped spam      {_STAT['dropped_spam']}")
    print(f"  dropped no-beat   {_STAT['dropped_no_beat']}")
    print(f"  dropped no-org    {_STAT['dropped_no_org']}")
    print(f"  syndicated dupes  {_STAT['dropped_syndicated']} collapsed into their lead story")
    print(f"  BigQuery          {_STAT['queries']} queries, {_STAT['billed_gb']:.1f} GB billed")
    by_src = collections.Counter(x["source"] for x in arts)
    print(f"\n  top sources ({len(by_src)} distinct):")
    for s, c in by_src.most_common(15):
        print(f"    {c:5d}  {s}")
    by_beat = collections.Counter(q for x in arts for q in x["queries"])
    print(f"\n  articles per beat ({len(by_beat)}/{len(beats())} beats fired):")
    for q, c in by_beat.most_common():
        print(f"    {c:5d}  {q}")
    dead = [b["query"] for b in beats() if b["query"] not in by_beat]
    if dead:
        print(f"\n  SILENT beats ({len(dead)}) -- surfaced nothing this window:")
        for q in dead:
            print(f"    {q}")
    by_day = collections.Counter(x["published_date"] for x in arts)
    print(f"\n  {len(by_day)} distinct publish days; median {int(sorted(by_day.values())[len(by_day) // 2])} "
          f"articles/day" if by_day else "\n  no dated articles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
