"""gt_healthcheck.py — prune dead / link-rotted URLs from the ground truth (data/gem_ground_truth.json).

News aggregators (esp. marketbeat.com's automated 13F/ownership-churn boilerplate) delete old articles,
so their URLs rot — they 404 or silently redirect to a generic hub (/instant-alerts/, the bare ticker page).
Those aren't useful GT targets (and render as dead links in the dashboard). This drops them, but KEEPS
genuinely WALLED targets (etf.com Cloudflare 403, WSJ/MarketWatch paywall 401/403) — those are real
articles behind a wall, which is exactly the point of the "missed/forward-reachable" markers.

Run after any GT (re)build. Rewrites gem_ground_truth.json in place; then re-derive the dashboard JSON.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

REPO = Path(__file__).resolve().parent.parent
GT = REPO / "data" / "gem_ground_truth.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36"}


def classify(url: str) -> str:
    """'dead' (404/410/connection error), 'rot' (200 but redirected to a generic hub), or 'keep'."""
    try:
        r = requests.get(url, headers=UA, timeout=12, allow_redirects=True)
    except Exception:  # noqa: BLE001
        return "dead"
    if r.status_code in (404, 410):
        return "dead"
    p = urlparse(r.url).path.rstrip("/")
    hub = ("instant-alerts" in r.url) or bool(re.match(r"^/stocks/[A-Za-z]+/[A-Za-z.]+$", p)) \
        or p in ("", "/news", "/markets", "/quote")
    if r.url.rstrip("/") != url.rstrip("/") and hub:   # redirected away from the article to a landing page
        return "rot"
    return "keep"                                       # incl. walled 401/403 that still resolve to the article


def main():
    gt = json.loads(GT.read_text())
    removed = {}
    for g in list(gt):
        keep = []
        for a in gt[g]:
            c = classify(a["url"])
            (removed.setdefault(g, []).append((c, a["title"][:40])) if c in ("dead", "rot") else keep.append(a))
        gt[g] = keep
    GT.write_text(json.dumps(gt, indent=1))
    print("removed dead/rotted GT URLs (walled targets kept):")
    for g, items in removed.items():
        rot = sum(1 for c, _ in items if c == "rot")
        print(f"  {g}: -{len(items)} ({rot} link-rot, {len(items)-rot} 404/err)")
    print("GT counts now:", {g: len(v) for g, v in gt.items()})
    print("\nnow re-derive: python scripts/retrieval_backtest.py (or re-summarize from the ckpt)")


if __name__ == "__main__":
    main()
