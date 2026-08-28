#!/usr/bin/env python3
"""compare_curations.py — two curations of the same corpus, on MECHANISM not P&L.

Non-negotiable #6: a single curation's P&L cannot adjudicate a change. The same settings run twice
gave median finals of $117,200 and $62,997, and one cell swung $588,538 -> $75,132. So this prints
what REPRODUCES -- bundle composition, coverage, cull-at-birth, what the scout was shown -- and
deliberately prints no returns at all. If a number here moves, the change did something; if only
the P&L moves, it did not.
"""
from __future__ import annotations
import argparse, collections, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def load(run: str) -> dict:
    d = ROOT / run
    j = json.loads((d / "journal.json").read_text())
    ev = j.get("events") or j
    m = json.loads((d / "curator_metrics.json").read_text())
    return {"run": run, "ev": ev, "metrics": m,
            "prov": json.loads((d / "provenance.json").read_text())}


def rows(c: dict) -> dict:
    ev, m = c["ev"], c["metrics"]
    op = cl = oc = 0
    tick = collections.Counter()
    for e in ev.values():
        ents = e.get("entries") or []
        if not ents:
            continue
        op += 1
        live = str(e.get("status", "")).lower() == "live"
        if not live:
            cl += 1
        if len(ents) == 1 and not live:
            oc += 1
        for v in (e.get("vehicles") or e.get("tickers") or []):
            t = v if isinstance(v, str) else (v.get("ticker") or "")
            if t:
                tick[t] += 1
    last = m[-1] if m else {}
    return {"events opened (total)": op,
            "opened & closed at birth": oc,
            "cull-at-birth rate": f"{100*oc/max(op,1):.1f}%",
            "distinct tickers named": len(tick),
            "events live at last scan": last.get("events_live"),
            "picks live at last scan": last.get("picks_live"),
            "vehicles live at last scan": last.get("vehicles_live"),
            "articles read (last scan)": last.get("articles_read"),
            "articles gated (last scan)": last.get("articles_gated"),
            "gate rate (last scan)": f"{100*(last.get('articles_gated') or 0)/max(last.get('articles_read') or 1,1):.1f}%"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    x = ap.parse_args()
    A, B = load(x.a), load(x.b)
    ra, rb = rows(A), rows(B)
    print(f"\n{'':32} {x.a:>18} {x.b:>18}")
    print(f"{'':32} {'-'*18} {'-'*18}")
    for k in ra:
        va, vb = ra[k], rb[k]
        mark = "" if str(va) == str(vb) else "  <-"
        print(f"  {k:30} {str(va):>18} {str(vb):>18}{mark}")
    ka = (A["prov"].get("knobs") or {}); kb = (B["prov"].get("knobs") or {})
    diff = {k: (ka.get(k), kb.get(k)) for k in set(ka) | set(kb) if ka.get(k) != kb.get(k)}
    print(f"\n  curation knobs that differ: {diff or 'NONE — same config'}")
    print(f"  hashes: {A['prov'].get('hash')}  vs  {B['prov'].get('hash')}")
    print(f"  models_resolved: {B['prov'].get('models_resolved')}")
    print("\n  NO P&L SHOWN, deliberately -- see the module docstring and non-negotiable #6.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
