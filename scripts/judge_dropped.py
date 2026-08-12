#!/usr/bin/env python3
"""judge_dropped.py — an INDEPENDENT check that a funnel filter dropped the right articles.

Why an LLM and not more code: the filters under test ARE regexes, so testing them with regexes is
circular — it would only confirm the pattern matches what the pattern matches. The judge has to reason
about the headline the way a reader would, with no knowledge of the rule that killed it. So the prompt
deliberately does NOT mention which pattern fired, or that a filter was involved at all: it asks only
"would a trader hunting early catalyst coverage want this?".

Two tiers, because a cheap model is fine at bulk triage and untrustworthy at the verdict that matters:
  1. SCREEN   — the cheap scout model reads every dropped headline (~$0.05 for 4,300).
  2. CONFIRM  — the strong model re-reads ONLY the ones tier 1 called a mistake. False positives are
                the expensive error (a real gem article thrown away), and they are rare, so paying a
                good model for that small set costs little and is where accuracy actually matters.

Reports the FALSE-POSITIVE RATE: of everything the filter dropped, what share was real reporting the
curator should have seen. That number, not a vibe, says whether the filter is behaving.

  python scripts/judge_dropped.py --stage spam --limit 0        # full census
  python scripts/judge_dropped.py --stage no_beat --limit 500   # sample a big stage
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import llm  # noqa: E402
from optimizer import load_financial_model, resolve_stage_models  # noqa: E402
from util import load_dotenv  # noqa: E402

BATCH = 40

# No mention of filters, patterns, or the fact these were dropped -- the judge must not be primed to
# agree with a decision it is supposed to check.
SYSTEM = """You triage financial news headlines for a trader who hunts EARLY coverage of specific,
datable company catalysts — an export ban, a contract award, an FDA decision, a supply shock, a
chokepoint closing — ideally while the story is still under-the-radar.

For each numbered headline, answer KEEP or SKIP.

KEEP  = real reporting on a specific company or sector event a trader could act on.
SKIP  = automated or boilerplate market plumbing with no news in it: analyst price-target changes and
        rating actions, 13F/fund position updates, insider Form-4 filings, "N best stocks to buy"
        listicles, earnings-call transcripts, technical-signal bots (52-week highs, moving averages),
        short-interest updates, and empty or non-article titles.

Judge ONLY the headline as written. Do not speculate about what the body might contain.

Return ONLY JSON: {"v":[{"i":1,"a":"KEEP","w":"3-8 word reason"},...]} with one entry per headline."""


# The recycle gate is a different question from the headline filters: it already HAS the article text,
# and the only issue is whether that text belongs to that headline. So the judge sees the PAIR and rules
# on identity, not newsworthiness. It is never told a rule exists or which way the rule went.
PAIR_SYSTEM = """You are checking whether a news headline and a block of page text describe the SAME
article.

Web pages get re-pointed: a URL that once served one story can later serve a completely different one.
Your job is to spot that. Paraphrase is normal and expected — a lede rarely repeats the headline's
words — so judge the SUBJECT MATTER, not word overlap.

SAME  = the text is plausibly the article that headline belongs to (same company, event or topic),
        even if it shares no vocabulary with the headline.
DIFF  = the text is about an unrelated subject, or is navigation/boilerplate/consent text rather than
        an article.

