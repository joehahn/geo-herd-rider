#!/usr/bin/env python
"""run_bakeoff.py — the reproducible LLM bake-off: WARM-scan every curator model over each bake-off
gem on the CURRENT investor_profile + prompts, then rebuild the LLM sweep plot.

Two-tier by design:
  * SCANS (this script) = the expensive, DELIBERATE step (~$16, ~85 min for 7 models). Each model
    re-reads investor_profile.md (curator config) and the live prompts in agent.py, then warm-scans
    each gem (cache-only enrich; pools already cached). Same prompt => run_harness resume is instant;
    a prompt change => fresh scans. So cost is proportional to what actually changed.
  * SCORING lives in build_dashboard.build_sweeps (cheap, re-reads the profile's backtest config, runs
    on every dashboard refresh). This script calls it once at the end to produce the plot.

  python scripts/run_bakeoff.py                        # all models x all gems, warm, parallel-by-model
  python scripts/run_bakeoff.py --models sonnet5,opus  # subset of models
  python scripts/run_bakeoff.py --gems mp,bwet         # subset of gems
  python scripts/run_bakeoff.py --plot-only            # skip scans, just rebuild the plot
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from optimizer import CURATOR_MODELS  # noqa: E402

# each bake gem -> (scan start, scan end, seed file stem): the WARM scan window (GDELT pool cached).
BAKEOFF_GEMS = {
    "mp":       ("2025-03-21", "2025-12-26", "mp_seeds"),          # per-gem news-derived seeds (gems_seeds was hollowed out)
    "bwet":     ("2025-12-26", "2026-07-03", "bwet_seeds"),
    "geo_mstr": ("2024-08-01", "2025-03-01", "election_2024_seeds"),
    "gdx":      ("2024-11-01", "2026-03-27", "gdx_seeds"),         # all 6 gems now scored in the bake-off
    "smr":      ("2024-04-19", "2024-09-13", "smr_seeds"),
    "rnmby":    ("2024-10-18", "2025-12-12", "rnmby_seeds"),
}
DEFAULT_MODELS = ["mimo", "llama4", "deepseek", "grok4", "sonnet4", "sonnet5", "opus"]
LOGDIR = ROOT / "data" / "windows" / "bakeoff" / "logs"


def _scan(short: str, gem: str) -> int:
    """One warm curator scan (model x gem) -> data/windows/bakeoff/firehose_scans_<gem>__<model>.json."""
    mid, prov = CURATOR_MODELS[short]
    start, end, seed = BAKEOFF_GEMS[gem]
    out = ROOT / "data" / "windows" / "bakeoff" / f"firehose_scans_{gem}__{short}.json"
    cmd = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/run_harness.py"), "--event-first",
           "--model", mid, "--provider", prov,
           "--seed", str(ROOT / "data/fixtures" / f"{seed}.json"), "--no-targeted",
           "--start", start, "--end", end, "--enrich", "--enrich-cache-only",
           "--dump-scans", str(out)]
    with open(LOGDIR / f"{short}_{gem}.log", "w") as fh:
        return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode


def _scan_model(short: str, gems: list) -> str:
    """A model's gems, SERIAL within the model (so one model doesn't hammer its own provider)."""
    for g in gems:
        rc = _scan(short, g)
        print(f"  [{time.strftime('%H:%M:%S')}] {short}/{g}: {'ok' if rc == 0 else 'FAIL rc=' + str(rc)}", flush=True)
    print(f"  [{time.strftime('%H:%M:%S')}] <<< {short} DONE", flush=True)
    return short


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS), help="comma-sep model short names")
    ap.add_argument("--gems", default=",".join(BAKEOFF_GEMS), help="comma-sep bake gems")
    ap.add_argument("--plot-only", action="store_true", help="skip scans; just rebuild the plot")
    a = ap.parse_args()
    models = [m for m in a.models.split(",") if m in CURATOR_MODELS]
    gems = [g for g in a.gems.split(",") if g in BAKEOFF_GEMS]
    bad_m = [m for m in a.models.split(",") if m not in CURATOR_MODELS]
    bad_g = [g for g in a.gems.split(",") if g not in BAKEOFF_GEMS]
    if bad_m or bad_g:
        print(f"  (ignored unknown: models={bad_m} gems={bad_g})", file=sys.stderr)
    if not models or not gems:
        print("nothing to do (no valid models/gems)", file=sys.stderr)
        return 2
    if not a.plot_only:
        LOGDIR.mkdir(parents=True, exist_ok=True)
        print(f"bake-off: {len(models)} models x {len(gems)} gems, WARM, parallel-by-model "
              f"@ {time.strftime('%H:%M:%S')}  (models={models})")
        with cf.ThreadPoolExecutor(max_workers=max(1, len(models))) as ex:
            list(ex.map(lambda m: _scan_model(m, gems), models))
        print(f"all scans done @ {time.strftime('%H:%M:%S')}")
    import build_dashboard as bd  # noqa: PLC0415
    bd.build_sweeps()
    print("LLM sweep plot rebuilt -> docs/sweeps/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
