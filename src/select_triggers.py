"""select_triggers.py — the LLM trigger scout: which Trump posts are worth laddering?

Stage between the raw feed and the curator. trump_feed.py hands over the COMPLETE windowed
post stream (no human picking); this module leans on the Anthropic key to read every post and
keep only the market-moving TRIGGERS — posts announcing/signaling a geopolitical, macro,
policy, or corporate event that could start a causal chain with tradeable implications. The
human never chooses; the LLM does. Output is rows in map_event.py's input schema, so the
curator can ladder them next.

Hard rule (CLAUDE.md non-negotiable #1): the scout forecasts NOTHING — no tickers, direction,
magnitude, or size. It makes one call only: "is this post a worthy trigger?" Sizing and the
ladder are downstream.

Cheap by design: a high-volume binary-ish screen, so it defaults to Haiku and fans the batches
out concurrently. Look-ahead note: each post is judged on its own text, but the model's
training postdates these events, so retrospective selection is still hindsight-tinged — an
upper bound, like every backtest here. The clean test is forward.

    python src/select_triggers.py --start 2025-11-01 --end 2026-06-18
    python src/select_triggers.py --start 2026-01-01 --end 2026-03-01 --model claude-opus-4-8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
import trump_feed  # noqa: E402
import costs  # noqa: E402
from map_event import _load_dotenv  # noqa: E402  (reuse the zero-dep .env loader)

MODEL = "claude-opus-4-8"    # selection quality is the bottleneck — Haiku self-contradicts on
                             # this screen; Opus is the proven default. Override with --model.
BATCH = 40                    # posts per LLM call
WORKERS = 8                   # concurrent batches
MAX_TEXT = 400                # truncate each post to bound tokens

SYSTEM = """You are a trigger scout for an event-driven trading research system. Be RUTHLESS. \
Your default is DROP. Expect to keep only a small handful of posts — well under 1 in 20. When \
in doubt, DROP. It is far worse to admit noise than to miss a marginal trigger.

KEEP a post ONLY if ALL THREE hold:
  1. CONCRETE EVENT, not talk. It reports a specific action that has HAPPENED or is explicitly \
IMMINENT — ordered, launched, signed, imposed, closed, struck, seized, banned, deployed. \
NOT an opinion, a general threat, a campaign promise, a poll/process update, a meeting readout, \
a nomination, or an aspiration ("we will", "I want", "they should").
  2. MATERIAL MARKET MOVE. The event would, on its own, move a SPECIFIC sector / commodity / \
currency enough that a trader would reposition that day — not a vague, diffuse, or third-order \
"this is generally good/bad for the economy" effect.
  3. NAMEABLE INSTRUMENT. You can name the one asset/sector/commodity in play in <=8 words. If \
you cannot, DROP.

Worked examples:
  KEEP: "Carriers ordered to the Gulf" (oil, defense, tanker rates) · "25% tariff on all steel \
imports effective Monday" (domestic steel, autos) · "We have struck Iran's main refinery" \
(crude oil) · "Strait of Hormuz is now closed to shipping" (tankers, oil).
  DROP: "Great meeting with President Xi" (no concrete action) · "Vote Republican for lower \
energy prices" (campaign) · "TERMINATE THE FILIBUSTER" (process) · "SNAP benefits will end" \
(fiscal, diffuse) · "I am endorsing X for Governor" (electoral) · "Tariffs case is the most \
important in history" (commentary about a pending event, not the event) · "Sean Duffy did a \
great job at NASA" (personnel praise).

The line that catches most false positives: a post ABOUT a market-relevant topic is not a \
trigger unless it announces a concrete EVENT. Commentary, framing, threats, and campaigning on \
trade/energy/foreign-policy are still DROP.

You forecast NOTHING — no tickers, direction, magnitude, or size. One call: clears all three or \
not. Judge each post by its own text only; ignore anything you know happened afterward.

