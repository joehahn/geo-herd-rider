"""picker.py — the weekly LLM AGENT-PICKER: the max_agents PORTFOLIO cull.

Replaces the old conviction-ranked cull. Given every currently-live event-agent (catalyst, milestones,
exit condition, weeks-alive, cumulative P&L), it returns an ORDERED KEEP-LIST — the events most worth
holding capital next week. It emits ONLY a ranking/selection; the mechanical optimizer sizes whatever it
keeps (non-negotiable #1: the LLM never sets weights, sizes, or expected-return numbers). It ranks on the
catalyst ARC using the accrued evidence — early/building over crested/near-resolution, with slots reserved
for fresh events — NOT on a predicted-return forecast.

Validated 2026-07-14 (scripts/proto_select.py, post-hoc replay over the whole book): conviction ≈ random;
a cheap picker (deepseek) < random; a STRONG picker (sonnet5) beat random (83rd %ile, +162%, funded MU/MP).
Model quality is the whole story — run it on a strong model. Backtest is an upper bound (the picker may be
recognizing training-set winners); the forward paper trade is the clean test. See memory agent-picker-findings.

Responses are cached MODEL-SPECIFICALLY (data/windows/picker_cache.json) so replays/re-reports are free;
only clean JSON successes are cached (errors get retried, and fall back to "keep the first max_keep").
"""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

import llm
import picker_log
from optimizer import resolve_curator_model

_ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH = _ROOT / "data" / "windows" / "picker_cache.json"

PICKER_SYS = (
    "You are the weekly AGENT-PICKER for a news-driven portfolio. You are given every currently-live "
    "event-agent, each with: ticker, catalyst, milestones-to-date, its stated exit condition, and "
    "weeks_alive. Output an ORDERED keep-list of the agents most worth holding capital next week (top = "
    "most worth a slot). You do NOT assign weights, sizes, or expected returns — a mechanical optimizer "
    "sizes whatever you keep. Rank on THESIS ARC AND HEALTH, using the evidence:\n"
    "- FAVOR catalysts still early / building — the shock is unfolding, milestones still landing, thesis "
    "unresolved and under-owned.\n"
    "- DEMOTE catalysts that have crested or are near resolution — if a long-lived winner's catalyst is "
    "about to resolve (award final, vote passed, chokepoint reopening), rank it DOWN: take the gain before "
    "the thesis dies, don't ride it into resolution.\n"
    "- RESERVE a few slots for the newest agents — fresh events are fishing expeditions; most won't pay, so "
    "keep several lines in the water. Don't let established agents crowd out all exploration.\n"
    'Return JSON: {"keep": ["TICKER", ...]} ordered best-first.'
    # NOTE: cumulative P&L was REMOVED as an input (2026-07-14) — it depended on prior cull decisions, creating
    # a chaotic feedback loop that made the backtest non-reproducible. Inputs are now stable/scan-derived only.
)
PICKER_SCHEMA = {"type": "object", "properties": {"keep": {"type": "array", "items": {"type": "string"}}},
                 "required": ["keep"]}


def make_picker(fm: dict, cache_path: Path | None = None):
    """Return (pick_fn, stats_fn). pick_fn(cand_meta, max_keep) -> ordered keep-list of tickers.

    picker_model resolves through the curator-model registry; default 'sonnet5' (the picker NEEDS a strong
    model — cheap models tie or trail random). cand_meta = [{ticker, catalyst, milestones, exit_condition,
    weeks_alive, cum_pnl_usd}, ...]."""
    short = fm.get("picker_model") or "sonnet5"
    effort = str(fm.get("picker_effort", "low")).lower()   # ranking task -> 'low' by default (cheap/fast); 'high' for the forward test
    mid, prov = resolve_curator_model(short)
    client = llm.make_client(prov, mid)
    path = cache_path or _CACHE_PATH
    cache = json.loads(path.read_text()) if path.exists() else {}
    calls = [0]

    def pick(cand_meta: list[dict], max_keep: int, context: str = "") -> list[str]:
        tickers = [a["ticker"] for a in cand_meta]
        user = json.dumps({"max_keep": max_keep, "agents": cand_meta}, sort_keys=True)
        key = hashlib.sha256((short + "|" + PICKER_SYS + user).encode()).hexdigest()[:20]   # model-specific
        if key in cache:
            keep = cache[key]
        else:
            calls[0] += 1
            try:
                txt = client.complete(PICKER_SYS, user, use_web_search=False, label="picker",
                                      stage="picker", json_schema=PICKER_SCHEMA, effort=effort)
                m = re.search(r"\{.*\}", txt, re.S)      # tolerate markdown fences / stray prose
                keep = json.loads(m.group(0) if m else txt).get("keep", [])
                keep = [t for t in keep if t in tickers]  # drop hallucinated tickers, preserve order
                cache[key] = keep                        # cache ONLY clean successes
                path.write_text(json.dumps(cache))
            except Exception as e:  # noqa: BLE001
                import sys  # noqa: PLC0415
                print(f"  picker error ({e}); falling back to first {max_keep}", file=sys.stderr)
                keep = tickers[:max_keep]
        picker_log.log("agent", {"context": context, "model": short, "max_keep": max_keep,   # OFF unless enabled
                                 "inputs": cand_meta, "kept": keep,
                                 "culled": [t for t in tickers if t not in keep]})
        return keep

    return pick, lambda: (calls[0], f"{short} ({prov})")
