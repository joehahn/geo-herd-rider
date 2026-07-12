"""gem_detect.py — backtest-gem detection over a retrieved article pool (Tavily retrieval backtest).

Classifies each retrieved article per known backtest gem. Each gem has:
  name    -> distinctive company/product strings, matched as SUBSTRING (safe: "rheinmetall")
  ticker  -> exchange symbol, matched as a WHOLE TOKEN only ($TK, (NYSE:TK)/(TK) parenthetical, or bare
             uppercase \\bTK\\b for len>=3); len<3 (MP) or `strict` gems (DRAM) require the $/paren form
  thesis  -> sector/catalyst phrases, matched as SUBSTRING — the EARLY signal that precedes by-name

A hit is BY-NAME (name or ticker matched = the vehicle is explicitly named) vs THESIS-ONLY (only the
sector phrase matched = the catalyst is visible but the ticker isn't yet). Early BY-NAME is the strong
result; thesis-only is the weaker "catalyst was retrievable, vehicle not named" result. The gem `form`
(single stock / ETF wrapper / foreign ADR) is the axis the backtest found decisive — see the dashboard.
"""
from __future__ import annotations
import re

# form: how the gem trades — the axis retrievability splits on (single stocks name early; ETF wrappers late)
GEMS = {
    "MP":    {"form": "single stock", "name": ["mp materials"], "ticker": ["MP"],
              "thesis": ["rare earth", "rare-earth", "neodymium", "critical mineral"]},
    "AREC":  {"form": "single stock", "name": ["american resources", "reelement"], "ticker": ["AREC"],
              "thesis": ["rare earth", "rare-earth", "critical mineral", "ndfeb", "gallium", "magnet"]},
    "TSM":   {"form": "single stock", "name": ["taiwan semiconductor", "tsmc"], "ticker": ["TSM"],
              "thesis": ["semiconductor", "foundry", "ai chip", "chipmaker", "advanced node"]},
    "GDX":   {"form": "ETF wrapper", "name": ["vaneck gold", "gold miners etf"], "ticker": ["GDX"],
              "thesis": ["gold miner", "gold mining stock"]},
    "RNMBY": {"form": "foreign ADR", "name": ["rheinmetall"], "ticker": ["RNMBY"],
              "thesis": ["german defen", "europe defen", "defense spending", "rearmament"]},
    "BWET":  {"form": "ETF wrapper", "name": ["breakwave"], "ticker": ["BWET"],
              "thesis": ["vlcc", "tanker rate", "tanker stock", "freight rate", "supertanker"]},
    # DRAM: ticker collides with "DRAM" the memory commodity -> STRICT (require $DRAM/(DRAM) form, never
    # the bare word, else Micron/DRAM-chip articles false-positive). name catches the Roundhill Memory ETF.
    "DRAM":  {"form": "ETF wrapper", "name": ["roundhill memory", "memory etf"], "ticker": ["DRAM"],
              "strict": True, "thesis": ["memory chip", "dram shortage", "memory shortage",
                                         "memory price", "dram price"]},
}
PEAK = {"MP": "2025-07-10", "GDX": "2026-02-13", "RNMBY": "2025-11-19", "BWET": "2026-04-25", "DRAM": "2026-06-18",
        "AREC": "2025-10-14", "TSM": "2026-06-30"}


def _ticker_hit(tk: str, raw: str, strict: bool = False) -> bool:
    """Whole-token ticker match: $TK, or a (EXCH: TK)/(TK) parenthetical. When not strict, also a bare
    uppercase \\bTK\\b for len>=3. strict=True (e.g. DRAM, a common word) requires the $/paren form."""
    if _ticker_form(tk, raw):
        return True
    if not strict and len(tk) >= 3 and re.search(rf"(?<![A-Za-z0-9]){tk}(?![A-Za-z0-9])", raw):
        return True
    return False


def _ticker_form(tk: str, raw: str) -> bool:
    """A DELIBERATE editorial ticker tag: $TK or a (EXCH: TK)/(TK) parenthetical (never a bare word).
    These survive as real ticker references even inside a snippet; a bare company NAME does not —
    Tavily scrapes page chrome (related-article widgets, image captions like 'the MP Materials logo
    is seen on a phone screen'), so a bare name in the snippet alone is unreliable."""
    return bool(re.search(rf"\${tk}\b", raw)
                or re.search(rf"\((?:NYSE|NASDAQ|NYSEARCA|OTC|BATS)?[:\s]*{tk}\)", raw))


def _named_in(text: str, kw: dict) -> bool:
    low = text.lower()
    return any(n in low for n in kw["name"]) or any(_ticker_hit(t, text, kw.get("strict", False)) for t in kw["ticker"])


def detect(pool: list[dict]) -> dict:
    """-> {gem: {"by_name":[arts], "thesis":[arts]}} sorted by date; each list = matching articles.

    BY-NAME requires the gem in the article TITLE, or a deliberate ticker tag ($TK / (TK)) ANYWHERE
    (title+snippet). A bare company-name in the snippet ONLY is rejected — that pattern is Tavily
    page-chrome (sidebars / image captions / related-headline widgets) far more often than the
    article's real subject (e.g. an Opendoor article whose scraped snippet carries an 'MP Materials'
    related-link thumbnail). THESIS-only = the sector/catalyst phrase but no by-name."""
    out = {g: {"by_name": [], "thesis": []} for g in GEMS}
    for a in pool:
        title = a.get("title", "") or ""
        blob = title + "  " + (a.get("snippet", "") or "")
        low = blob.lower()
        for g, kw in GEMS.items():
            named = _named_in(title, kw) or any(_ticker_form(t, blob) for t in kw["ticker"])
            thesis = any(t in low for t in kw["thesis"])
            if named:
                out[g]["by_name"].append(a)
            elif thesis:
                out[g]["thesis"].append(a)
    for g in out:
        for k in out[g]:
            out[g][k].sort(key=lambda x: x.get("published_date", ""))
    return out
