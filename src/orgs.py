"""orgs.py — turn GKG's raw subject-org strings into a usable ENTITY KEY, then group news by it.

WHY THIS EXISTS. The curator kept seeing that a ticker had moved without seeing why. Measured
2026-08-14 on the 3-year corpus: of 53 Rocket Lab articles in one scan window, the 3 that reached the
scout were all "RKLB is skyrocketing" — while "Rocket Lab Lift Off: Huge $5.6 Billion Neutron Win" and
"Joins Space Force Launch Program" were filtered out, because plain catalyst reporting carries no
superlative. The scout was handed a move with no cause and correctly refused to open an event on it.

The fix is to stop judging headlines one at a time and judge a TICKER'S COVERAGE TOGETHER: the
move-signal and the driver are the same story, and only look like one story when they sit side by side.
GKG already knows which companies an article is about (`V2Organizations`, filtered to subjects by
character offset) — gkg.py computed that list, used it as a yes/no filter and threw it away. It is now
persisted as `orgs`, and this module makes it groupable.

TWO PROBLEMS WITH RAW GKG ORGS, both measured on the 6-month corpus (21,627 articles, 7,124 distinct
org strings):

  1. NON-COMPANIES DOMINATE. The single most common "org" is `United States` at 5,228 — nearly 4x the
     next entry. Also `York Stock Exchange` (a truncation of New York Stock Exchange), `Drug
     Administration` (of Food and Drug Administration), `Oval Office`, `Trump Administration`,
     `Newsfile` (a wire), `World Gold Council` (a trade body), `Blackwell` (an Nvidia PRODUCT).
     Grouping on any of these produces one enormous meaningless bundle.

  2. ONE COMPANY, SEVERAL STRINGS. `Taiwan Semiconductor` / `Taiwan Semiconductor Manufacturing` /
     `Taiwan Semiconductor Manufacturing Company` are one company in three groups; likewise `Rocket
     Lab` / `Rocket Lab United States` / `Rocket Lab Launch`. Left unmerged, the corroboration this
     whole design depends on is split across bundles and lost.

Both are ordinary data cleaning, but skipping either makes the grouping useless rather than merely
noisy, so they are handled here rather than left to the caller.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Corporate suffixes stripped before comparing. A company is the same company whether the article
# writes "Rocket Lab" or "Rocket Lab Inc".
# ONLY TRAILING LEGAL FORMS, and only at the END. An earlier version stripped `lab`, `group`,
# `technologies`, `systems`, `solutions`, `resources`, `therapeutics` anywhere in the string -- which
# turned "Rocket Lab" into "rocket" and would have merged it with any other rocket company, and
# "Quantum Computing Inc" into "quantum". Those words are usually the DISTINCTIVE part of a name, not
# boilerplate. Anchoring to the end and keeping the list to legal forms is the conservative choice:
# under-merging leaves two groups for one company, over-merging silently fuses two companies.
_SUFFIX = re.compile(
    r"[\s,]+(inc|corp|corporation|co|company|ltd|limited|plc|llc|l\.l\.c|lp|nv|n\.v|sa|s\.a|ag|se|"
    r"holdings?|holding|sarl|gmbh|pte|bhd|ab|oyj|asa)\.?$", re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
_ENTITY = re.compile(r"&#?[a-z0-9]{1,8};", re.I)

# NOT COMPANIES. GKG's org extractor returns countries, agencies, exchanges, wires, trade bodies and
# occasionally product names. These are not investable entities and grouping on them is worse than
# useless -- `United States` alone would swallow 5,228 articles into one bundle. Kept as normalised
# forms (suffix-stripped, lowercase) so the check is one set lookup.
_NOT_A_COMPANY = {
    # countries / regions / governments
    "united states", "united kingdom", "european union", "north america", "south america",
    "middle east", "hong kong", "new zealand", "saudi arabia", "south africa", "south korea",
    "north korea", "british columbia", "new york", "white house", "oval office",
    "trump administration", "biden administration", "congress", "senate", "house representatives",
    "supreme court", "federal reserve", "treasury", "pentagon", "state department",
    # regulators / agencies (incl. GKG's characteristic truncations)
    "drug administration", "food drug administration", "securities exchange commission",
    "federal trade commission", "federal communications commission", "environmental protection",
    "european central bank", "international monetary fund", "world bank", "world health organization",
    "european commission", "national aeronautics space administration",
    # exchanges / indices (incl. truncations)
    "york stock exchange", "stock exchange", "nasdaq", "nyse", "s&p", "dow jones", "russell",
    "ftse", "nikkei", "hang seng", "cboe", "cme",
    # wires / publishers / data vendors that GKG mislabels as subjects
    "newsfile", "globe newswire", "globenewswire", "business wire", "businesswire", "pr newswire",
    "prnewswire", "accesswire", "canadian press", "press association", "yahoo finance",
    "zacks investment research", "motley fool", "seeking alpha", "benzinga", "marketbeat",
    "simply wall st", "insider monkey", "tipranks",
    # trade bodies / cartels / standards
    "organization petroleum exporting countries", "opec", "world gold council", "silver institute",
    "international energy agency", "world trade organization", "nato", "united nations",
    # law firms that appear on every class-action notice
    "robbins geller rudman dowd", "pomerantz", "rosen law firm", "bronstein gewirtz grossman",
    "levi korsinsky", "glancy prongay murray", "bragar eagel squire", "berger montague",
    "faruqi faruqi", "kirby mcinerney", "bernstein liebhard", "lowey dannenberg", "portnoy law",
    "gross law firm", "schall law firm", "hagens berman", "kahn swick foti", "block leviton",
    # courts and wire prefixes that GKG emits as if they were subject companies
    "united states district court", "district court", "prnewswire robbins", "globe newswire robbins",
    # ADDED 2026-08-16 after a spot-check of the largest bundles. Each was holding real articles under
    # a key that is not an investable company, so the bundle could never correspond to a ticker.
    # 1,844 articles across these on the 3-year corpus.
    "blackwell",                 # an Nvidia PRODUCT. Named as an example in this module's own
                                 # docstring since the file was written, but never actually stoplisted
                                 # -- it was holding 321 articles.
    "alliance news", "canadian press on", "xinhua", "pa media", "dow jones newswires",
    "critical minerals", "rare earths", "exchange traded funds", "bitcoin trust", "technology",
    "stock market", "artificial intelligence", "electric vehicles",
    "world economic forum", "european parliament", "european council", "state council",
    # GKG truncates long org strings, so the STOPLIST HAS TO CARRY THE TRUNCATION, not the real
    # name -- the full-name entries above never fire on these. Confirmed by printing the exact keys:
    # a guess at the cut point ("...newsfil") missed and left 694 articles grouped under a wire.
    "organization of the petroleum",   # OPEC
    "british columbia newsfile",       # Newsfile wire dateline, not a company
}

# NON-COMPANY PREFIXES. Exact-string stoplisting cannot keep up with GKG here: it emits the same
# non-company at many truncation lengths, and sometimes misspelled. OPEC alone appears as
# "organization of the petroleum exporting countries", "organization of petroleum exporting
# countries", "organization for petroleum exporting states", "organization of oil exporting
# countries" and "organization of the petroeum exporting countries" (sic) -- eleven variants, and a
# stoplist entry per variant is a losing game. These match on the START of the normalised key.
# Deliberately short and conservative: each is a stem that CANNOT begin an investable company's name.
_NOT_A_COMPANY_PREFIX = (
    "organization of the petroleum", "organization of petroleum", "organization for petroleum",
    "organization of oil exporting", "organizations of the petroleum", "organization of the petroeum",
    "world economic forum", "canadian press", "british columbia newsfile", "alliance news",
    "exchange traded fund", "globe newswire", "business wire", "pr newswire",
)

_MIN_LEN = 3          # single/double-character "orgs" are extraction noise
_MAX_WORDS = 6        # a 7-word "org" is a sentence fragment, not a company name


def normalise(name: str) -> str:
    """One canonical, comparable form for an org string. Empty string = not usable as a key."""
    s = _PUNCT.sub(" ", (name or "").lower())
    s = _WS.sub(" ", s).strip()
    for _ in range(3):                       # "Foo Holdings Inc" -> "Foo Holdings" -> "Foo"
        t = _SUFFIX.sub("", s).strip()
        if t == s:
            break
        s = t
    if len(s) < _MIN_LEN or len(s.split()) > _MAX_WORDS or s in _NOT_A_COMPANY:
        return ""
    if s.startswith(_NOT_A_COMPANY_PREFIX):
        return ""
    if s.isdigit():
        return ""
    return s


def _canonical_map(counts: collections.Counter) -> dict:
    """Merge org variants onto one key: `taiwan semiconductor manufacturing` -> `taiwan semiconductor`.

    RULE: if A's words are a prefix of B's words, they are the same company and the SHORTER form wins
    (it is the one a second article is likeliest to also use). Prefix, not substring, on purpose --
    substring matching merges `rocket lab` into `rocket companies`-style false pairs, and word-boundary
    prefixes do not. Longest names are folded first so a three-step chain
    (`taiwan semiconductor manufacturing company` -> `... manufacturing` -> `taiwan semiconductor`)
    resolves to the root rather than stopping halfway."""
    # SHORTEST FIRST. Sorting longest-first meant "taiwan semiconductor manufacturing" became a root
    # before "taiwan semiconductor" was seen, so the two never merged -- the exact split this map
    # exists to remove. Shortest-first makes the root available when its variants arrive.
    keys = sorted(counts, key=lambda k: (len(k.split()), -counts[k]))
    canon: dict = {}
    roots: list = []
    for k in keys:
        kw = k.split()
        hit = ""
        for r in roots:
            rw = r.split()
            # A ONE-WORD ROOT MAY NOT ABSORB LONGER NAMES. "quantum" would otherwise swallow "quantum
            # computing" (a specific company) and anything else starting with the word, fusing
            # unrelated issuers into one group. Two-word roots are specific enough to be safe:
            # "rocket lab" absorbing "rocket lab launch" is right, "rocket" absorbing "rocket lab" and
            # "rocket companies" is not. Under-merging costs a split group; over-merging invents a
            # company that does not exist.
            if len(rw) < 2:
                continue
            if len(rw) < len(kw) and kw[:len(rw)] == rw:
                hit = r
                break
        if hit:
            canon[k] = canon.get(hit, hit)
        else:
            canon[k] = k
            roots.append(k)
    # roots were discovered longest-first, so re-point any key whose root itself got folded
    for k, v in list(canon.items()):
        seen = set()
        while v in canon and canon[v] != v and v not in seen:
            seen.add(v)
            v = canon[v]
        canon[k] = v
    return canon


# TICKER IN THE TITLE. GKG's org extractor misses a company named plainly in the headline --
# "Nauticus Robotics, Inc. (KITT)", "Rockwell Automation, Inc. (ROK)" -- and since the grouping key is
# derived from V2Organizations ALONE, those articles join no bundle at all. Measured on the 3-year
# corpus: 6,732 of the 17,290 articles that reach the curator in NO role name a ticker in their own
# title. This recovers the unambiguous ones.
_TICKER = re.compile(r"\(([A-Z]{1,5})\)|\b(?:NASDAQ|NYSE|NYSEARCA|AMEX):\s?([A-Z]{1,5})\b")


def title_tickers(a: dict) -> set:
    """Symbols named in an article's own title. Pattern-matched, so no ticker universe is needed."""
    return {m.group(1) or m.group(2) for m in _TICKER.finditer(a.get("title") or "")}


