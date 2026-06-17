"""polymarket.py — Step 2: the probability signal (Polymarket odds).

The SPEC's second signal role: *will the upstream event actually resolve?* A trigger
spawns a causal ladder, but the ladder only pays if the telegraphed action happens.
Polymarket's implied probability answers that with a real market price instead of a
maybe — so a ladder can be timed/sized against a probability the herd is also watching.

This module is MECHANICAL: it finds the market that matches a triggering event and reads
its YES price. It never forecasts — the number is the market's, not ours (and not the
LLM's; non-negotiable #1 is preserved). Judgment — *which* resolvable question a telegraph
implies — belongs upstream in the curator, which can emit a `polymarket_query`; here we
just fetch.

Two odds modes:

  - LIVE (`live_yes_odds`)      — the market's current YES price, from Gamma's
    `outcomePrices`. This is the forward-clean path: logged in real time, no look-ahead.
  - HISTORICAL (`historical_yes_odds`) — the YES price at/just before a past timestamp,
    via CLOB `/prices-history` bounded by `endTs` (look-ahead-safe by construction).

    *** KNOWN LIMITATION (verified) ***  The free CLOB price-history endpoint silently
    returns coarse (~12h) data or NOTHING for markets that have already RESOLVED — exactly
    the case a retrospective backtest hits. So historical enrichment of the seed events is
    best-effort and usually empty; the clean use of this signal is forward logging of LIVE
    odds. This is why Step 2 is forward-shaped, not a retrospective scoreboard rung — see
    SPEC.md (deferred decision #2 resolved).

Access is free and keyless. Reads only: Gamma (discovery) + CLOB (price history).

Usage
-----
    python src/polymarket.py "Trump reciprocal tariffs"            # live odds for a query
    python src/polymarket.py "Trump tariffs Japan" --as-of 2025-01-15
    python src/polymarket.py --enrich                              # add odds to events_mapped.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPPED_CSV = REPO_ROOT / "data" / "events_mapped.csv"

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TIMEOUT = 20

# A browser-like User-Agent: the endpoints sit behind Cloudflare, which 403s the default
# python-requests UA on some queries.
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (geo-herd-rider research backtest)"})


def _get(url: str, params: dict) -> dict | list:
    r = _SESSION.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _loads(value):
    """Gamma returns `outcomes`/`outcomePrices`/`clobTokenIds` as JSON-encoded strings."""
    return json.loads(value) if isinstance(value, str) else value


def search_markets(query: str, limit: int = 10, keep_closed: bool = True) -> list[dict]:
    """Markets matching `query`, best-first. Free-text search lives on /public-search;
    the /markets list endpoint has no `q`. Markets are nested under events."""
    data = _get(f"{GAMMA}/public-search", {
        "q": query, "limit_per_type": limit, "keep_closed_markets": 1 if keep_closed else 0,
    })
    out: list[dict] = []
    for ev in data.get("events", []):
        out.extend(ev.get("markets", []))
    return out


def yes_index(market: dict) -> int:
    """Index of the YES-equivalent outcome (the one whose price = P(action happens))."""
    outcomes = _loads(market.get("outcomes") or "[]")
    for i, o in enumerate(outcomes):
        if str(o).strip().lower() in {"yes", "true"}:
            return i
    return 0  # binary non-Yes/No (e.g. candidate names): caller sees the label, decides


def live_yes_odds(market: dict) -> float | None:
    """Current YES price (implied probability) from Gamma's `outcomePrices`."""
    prices = _loads(market.get("outcomePrices") or "[]")
    if not prices:
        return None
    try:
        return float(prices[yes_index(market)])
    except (ValueError, IndexError):
        return None


def historical_yes_odds(market: dict, as_of_ts: int, fidelity: int = 720) -> float | None:
    """YES price at/just before `as_of_ts` (Unix s) via CLOB /prices-history (look-ahead
    -safe: bounded by endTs). fidelity is in minutes; 720 (=12h) is the finest that
    returns anything for resolved markets, and even that is often empty. Returns None
    when no point exists at/<= the cutoff."""
    tokens = _loads(market.get("clobTokenIds") or "[]")
    if not tokens:
        return None
    token = tokens[yes_index(market)]
    try:
        hist = _get(f"{CLOB}/prices-history", {
            "market": token, "startTs": as_of_ts - 90 * 86400, "endTs": as_of_ts, "fidelity": fidelity,
        }).get("history", [])
    except requests.RequestException:
        return None  # resolved markets commonly 400 or return empty here — treat as no data
    pts = [p for p in hist if p.get("t", 0) <= as_of_ts]
    return float(pts[-1]["p"]) if pts else None


