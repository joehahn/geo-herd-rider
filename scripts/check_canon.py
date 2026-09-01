#!/usr/bin/env python3
"""check_canon.py — are the three published dashboards describing the canonical book?

Answers in one command what previously took reading three pages and a git log. Reports the canonical
inputs, then each published page's agreement with them, then any OTHER curation on disk that also
matches the profile (an ambiguous canon is its own trap: two runs matching means the pointer in
src/provenance.py is a coin flip).

    python scripts/check_canon.py            # status
    python scripts/check_canon.py --runs     # also classify every curation in data/

Exit code is 1 if anything is off, so it works as a pre-publish or pre-commit check.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import provenance as P  # noqa: E402
import optimizer as opt  # noqa: E402

OK, BAD, WARN = "✓", "✗", "!"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", action="store_true", help="classify every curation dir in data/")
    a = ap.parse_args(argv)
    fm = opt.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    bad = 0

    ci = P.corpus_id(P.CANON_CORPUS)
    key = P.curation_key(fm, P.CANON_CORPUS)
    print("CANONICAL INPUTS")
    print(f"  corpus       {ci['path']}  ({ci['articles']:,} articles)" if ci["articles"]
          else f"  corpus       {ci['path']}  (unreadable)")
    print(f"  curation     {P.CANON_RUN}")
    print(f"  sweep        {P.CANON_SWEEP}")
    print(f"  fingerprint  {key['hash']}   (profile's {len(P.CURATION_KNOBS)} curation knobs + corpus + arm)")

    # DOES THE CANONICAL SWEEP ACTUALLY DESCRIBE THE CANONICAL CURATION?
    # Nothing checked this until 2026-08-31 -- CANON_SWEEP was printed and never verified. The names
    # invite the mistake rather than guard against it: every sweep on disk is swept over a DIFFERENT
    # run than its number suggests (sweep_v24 -> v22, sweep_v25 -> v23, sweep_v26 -> v24), so
    # `sweep_v25` and `cbt_3yr_v25_vehgate` look like a pair and are TWO curations apart. That is the
    # silent-drift class this whole script exists for, and it was one `cp` away from publishing an
    # SBT that ranked one book while CBT drew another. Every sweep already records the run it swept,
    # so the check is one comparison; names cannot mislead once the build fails on a mismatch.
    try:
        _sw = json.loads((ROOT / P.CANON_SWEEP).read_text())
        _swept = str(_sw.get("run") or "")
        if Path(_swept).name != Path(P.CANON_RUN).name:
            bad += 1
            print(f"\n  {BAD} CANON_SWEEP WAS SWEPT OVER A DIFFERENT CURATION")
            print(f"      {P.CANON_SWEEP} ran over {_swept}")
            print(f"      but CANON_RUN is {P.CANON_RUN}")
            print(f"      SBT would rank one book while CBT draws another. Re-sweep, or fix the pointer.")
        else:
            print(f"  sweep ran over {_swept}  {OK}")
    except Exception as e:  # noqa: BLE001 -- a missing/garbled sweep is itself worth reporting
        bad += 1
        print(f"\n  {BAD} CANON_SWEEP unreadable ({type(e).__name__}: {e})")

    unclassified = P.check_partition_covers_profile()
    if unclassified:
        bad += 1
        print(f"\n  {BAD} UNCLASSIFIED PROFILE KNOBS: {unclassified}")
        print(f"      Add each to CURATION_KNOBS or BOOK_KNOBS in src/provenance.py.")

    # WHAT BUILT THE CANONICAL CORPUS. Reported, not enforced: the corpus predates ingest_stamp, so
    # an absent stamp is expected and harmless -- a replay reads articles, not the config that found
    # them. A stamp that DISAGREES with today's retrieval_config is the interesting case.
    try:
        import article_contract as _ac
        _pool = json.loads((P.REPO_ROOT / P.CANON_CORPUS / "pool.json").read_text())
        _sc = _ac.check_stamp(_pool if isinstance(_pool, dict) else {})
        _st = (_pool or {}).get("ingest") if isinstance(_pool, dict) else None
        print("\nCORPUS INGEST STAMP")
        if _st:
            print(f"  {OK} {_st.get('source','?')} · {_st.get('n_beats','?')} beats · "
                  f"vocab {_st.get('beat_vocab','?')} · {_st.get('stamped_at','?')[:10]}")
        for _c in _sc:
            print(f"  {WARN} {_c}")
    except Exception as _e:  # noqa: BLE001
        print(f"\n  {WARN} could not read the corpus ingest stamp: {_e}")

    # THE BOOTSTRAP'S GKG HALF must come from the canonical corpus. bootstrap_corpus.GKG_RUN now
    # DERIVES from CANON_CORPUS, so this cannot drift without someone re-hard-coding it -- which is
    # exactly what happened before (it sat on v3 after the promotion to v5, and FBS/CBS therefore
    # read a pool with NO `orgs` at all while FBT/CBT read one with 93%).
    try:
        import bootstrap_corpus as _bc
        if _bc.GKG_RUN != P.CANON_CORPUS:
            bad += 1
            print(f"\n  {BAD} bootstrap GKG half reads {_bc.GKG_RUN}, canonical is {P.CANON_CORPUS}")
            print(f"      src/bootstrap_corpus.py must DERIVE this, never name a corpus.")
    except Exception as _e:  # noqa: BLE001
        print(f"\n  {WARN} could not check the bootstrap corpus source: {_e}")

    # BEAT VOCABULARY. Not covered by the fingerprint (retrieval_config.json is an INGEST-time
    # input, while a replay reads the corpus), but a beat name is also a JOIN KEY -- so a rename
    # breaks the curator's gem scoring against an unchanged corpus, silently. See
    # provenance.check_beat_vocabulary.
    vocab = P.check_beat_vocabulary()
    print("\nBEAT VOCABULARY")
    if vocab:
        bad += 1
        print(f"  {BAD} beat references do not resolve:")
        for _c in vocab:
            print(f"      - {_c}")
    else:
        print(f"  {OK} every beat_parent entry and every corpus beat tag resolves to a live beat")

    print("\nCANONICAL CURATION")
    v = P.verify(P.CANON_RUN, fm)
    if v["ok"]:
        print(f"  {OK} {P.CANON_RUN} matches the profile's curation knobs")
    else:
        bad += 1
        print(f"  {BAD} {P.CANON_RUN}: {v['reason']}")
        for k, got, want in v["diffs"]:
            print(f"      {k}: ran under {got!r}, profile says {want!r}")
    if v.get("unverifiable"):
        print(f"  {WARN} {len(v['unverifiable'])} knobs were never recorded by that run "
              f"and cannot be checked")

    # CODE DRIFT. curator_code_id() has stamped the scan-path digest on every run for weeks and
    # nothing compared it, so this file printed ALL CONSISTENT while both live curations had drifted
    # underneath it. Knobs are only half of "could this curation have been produced today".
    print("\nCURATION CODE DRIFT")
    for _run in (P.CANON_RUN, P.CANON_BOOTSTRAP_RUN):
        if not (ROOT / _run).exists():
            continue
        _d = P.code_drift(_run)
        if not _d:
            print(f"  {WARN} {_run:26} no code digest stamped — cannot tell what produced it")
            continue
        _st = _d["files"]
        if all(v[2] == "match" for v in _st.values()):
            print(f"  {OK} {_run:26} scan-path code unchanged since curation")
        else:
            _crit = _d["critical"]
            _mark = BAD if _crit else WARN
            print(f"  {_mark} {_run:26} git {_d['git'][0]} -> {_d['git'][1]}")
            for _f, (_was, _now, _state) in _st.items():
                if _state == "match":
                    continue
                _tag = "accepted" if _state == "accepted" else "UNREVIEWED"
                _why = _d["accepted"].get(_f, "")
                print(f"      {_f:<20} {_was} -> {_now}  {_tag}")
                if _why:
                    print(f"          {_why[:96]}")
            if _crit:
                bad += 1
                print(f"      {BAD} {', '.join(_crit)} is scan-path: this curation CANNOT be "
                      f"reproduced by today's code. Re-curate, or record why it is still valid in "
                      f"provenance.ACCEPTED_CODE_DRIFT.")

    print("\nPUBLISHED PAGES")
    for page, pat in (("docs/cbt.html", r"curation fingerprint</td><td>([0-9a-f]{12})"),
                      ("docs/fbt.html", r"<td>Corpus</td><td>([^<]*)"),
                      ("docs/sbt.html", r"FIXED curation \(([^)]*)\)")):
        f = ROOT / page
        if not f.exists():
            print(f"  {WARN} {page:16} not built"); continue
        m = re.search(pat, f.read_text())
        got = m.group(1) if m else "(not stated on the page)"
        want = {"docs/cbt.html": key["hash"], "docs/fbt.html": P.CANON_CORPUS,
                "docs/sbt.html": P.CANON_RUN}[page]
        good = got == want
        bad += (not good)
        print(f"  {OK if good else BAD} {page:16} {got}" + ("" if good else f"   expected {want}"))
        if not good:
            print(f"      rebuild it: python scripts/{Path(page).stem[:1]}"
                  f"{'bt' if 'bt' in page else ''} ... (see scripts/README.md)")

    # An ambiguous canon: more than one run on disk matching the profile.
    matches = [d.name for d in sorted((ROOT / "data").iterdir())
               if d.is_dir() and (d / "provenance.json").exists()
               and P.verify(d, fm)["ok"]]
    if len(matches) > 1:
        print(f"\n  {WARN} {len(matches)} curations match the profile: {matches}")
        print(f"      CANON_RUN picks {Path(P.CANON_RUN).name}; the others are indistinguishable by config.")

    if a.runs:
        print("\nALL CURATIONS ON DISK")
        for d in sorted((ROOT / "data").iterdir()):
            if not d.is_dir() or not (d / "journal.json").exists():
                continue
            pf = d / "provenance.json"
            if not pf.exists():
                print(f"  {WARN} {d.name:26} unstamped"); continue
            r = P.verify(d, fm)
            tag = f"{OK} canonical" if r["ok"] else f"{BAD} {len(r['diffs'])} knob(s) differ"
            print(f"  {tag:22} {d.name:26} {json.loads(pf.read_text()).get('hash','?')}")

    print(f"\n{'ALL CONSISTENT' if not bad else f'{bad} PROBLEM(S)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
