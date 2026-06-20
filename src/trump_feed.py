"""trump_feed.py — the trigger feed: Trump's Truth Social posts, gathered not hand-picked.

The honesty fix for the trigger layer. The first backtests fed the curator HAND-WRITTEN
event rows (data/windows/iran.csv) — a human who already knew how the war played out chose
which events mattered, baking selection + hindsight bias in BEFORE the curator ran. This
module replaces that with a complete, timestamped, point-in-time-sliceable pull of every
Trump post, so the LLM judges the WHOLE windowed stream and decides what's worthy — never a
human. (Musk, Dimon, et al. come later; Trump suffices for now.)

Division of labor (CLAUDE.md): this file is DETERMINISTIC PLUMBING only — fetch, normalize,
slice. It makes no judgment about which posts matter; that's the curator's job (the Anthropic
key). Selection and laddering happen downstream in map_event.py.

Source: an auto-updating public archive of @realDonaldTrump's Truth Social posts (the
`stiles/trump-truth-social-archive` project, mirrored by CNN). Every record carries an exact
`created_at` UTC timestamp, which is what makes a HARD look-ahead guarantee possible: slice
to `created_at < catalyst` locally and no later post can leak in. No API key or account is
needed — only the Anthropic key, used downstream for the judgment.

    python src/trump_feed.py --start 2025-11-01 --end 2026-06-18      # window -> CSV
    python src/trump_feed.py --start 2026-01-01 --end 2026-03-01 --min-engagement 5000
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "triggers_cache"   # gitignored: raw pulls
SOURCE = "Trump (Truth Social)"
TIMEOUT = 60

# Complete archive (newest-first JSON), with a fallback mirror. Both are the same dataset.
ARCHIVE_URLS = [
    "https://ix.cnn.io/data/truth-social/truth_archive.json",
    "https://raw.githubusercontent.com/stiles/trump-truth-social-archive/main/data/truth_archive.json",
]

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (geo-herd-rider research backtest)"})

_TAG = re.compile(r"<[^>]+>")


def _strip_html(raw: str) -> str:
    """Truth Social `content` is HTML. Flatten to plain text (the curator reads text)."""
    text = _TAG.sub(" ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_archive(use_cache: bool = True) -> list[dict]:
    """The full post archive (every @realDonaldTrump post). Cached raw to disk so a backtest
    re-run is reproducible and offline-friendly; pass use_cache=False to force a refresh."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "truth_archive.json"
    if use_cache and cache.exists():
        return json.loads(cache.read_text())
    last_err = None
    for url in ARCHIVE_URLS:
        try:
            r = _SESSION.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            cache.write_text(json.dumps(data))
            return data
        except Exception as e:  # noqa: BLE001 — try the next mirror
            last_err = e
    raise RuntimeError(f"could not fetch the Trump archive from any mirror: {last_err}")


def to_frame(records: list[dict]) -> pd.DataFrame:
    """Normalize raw archive records to one tidy, time-sorted row per post."""
    rows = []
    for r in records:
        ts = r.get("created_at")
        if not ts:
            continue
        rows.append({
            "post_id": str(r.get("id", "")),
            "created_at": pd.to_datetime(ts, utc=True),
            "source": SOURCE,
            "text": _strip_html(r.get("content", "")),
            "media_count": len(r.get("media") or []),
            "url": r.get("url", ""),
            "replies": r.get("replies_count", 0),
            "reblogs": r.get("reblogs_count", 0),
            "favourites": r.get("favourites_count", 0),
        })
    df = pd.DataFrame(rows)
    return df.sort_values("created_at").reset_index(drop=True)


def window(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    """Posts inside [start, end] (UTC, inclusive). The look-ahead-safe slice: pass the
    catalyst date as `end` and no later post can enter the curator's view."""
    out = df
    if start:
        out = out[out["created_at"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        out = out[out["created_at"] <= pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]
    return out.reset_index(drop=True)


def candidate_posts(start: str | None = None, end: str | None = None,
                    min_engagement: int = 0, drop_empty: bool = True,
                    use_cache: bool = True) -> pd.DataFrame:
    """The candidate trigger pool the curator will judge: every post in the window, optionally
    filtered to those with text and a floor of engagement (a crude reach proxy, NOT a relevance
    call — relevance is the LLM's job). This is plumbing; it picks no winners."""
    df = window(to_frame(fetch_archive(use_cache=use_cache)), start, end)
    if drop_empty:
        df = df[df["text"].str.len() > 0]
    if min_engagement:
        df = df[(df["replies"] + df["reblogs"] + df["favourites"]) >= min_engagement]
    return df.reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="window start, YYYY-MM-DD (UTC)")
    ap.add_argument("--end", help="window end, YYYY-MM-DD (UTC, inclusive)")
    ap.add_argument("--min-engagement", type=int, default=0,
                    help="floor on replies+reblogs+favourites (reach proxy, not relevance)")
    ap.add_argument("--keep-empty", action="store_true", help="keep media-only/empty-text posts")
    ap.add_argument("--no-cache", action="store_true", help="force a fresh archive fetch")
    ap.add_argument("--out", help="write CSV here (default: data/windows/trump_posts_<start>_<end>.csv)")
    args = ap.parse_args(argv)

    df = candidate_posts(args.start, args.end, args.min_engagement,
                         drop_empty=not args.keep_empty, use_cache=not args.no_cache)
    if df.empty:
        print("No posts in that window/filter.", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else (
        REPO_ROOT / "data" / "windows" /
        f"trump_posts_{args.start or 'all'}_{args.end or 'all'}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    span = f"{df['created_at'].min().date()} .. {df['created_at'].max().date()}"
    print(f"{len(df)} candidate posts ({span}) -> {out}")
    print("Next: the curator (map_event.py) reads these and selects the worthy triggers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