def odds_for_query(query: str, as_of: str | None = None) -> dict:
    """Find the best market for `query` and return its YES odds. LIVE if `as_of` is None,
    else the historical price at/just before `as_of` (date or ISO timestamp)."""
    markets = search_markets(query)
    if not markets:
        return {"matched": False, "query": query}
    m = markets[0]  # /public-search returns relevance-ranked; take the top hit
    info = {
        "matched": True, "query": query,
        "question": m.get("question"), "slug": m.get("slug"),
        "closed": bool(m.get("closed")),
        "outcome": _loads(m.get("outcomes") or "[]")[yes_index(m)] if m.get("outcomes") else None,
    }
    if as_of is None:
        info.update(odds=live_yes_odds(m), as_of="live")
    else:
        ts = int(pd.Timestamp(as_of).timestamp())
        info.update(odds=historical_yes_odds(m, ts), as_of=as_of)
    return info


def enrich(mapped: pd.DataFrame) -> pd.DataFrame:
    """Add a `polymarket_odds` column to mapped events, look-ahead-bounded by each
    telegraph date. Uses a `polymarket_query` column if the curator emitted one, else
    falls back to a heuristic query from the telegraph text. Best-effort: most resolved
    markets return no usable history (see module docstring)."""
    out = mapped.copy()
    odds_col, q_col, note_col = [], [], []
    for _, r in mapped.iterrows():
        query = str(r["polymarket_query"]) if "polymarket_query" in mapped.columns \
            and pd.notna(r.get("polymarket_query")) else _heuristic_query(r)
        try:
            res = odds_for_query(query, as_of=str(r["telegraph_ts"]))
        except requests.RequestException as e:
            odds_col.append(None); q_col.append(query); note_col.append(f"error: {e}")
            continue
        odds_col.append(res.get("odds"))
        q_col.append(query)
        if not res.get("matched"):
            note_col.append("no market")
        elif res.get("odds") is None:
            note_col.append("market matched, no pre-catalyst history")
        else:
            note_col.append(f"matched: {res.get('question')}")
    out["polymarket_query"] = q_col
    out["polymarket_odds"] = odds_col
    out["polymarket_note"] = note_col
    return out


def _heuristic_query(row: pd.Series) -> str:
    """Crude fallback query when the curator hasn't emitted a `polymarket_query`: the
    first several alphanumeric words of the telegraph (punctuation stripped — '%' and the
    like trip Cloudflare's WAF). Good matching really needs the LLM to phrase the
    resolvable question; this just exercises the plumbing."""
    words = "".join(c if c.isalnum() or c.isspace() else " "
                    for c in str(row.get("telegraph_text", ""))).split()
    return " ".join(words[:10])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Polymarket probability signal (Step 2).")
    ap.add_argument("query", nargs="?", help="search phrase for a resolvable event")
    ap.add_argument("--as-of", default=None, help="historical odds at/before this date (default: live)")
    ap.add_argument("--enrich", action="store_true", help="add odds columns to events_mapped.csv")
    ap.add_argument("--mapped", type=Path, default=MAPPED_CSV)
    args = ap.parse_args(argv)

    if args.enrich:
        mapped = pd.read_csv(args.mapped)
        out = enrich(mapped)
        out.to_csv(args.mapped, index=False)
        with_odds = int(out["polymarket_odds"].notna().sum())
        print(f"Enriched {len(out)} events -> {args.mapped}")
        print(f"  usable pre-catalyst odds: {with_odds}/{len(out)}")
        print("  (Expected to be ~0 retrospectively: resolved markets return no price history, "
              "and a fuzzy text query returns a likely-wrong top hit anyway. Trustworthy odds "
              "need the curator to emit a clean `polymarket_query` AND live (forward) logging — "
              "see module docstring.)")
        return 0

    if not args.query:
        ap.error("provide a query, or use --enrich")
    res = odds_for_query(args.query, as_of=args.as_of)
    if not res["matched"]:
        print(f"No Polymarket market found for: {args.query!r}")
        return 0
    odds = res["odds"]
    print(f"Market : {res['question']}")
    print(f"Outcome: {res['outcome']}    closed: {res['closed']}    as_of: {res['as_of']}")
    print(f"YES odds: {odds * 100:.1f}%" if odds is not None else "YES odds: n/a (no history at cutoff)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