Return JSON only, no prose: {"selected":[{"id":"<post id>","why":"<=8 words: the asset/sector \
in play"}]}. Keeping nothing in a batch is a fine and common answer: {"selected":[]}."""


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


def _screen_batch(client, model: str, batch: pd.DataFrame) -> list[dict]:
    """Screen one batch; return the kept records [{id, why}]."""
    lines = [f"[{r.post_id}] {r.created_at.date()} :: {r.text[:MAX_TEXT]}"
             for r in batch.itertuples()]
    user = ("Screen these posts. Keep only market-moving triggers.\n\n"
            + "\n".join(lines))
    resp = client.messages.create(model=model, max_tokens=2000, system=SYSTEM,
                                  messages=[{"role": "user", "content": user}])
    costs.record("scout", model, f"batch[{batch.index[0]}:{batch.index[-1]}]",
                 costs.extract(resp.usage))
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return _extract_json(text).get("selected", [])
    except Exception:  # noqa: BLE001 — a malformed batch shouldn't kill the run
        return []


def select(posts: pd.DataFrame, model: str = MODEL, workers: int = WORKERS) -> pd.DataFrame:
    """Run the scout over all posts; return the kept posts with the scout's one-line why."""
    import anthropic
    client = anthropic.Anthropic()
    batches = [posts.iloc[i:i + BATCH] for i in range(0, len(posts), BATCH)]
    print(f"Screening {len(posts)} posts in {len(batches)} batches via {model} ...",
          file=sys.stderr)

    kept: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for sel in ex.map(lambda b: _screen_batch(client, model, b), batches):
            for s in sel:
                pid = str(s.get("id", "")).strip()
                if pid:
                    kept[pid] = str(s.get("why", "")).strip()
            done += 1
            print(f"\r  {done}/{len(batches)} batches, {len(kept)} kept", end="", file=sys.stderr)
    print(file=sys.stderr)

    picks = posts[posts["post_id"].isin(kept)].copy()
    picks["why"] = picks["post_id"].map(kept)
    picks = picks.sort_values("created_at").reset_index(drop=True)

    # Light dedup: one same-day post per near-identical opener (Trump often re-posts the
    # same statement). Collapses inflated counts without an LLM call.
    picks["_key"] = (picks["created_at"].dt.date.astype(str) + "|"
                     + picks["text"].str.replace(r"\s+", " ", regex=True).str[:60].str.lower())
    picks = picks.drop_duplicates("_key").drop(columns="_key").reset_index(drop=True)
    return picks


def to_triggers(picks: pd.DataFrame) -> pd.DataFrame:
    """Map the kept posts into map_event.py's input schema.

    telegraph_ts keeps the FULL post timestamp (UTC ISO), not a truncated date — the backtest's
    entry_index uses the post's time-of-day to decide same-day-close vs next-day entry. Dropping
    the time made every post parse as midnight (hour 0 < 16:00 ET), so all triggers were treated
    as same-day-actable and bought at the trigger-day close — a look-ahead leak. Keep the time."""
    out = pd.DataFrame({
        "event_id": [f"TRT{i + 1:03d}" for i in range(len(picks))],
        "telegraph_ts": picks["created_at"].dt.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": picks["source"],
        "telegraph_text": picks["text"],
        "post_id": picks["post_id"],  # provenance + lets a trigger be rejoined to the raw post
    })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="window start, YYYY-MM-DD (UTC)")
    ap.add_argument("--end", help="window end, YYYY-MM-DD (UTC, inclusive)")
    ap.add_argument("--min-engagement", type=int, default=0,
                    help="reach floor before screening (plumbing, not relevance; default 0 = all)")
    ap.add_argument("--model", default=MODEL, help=f"scout model (default {MODEL})")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--out", help="triggers CSV (default data/windows/trump_triggers.csv)")
    args = ap.parse_args(argv)

    _load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set (export it or put it in .env).", file=sys.stderr)
        return 2

    posts = trump_feed.candidate_posts(args.start, args.end, args.min_engagement)
    if posts.empty:
        print("No candidate posts in that window.", file=sys.stderr)
        return 1

    picks = select(posts, model=args.model, workers=args.workers)
    if picks.empty:
        print("Scout kept nothing.", file=sys.stderr)
        return 1

    triggers = to_triggers(picks)
    out = Path(args.out) if args.out else REPO_ROOT / "data" / "windows" / "trump_triggers.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    triggers.to_csv(out, index=False)

    rate = len(picks) / len(posts)
    print(f"\nKept {len(picks)}/{len(posts)} posts ({rate:.0%}) as triggers -> {out}")
    print("Sample of what the scout kept:")
    for r in picks.head(12).itertuples():
        print(f"  {r.created_at.date()}  [{r.why}]  {r.text[:80]}")
    print("\nNext: ladder them — python src/map_event.py --events", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
