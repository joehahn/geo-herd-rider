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


_TITLE_RX: dict = {}          # id(canon) -> (len(canon), compiled alternation); see _title_orgs
_SECTOR: set | None = None


def _sector_terms() -> set:
    """Sector/domain words that must never be matched as a COMPANY name in a title.

    Not a hand-written stoplist -- it is the project's own beat vocabulary (every `keywords` atom and
    every word of every beat `query` in retrieval_config.json). GKG's V2Organizations genuinely emits
    bare sector words as organisations: `energy`, `materials`, `uranium`, `nuclear` and `mining` are
    all canon keys here, each with exactly ONE variant, i.e. GDELT called them companies. Harmless
    while attachment came from GKG's own per-article org list; catastrophic for a title matcher,
    because "Energy is Crushing the Market" would attach to the company `energy`.

    Reusing the beat vocabulary means the block list cannot drift from the sectors we actually search,
    and it needs no maintenance. Measured: blocks 45 of 3,834 canon names -- every sector word in the
    audit, and no real company (micron, nvidia, samsung, novo nordisk, aris mining all survive)."""
    global _SECTOR
    if _SECTOR is None:
        out: set = set()
        try:
            cfg = json.loads((REPO_ROOT / "retrieval_config.json").read_text())
            for b in list(cfg.get("gem_beats") or []) + list(cfg.get("coverage_beats") or []):
                for k in b.get("keywords") or []:
                    k = k.lower().strip()
                    out.add(k)
                    out.update(k.split())
                out.update((b.get("query") or "").lower().split())
        except Exception:  # noqa: BLE001 -- a missing config must not break org attachment
            pass
        _SECTOR = out
    return _SECTOR


_SUBJECT_COUNTS: dict = {}   # id(canon) -> (len(canon), Counter); filled by build_canon
_TITLE_MIN_SUBJECT = 10   # articles GKG must have called a name the SUBJECT of, before a bare title
                          # match may attach to it. See _title_orgs; measured, not guessed.


def _subject_counts(canon: dict, arts: list) -> collections.Counter:
    """{canonical org: how many articles GKG named it the SUBJECT of} -- the evidence a title match
    is allowed to lean on."""
    c: collections.Counter = collections.Counter()
    for a in arts:
        for o in (a.get("orgs") or []):
            n = normalise(o)
            if n:
                c[canon.get(n, n)] += 1
    return c


_WIDE_RX: dict = {}


def _wide_rx(canon: dict):
    """Alternation over EVERY canon name -- used only to detect that a title names several companies."""
    hit = _WIDE_RX.get(id(canon))
    if hit is None or hit[0] != len(canon):
        names = sorted({n for n in canon if len(n) >= 5}, key=len, reverse=True)
        rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I) if names else None
        hit = (len(canon), rx)
        _WIDE_RX[id(canon)] = hit
    return hit[1]


