"""synthesize.py — weekly aggregate synthesis: read the week's signal, name the CENTER.

The fix for "every Trump tweet is the root of its own tree." The premise: every author is an
influencer jostling to move the herd; the curator should read the WEEK's aggregate signal and
decide what's central — regardless of who said it or how many posts mention it. So each weekly
run (a Friday-after-close cron) reads the trailing `news_lookback_days` of posts and has the LLM
identify the 1-3 DOMINANT market-moving developments of the week, merging many posts about the
same thing into one. Those developments — not individual tweets — become the roots that
map_event ladders to instruments.

v1 reads Trump's Truth Social posts (src/trump_feed). The news arm (web_search over
news_sources.md) and other influencers (Fed/Musk/Dimon/Pelosi) are added in later phases; the
synthesis logic is source-agnostic by design.

Look-ahead: a given Friday's synthesis sees ONLY posts dated on/before that Friday's 16:30 ET
cron; the decision timestamp it stamps is that cron time, so entry is strictly after. The model's
training still postdates the events, so retrospective output is an upper bound — forward is clean.

    python src/synthesize.py --start 2025-11-01 --end 2026-06-18 --model claude-opus-4-8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
import trump_feed  # noqa: E402
import costs  # noqa: E402
from map_event import _load_dotenv  # noqa: E402

MODEL = "claude-opus-4-8"
CRON_HOUR, CRON_MIN = 16, 30          # weekly scan: Friday 16:30 ET
MAX_TEXT = 320                         # truncate each post in the prompt
WORKERS = 8

SYSTEM = """You are a market-news synthesist for an event-driven trading system. You receive ALL
of one week's high-reach posts from influential authors (this week: Donald Trump's Truth Social).
Treat them as an aggregate signal, not individual triggers.

Your job: identify the 1-3 DOMINANT, market-moving DEVELOPMENTS of the week — the concrete
events/themes a professional trader would reposition around. Rules:
  - MERGE many posts about the same development into ONE (e.g., a dozen posts about the Iran war
    -> a single "Iran war escalation" development). Do not emit one per post.
  - Keep only developments with a clear, tradeable market consequence (geopolitics/military,
    tariffs/trade, energy/oil/shipping, Fed/rates, sanctions, major company/sector actions).
  - DROP the noise: campaign/electoral, domestic culture-war, personal attacks, polls, congrats.
  - Some weeks have only ONE real development; some have none. Fewer is better than padded.

You forecast NOTHING — no tickers, direction, magnitude, or probability. Judge only by the posts
given; ignore anything you know happened later.

Return JSON only: {"developments":[{"date":"YYYY-MM-DD","development":"<=25-word concrete
description of what is happening>","why":"<=12 words: the market in play"}]}. The date is when
the development crystallized within the week. Empty list is fine: {"developments":[]}."""


def _extract_json(text: str) -> dict:
    t = text.strip()
    if "```" in t:
        for chunk in reversed(t.split("```")):
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                return json.loads(c)
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        return json.loads(t[s:e + 1])
    raise ValueError("no JSON object in model output")


def _weekly_anchors(start: str, end: str) -> list[pd.Timestamp]:
    """Friday 16:30 ET decision points spanning the window (the weekly cron)."""
    fridays = pd.date_range(start, end, freq="W-FRI", tz="America/New_York")
    return [f.normalize() + pd.Timedelta(hours=CRON_HOUR, minutes=CRON_MIN) for f in fridays]


def _synth_week(client, model: str, anchor: pd.Timestamp, posts: pd.DataFrame) -> list[dict]:
    lines = [f"[{r.created_at.tz_convert('America/New_York').date()}] {r.text[:MAX_TEXT]}"
             for r in posts.itertuples()]
    user = (f"Week ending {anchor.date()} — all high-reach posts from the trailing window:\n\n"
            + "\n".join(lines) + "\n\nName the week's dominant market-moving development(s).")
    resp = client.messages.create(model=model, max_tokens=1500, system=SYSTEM,
                                  messages=[{"role": "user", "content": user}])
    costs.record("synthesize", model, f"week-{anchor.date()}", costs.extract(resp.usage))
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        out = _extract_json(text).get("developments", [])
    except Exception:  # noqa: BLE001
        return []
    return [{**d, "anchor": anchor} for d in out]


def synthesize(start: str, end: str, lookback_days: int = 7, model: str = MODEL,
               workers: int = WORKERS) -> pd.DataFrame:
    import anthropic
    client = anthropic.Anthropic()
    posts = trump_feed.candidate_posts(start, end)
    anchors = _weekly_anchors(start, end)
    print(f"Synthesizing {len(anchors)} weeks ({lookback_days}d lookback) via {model} ...",
          file=sys.stderr)

    def week(anchor):
        lo = anchor - pd.Timedelta(days=lookback_days)
        wk = posts[(posts["created_at"] > lo) & (posts["created_at"] <= anchor)]
        return _synth_week(client, model, anchor, wk) if len(wk) else []

    devs = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for got in ex.map(week, anchors):
            devs.extend(got)
    devs.sort(key=lambda d: d["anchor"])
    rows = [{
        "event_id": f"WK{i + 1:03d}",
        "telegraph_ts": d["anchor"].strftime("%Y-%m-%dT%H:%M:%S%z"),  # the weekly cron decision
        "source": "Weekly synthesis (Trump posts)",
        "telegraph_text": str(d.get("development", "")).strip(),
    } for i, d in enumerate(devs) if str(d.get("development", "")).strip()]
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-06-18")
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "windows" / "weekly_events.csv"))
    args = ap.parse_args(argv)

    _load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        return 2

    df = synthesize(args.start, args.end, args.lookback_days, args.model, args.workers)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"\n{len(df)} weekly central developments -> {args.out}")
    for r in df.itertuples():
        print(f"  {r.telegraph_ts[:10]}  {r.telegraph_text[:84]}")
    print("\nNext: ladder them — python src/map_event.py --events", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