Return ONLY JSON: {"v":[{"i":1,"a":"SAME","w":"3-8 word reason"},...]} with one entry per pair."""


def _judge_pairs(client, rows, label):
    body = "\n\n".join(
        f"{i + 1}. HEADLINE: {r['title'][:160]}\n   PAGE TEXT: {r.get('rejected_text', '')[:320]}"
        for i, r in enumerate(rows))
    try:
        txt = client.complete(PAIR_SYSTEM, body, use_web_search=False, label=label,
                              stage="agent", effort="low")
    except Exception as e:  # noqa: BLE001
        print(f"    batch failed ({type(e).__name__})", file=sys.stderr)
        return []
    try:
        s = txt[txt.index("{"):txt.rindex("}") + 1]
        return json.loads(s).get("v", [])
    except Exception:  # noqa: BLE001
        return []


def _judge(client, rows, label):
    body = "\n".join(f"{i + 1}. {r['title'][:180]}" for i, r in enumerate(rows))
    try:
        # no web search: the judge must rule on the HEADLINE AS WRITTEN, exactly what the
        # filter saw. Letting it look the story up would test a different question.
        txt = client.complete(SYSTEM, body, use_web_search=False, label=label,
                              stage="agent", effort="low")
    except Exception as e:  # noqa: BLE001
        print(f"    batch failed ({type(e).__name__}); treating as unjudged", file=sys.stderr)
        return []
    try:
        s = txt[txt.index("{"):txt.rindex("}") + 1]
        return json.loads(s).get("v", [])
    except Exception:  # noqa: BLE001
        return []


def run(rows, client, tag, workers=6, pairs=False):
    judge = _judge_pairs if pairs else _judge
    n = 12 if pairs else BATCH          # pair prompts are ~10x longer per item
    batches = [rows[i:i + n] for i in range(0, len(rows), n)]
    out = [None] * len(rows)
    def one(bi):
        b = batches[bi]
        for v in judge(client, b, f"{tag}-{bi}"):
            try:
                k = int(v.get("i", 0)) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= k < len(b):
                out[bi * n + k] = {"a": str(v.get("a", "")).upper(), "w": v.get("w", "")}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, range(len(batches))))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dropped", default="data/backtest_1yr/dropped.json")
    ap.add_argument("--stage", default="spam",  # or 'corpus' for the false-negative check
                    help="a funnel stage from dropped.json, or 'url_recycled' to audit the "
                         "title-consistency gate from the lede cache")
    ap.add_argument("--strong-only", action="store_true",
                    help="skip the cheap screen; judge every row with the strong model (calibration)")
    ap.add_argument("--cache", default="data/backtest_1yr/lede_live_cache.json")
    ap.add_argument("--pool", default="data/backtest_1yr/pool.json")
    ap.add_argument("--limit", type=int, default=0, help="0 = judge every dropped article")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    load_dotenv()

    pairs = a.stage == "url_recycled"
    # FALSE-NEGATIVE mode: sample what SURVIVED and ask whether it should have been dropped. Every
    # filter fix so far traded FP for FN -- softer spam patterns, plural atoms, the named-ticker rescue
    # and the relaxed recycle gate all ADMIT more material -- and measuring only the FP side optimises
    # one direction blind to the other. Here the verdict inverts: a SKIP means junk reached the corpus.
    if a.stage == "corpus":
        rows = json.loads(Path(ROOT / a.pool).read_text())["articles"]
        if a.limit and len(rows) > a.limit:
            rows = random.Random(a.seed).sample(rows, a.limit)
        fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
        (sid, sprov), (eid, eprov) = resolve_stage_models(fm)
        # CALIBRATION mode: run the STRONG model over the whole sample rather than only over what a
        # cheap screen flagged. The two-tier design can only ever shrink tier 1's flag set, so it
        # measures the cheap model's precision and is blind to its RECALL -- junk tier 1 waved through
        # is invisible. A direct strong-model pass over the same rows is the only way to know whether
        # the headline junk-rate is real or an artefact of the screen.
        if a.strong_only:
            sid, sprov = eid, eprov
            print(f"CALIBRATION: {len(rows):,} corpus articles judged DIRECTLY by {sid} "
                  f"(no cheap screen)", flush=True)
        else:
            print(f"FN CHECK: {len(rows):,} articles sampled from the CORPUS -> {sid}", flush=True)
        t1 = run(rows, llm.make_client(sprov, sid), "fn-screen")
        flagged = [i for i, v in enumerate(t1) if v and v["a"] == "SKIP"]
        unj = sum(1 for v in t1 if v is None)
        print(f"  tier 1: {len(flagged)} flagged as junk that got through, {unj} unjudged", flush=True)
        confirmed = []
        if a.strong_only:                       # already the strong model: its verdict IS the answer
            confirmed = [(rows[i], t1[i]) for i in flagged]
        elif flagged:
            sub = [rows[i] for i in flagged]
            t2 = run(sub, llm.make_client(eprov, eid), "fn-confirm")
            confirmed = [(sub[j], t2[j]) for j in range(len(sub)) if t2[j] and t2[j]["a"] == "SKIP"]
        judged = len(rows) - unj
        print(f"\n{'=' * 96}")
        print(f"CORPUS false-negative check: {judged:,} judged")
        print(f"  tier-1 flagged junk  : {len(flagged):,} ({100 * len(flagged) / max(judged, 1):.1f}%)")
        print(f"  tier-2 CONFIRMED junk: {len(confirmed):,} ({100 * len(confirmed) / max(judged, 1):.1f}%)")
        print(f"  -> {100 * (judged - len(confirmed)) / max(judged, 1):.1f}% of the corpus is material "
              f"the curator should see")
        for r, v in confirmed[:25]:
            print(f"  [{r.get('source','')[:24]:24s}] {str(r.get('title'))[:84]}\n       {v['w']}")
        if a.out:
            Path(a.out).write_text(json.dumps(
                {"mode": "corpus_fn", "n_judged": judged, "confirmed_junk": len(confirmed),
                 "junk_rate_pct": round(100 * len(confirmed) / max(judged, 1), 2),
                 "examples": [{"title": r.get("title"), "source": r.get("source"), "why": v["w"]}
                              for r, v in confirmed]}, indent=1))
        return 0

    if pairs:
        # evidence lives in the lede cache, not the funnel dump: these articles were fetched fine and
        # then had their TEXT discarded by the title-consistency gate.
        cache = json.loads(Path(ROOT / a.cache).read_text())
        rows = [{"title": "", "source": "", "url": u, "rejected_text": v.get("rejected_text", "")}
                for u, v in cache.items()
                if isinstance(v, dict) and v.get("miss") == "url_recycled" and v.get("rejected_text")]
        by_url = {x["url"]: x for x in json.loads(Path(ROOT / a.pool).read_text())["articles"]}
        for r in rows:                       # attach the GKG headline the gate compared against
            r["title"] = by_url.get(r["url"], {}).get("title", "")
            r["source"] = by_url.get(r["url"], {}).get("source", "")
        rows = [r for r in rows if r["title"]]
    else:
        rows = json.loads(Path(ROOT / a.dropped).read_text())[a.stage]
    if a.limit and len(rows) > a.limit:
        rows = random.Random(a.seed).sample(rows, a.limit)
    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    (sid, sprov), (eid, eprov) = resolve_stage_models(fm)

    # A cheap screen in front of a strong confirm can only ever SHRINK what the strong model sees, so
    # it measures the screen's precision and is blind to its RECALL -- anything tier 1 waves through is
    # invisible. Measured on a 300-article corpus sample: the cheap screen missed 37 junk articles the
    # strong model caught, while over-flagging only 3, so every tiered number is a LOWER BOUND. Prefer
    # --strong-only on a smaller sample: fewer rows judged well beats more rows judged through a filter
    # whose own error rate is unknown.
    if a.strong_only:
        sid, sprov = eid, eprov
        print(f"DIRECT: {len(rows):,} dropped by '{a.stage}' judged by {sid} (no cheap screen)",
              flush=True)
    else:
        print(f"TIER 1 screen: {len(rows):,} headlines dropped by '{a.stage}' -> {sid}", flush=True)
    t1 = run(rows, llm.make_client(sprov, sid), "screen", pairs=pairs)

    good = "SAME" if pairs else "KEEP"   # pairs: SAME = the gate was wrong to reject
    flagged = [i for i, v in enumerate(t1) if v and v["a"] == good]
    unjudged = sum(1 for v in t1 if v is None)
    print(f"  tier 1: {len(flagged)} flagged as wrongly dropped, {unjudged} unjudged", flush=True)

    confirmed = []
    if a.strong_only:
        confirmed = [(rows[i], t1[i]) for i in flagged]     # strong verdict IS the answer
    elif flagged:
        print(f"TIER 2 confirm: re-reading {len(flagged)} flagged -> {eid}", flush=True)
        sub = [rows[i] for i in flagged]
        sub = [rows[i] for i in flagged]
        t2 = run(sub, llm.make_client(eprov, eid), "confirm", pairs=pairs)
        confirmed = [(sub[j], t2[j]) for j in range(len(sub)) if t2[j] and t2[j]["a"] == good]

    judged = len(rows) - unjudged
    fp = len(confirmed)
    print(f"\n{'=' * 96}")
    print(f"STAGE '{a.stage}': {len(rows):,} dropped, {judged:,} judged")
    print(f"  tier-1 flagged wrongly dropped : {len(flagged):,} ({100 * len(flagged) / max(judged, 1):.1f}%)")
    print(f"  tier-2 CONFIRMED false positive: {fp:,} ({100 * fp / max(judged, 1):.1f}%)")
    print(f"  -> the filter correctly dropped {100 * (judged - fp) / max(judged, 1):.1f}% of what it removed")
    if confirmed:
        print(f"\nCONFIRMED FALSE POSITIVES (articles the curator should have seen):")
        for r, v in confirmed[:40]:
            print(f"  [{r['source'][:24]:24s}] {r['title'][:88]}")
            print(f"       why keep: {v['w']}")
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"stage": a.stage, "n_dropped": len(rows), "n_judged": judged,
             "tier1_flagged": len(flagged), "false_positives": fp,
             "fp_rate_pct": round(100 * fp / max(judged, 1), 2),
             "examples": [{**r, "why": v["w"]} for r, v in confirmed]}, indent=1))
        print(f"\n-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
