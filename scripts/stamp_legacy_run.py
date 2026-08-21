#!/usr/bin/env python3
"""stamp_legacy_run.py — write provenance.json for a curation made before provenance.py existed.

Reconstructs from data/<run>/archive/*.json, which records the per-week `config` the curator actually
ran under -- the ONLY on-disk evidence of a legacy run's settings. That config carries 9 of the 24
curation knobs, so the stamp is PARTIAL: unrecorded knobs are OMITTED rather than filled from
today's profile, which would fabricate agreement and defeat the check. `verify` then reports them as
unverifiable. One-shot; new runs are stamped at creation by backtest_gdelt.py.

    python scripts/stamp_legacy_run.py data/cbt_3yr_v9 --corpus data/backtest_3yr_v5 --arm fuller
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import provenance as P  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--corpus", default=P.CANON_CORPUS)
    ap.add_argument("--arm", default="fuller")
    a = ap.parse_args(argv)
    run = ROOT / a.run
    arch = sorted((run / "archive").glob("*.json"))
    if not arch:
        print(f"no archive/ in {run} -- nothing to reconstruct from", file=sys.stderr); return 1

    # Read EVERY week, not just the first: a config that changed mid-run means the arms were mixed,
    # and a stamp claiming one setting would be a lie about half the weeks.
    seen: dict = {}
    for f in arch:
        cfg = (json.loads(f.read_text()) or {}).get("config") or {}
        for k, v in cfg.items():
            seen.setdefault(k, set()).add(json.dumps(v, sort_keys=True, default=str))
    unstable = {k for k, v in seen.items() if len(v) > 1 and k in P.CURATION_KNOBS}
    if unstable:
        print(f"  !! curation knobs CHANGED mid-run: {sorted(unstable)}", file=sys.stderr)
        print(f"     This run is not one experiment; refusing to stamp it as one.", file=sys.stderr)
        return 1

    knobs = {k: json.loads(list(v)[0]) for k, v in seen.items() if k in P.CURATION_KNOBS}
    rec = {"corpus": P.corpus_id(a.corpus), "arm": a.arm,
           "knobs": {k: P._norm(knobs[k]) for k in sorted(knobs)},
           "partial": True, "reconstructed_from": f"archive/ ({len(arch)} weeks)",
           "note": ("Backfilled by stamp_legacy_run.py. Only the knobs the archive recorded are "
                    "listed; the rest are unverifiable, not assumed to match.")}
    import hashlib
    rec["hash"] = hashlib.sha256(json.dumps(
        {k: rec[k] for k in ("corpus", "arm", "knobs")}, sort_keys=True, default=str).encode()).hexdigest()[:12]
    (run / "provenance.json").write_text(json.dumps(rec, indent=1, sort_keys=True, default=str))
    print(f"  stamped {run}/provenance.json")
    print(f"    corpus     {rec['corpus']['path']} ({rec['corpus']['articles']:,} articles)")
    print(f"    recorded   {len(knobs)}/{len(P.CURATION_KNOBS)} curation knobs")
    print(f"    missing    {sorted(set(P.CURATION_KNOBS) - set(knobs))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
