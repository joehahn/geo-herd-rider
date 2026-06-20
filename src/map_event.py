"""map_event.py — Layer 2 of the geo-wave-rider Phase 1 pipeline.

The BLIND mapping agent. Given ONLY a telegraph (text + source + timestamp) and
information available on or before that timestamp, it emits a paper-trade plan:
which tickers, which direction, the causal mechanism, and a holding horizon. It
NEVER forecasts magnitude (that discipline is carried over from
portfolio-wave-rider) and it NEVER uses knowledge of what happened after the
telegraph.

Two columns beyond the SPEC schema are emitted to test Joe's herd model
(see README): `chain_depth` (how many causal hops from telegraph to instrument —
the diffusion-lag proxy) and `audience_breadth` (how loudly/publicly the signal
was broadcast). The hypothesis: edge concentrates in deep chains off quiet
sources, not shallow calls off megaphones.

    Input : events.csv         (event_id, telegraph_ts, source, telegraph_text)
    Output: data/events_mapped.csv  (input cols + mapping cols)

Look-ahead hygiene
------------------
The agent is instructed to restrict every web query to information dated on or
before the telegraph timestamp (Google-style ``before:YYYY-MM-DD``) and to
discard anything published after it — the same hygiene the portfolio-wave-rider
curator uses.

    *** KNOWN LIMITATION ***  This is a RETROSPECTIVE test run by a model whose
    training data extends past these 2024-2026 events, so the model's parametric
    memory is itself a hindsight leak that web-query hygiene cannot remove. Phase
    1 therefore measures a best-effort, hindsight-contaminated base rate. The
    only clean test of the hypothesis is the Phase 2 live forward logger, which
    maps telegraphs the model has never seen the outcome of. Treat a Phase 1
    "go" as necessary-but-not-sufficient; treat a clean "no-go" as credible.

Requires ANTHROPIC_API_KEY in the environment.

Usage
-----
    python src/map_event.py                 # map all events.csv -> data/events_mapped.csv
    python src/map_event.py --limit 3       # only the first 3 (smoke test)
    python src/map_event.py --no-web-search # map from priors only (no live search)
    python src/map_event.py --force         # remap events already in the output
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

import costs

REPO_ROOT = Path(__file__).resolve().parent.parent
EVENTS_CSV = REPO_ROOT / "events.csv"
OUT_CSV = REPO_ROOT / "data" / "events_mapped.csv"

MODEL = "claude-opus-4-8"

# Adaptive thinking + the effort knob work on Opus 4.x / Sonnet 4.6 / Fable, but ERROR on
# Haiku 4.5 and older. Gate them so a cheap curator (Haiku) can be used for playtests.
_ADVANCED_PARAM_MODELS = ("opus-4", "sonnet-4-6", "fable", "mythos")


def _supports_advanced(model: str) -> bool:
    return any(k in model for k in _ADVANCED_PARAM_MODELS)

# The mapping columns the agent appends to each event row.
MAPPING_COLUMNS = [
    "mapped_tickers",          # ;-separated US-listed tickers/ETFs
    "direction",               # long | short
    "mechanism",               # one-line causal chain: telegraph -> instrument
    "horizon_days",            # integer calendar days to hold
    "chain_depth",             # 1-4: causal hops from telegraph to instrument
    "audience_breadth",        # megaphone | broad | niche | quiet
    "polymarket_query",        # search phrase naming the resolvable question (or empty)
    "confidence",              # low | medium | high
    "rationale",               # short free text (pre-catalyst reasoning only)
]

# JSON Schema the agent must satisfy. Kept in the prompt (not as output_config)
# because we run alongside the server-side web_search tool and parse the final
# message's fenced JSON ourselves.
RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "mapped_tickers": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "US-listed tickers or ETFs forming an equal-weight basket.",
        },
        "direction": {"type": "string", "enum": ["long", "short"]},
        "mechanism": {
            "type": "string",
            "description": "One sentence: telegraph -> ... -> these instruments.",
        },
        "horizon_days": {
            "type": "integer",
            "minimum": 1,
            "maximum": 365,
            "description": "Calendar days to hold. Pick the horizon, never the size.",
        },
        "chain_depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 4,
            "description": (
                "Causal hops from telegraph to instrument. 1 = direct/obvious "
                "(tariff on steel -> steelmakers). 3-4 = multi-hop, non-obvious "
                "(carriers near Iran -> Hormuz risk -> tanker rates -> dry-bulk ETF)."
            ),
        },
        "audience_breadth": {
            "type": "string",
            "enum": ["megaphone", "broad", "niche", "quiet"],
            "description": (
                "How loudly the signal was broadcast. megaphone = top Trump/Musk "
                "post the whole market reads instantly; quiet = credible but "
                "low-reach source few investors parse quickly."
            ),
        },
        "polymarket_query": {
            "type": ["string", "null"],
            "description": (
                "A short search phrase naming the resolvable PUBLIC question whose outcome "
                "would confirm this trigger's premise, phrased like a prediction market "
                "(e.g. 'Trump reciprocal tariffs by April 2025', 'Fed cuts rates in Q1 2025'). "
                "This is the QUESTION, never the probability — the odds are fetched "
                "mechanically from Polymarket downstream. Null when the trigger has no clean "
                "market-resolvable upstream event (true for most single-company/equity triggers)."
            ),
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale": {
            "type": "string",
            "description": "Brief reasoning from PRE-catalyst information only.",
        },
    },
    "required": [
        "mapped_tickers", "direction", "mechanism", "horizon_days",
        "chain_depth", "audience_breadth", "polymarket_query",
        "confidence", "rationale",
    ],
}

SYSTEM_PROMPT = """\
You are the blind mapping layer of a falsification experiment. You receive a single \
"telegraph" — a public statement by a high-reach figure (Trump, Musk, RFK Jr., etc.) \
— and you map it to a paper trade: a basket of US-listed tickers, a direction, the \
causal mechanism, and a holding horizon.

