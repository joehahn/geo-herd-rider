"""run_harness.py — multi-event backtest harness: does the firehose harvest the DISTRIBUTION?

Runs the firehose over the locked gem set's window (data/fixtures/gems.json) with a realistic,
date-honored GDELT firehose and BROAD market-beat queries (themes, never the gem tickers — the
analyst watches beats; the curator must discover the names). Scores the book against the gems on
three axes the single BWET number can't measure:

  RECALL    — of the N gems, how many did the book ever hold?  (does it catch the medium tier,
              or only the loud tail?)
  PRECISION — of all distinct names it held, how many were gems vs noise/fizzles?  (the cost of
              casting a wide net — the false positives.)
  TAIL      — aggregate book vs SPY, and how concentrated the P&L is in the top names.
  CONTROLS  — did it (wrongly) hold a known broken-thesis trap, e.g. PTON?

This is the single-scan BASELINE. Everything here is a hindsight upper bound (survivor gem set +
a curator model trained past the events + GDELT's late niche coverage) — a dev instrument, not a
verdict. The forward eval (src/forward.py) remains the only clean test.

    python scripts/run_harness.py                      # full window from gems.json
    python scripts/run_harness.py --start 2024-01-01 --end 2024-06-30   # short smoke window
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import firehose  # noqa: E402
import agent  # noqa: E402
import score  # noqa: E402
from optimizer import load_financial_model, resolve_curator_model, CURATOR_MODELS  # noqa: E402
from util import load_dotenv  # noqa: E402

GEMS_JSON = ROOT / "data" / "fixtures" / "gems.json"
REPORT = ROOT / "data" / "windows" / "harness_report.json"

# GDELT firehose queries — GEM-AGNOSTIC, derived from SCAN_SYSTEM's own search intent so backtest
# retrieval mirrors what the forward Anthropic web_search would pull (no hindsight beat-picking).
# SCAN_SYSTEM tells the model to hunt "a standout trade on a live thesis (geopolitics, energy/
# shipping, tariffs, Fed, a sector catalyst)" — so the list is: superlative discovery framing +
# the macro beats the prompt names + an EVEN top-level sector sweep (the standard market partition,
# NOT gem sub-niches like "uranium"/"rare earth"/"weight loss drug" — those were reverse-engineered
# from known winners and are dropped). The list is FROZEN, not LLM-generated per week: the curator
# is trained past these events, so letting it pick weekly queries would leak the answer. GDELT needs
# single words or QUOTED phrases. See agent_design.md "Retrieval: GDELT and seeds".
HARNESS_QUERIES = [
    # discovery superlatives (cross-vertical — the purest "press names a standout")
    '"best performing stock"', '"biggest gainers"', '"best performing etf"',
    # macro beats SCAN_SYSTEM names (geopolitics / war / energy-shipping / tariffs / Fed); "war"
    # retrieves kinetic-conflict coverage that the literal term "geopolitics" misses on GDELT.
    "geopolitics", "war", "shipping", "tariffs", '"interest rates"',
    # even top-level sector sweep — the standard GICS sectors (10 beats covering all 11; Consumer
    # Staples + Discretionary merged into one "consumer"), so every gem is reachable via its SECTOR
    # not its sub-niche, and no sector is privileged by hindsight. Gem-agnostic by construction
    # (a complete partition privileges nothing; utilities is also nuclear's GICS home).
    '"technology stocks"', '"energy stocks"', '"financial stocks"', '"healthcare stocks"',
    '"industrial stocks"', '"materials stocks"', '"consumer stocks"',
    '"utility stocks"', '"real estate stocks"', '"telecom stocks"',
    # THESIS-DRIVEN theme layer — a PRE-REGISTERED forward thesis (from portfolio-wave-rider): non-
    # GICS asset classes (crypto) and emerging-tech areas where gems emerge but the GICS sweep is too
    # coarse (or doesn't reach, like crypto). NOT the gem-agnostic partition above, so recall on themed
    # gems (MSTR via "cryptocurrency", SMR/URA via "nuclear stocks") is partly thesis-aided — accepted
    # because the list is fixed BEFORE the eval (CLAUDE.md #5) and is an INDEPENDENT thesis, not
    # reverse-engineered from our winners. Still excluded: ticker-exact sub-niches ("uranium"/"rare
    # earth"/"weight loss drug"/"Milei").
    "cryptocurrency", '"space stocks"', '"robotics stocks"', '"quantum stocks"', '"nuclear stocks"',
]


def _held_weeks(bt: dict) -> dict[str, list[str]]:
    """ticker -> [weeks it carried nonzero weight], from the backtest log."""
    out: dict[str, list[str]] = {}
    for row in bt["log"]:
        for leg in str(row.get("weights", "")).split(";"):
            if ":" in leg:
                t, w = leg.split(":")
                if float(w) > 0:
                    out.setdefault(t.strip(), []).append(row["week"])
    return out


def _captured(ticker: str, weeks: list[str], panel: pd.DataFrame) -> float | None:
    """Realized multiple over the held span (first held week -> last), from close prices."""
    if ticker not in panel.columns or not weeks:
        return None
    s = panel[ticker].dropna()
    lo = s.loc[:weeks[0]].index
    hi = s.loc[:weeks[-1]].index
    if not len(lo) or not len(hi):
        return None
    return round(float(s.loc[hi[-1]] / s.loc[lo[-1]]), 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spec = json.loads(GEMS_JSON.read_text())
    ap.add_argument("--start", default=spec["window"]["start"])
    ap.add_argument("--end", default=spec["window"]["end"])
    ap.add_argument("--model", default=None,
                    help="override the curator model id; default resolves from investor_profile `model` knob")
    ap.add_argument("--provider", default=None, choices=["anthropic", "openrouter"],
                    help="override provider; default resolves from the profile `model` knob (mimo=>openrouter)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--chunk-days", type=int, default=90, help="GDELT pool fetch chunk (coarser = fewer throttled calls)")
    ap.add_argument("--per", type=int, default=150, help="GDELT records per query-chunk")
    ap.add_argument("--seed", default=None,
                    help="retrieval-perfect overlay: early-article seeds per gem (decomposition run)")
    ap.add_argument("--agent", action="store_true",
                    help="run the scout->per-event-agent variant instead of the single scan (the A/B)")
    ap.add_argument("--no-targeted", action="store_true",
                    help="fast variant: agents read the broad cached pool only (skip per-event GDELT fetches)")
    ap.add_argument("--event-first", action="store_true",
                    help="event-first engine: events own an evolving vehicle set (vs ticker-keyed --agent)")
    ap.add_argument("--enrich", action="store_true",
                    help="Wayback as-of-date snippet enrichment of GDELT headlines (event-first only)")
    ap.add_argument("--enrich-cache-only", action="store_true",
                    help="with --enrich: apply only already-cached Wayback ledes, make no archive.org "
                         "calls (fast; use when archive.org is degraded)")
    ap.add_argument("--dump-scans", default=None,
                    help="write {date: picks} to this path (e.g. data/windows/firehose_scans.json for the dashboard)")
    args = ap.parse_args(argv)

    load_dotenv()
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        return 2

    gems = {g["ticker"]: g for g in spec["gems"]}
    controls = {c["ticker"]: c for c in spec.get("controls", [])}
    fm = load_financial_model(str(ROOT / "investor_profile.md"))
    rebalance = int(fm.get("rebalance_days", 7))
    model_short = str(fm.get("model", "mimo")).strip().lower()
    resolved_id, resolved_prov = resolve_curator_model(model_short)
    if args.model is None:
        args.model = resolved_id
    else:  # explicit --model override: recover its short name for the sidecar (else use the id)
        model_short = {mid: s for s, (mid, _) in CURATOR_MODELS.items()}.get(args.model, args.model)
    if args.provider is None:
        args.provider = resolved_prov
    print(f"  curator model: {model_short} -> {args.model} ({args.provider})", file=sys.stderr)

    print(f"Harness: firehose over {args.start}..{args.end}, {len(gems)} gems, "
          f"{len(HARNESS_QUERIES)} broad queries, {rebalance}d cadence (single-scan baseline).",
          file=sys.stderr)
    if args.event_first:
        scans = agent.run_event_agent_scans(args.start, args.end, rebalance, args.model, args.workers,
                                            queries=HARNESS_QUERIES, seed=args.seed,
                                            pool_chunk_days=args.chunk_days, pool_per=args.per,
                                            provider=args.provider, targeted=not args.no_targeted,
                                            enrich=args.enrich, enrich_fetch=not args.enrich_cache_only,
                                            curator_memory_weeks=int(fm.get("curator_memory_weeks", 8) or 0),
                                            window_cap=int(fm.get("window_cap", agent.WINDOW_CAP)))
    elif args.agent:
        scans = agent.run_agent_scans(args.start, args.end, rebalance, args.model, args.workers,
                                      queries=HARNESS_QUERIES, seed=args.seed,
                                      pool_chunk_days=args.chunk_days, pool_per=args.per,
                                      provider=args.provider, targeted=not args.no_targeted)
    else:
        scans = firehose.run_scans(args.start, args.end, rebalance, args.model, args.workers,
                                   gdelt=True, queries=HARNESS_QUERIES, seed=args.seed,
                                   pool_chunk_days=args.chunk_days, pool_per=args.per)
    if args.seed:
        print(f"  (retrieval-perfect overlay {Path(args.seed).name})", file=sys.stderr)
    if args.dump_scans:  # persist {date: picks} so build_dashboard can render THIS engine's book
        Path(args.dump_scans).write_text(json.dumps(
            {a.date().isoformat(): scans[a] for a in scans}, indent=2, default=str))
        # sidecar: which curator model produced THIS book (the dashboard reads it to display it)
        meta_path = Path(args.dump_scans).with_suffix(".meta.json")
        meta_path.write_text(json.dumps(
            {"model": model_short, "model_id": args.model, "provider": args.provider}, indent=2))
        print(f"  dumped scans -> {args.dump_scans}  (model {model_short})", file=sys.stderr)
    bt = firehose.backtest(scans, fm, daily=False)

    held = _held_weeks(bt)
    all_held = set(held)
    gem_t, ctrl_t = set(gems), set(controls)
    caught = sorted(all_held & gem_t)
    missed = sorted(gem_t - all_held)
    ctrl_held = sorted(all_held & ctrl_t)
    noise = sorted(all_held - gem_t - ctrl_t)

    # trigger-relative recall: the value is catching a gem EARLY, near its catalyst — not holding
    # it at some arbitrary later week. "early" = first held within [-3wk, +12wk] of trigger_date.
    EARLY_LO, EARLY_HI = pd.Timedelta(weeks=3), pd.Timedelta(weeks=12)
    early = []
    for t in caught:
        first = pd.Timestamp(min(held[t]))
        trig = pd.Timestamp(gems[t]["trigger_date"])
        if trig - EARLY_LO <= first <= trig + EARLY_HI:
            early.append(t)
    early = sorted(early)

    # prices for captured-return per caught gem
    tickers = sorted(all_held | gem_t | {score.BENCHMARK})
    panel = score.fetch_panel(tickers, args.start,
                              (pd.Timestamp(args.end) + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                              use_cache=False)
    captures = {t: {"captured_x": _captured(t, held[t], panel), "available_x": gems[t]["peak_multiple"],
                    "weeks_held": len(held[t])} for t in caught}

    recall = len(caught) / len(gem_t) if gem_t else 0.0
    recall_early = len(early) / len(gem_t) if gem_t else 0.0
    precision = len(caught) / len(all_held) if all_held else 0.0  # of distinct positions, frac that were gems
    rep = {
        "window": [args.start, args.end], "cadence_days": rebalance,
        "variant": "single-scan baseline",
        "recall": {"pct": round(recall, 3), "early_pct": round(recall_early, 3),
                   "caught": caught, "caught_early": early, "missed": missed},
        "precision": {"pct": round(precision, 3), "distinct_held": len(all_held),
                      "gems_held": len(caught), "noise_held": noise, "controls_held": ctrl_held},
        "tail": {"book_ret": round(bt["final"] / 50000 - 1, 4),
                 "spy_ret": round(bt["spy_final"] / 50000 - 1, 4), "weeks": bt["weeks"]},
        "captures": captures,
    }
    tag = "event" if args.event_first else ("agent" if args.agent else ("seeded" if args.seed else None))
    out_path = REPORT.with_name(f"harness_report_{tag}.json") if tag else REPORT
    rep["variant"] = (("event-first" if args.event_first else "scout->per-event-agent" if args.agent
                       else "single-scan") + (" + seed overlay" if args.seed else ""))
    out_path.write_text(json.dumps(rep, indent=2, default=str))

    print("\n" + "=" * 64)
    print(f"MULTI-EVENT HARNESS — single-scan baseline ({args.start}..{args.end})")
    print("=" * 64)
    print(f"RECALL    early {recall_early:.0%} ({len(early)}/{len(gem_t)} caught near trigger): {', '.join(early) or '—'}")
    print(f"          any   {recall:.0%} ({len(caught)}/{len(gem_t)} ever held): {', '.join(caught) or '—'}")
    print(f"          missed entirely: {', '.join(missed) or '—'}")
    print(f"PRECISION {precision:.0%}  ({len(caught)} gems of {len(all_held)} distinct held)")
    print(f"          noise: {', '.join(noise) or '—'}")
    print(f"CONTROLS  held (should be empty): {', '.join(ctrl_held) or '— none ✓'}")
    print(f"TAIL      book {rep['tail']['book_ret']:+.0%} vs SPY {rep['tail']['spy_ret']:+.0%}")
    print("CAPTURE   gem: captured× / available× (weeks)")
    for t in caught:
        c = captures[t]
        print(f"          {t:5} {str(c['captured_x'])+'x':>7} / {c['available_x']}x  ({c['weeks_held']}w)")
    print(f"\n-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
