#!/usr/bin/env python3
"""measure_silent_holding.py — when a held event cites NO sources, did any news EXIST?

THE FINDING THIS ANSWERS. Live events that exit on the merits go quiet for a median of ONE scan;
live events retired by a timer go quiet for EIGHT -- exactly `max_stale_scans` -- and spend 84% of
their lives on journal entries that cite nothing (measured on cbt v27 and v28 independently, see
TODO.md). ev339 is the specimen: "No news on the $1B TMI loan; catalyst remains pending", verbatim,
scan after scan.

But `sources: []` is consistent with two very different worlds, and the journal cannot separate
them:
  NO ARTICLE EXISTED   -> the press stopped covering it, the thesis IS decayed, and the defect is
                          that we hold 8 more scans instead of exiting (non-negotiable #2).
  ARTICLES EXISTED     -> the agent was handed news and cited none of it. A reading failure, and
                          the caps are innocent.

The distinction decides where the fix goes, so it is measured, not assumed -- and NOTHING is swept
until it is answered, because sweeping the caps was already tried and reverted.

FREE AND OFFLINE. Every scan's whole pool is archived per anchor, and `curation_report.agent_inputs`
reconstructs the exact slice one event-agent read (deterministic: `agent._filter_event` is a
structural score with no LLM and no fitted weights). So we ask what the AGENT ITSELF was handed at
each silent scan, rather than re-matching keywords by hand.

    python scripts/measure_silent_holding.py [run ...]
"""
from __future__ import annotations

import collections
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import curation_report as cr    # noqa: E402
import re                       # noqa: E402


def _on_topic(read, e, entry, before):
    """How much of the slice is ABOUT this event, by `_filter_event`'s own top bonus: the vehicle
    named as a WORD in the TITLE (+3), or a catalyst keyword in the title (+2). An article that
    matched only through the snippet scores 0 and is incidental -- it mentions the ticker in a
    market-wrap list. Availability turned out not to separate the buckets (every silent scan was
    handed a full 20), so the question moves to whether the 20 were about anything."""
    veh = sorted({str(v).upper() for x in (before + [entry]) for v in (x.get("vehicles") or [])}
                 or {str(v).upper() for v in (e.get("vehicles") or [])})
    vrx = re.compile(r"\b(" + "|".join(re.escape(v.lower()) for v in veh) + r")\b") if veh else None
    kws = [w for w in str(e.get("catalyst") or "").lower().replace(",", " ").split() if len(w) > 4]
    hi = kw = 0
    for a in read or []:
        title = (a.get("title") or "").lower()
        if vrx and vrx.search(title):
            hi += 1
        elif any(k in title for k in kws):
            kw += 1
    return hi, kw

RUNS = ("data/cbt_3yr_v27_catalyst", "data/cbt_3yr_v28_exposure")


def _pools(run: Path) -> dict:
    """{anchor date: the week's whole archived pool}."""
    out = {}
    for f in sorted((run / "archive").glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text()).get("pool") or []
        except Exception as exc:                      # noqa: BLE001
            print(f"    (skipped {f.name}: {exc})", file=sys.stderr)
    return out


def _how_it_ended(e: dict, last: str) -> str:
    t = e["entries"][-1]
    if not t.get("thesis_live"):
        return "decayed"
    return "open" if t["date"] == last else "aged_out"