ABSOLUTE RULES
1. NO LOOK-AHEAD. Reason ONLY from information available on or before the telegraph \
   timestamp. When you search the web, constrain every query to that date or earlier \
   (append "before:YYYY-MM-DD" using the telegraph date) and DISCARD anything dated \
   after it. You do not know what happened after the telegraph. Do not let any later \
   knowledge influence the basket, direction, or horizon.
2. NEVER FORECAST MAGNITUDE OR PROBABILITY. You pick the basket, the direction, the \
   horizon, and (if one exists) the resolvable question. You never estimate how big the \
   move will be, expected return, position size, or the probability the event resolves — \
   sizing is mechanical downstream, and the probability comes from the market, not you.
3. PICK THE MOST DIRECT TRADEABLE INSTRUMENTS. Prefer liquid US-listed ETFs or stocks \
   whose price most cleanly expresses the mechanism. A basket may be 1-5 tickers, \
   equal-weighted.
4. BE HONEST ABOUT chain_depth and audience_breadth — they are the variables under \
   test. Do not inflate chain_depth to look clever; score the genuine number of causal \
   hops. Do not soften audience_breadth; a top Trump post is "megaphone".

Return your answer as a single fenced ```json code block matching this schema exactly:
%s
Output ONLY the JSON block as your final message (after any web searches). No prose \
outside the block.""" % json.dumps(RESPONSE_SCHEMA, indent=2)

USER_TEMPLATE = """\
Telegraph to map (map this and nothing else):

  event_id:      {event_id}
  telegraph_ts:  {telegraph_ts}
  source:        {source}
  telegraph_text: {telegraph_text}

The telegraph date for your "before:" search constraint is {telegraph_date}.
Map it now under the absolute rules. Final message = the JSON block only."""


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of the model's final message (fenced or bare)."""
    t = text.strip()
    if "```" in t:
        # take the content of the last fenced block
        parts = t.split("```")
        # parts alternate text / code / text / code ...
        for chunk in reversed(parts):
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                return json.loads(c)
    # fall back to first '{' .. last '}'
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(t[start : end + 1])
    raise ValueError("no JSON object found in model output")