def learn_ticker_evidence(arts: list, canon: dict | None = None, store: dict | None = None) -> dict:
    """Accumulate (ticker -> company) sightings into `store`, for map-building ACROSS curations.

    Exists because a single curation window is far too small to learn from. Measured on the 6-month
    corpus: a 30-day window yields 0-15 symbols against 223 learned corpus-wide, because the strict
    rule needs >=3 sightings of the same unambiguous pairing and one month rarely contains them.
    Learning from the WHOLE corpus instead would leak -- it would use articles the curator has not
    reached yet -- so evidence is ACCUMULATED as curations run forward in time. Early curations get a
    thin map and later ones a fuller one, which is the honest shape: you cannot know a mapping before
    you have seen the evidence for it."""
    store = {} if store is None else store
    canon = canon or {}
    for a in arts:
        ks = article_orgs(a, canon)
        ts = title_tickers(a)
        if len(ks) == 1 and len(ts) == 1:
            store.setdefault(next(iter(ts)), collections.Counter())[ks[0]] += 1
    return store


def ticker_map_from(store: dict) -> dict:
    """Apply the strict thresholds to accumulated evidence -- see ticker_map for why they are strict."""
    out = {}
    for t, c in store.items():
        (k, n), = c.most_common(1)
        if n >= 3 and n / sum(c.values()) >= 0.8:
            out[t] = k
    return out


