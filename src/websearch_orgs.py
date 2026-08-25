"""websearch_orgs.py — subject-company extraction for the WEBSEARCH ingest.

WHY THIS IS A SEPARATE FILE, AND WHY IT IS INGEST AND NOT CURATOR CODE.

The solution has TWO ingest sources feeding THREE corpora, and one curator that must read all three
without knowing which source it is looking at:

    gkg + wayback  --.
                      >-- backtest corpus / bootstrap corpus / forward corpus --> ONE curator
    anthropic + tavily -'

That only works if both ingests hand the curator the SAME article shape. GKG gets subject companies
free from BigQuery's V2Organizations (gkg.py stamps `orgs`); the websearch gather supplies nothing
equivalent, so `orgs.article_orgs()` had nothing to read and attached 1.0% of the websearch half
against 85.1% of the GKG half.

This logic first landed INSIDE orgs.py as a fallback tier, gated on the `orgs` key being absent --
which meant the curator was sniffing which source an article came from. That is a source-specific
hack in the one component that is supposed to be source-agnostic. Same code, moved one layer down,
stops being a hack: the websearch ingest fills `orgs` itself, and the curator goes back to simply
reading the field.

The quality filters below are therefore INGEST quality control, not curator logic. They exist
because GKG's own vocabulary is noisy -- it emits `energy`, `uranium`, `holdings` and `driver` as
organisations -- and matching a bare title against that vocabulary needs guarding. Measured on the
bootstrap corpus: 63.2% of websearch titles name a company the canon already knows; the filters
below take that to a conservative 9.3% attach rate, erring toward no map over a wrong one, because
a mis-map files an article under the wrong bundle where it can push the scout to propose a ticker
the article was never about.

    from websearch_orgs import attach_orgs
    attach_orgs(articles, canon)      # stamps a["orgs"], in place
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

from orgs import normalise, title_tickers   # noqa: E402  vocabulary helpers, not curator policy


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


def attach_orgs(arts: list[dict], canon: dict, counts: "collections.Counter | None" = None) -> int:
    """Stamp `orgs` on every websearch article, so the curator sees ONE article shape.

    Idempotent and non-destructive: an article that already carries an `orgs` KEY is left alone --
    that is a GKG article, and GKG's own extraction (offset-gated on V2Organizations) is always
    better evidence than a title match. An empty list is a real finding, not a gap.

    `canon`/`counts` come from the corpus the ingest is being folded into; without them there is no
    vocabulary to match against and every article is stamped `[]`, which is honest rather than empty.
    Returns how many articles got a non-empty list."""
    if counts is None and canon:
        counts = _subject_counts(canon, arts)
    n = 0
    for a in arts:
        if "orgs" in a:                      # GKG-ingested; never override
            continue
        found = _title_orgs(a, canon, counts) if canon else []
        a["orgs"] = found
        n += bool(found)
    return n
