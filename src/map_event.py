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

import llm

REPO_ROOT = Path(__file__).resolve().parent.parent
EVENTS_CSV = REPO_ROOT / "events.csv"
OUT_CSV = REPO_ROOT / "data" / "events_mapped.csv"

MODEL = "claude-opus-4-8"  # default curator model (Anthropic); see --provider/--model

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

# Strict-subset version for provider structured-outputs (OpenRouter/OpenAI): drops the numeric
# bounds / minItems / null-unions that strict mode rejects, so DeepSeek et al. are forced to
# emit valid, parseable JSON (the fix for the ~27% JSON-format failures in the first bake-off).
STRICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "mapped_tickers": {"type": "array", "items": {"type": "string"}},
        "direction": {"type": "string", "enum": ["long", "short"]},
        "mechanism": {"type": "string"},
        "horizon_days": {"type": "integer"},
        "chain_depth": {"type": "integer"},
        "audience_breadth": {"type": "string", "enum": ["megaphone", "broad", "niche", "quiet"]},
        "polymarket_query": {"type": "string"},  # "" when none (strict can't do null-union)
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale": {"type": "string"},
    },
    "required": ["mapped_tickers", "direction", "mechanism", "horizon_days", "chain_depth",
                 "audience_breadth", "polymarket_query", "confidence", "rationale"],
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
3. SELECT THE PUREST VEHICLE — do NOT default to the obvious large-cap. Identify the END \
   of your causal chain: the specific thing that actually moves (a commodity price, a \
   shipping/freight RATE, an interest rate or spread, a narrow sector). Then explicitly \
   weigh the MENU of US-listed vehicles and choose the one whose price tracks THAT driver \
   most directly, even if it is a smaller, specialized fund rather than a familiar stock: \
     - a commodity/rate move -> the commodity- or rate-tracking ETF/ETN itself (crude -> \
       USO/BNO; dry-bulk or tanker FREIGHT RATES -> a freight-rate ETN such as BDRY or BWET), \
       NOT merely a producer/operator equity whose price is diluted by company-specific factors; \
     - a sector move -> a focused sector/thematic ETF over a single diversified mega-cap. \
   A pure-rate ETN beats an operator stock when the thesis is about the RATE. Liquidity \
   matters, but do not discard the most direct vehicle just because a household name is more \
   familiar. Prefer one such purest instrument or a tight 1-5 ticker equal-weight basket. \
   In `rationale`, name the vehicles you considered and why you chose the one you did.
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


def map_one(client: "llm.LLMClient", event: pd.Series, use_web_search: bool) -> dict:
    """Run the agent on a single event; return a dict of MAPPING_COLUMNS. The LLM call (and
    its cost accounting) is delegated to the provider-agnostic client (Anthropic or OpenRouter)."""
    telegraph_date = str(event["telegraph_ts"])[:10]
    user_msg = USER_TEMPLATE.format(
        event_id=event["event_id"],
        telegraph_ts=event["telegraph_ts"],
        source=event["source"],
        telegraph_text=event["telegraph_text"],
        telegraph_date=telegraph_date,
    )
    final_text = client.complete(SYSTEM_PROMPT, user_msg, use_web_search=use_web_search,
                                 label=str(event["event_id"]), stage="ladder",
                                 json_schema=STRICT_SCHEMA,
                                 search_query=str(event["telegraph_text"]),
                                 before_date=telegraph_date)
    data = _extract_json(final_text)

    query = data.get("polymarket_query")
    return {
        "mapped_tickers": ";".join(t.strip().upper() for t in data["mapped_tickers"]),
        "direction": data["direction"],
        "mechanism": data["mechanism"],
        # Clamp the integer fields to their documented ranges — strict structured-output mode
        # (OpenRouter) can't carry the min/max bounds, so a weaker model may emit out-of-range
        # values (DeepSeek once returned chain_depth=194). Keep them in-spec downstream.
        "horizon_days": _clamp(data["horizon_days"], 1, 365),
        "chain_depth": _clamp(data["chain_depth"], 1, 4),
        "audience_breadth": data["audience_breadth"],
        "polymarket_query": "" if not query else str(query).strip(),
        "confidence": data["confidence"],
        "rationale": data["rationale"],
    }


def _clamp(value, lo: int, hi: int) -> int:
    """Coerce to int and clamp to [lo, hi] (defends against out-of-range model output)."""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return lo


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
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openrouter"],
                    help="LLM provider (openrouter = cheap-model bake-off; needs OPENROUTER_API_KEY)")
    ap.add_argument("--model", default=MODEL,
                    help="curator model (default claude-opus-4-8; e.g. claude-haiku-4-5, or "
                         "deepseek/deepseek-chat-v3.2 with --provider openrouter)")
    ap.add_argument("--workers", type=int, default=1,
                    help="ladder events concurrently (default 1; web search makes each call slow)")
    args = ap.parse_args(argv)

    _load_dotenv()
    need_key = "OPENROUTER_API_KEY" if args.provider == "openrouter" else "ANTHROPIC_API_KEY"
    if not os.environ.get(need_key):
        print(f"ERROR: {need_key} is not set (export it or put it in .env).", file=sys.stderr)
        return 2

    try:
        client = llm.make_client(args.provider, args.model)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    events = pd.read_csv(args.events)
    if args.limit:
        events = events.head(args.limit)

    done: dict[str, dict] = {}
    if args.out.exists() and not args.force:
        prev = pd.read_csv(args.out)
        done = {r["event_id"]: r.to_dict() for _, r in prev.iterrows()}

    rows = [done[e["event_id"]] for _, e in events.iterrows()
            if e["event_id"] in done and not args.force]
    pending = [e for _, e in events.iterrows()
               if e["event_id"] not in done or args.force]
    if len(rows):
        print(f"  {len(rows)} cached, {len(pending)} to map")

    def _map(event):
        eid = event["event_id"]
        try:
            mapping = map_one(client, event, use_web_search=not args.no_web_search)
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