def ticker_map(arts: list, canon: dict | None = None) -> dict:
    """{TICKER: canonical org key}, learned from the corpus itself -- no external symbol list.

    STRICT ON PURPOSE, and the strictness is the whole design. The obvious rule -- map a ticker to the
    company it most often co-occurs with -- is WRONG, because tickers routinely appear in titles about
    OTHER companies. Learned that way on this corpus, `AMZN` mapped to `nvidia` (Amazon is named in
    many Nvidia stories), `GSK` to `pfizer`, and a hand-read of 14 samples found about half mis-mapped.
    A mis-map is worse than no map: it files an article under the WRONG ticker's bundle, where it can
    push the scout to propose a name the article was never about.

    So a pair is learned ONLY from articles that name exactly ONE ticker and carry exactly ONE company,
    and is kept only if that pairing is both repeated (>=3) and dominant (>=80% of that ticker's
    sightings). Same hand-read on the strict map: 14 of 14 correct. It recovers 978 of the 17,290
    rather than 3,421 -- precision bought with recall, deliberately."""
    canon = canon or {}
    seen: dict = collections.defaultdict(collections.Counter)
    for a in arts:
        ks = article_orgs(a, canon)
        ts = title_tickers(a)
        if len(ks) == 1 and len(ts) == 1:
            seen[next(iter(ts))][ks[0]] += 1
    out = {}
    for t, c in seen.items():
        (k, n), = c.most_common(1)
        if n >= 3 and n / sum(c.values()) >= 0.8:
            out[t] = k
    return out


