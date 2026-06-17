"""forward.py — Step 2: the forward paper-trade logger (the only clean test).

Every retrospective number in this repo is hindsight-contaminated: the model that maps a
2024-2025 telegraph was trained past the outcome, and resolved Polymarket markets give no
usable history. SPEC says so plainly. The clean test is therefore FORWARD — log a decision
the moment a fresh trigger arrives, before anyone (the model included) knows how it plays
out, then settle it after the horizon elapses. Look-ahead is impossible by construction:
the decision row is written at `decision_ts`, and nothing about the outcome exists yet.

Three modes:

  --add     For each pending trigger not already logged: run the LLM curator (map_event)
            to get the causal ladder + the resolvable question, fetch the LIVE Polymarket
            odds for that question (polymarket), and append an OPEN row stamped with
            decision_ts = now. Needs ANTHROPIC_API_KEY (costs tokens); use fresh triggers.
  --settle  For each OPEN row whose horizon has elapsed, score the realized trade the same
            way the scoreboard does (score.score_event), entering at the first close after
            decision_ts. Marks the row CLOSED with its forward excess-vs-SPY.
  --report  Summarize CLOSED forward trades: excess, hit rate, by chain_depth / audience,
            and Polymarket calibration — the clean evidence the rest of the project is
            building toward.

Input  : data/forward_events.csv   (event_id, telegraph_ts, source, telegraph_text)
State  : data/forward_log.csv      (append-only decision log; settle fills outcome cols)

The look-ahead caveat for --add: log triggers AS THEY HAPPEN. Feeding a stale (already-
resolved) telegraph defeats the purpose — the model may know the outcome, and that's the
exact contamination this logger exists to avoid. A soft warning fires if a telegraph is
more than a few days old at logging time.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

import map_event
import polymarket
import score

REPO_ROOT = Path(__file__).resolve().parent.parent
PENDING_CSV = REPO_ROOT / "data" / "forward_events.csv"
LOG_CSV = REPO_ROOT / "data" / "forward_log.csv"

STALE_DAYS = 4  # a telegraph older than this at logging time isn't a clean forward entry


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def log_pending(pending: pd.DataFrame, log: pd.DataFrame, client) -> pd.DataFrame:
    """Map + price each not-yet-logged trigger and append an OPEN row. Returns the log."""
    seen = set(log["event_id"]) if len(log) else set()
    rows: list[dict] = []
    for _, ev in pending.iterrows():
        eid = ev["event_id"]
        if eid in seen:
            print(f"  {eid}: already logged, skipping")
            continue
        tele = pd.Timestamp(ev["telegraph_ts"])
        age = (_now() - (tele.tz_convert("UTC") if tele.tzinfo else tele.tz_localize("UTC"))).days
        if age > STALE_DAYS:
            print(f"  {eid}: WARNING telegraph is {age}d old — not a clean forward entry "
                  f"(log triggers as they happen).", file=sys.stderr)
        print(f"  {eid}: mapping ...", flush=True)
        try:
            mapping = map_event.map_one(client, ev, use_web_search=True)
        except Exception as e:  # one bad trigger shouldn't sink the batch
            print(f"  {eid}: FAILED ({e})", file=sys.stderr)
            continue
        odds = None
        query = mapping.get("polymarket_query") or ""
        if query:
            try:
                odds = polymarket.odds_for_query(query).get("odds")  # LIVE odds, no look-ahead
            except Exception as e:
                print(f"  {eid}: odds fetch failed ({e})", file=sys.stderr)
        rows.append({
            "decision_ts": _now().isoformat(),
            **{k: ev[k] for k in ("event_id", "telegraph_ts", "source", "telegraph_text")},
            **mapping,
            "polymarket_odds": "" if odds is None else odds,
            "status": "open",
        })
        print(f"  {eid}: logged {mapping['direction']} {mapping['mapped_tickers']} "
              f"(horizon {mapping['horizon_days']}d, odds={odds})")
    return pd.concat([log, pd.DataFrame(rows)], ignore_index=True) if rows else log


def settle(log: pd.DataFrame) -> pd.DataFrame:
    """Score OPEN rows whose horizon has elapsed; enter at the first close after the
    decision was logged (decision_ts), so the trade is exactly what we committed to."""
    log = log.copy()
    open_rows = log[log["status"] == "open"]
    if open_rows.empty:
        print("No open positions to settle.")
        return log

    tickers = {score.BENCHMARK}
    for cell in open_rows["mapped_tickers"]:
        tickers.update(t.strip().upper() for t in str(cell).split(";") if t.strip())
    dts = pd.to_datetime(open_rows["decision_ts"], utc=True).dt.tz_localize(None)
    start = (dts.min() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    end = (_now().tz_localize(None) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Settling {len(open_rows)} open positions; fetching prices {start} .. {end} ...")
    panel = score.fetch_panel(sorted(tickers), start, end, use_cache=False)

    settled = 0
    for i, r in open_rows.iterrows():
        # Enter at the decision, not the telegraph: build an event whose timing is decision_ts.
        ev = r.copy()
        ev["telegraph_ts"] = r["decision_ts"]
        res = score.score_event(ev, panel)
        if not res or res.get("status") != "ok":
            continue  # horizon not yet elapsed / no price window — leave open, retry later
        for col in ("entry_date", "exit_date", "strategy_return", "spx_return",
                    "excess_return", "hit", "path_shape"):
            log.at[i, col] = res[col]
        log.at[i, "status"] = "closed"
        log.at[i, "settled_ts"] = _now().isoformat()
        settled += 1
    print(f"Settled {settled} position(s); {len(open_rows) - settled} still open.")
    return log


def report(log: pd.DataFrame) -> None:
    """The clean forward eval: excess vs SPY on settled trades, by cohort, with calibration."""
    closed = log[log["status"] == "closed"].copy() if "status" in log else pd.DataFrame()
    print("\n" + "=" * 60)
    print("geo-herd-rider — FORWARD paper-trade scoreboard (look-ahead-clean)")
    print("=" * 60)
    if closed.empty:
        n_open = int((log["status"] == "open").sum()) if len(log) else 0
        print(f"No settled trades yet ({n_open} open). Run --add as triggers arrive, then "
              f"--settle after their horizons elapse.")
        return
    closed["excess_return"] = pd.to_numeric(closed["excess_return"], errors="coerce")
    closed["hit"] = closed["hit"].astype(str).isin({"True", "true", "1", "1.0"})
    n = len(closed)
    print(f"settled trades: {n}")
    print(f"  median excess vs SPY : {closed['excess_return'].median() * 100:+.2f}%")
    print(f"  mean   excess vs SPY : {closed['excess_return'].mean() * 100:+.2f}%")
    print(f"  hit rate             : {closed['hit'].mean() * 100:.0f}%")

    mb = closed[(pd.to_numeric(closed["chain_depth"], errors="coerce") >= 2)
                & (~closed["audience_breadth"].isin({"megaphone"}))]
    if len(mb):
        print(f"  middle band (n={len(mb)}) : median {mb['excess_return'].median() * 100:+.2f}%"
              f"  hit {mb['hit'].mean() * 100:.0f}%")

    cal = closed[pd.to_numeric(closed["polymarket_odds"], errors="coerce").notna()]
    if len(cal):
        cal = cal.copy()
        cal["odds"] = pd.to_numeric(cal["polymarket_odds"])
        likely = cal[cal["odds"] >= 0.5]
        print(f"  Polymarket calibration (n={len(cal)} with odds): "
              f"odds>=50% hit {likely['hit'].mean() * 100:.0f}% (n={len(likely)})"
              if len(likely) else f"  Polymarket calibration: {len(cal)} with odds, none >=50%")
    print("=" * 60)


def _read_log() -> pd.DataFrame:
    return pd.read_csv(LOG_CSV) if LOG_CSV.exists() else pd.DataFrame(columns=["event_id", "status"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Forward paper-trade logger (Step 2, the clean test).")
    ap.add_argument("--add", action="store_true", help="map+price+log pending triggers (needs API key)")
    ap.add_argument("--settle", action="store_true", help="score open positions whose horizon elapsed")
    ap.add_argument("--report", action="store_true", help="summarize settled forward trades")
    ap.add_argument("--events", type=Path, default=PENDING_CSV)
    args = ap.parse_args(argv)
    if not (args.add or args.settle or args.report):
        ap.error("choose at least one of --add / --settle / --report")

    log = _read_log()

    if args.add:
        map_event._load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY not set (export it or put it in .env).", file=sys.stderr)
            return 2
        if not args.events.exists():
            print(f"ERROR: {args.events} not found. Add pending triggers there "
                  f"(event_id, telegraph_ts, source, telegraph_text).", file=sys.stderr)
            return 2
        import anthropic
        log = log_pending(pd.read_csv(args.events), log, anthropic.Anthropic())
        LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
        log.to_csv(LOG_CSV, index=False)
        print(f"Log now holds {len(log)} decisions -> {LOG_CSV}")

    if args.settle:
        log = settle(log)
        log.to_csv(LOG_CSV, index=False)

    if args.report:
        report(log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
