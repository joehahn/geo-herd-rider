"""check_retired_gate.py -- assert the retired-ticker gate admits new catalysts and rejects restatements.

WHY A SCRIPT AND NOT pytest. This repo has no test suite and no pytest dependency; adding one for a
single function would be a bigger change than the function. This runs standalone (`python
scripts/check_retired_gate.py`), exits non-zero on failure, and is cheap enough to run before any
curation that depends on the gate.

WHAT IT PROTECTS. `_restates_resolved` is the enforced half of the 2026-08-22 change from a
categorical retired-ticker ban to a raised evidentiary bar. It is a SUBTRACTIVE filter, and this
repo's history with those is bad -- `max_group_articles` and `max_article_orgs` were both added and
deleted the same day for silently deleting real news. So the cases below are not decoration: the
REJECT side keeps the gate useful, and the ADMIT side is the regression test that stops it quietly
becoming the ban it replaced. Every ADMIT case is a real catalyst measured as suppressed by the old
ticker-keyed guard (see TODO.md, 'retired-ticker guard').
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from agent import _restates_resolved as R  # noqa: E402

ADMIT = [  # a NEW dated catalyst on a retired ticker -- MUST get through
    ("Oracle to purchase up to 2.8 gigawatts of fuel-cell power",
     "AI datacenter power demand lifts fuel cell makers"),                      # BE, 2026-04-14
    ("Greenland approves transfer of the Tanbreez mining licence",
     "rare earth export curbs lift western miners"),                            # GNENF, 2026-04-17
    ("CoreWeave to acquire Core Scientific for $9 billion",
     "bitcoin miners pivot to AI hosting"),                                     # CORZ, 2025-07-07
    ("Pecos campus expansion to 1.5 GW for AI datacenters",
     "CoreWeave hosting deal"),                                                 # CORZ, 2026-05-01
    ("Cyient GaN licensing partnership for India",
     "GaN power semiconductor adoption grows"),                                 # NVTS, 2025-12-08
    ("FDA decision on the lead asset due in March",
     "phase 3 trial readout succeeded"),
    ("DOE awards a HALEU enrichment contract",
     "uranium spot price squeeze"),
]
REJECT = [  # the SAME catalyst restated -- the lingering hype the roster exists to stop
    ("continued datacenter demand expected to lift shares", "datacenter demand surge lifts shares"),
    ("further gains expected from the datacenter demand surge", "datacenter demand surge"),
    ("ongoing momentum in the uranium squeeze", "uranium squeeze"),
    ("more upside from the rare earth export curbs", "rare earth export curbs"),
    ("the export curbs continue to drive rare earth prices higher", "rare earth export curbs"),
]


def main() -> int:
    bad = 0
    for pn, rt in ADMIT:
        if R(pn, rt):
            print(f"  FAIL admit: gate REJECTED a new catalyst -> {pn!r} (retired: {rt!r})"); bad += 1
    for pn, rt in REJECT:
        if not R(pn, rt):
            print(f"  FAIL reject: gate ADMITTED a restatement -> {pn!r} (retired: {rt!r})"); bad += 1
    n = len(ADMIT) + len(REJECT)
    print(f"  retired-gate: {n - bad}/{n} pass" + ("" if bad else "  (admit-side and reject-side both hold)"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
