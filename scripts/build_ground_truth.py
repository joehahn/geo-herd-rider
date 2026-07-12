"""build_ground_truth.py — the RECALL TARGET for the retrieval backtest.

For each historic gem, search Tavily BY NAME + superlative ("skyrocketing", "soaring", "up 600%", …)
over the gem's era to collect the news articles we WANT the generic-beat retriever to catch. This is
gem-specific by design — it DEFINES the target set, it is NOT the retriever under test (that runs the
generic aligned beats). Writes data/gem_ground_truth.json: {gem: [{url, date, title}]}.

Then a live retriever's recall = (ground-truth articles present in its pool) / (all ground-truth). The
dashboard overlays detected GT (big blue dot) vs missed GT (orange square) on each gem's price chart.
"""
from __future__ import annotations
import json
import re
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
from util import load_dotenv  # noqa: E402

load_dotenv()
import search  # noqa: E402
import gem_detect  # noqa: E402 — reuse the robust by-name matcher so GT only holds articles that REALLY name the gem

OUT = REPO / "data" / "gem_ground_truth.json"
# gem -> display names to search by, and its era
GEMS_GT = {
    "MP":    {"names": ["MP Materials stock", "$MP rare earth"], "start": "2025-01-01", "end": "2025-12-31"},
    "AREC":  {"names": ["American Resources AREC stock", "ReElement rare earth AREC"], "start": "2025-01-01", "end": "2026-05-01"},
    "DRAM":  {"names": ["Roundhill Memory ETF DRAM", "DRAM memory ETF"], "start": "2026-01-01", "end": "2026-07-11"},
    "BWET":  {"names": ["Breakwave Tanker Shipping ETF", "BWET tanker ETF"], "start": "2026-01-01", "end": "2026-07-11"},
    "GDX":   {"names": ["VanEck Gold Miners ETF GDX", "GDX gold miners"], "start": "2025-01-01", "end": "2026-07-11"},
    "RNMBY": {"names": ["Rheinmetall stock", "RNMBY defense stock"], "start": "2025-01-01", "end": "2026-02-28"},
}
SUPER = ["skyrocket", "soar", "surg", "best performing", "best-performing", "little-known", "little known",
         "under the radar", "outperform", "rocketing", "explod", "on fire", "breakout", "record high",
         "all-time high", "up 1", "up 2", "up 3", "up 4", "up 5", "up 6", "up 7", "up 8", "up 9", "jump", "spike",
         # unprecedented-growth family (e.g. RNMBY "growth we have never experienced before") + record-X
         "unprecedented", "never seen", "never experienced", "record order", "record backlog", "highest ever"]
SUPER_Q = ["skyrocketing soaring surging record high", "best performing stock outperforming",
           "little-known under the radar breakout"]


def _norm(u: str) -> str:
    p = urlparse(u)
    return (p.netloc.replace("www.", "") + p.path).rstrip("/").lower()


def _iso(raw: str) -> str | None:
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:  # noqa: BLE001
        return raw[:10] if raw[:4].isdigit() else None


def build() -> dict:
    out = {}
    for g, cfg in GEMS_GT.items():
        seen = {}
        for name in cfg["names"]:
            for sq in SUPER_Q:
                for r in search.search(f"{name} {sq}", before_date=cfg["end"], start_date=cfg["start"], max_results=15):
                    title = r.get("title") or ""
                    blob = title + "  " + (r.get("content") or "")
                    # REALLY names the gem (distinctive company name in the title, or an explicit $TK/(TK)
                    # tag anywhere) — the same rule gem_detect uses; rejects generic-word chrome matches
                    kw = gem_detect.GEMS[g]
                    named = gem_detect._named_in(title, kw) or any(gem_detect._ticker_form(t, blob) for t in kw["ticker"])
                    if not (named and any(s in blob.lower() for s in SUPER)):
                        continue
                    d = _iso(r.get("published_date") or "")
                    if not d or not (cfg["start"] <= d <= cfg["end"]):
                        continue
                    nu = _norm(r["url"])
                    if nu not in seen:
                        seen[nu] = {"url": r["url"], "date": d, "title": (r.get("title") or "")[:160]}
        out[g] = sorted(seen.values(), key=lambda x: x["date"])
        print(f"  {g:6}: {len(out[g])} ground-truth superlative articles ({cfg['start']}..{cfg['end']})")
    OUT.write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    gt = build()
    # immediate recall readout vs the current retrieved pool (ckpt has the full pool)
    ck = REPO / "data" / "retrieval_backtest.ckpt.json"
    if ck.exists():
        pool = {_norm(a["url"]) for a in json.loads(ck.read_text())["arts"]}
        print("\n=== recall vs current pool ===")
        for g, arts in gt.items():
            hit = sum(1 for a in arts if _norm(a["url"]) in pool)
            print(f"  {g:6}: {hit}/{len(arts)} detected" + (f" ({100*hit//len(arts)}%)" if arts else ""))
    print(f"\nwrote {OUT}")
