# TODO — backlog (not yet scoped into the SPEC baby-step ladder)

Actionable ideas parked here until promoted into a scoreboard-gated step. See
[`SPEC.md`](SPEC.md) for the committed plan and [`CLAUDE.md`](CLAUDE.md) for the rules.

## Regime-contrast study: loud geopolitics vs. quiet stretches

Run the solution across **matched multi-month windows** and check whether it behaves the way
the thesis predicts — finds signal when the world is loud, and a clean **null** when it isn't.

- **~3 loud, market-moving geopolitical events**, each lasting several months:
  1. The Iran war (Israel–Iran escalation, 2024–25).
  2. _TBD_ (candidates: a Russia–Ukraine phase; the 2025 tariff war; a Fed pivot window).
  3. _TBD_.
- **~3 multi-month "quiet" stretches** with no dominant geopolitical driver, as controls.
- Restricted to **the period Polymarket data exists** (roughly 2024+ for rich coverage).

**Success looks like:** the curated middle-band book beats SPY in the loud windows (a "hidden
gem"), and shows **no edge** in the quiet windows (confirms the null — the system shouldn't
manufacture signal from noise). Either outcome is informative; a manufactured-signal result in
quiet periods would be a red flag worth acting on.

### Prerequisites & honest caveats (read before starting)

- **Hindsight contamination (the big one).** The curator LLM was trained past 2024–25, so a
  retrospective run over these windows is contaminated at the *curation* layer — not fixable
  by price hygiene. Treat this as a **structural sanity check** (does behaviour differ
  sensibly across regimes?), not clean proof. The cleanest version uses windows *after* the
  model's training cutoff, or a model with an earlier cutoff — which collapses toward the
  forward eval ([`src/forward.py`](src/forward.py)).
- **Polymarket historical odds.** The free CLOB endpoint gives coarse/empty data for resolved
  markets (deferred decision #2). Including the probability signal here needs on-chain
  reconstruction or a third-party historical dump; otherwise run the trigger→curator→backtest
  legs odds-free and add probability only in the forward eval.
- **Inputs to build.** A per-window `events.csv` of that period's real triggers (loud events
  + a representative sample for the quiet windows), then `map_event → score → curator --backtest`
  per window, compared against each window's SPY.
- **Pre-register the contrast** before running (which windows, what "edge" threshold) so the
  loud-vs-quiet comparison can't be tuned to the data — same discipline as the Step-1 bar.