def article_orgs(a: dict, canon: dict | None = None, tmap: dict | None = None) -> list:
    """The usable, canonical org keys for one article (dropped non-companies removed).

    `tmap` (from ticker_map) is a FALLBACK ONLY -- consulted when GKG gave nothing usable, never to
    override an org GKG did supply, and only when the title names exactly one ticker. A title naming
    two symbols is ambiguous about which one it is ABOUT, so it is left alone."""
    out = []
    for o in (a.get("orgs") or []):
        n = normalise(o)
        if not n:
            continue
        n = (canon or {}).get(n, n)
        if n not in out:
            out.append(n)
    if not out and tmap:
        ts = title_tickers(a)
        if len(ts) == 1:
            k = tmap.get(next(iter(ts)))
            if k:
                out.append(k)
    return out


def build_canon(arts: list) -> dict:
    """The variant->canonical map for a corpus. Built once per run, not per article."""
    c = collections.Counter()
    for a in arts:
        for o in (a.get("orgs") or []):
            n = normalise(o)
            if n:
                c[n] += 1
    return _canonical_map(c)


def group(arts: list, canon: dict | None = None, min_articles: int = 1,
          tmap: dict | None = None) -> dict:
    """{org_key: [articles, oldest first]} — the unit the curator judges.

    An article joins EVERY org it names. A two-company story is real evidence for both, and assigning
    it to one arbitrarily would discard signal.

    NO LISTICLE RULE HERE ANY MORE. There was one (`max_article_orgs`): above N companies an article
    joined a group only if that company was in its TITLE. It was deleted 2026-08-15 as redundant and
    harmful. Redundant because actual listicles are already dropped at INGEST by
    spam_title_patterns ("N best stocks", "stocks to buy now") -- after that filter only 75 of 21,233
    articles (0.35%) name more than four orgs. Harmful because those 75 are mostly NOT listicles but
    genuine multi-company news, and the title rule mangles them: "Uranium stocks extend gains as Trump
    signs orders to boost nuclear industry" names no company in its title, so it would have joined NO
    group -- a real nuclear catalyst deleted, in the vertical this work is trying to fix.
    """
    canon = canon if canon is not None else build_canon(arts)
    out: dict = collections.defaultdict(list)
    for a in arts:
        keys = article_orgs(a, canon, tmap)
        if not keys:
            continue
        for k in keys:
            out[k].append(a)
    # COLLAPSE NEAR-DUPLICATE HEADLINES inside a group. Syndication means the same story arrives from
    # biztoc AND benzinga, and some wires emit a title twice; grouped, those burn slots that the cap
    # then denies to a real article. Measured on the Rocket Lab group: 2 of 12 kept slots were exact
    # duplicate headlines. Collapsed on a normalised title, keeping the earliest (the original).
    for k in out:
        out[k].sort(key=lambda x: (x.get("published_date") or ""))
        seen, dedup = set(), []
        for x in out[k]:
            # strip HTML entities FIRST: "Inc.&#xA0;(RKLB)" and "Inc. (RKLB)" are the same headline,
            # but &#xA0; survives _PUNCT as the letters "xa0" and split the pair into two slots.
            _t0 = _ENTITY.sub(" ", (x.get("title") or "").lower())
            t = _WS.sub(" ", _PUNCT.sub(" ", _t0)).strip()[:90]
            if t and t in seen:
                continue
            seen.add(t)
            dedup.append(x)
        out[k] = dedup
    return {k: v for k, v in out.items() if len(v) >= min_articles}
