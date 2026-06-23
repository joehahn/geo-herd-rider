# scripts/ — runnable entry points

What each script does and how to run it (run from the repo root, in the `.venv`). The core
pipeline modules live in [`../src/`](../src/); the headline how-to is in the
[README's "Run it"](../README.md#run-it). Everything historical is a hindsight **upper bound** —
the forward eval is the only clean test.

## `run_harness.py` — the multi-event backtest harness
Runs a curator over the locked gem set's window ([`../data/fixtures/gems.json`](../data/fixtures/gems.json))
and scores it (recall / precision / tail / controls vs the gems). The dev loop.

```bash
# single-scan baseline (Opus):
python scripts/run_harness.py
# scout -> per-event agent (ticker-keyed), cheap dev model:
python scripts/run_harness.py --agent --provider openrouter --model xiaomi/mimo-v2.5-pro
# event-first engine (events own an evolving vehicle set):
python scripts/run_harness.py --event-first --provider openrouter --model xiaomi/mimo-v2.5-pro
```
Useful flags: `--seed data/fixtures/gems_seeds.json` (retrieval-perfect overlay), `--no-targeted`
(fast: skip per-event GDELT fetches), `--start/--end`, `--provider/--model`. GDELT pools and the
agent loop checkpoint to `data/windows/` (gitignored) and resume on re-run.

## `build_dashboard.py` — the $50K dashboard
Renders the GitHub-Pages dashboard ([`../docs/`](../docs/): portfolio vs SPY, allocation, firehose
log, cost) from the saved scan log (`data/windows/firehose_scans.json`). **No LLM cost.** The
committed dashboard is the **event-first agent** book over the BWET window (GDELT firehose + BWET
seed) — regenerate that scan log with `run_harness.py --event-first … --dump-scans`, then rebuild:

```bash
# regenerate the agent scan log (the on-screen book):
python scripts/run_harness.py --event-first --provider openrouter --model xiaomi/mimo-v2.5-pro \
  --seed data/fixtures/firehose_bwet.json --no-targeted \
  --start 2026-02-06 --end 2026-06-18 --dump-scans data/windows/firehose_scans.json
python scripts/build_dashboard.py        # reads data/windows/firehose_scans.json -> docs/
python -m http.server -d docs            # then open localhost:8000
```

## `plot_shipping.py` — the motivating figure
The BWET-vs-SPY chart (indexed to 100 at the Feb-2026 carrier deployment) → `assets/bwet_vs_spy.png`
(embedded in the README). Pulls prices via yfinance.

```bash
python scripts/plot_shipping.py
```

## See also (CLI entry points in `src/`)
- `python src/firehose.py --fixture data/fixtures/firehose_bwet.json …` — look-ahead-clean mechanics
  test (BWET-only, perfect-retrieval assumption).
- `python src/forward.py --scan` / `--report` — the live, contamination-free forward eval (the verdict).
