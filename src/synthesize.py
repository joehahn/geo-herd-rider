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

SYSTEM = """You are a market-news synthesist for an event-driven trading system. You read a week's
AGGREGATE signal from influential authors — and you also SEARCH that week's market news — to
decide what the herd is being pushed toward. Every author (a head of state's posts, a beat
reporter's article) is an influencer; treat them together, not as separate triggers.

INPUTS: (1) all of this week's Donald Trump Truth Social posts (given below); (2) the financial
news you retrieve with the web_search tool. SEARCH the week's dominant market stories from the
trusted outlets, and constrain every query to on/before the cron date with "before:<that date>";
DISCARD any result dated after it (no look-ahead). News often names the specific instrument or
trade the tweets only imply — use it.

Your job: identify the 1-3 DOMINANT, market-moving DEVELOPMENTS of the week — the concrete
events/themes a trader would reposition around. Rules:
  - MERGE many posts/articles about the same development into ONE (a dozen Iran-war posts -> one
    "Iran war escalation"). Do not emit one per post.
  - Keep only developments with a clear, tradeable consequence (geopolitics/military, tariffs/
    trade, energy/oil/shipping, Fed/rates, sanctions, major company/sector actions).
  - DROP noise: campaign/electoral, culture-war, personal attacks, polls, congrats.
  - Fewer is better than padded; some weeks have one development, some none.

You forecast NOTHING — no tickers, direction, magnitude, or probability. Judge only by the posts
and the dated news you retrieved.

After any searches, output ONLY this JSON: {"developments":[{"date":"YYYY-MM-DD","development":
"<=25-word concrete description","why":"<=12 words: the market in play","evidence_urls":["the
news URLs that informed this development"]}]}. Empty list is fine: {"developments":[]}."""


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


def news_domains() -> list[str]:
    """Domains the news search is scoped to — parsed from news_sources.md (user-managed)."""
    f = REPO_ROOT / "news_sources.md"
    if not f.exists():
        return []
    import re
    doms = re.findall(r"https?://(?:www\.)?([a-z0-9.\-]+\.[a-z]{2,})", f.read_text())
    return sorted(set(doms))


def _weekly_anchors(start: str, end: str) -> list[pd.Timestamp]:
    """Friday 16:30 ET decision points spanning the window (the weekly cron)."""
    fridays = pd.date_range(start, end, freq="W-FRI", tz="America/New_York")
    return [f.normalize() + pd.Timedelta(hours=CRON_HOUR, minutes=CRON_MIN) for f in fridays]


def _synth_week(client, model: str, anchor: pd.Timestamp, posts: pd.DataFrame,
                domains: list[str]) -> list[dict]:
    lines = [f"[{r.created_at.tz_convert('America/New_York').date()}] {r.text[:MAX_TEXT]}"
             for r in posts.itertuples()]
    prefer = ", ".join(domains) if domains else "major financial news outlets"
    user = (f"Week ending {anchor.date()} (cron date for your before: filter: {anchor.date()}).\n"
            f"All high-reach posts from the trailing window:\n\n" + "\n".join(lines)
            + f"\n\nSearch the week's market news (append 'before:{anchor.date()}' to queries; "
            f"prefer these outlets: {prefer}), then name the week's dominant market-moving "
            "development(s) with evidence_urls.")
    # Open web search (not allowed_domains — several trusted outlets block the crawler); the
    # prompt steers toward the news_sources.md outlets and discards post-cron results.
    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    messages = [{"role": "user", "content": user}]
    kw = {"model": model, "max_tokens": 3000, "system": SYSTEM, "tools": tools, "messages": messages}
    tally = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "web_searches": 0}
    text = ""
    for _ in range(6):  # server web search can pause_turn; resume
        resp = client.messages.create(**kw)
        u = costs.extract(resp.usage)
        for k in tally:
            tally[k] += u.get(k, 0)
        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    costs.record("synthesize", model, f"week-{anchor.date()}", tally)
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
    domains = news_domains()
    print(f"Synthesizing {len(anchors)} weeks ({lookback_days}d lookback) via {model}; "
          f"news search over {len(domains)} domains ...", file=sys.stderr)

    def week(anchor):
        lo = anchor - pd.Timedelta(days=lookback_days)
        wk = posts[(posts["created_at"] > lo) & (posts["created_at"] <= anchor)]
        return _synth_week(client, model, anchor, wk, domains) if len(wk) else []

    devs = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for got in ex.map(week, anchors):
            devs.extend(got)
    devs.sort(key=lambda d: d["anchor"])
    rows = [{
        "event_id": f"WK{i + 1:03d}",
        "telegraph_ts": d["anchor"].strftime("%Y-%m-%dT%H:%M:%S%z"),  # the weekly cron decision
        "source": "Weekly synthesis (Trump posts + news)",
        "telegraph_text": str(d.get("development", "")).strip(),
        "evidence_urls": ";".join(d.get("evidence_urls", []) or []),
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