def main() -> int:
    runs = [Path(x) for x in (sys.argv[1:] or RUNS)]
    for run in runs:
        run = run if run.is_absolute() else ROOT / run
        jf = run / "journal.json"
        if not jf.exists():
            print(f"  {run.name}: no journal.json -- skipped")
            continue
        J = json.loads(jf.read_text())
        ev, pools = J["events"], _pools(run)
        stamp = json.loads((run / "provenance.json").read_text()) if (run / "provenance.json").exists() else {}
        # BOTH read exactly as the dashboard reads them (build_cbt_dashboard.py:2690,
        # curation_report.py:694). `filter_version` is nested under `code` in the stamp, and a
        # first cut that read it flat got version=1 -- the loose pre-2026-09-05 substring match --
        # which matched ~280 articles per event and reported 100% news at every silent scan.
        ver = int((stamp.get("code") or {}).get("filter_version", 1) or 1)
        # `event_news_cap` lives under `knobs`, not at the top level -- read flat it came back 0
        # (uncapped), which reports the whole matched set rather than the 20 the agent was handed.
        _kn = stamp.get("knobs") or {}
        cap = int(_kn.get("event_news_cap") or 0)
        last = max(x["date"] for e in ev.values() for x in (e.get("entries") or []))

        # every SILENT scan of every live event, bucketed by how that event ended
        tally = collections.defaultdict(lambda: {"scans": 0, "with_news": 0, "sizes": []})
        for e in ev.values():
            ents = e.get("entries") or []
            if len(ents) < 2 or not any(x.get("thesis_live") for x in ents):
                continue
            how = _how_it_ended(e, last)
            for i, x in enumerate(ents):
                if (x.get("sources") or []) or not x.get("thesis_live"):
                    continue                          # not a silent scan of a held event
                pool = pools.get(x["date"])
                if pool is None:
                    continue                          # no archive for this anchor
                try:
                    got = cr.agent_inputs(pool, e, x, ents[:i], cap, version=ver)
                except Exception as exc:              # noqa: BLE001
                    print(f"    (agent_inputs failed on {e['id']} @ {x['date']}: {exc})", file=sys.stderr)
                    continue
                # (read, matched, ok) -- `read` is the CAPPED slice the agent actually saw, which
                # is the question here; `matched` is everything that survived the filter.
                read, n_matched, _ok = got     # `matched` is already a COUNT, not a list
                # AND THE UNCAPPED SET. The slice is 20 of ~250 matched, so "the agent saw no
                # on-topic article" and "on-topic coverage existed but ranked below 20" are
                # different failures with different fixes. `cap=0` returns the full ranked set.
                full = cr.agent_inputs(pool, e, x, ents[:i], 0, version=ver)[0]
                n = len(read or [])
                t = tally[how]
                t["scans"] += 1
                t["with_news"] += bool(n)
                t["sizes"].append(n)
                t.setdefault("matched", []).append(int(n_matched or 0))
                _hi, _kw = _on_topic(read, e, x, ents[:i])
                t.setdefault("veh_title", []).append(_hi)
                t.setdefault("kw_title", []).append(_kw)
                t.setdefault("none", []).append(1 if not (_hi or _kw) else 0)
                _fhi, _ = _on_topic(full, e, x, ents[:i])
                t.setdefault("veh_full", []).append(_fhi)
                t.setdefault("buried", []).append(max(_fhi - _hi, 0))

        print(f"\n  {run.name}  (filter_version={ver}, event_news_cap={cap or 'uncapped'}, "
              f"max_silent_scans={_kn.get('max_silent_scans')}, "
              f"max_event_scans={_kn.get('max_event_scans')}, {len(pools)} archived pools)")
        print("    SILENT scans of a HELD event -- did the agent's own slice hold anything?")
        for how in ("decayed", "aged_out", "open"):
            t = tally.get(how)
            if not t or not t["scans"]:
                continue
            sizes = [s for s in t["sizes"] if s]
            print(f"      {how:<9} silent scans {t['scans']:5d} · "
                  f"slice held articles at {t['with_news']:5d} ({100*t['with_news']/t['scans']:3.0f}%) · "
                  f"median slice {st.median(sizes) if sizes else 0:.0f} "
                  f"(of {st.median([m for m in t['matched'] if m]) if any(t['matched']) else 0:.0f} matched)")
            print(f"                  of that slice: vehicle in TITLE "
                  f"mean {st.mean(t['veh_title']):.1f} · keyword in title mean {st.mean(t['kw_title']):.1f} · "
                  f"slices with NEITHER {100*st.mean(t['none']):.0f}%")
            # MEDIANS, not means: burial is concentrated in a few very large events (an Iran-war
            # event with 30+ vehicle-titled articles), and the mean reads as if the cap were
            # systematically hiding coverage when at most scans it hides none.
            _b = sorted(t["buried"]); _f = sorted(t["veh_full"])
            _p90 = _b[int(0.9 * (len(_b) - 1))] if _b else 0
            print(f"                  uncapped: vehicle-in-title median {st.median(_f):.0f} "
                  f"· cap buried >=1 at {100*st.mean([b>0 for b in _b]):.0f}% of scans "
                  f"(median buried {st.median(_b):.0f}, p90 {_p90:.0f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