def map_one(client, event: pd.Series, use_web_search: bool, model: str = MODEL) -> dict:
    """Run the agent on a single event; return a dict of MAPPING_COLUMNS."""
    telegraph_date = str(event["telegraph_ts"])[:10]
    user_msg = USER_TEMPLATE.format(
        event_id=event["event_id"],
        telegraph_ts=event["telegraph_ts"],
        source=event["source"],
        telegraph_text=event["telegraph_text"],
        telegraph_date=telegraph_date,
    )

    # Dynamic-filtering web search (_20260209) needs programmatic tool calling, which only
    # the advanced models support; cheaper models (Haiku) use the basic variant.
    if use_web_search:
        ws = "web_search_20260209" if _supports_advanced(model) else "web_search_20250305"
        tools = [{"type": ws, "name": "web_search"}]
    else:
        tools = []

    messages = [{"role": "user", "content": user_msg}]
    create_kwargs = {"model": model, "max_tokens": 8000, "system": SYSTEM_PROMPT,
                     "tools": tools, "messages": messages}
    if _supports_advanced(model):  # Haiku 4.5 rejects effort + adaptive thinking
        create_kwargs["thinking"] = {"type": "adaptive"}
        create_kwargs["output_config"] = {"effort": "high"}
    final_text = ""
    tally = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "web_searches": 0}
    # Server-side web search runs an internal loop; it can return pause_turn when
    # it hits the per-request tool-iteration cap. Re-send to resume (no extra
    # user message — the API detects the trailing server_tool_use and continues).
    for _ in range(6):
        resp = client.messages.create(**create_kwargs)
        u = costs.extract(resp.usage)
        for k in tally:
            tally[k] += u.get(k, 0)
        final_text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model refused mapping for {event['event_id']}")
        break

    costs.record("ladder", model, str(event["event_id"]), tally)
    data = _extract_json(final_text)

    query = data.get("polymarket_query")
    return {
        "mapped_tickers": ";".join(t.strip().upper() for t in data["mapped_tickers"]),
        "direction": data["direction"],
        "mechanism": data["mechanism"],
        "horizon_days": int(data["horizon_days"]),
        "chain_depth": int(data["chain_depth"]),
        "audience_breadth": data["audience_breadth"],
        "polymarket_query": "" if not query else str(query).strip(),
        "confidence": data["confidence"],
        "rationale": data["rationale"],
    }


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a repo-root .env into os.environ (no dependency,
    won't override anything already set). Lets a cloner just edit .env and run."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Blind mapping agent (Phase 1, layer 2).")
    ap.add_argument("--limit", type=int, default=None, help="map only the first N events")
    ap.add_argument("--no-web-search", action="store_true", help="map from priors only")
    ap.add_argument("--force", action="store_true", help="remap events already in output")
    ap.add_argument("--events", type=Path, default=EVENTS_CSV)
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    ap.add_argument("--model", default=MODEL,
                    help="curator model (default claude-opus-4-8; e.g. claude-haiku-4-5 for cheap playtests)")
    ap.add_argument("--workers", type=int, default=1,
                    help="ladder events concurrently (default 1; web search makes each call slow)")
    args = ap.parse_args(argv)

    _load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set (export it or put it in .env). "
              "The mapping agent needs it.", file=sys.stderr)
        return 2

    import anthropic  # imported here so --help works without the package

    events = pd.read_csv(args.events)
    if args.limit:
        events = events.head(args.limit)

    done: dict[str, dict] = {}
    if args.out.exists() and not args.force:
        prev = pd.read_csv(args.out)
        done = {r["event_id"]: r.to_dict() for _, r in prev.iterrows()}

    client = anthropic.Anthropic()
    rows = [done[e["event_id"]] for _, e in events.iterrows()
            if e["event_id"] in done and not args.force]
    pending = [e for _, e in events.iterrows()
               if e["event_id"] not in done or args.force]
    if len(rows):
        print(f"  {len(rows)} cached, {len(pending)} to map")

    def _map(event):
        eid = event["event_id"]
        try:
            mapping = map_one(client, event, use_web_search=not args.no_web_search, model=args.model)
        except Exception as e:  # one bad event shouldn't sink the batch
            print(f"  {eid}: FAILED ({e})", file=sys.stderr)
            return None
        print(f"  {eid}: {mapping['direction']} {mapping['mapped_tickers']} "
              f"({mapping['horizon_days']}d, chain={mapping['chain_depth']}, "
              f"aud={mapping['audience_breadth']})", flush=True)
        return {**event.to_dict(), **mapping}

    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            rows += [r for r in ex.map(_map, pending) if r is not None]
    else:
        rows += [r for r in (_map(e) for e in pending) if r is not None]

    out = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"\nWrote {len(out)} mapped events -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
