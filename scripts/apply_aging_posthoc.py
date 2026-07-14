#!/usr/bin/env python3
"""Apply the aging-retirement rule POST-HOC to the baseline full-pool scans (no LLM), re-slice per gem,
and rebuild all 10 dashboards -- so they show the ~60%-fewer agents immediately. APPROXIMATE: it drops a
retired event's picks but can't re-nominate a genuine revival (the clean version is a full re-run with
aging_patience>0). The baseline firehose_scans_full.json is preserved (re-sliceable without aging)."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from util import load_dotenv; load_dotenv()  # noqa: E402
import backtest_retrieval_curator as brc  # noqa: E402
import build_dashboard as bd  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
FLOOR, PAT = int(fm.get("aging_floor", 1)), int(fm.get("aging_patience", 3))
full = json.loads((ROOT / "data" / "windows" / "firehose_scans_full.json").read_text())

# aging retirement: retire a thesis after PAT consecutive weeks at conviction <= FLOOR; drop its picks after
anchors = sorted(full)
streak, retired_at = {}, {}
for wk in anchors:
    for p in full[wk]:
        th = p.get("thesis", "")
        if not th or th in retired_at:
            continue
        c = int(p.get("conviction", 5) or 5)
        if c <= FLOOR:
            streak[th] = streak.get(th, 0) + 1
            if streak[th] >= PAT:
                retired_at[th] = wk
        else:
            streak[th] = 0
aged = {wk: [p for p in full[wk]
             if not (retired_at.get(p.get("thesis", "")) is not None and wk > retired_at[p.get("thesis", "")])]
        for wk in anchors}
n_events = len({p.get("thesis", "") for ps in full.values() for p in ps if p.get("thesis")})
print(f"aging (floor={FLOOR}, patience={PAT}): retired {len(retired_at)} of {n_events} events", flush=True)

brc.slice_full(aged)                                     # per-THEME gem slices + OTHER catch-all (aged, era-launched)
for g in list(brc.GEM_ERA) + ["OTHER"]:
    try:
        if g == "BWET":
            bd.build_gem("BWET", scans_override="firehose_scans_bwet.json", out_override="bwet_curator")
        else:
            bd.build_gem(g)
        print(f"  built docs/{'bwet_curator' if g == 'BWET' else g.lower()}/", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  {g}: skip ({e})", flush=True)