def _title_orgs(a: dict, canon: dict, counts: collections.Counter | None = None) -> list:
    """Canonical companies named in the TITLE, for articles that arrived with no `orgs` field.

    WHY. GKG hands every article a subject-company list (V2Organizations, offset-gated), and
    `article_orgs` was built around it. The websearch gather supplies NOTHING equivalent, so on the
    forward half the only path left was the single-ticker title fallback -- which fires on 10% of
    titles and resolves on 1%, because `ticker_map` is learned per-window and knew 33 symbols.
    Measured 2026-08-25 on the bootstrap corpus: 63.2% of websearch titles name a company the canon
    ALREADY knows, and 1.0% were being attached. The vocabulary was there; nothing matched it.

    TITLE ONLY, deliberately. GKG counts an org as the subject only if it appears before
    `ontopic_offset` characters -- being NAMED is not being ABOUT. A title match is the closest
    honest analogue we have; a further 25.4% of articles name a company only in the snippet and are
    left alone, because that bucket is where a mention gets mistaken for a subject.

    EXACTLY ONE, or nothing -- the same rule the ticker fallback already applies, and for the same
    reason: a title naming two companies is ambiguous about which it is ABOUT, and this module's
    standing lesson is that a mis-map is worse than no map, since it files the article under the
    wrong bundle where it can push the scout to propose a name the article was never about."""
    title = a.get("title") or ""
    if not title or not canon:
        return []
    key = (id(canon), id(counts))
    hit = _TITLE_RX.get(key)
    if hit is None or hit[0] != len(canon):
        # Longest-first so `taiwan semiconductor manufacturing` wins over `taiwan semiconductor`.
        # Cached on the canon object: this is called per article, and the corpus builds one canon.
        if counts is None:
            return []                     # fail closed: no evidence, no bare-title attachment
        sect = _sector_terms()
        # plural-tolerant: the atom list holds `data center`, titles say `data centers`
        ok = {n for n in canon if len(n) >= 6
              and n not in sect and n.rstrip("s") not in sect and n + "s" not in sect}
        if counts is not None:
            # EVIDENCE FLOOR. The sector filter kills `energy`/`uranium`; it does not kill the other
            # noise class -- generic English words GKG emitted as organisations and that survived
            # suffix-stripping: `holdings` (8 subject-articles), `exploration` (1), `driver` (1),
            # against nvidia 646, spacex 547, micron 120, serve robotics 11. Requiring the name to be
            # something GKG repeatedly called an article's SUBJECT is the same kind of guard
            # ticker_map already uses (n>=3 and 80% dominance) and for the same stated reason: a
            # mis-map is worse than no map. It is deliberately conservative -- it also drops real but
            # rarely-covered names (`circle`, 1) -- because a wrong bundle can push the scout to
            # propose a ticker the article was never about.
            ok = {n for n in ok if counts.get(canon.get(n, n), 0) >= _TITLE_MIN_SUBJECT}
        names = sorted(ok, key=len, reverse=True)
        rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I) if names else None
        hit = (len(canon), rx)
        _TITLE_RX[key] = hit
    rx = hit[1]
    if rx is None:
        return []
    found = {canon.get(m.group(1).lower(), m.group(1).lower()) for m in rx.finditer(title)}
    if len(found) != 1:
        return []
    # AMBIGUITY CHECK against the WIDER vocabulary. `found` only sees names that cleared the evidence
    # floor, so a title naming five companies of which one is well-covered looked unambiguous
    # ("Kulicke and Soffa, Applied Materials, AMD, Intel, and KLA ..." attached to applied materials).
    # Re-scan with every canon name, and with the title's own ticker symbols: if the headline names
    # more than one company by ANY measure, it is not about one of them -- leave it alone.
    wide = _wide_rx(canon)
    if wide is not None:
        names = {canon.get(m.group(1).lower(), m.group(1).lower()) for m in wide.finditer(title)}
        if len(names) > 1:
            return []
    if len(title_tickers(a)) > 1:
        return []
    return [next(iter(found))]


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
    if not out and canon and "orgs" not in a:  # noqa: SIM102
        # GATED ON THE KEY BEING ABSENT, not on it being empty, and the distinction is the whole
        # safety argument. `orgs: []` means GKG RAN its subject-org extraction and found nothing --
        # a real finding, and overriding it would silently rewrite the backtest. A MISSING key means
        # no extractor ever ran, which is true of every websearch article and no GKG one.
        # Measured 2026-08-25: key absent on 0 of 99,117 backtest articles, 0 of 9,215 bootstrap GKG
        # articles, and 2,280 of 2,280 websearch articles. Gating on `not a.get("orgs")` instead
        # changed attachment for 6.4% of the backtest corpus -- caught before it shipped.
        _ev = _SUBJECT_COUNTS.get(id(canon))
        out = _title_orgs(a, canon, _ev[1]) if _ev and _ev[0] == len(canon) else []
    return out


def build_canon(arts: list) -> dict:
    """The variant->canonical map for a corpus. Built once per run, not per article.

    Also records, against the returned map, how many articles GKG named each canonical org the
    SUBJECT of. `_title_orgs` needs that evidence and only ever sees one article at a time, so
    caching it here means no caller has to change and no caller can forget: a canon built the normal
    way arrives with its evidence attached. A canon assembled some other way simply has none, and
    title matching then stays OFF -- fail closed, which is this module's standing preference."""
    c = collections.Counter()
    for a in arts:
        for o in (a.get("orgs") or []):
            n = normalise(o)
            if n:
                c[n] += 1
    canon = _canonical_map(c)
    _SUBJECT_COUNTS[id(canon)] = (len(canon), _subject_counts(canon, arts))
    return canon


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
