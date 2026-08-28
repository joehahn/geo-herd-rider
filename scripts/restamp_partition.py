"""restamp_partition.py -- re-derive stamp hashes after a CURATION/BOOK partition change.

WHY THIS IS NEEDED AND WHY IT IS SAFE. `curation_key` hashes {corpus, arm, knobs} where `knobs` is
the CURATION_KNOBS subset. On 2026-08-22 `exit_patience_scans` and `max_stale_scans` were moved to
BOOK_KNOBS (they are read only by firehose._watch_clocks, reached only from firehose.backtest --
proven by holding the v18 journal fixed and varying them: $95,170 to $345,968). Shrinking the set
re-derives the hash for every run, so every existing stamp reads stale and every page fails the gate.

THE ONE RULE: re-derive from the values the run ITSELF recorded, never from the current profile.
Recomputing against today's profile would silently relabel a run that was curated under different
settings as if it had been curated under these -- manufacturing exactly the provenance lie this
module exists to prevent.

Nothing is lost: the demoted knobs' recorded values move to `book_knobs_at_curation` so what the
run actually ran under stays on disk, it simply stops counting toward curation identity.

ADDING A KNOB NEEDS THE SAME TREATMENT, AND A NARROWER LICENCE. `curation_key` iterates ALL of
CURATION_KNOBS, so a NEW knob changes every hash even when nobody set it. The generic guard below
refuses to rehash a stamp with unrecorded knobs, which is right -- an unrecorded knob is usually one
whose value we genuinely do not know. But a knob INTRODUCED after a run is different: the code path
did not exist, so its value is knowably unset. `--introduced KNOB` says exactly that, for exactly
the knobs named, and records the claim in `restamped`. It is not a general escape from the guard:
name the wrong knob and you are asserting something false, in a file that says you asserted it.
"""
import json, sys, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import provenance as pv  # noqa: E402


def main() -> int:
    apply = "--apply" in sys.argv
    intro = [sys.argv[i + 1] for i, x in enumerate(sys.argv) if x == "--introduced"]
    bad = [k for k in intro if k not in pv.CURATION_KNOBS]
    if bad:
        print(f"  --introduced names knob(s) not in CURATION_KNOBS: {bad}"); return 2
    note = sys.argv[sys.argv.index("--note") + 1] if "--note" in sys.argv else None
    n = 0
    for f in sorted(ROOT.glob("data/*/provenance.json")):
        rec = json.loads(f.read_text())
        knobs = rec.get("knobs")
        if not isinstance(knobs, dict):
            print(f"  SKIP {f.parent.name}: no knobs recorded"); continue
        demoted = {k: knobs[k] for k in sorted(pv.BOOK_KNOBS) if k in knobs}
        kept = {k: knobs[k] for k in sorted(pv.CURATION_KNOBS) if k in knobs}
        for k in intro:                      # knowably unset: it did not exist when this ran
            kept.setdefault(k, None)
        missing = [k for k in sorted(pv.CURATION_KNOBS) if k not in kept]
        if missing:
            # a partial legacy stamp -- rehashing it would invent agreement on knobs it never recorded
            print(f"  SKIP {f.parent.name}: partial stamp, {len(missing)} curation knob(s) unrecorded")
            continue
        key = {"corpus": rec.get("corpus"), "arm": rec.get("arm"), "knobs": kept}
        newhash = hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]
        old = rec.get("hash")
        if newhash == old:
            print(f"  ok   {f.parent.name:26} {old}  (unchanged)"); continue
        print(f"  {'WRITE' if apply else 'would':5} {f.parent.name:26} {old} -> {newhash}"
              f"   demoted kept as book_knobs_at_curation: {sorted(demoted)}")
        if apply:
            rec["knobs"] = kept
            if demoted:
                rec["book_knobs_at_curation"] = demoted
            rec["hash"] = newhash
            rec.setdefault("restamped", []).append(
                note or "2026-08-22 partition: exit_patience_scans, max_stale_scans -> BOOK")
            f.write_text(json.dumps(rec, indent=1, sort_keys=True, default=str))
        n += 1
    print(f"\n  {n} stamp(s) {'rewritten' if apply else 'would change'}" + ("" if apply else "  -- re-run with --apply"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
