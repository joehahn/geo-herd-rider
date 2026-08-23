"""catalyst_recheck.py -- did the scout walk past REAL new catalysts, or only momentum?

THE QUESTION. Five names (BE, CORZ, NVTS, BKSY, GNENF) kept rising for months-to-years after the
curator exited them. Measured 2026-08-22, the exit call is not what held us out: the scout put those
names back in front of the event agent in 8% (CORZ) and 20% (NVTS) of the gap weeks that HAD press
coverage, and 0% for the rest. The agent was never asked. That is the scout's momentum rule doing
what agent.py:224 tells it to do -- "after the event occurs the early-gem edge is gone".

So the open question is narrow: in those gap weeks, was the press reporting a NEW, DATED catalyst
(a fresh contract, approval, deal, capacity decision) or only that the stock had gone up? The first
is an event the design SHOULD have opened; the second is momentum we deliberately decline.

WHY THIS IS LOOK-AHEAD CLEAN. The judge never sees a price, a return, a peak date, or the fact that
we exited. It reads article text and answers a question about the text. Nothing about what the stock
subsequently did can reach it, so this cannot be tuned toward a known outcome (CLAUDE.md #7).

THE CONTROL IS THE POINT. A gap-window catalyst rate means nothing alone -- any corpus of financial
news mentions contracts. So each ticker is ALSO scored over its ENTRY window, the weeks the scout
DID admit it. Same ticker, same publications, same judge, differing only in period. If entry weeks
score far higher, the scout is separating catalyst from momentum correctly and the missed upside was
never ours. If the two rates are close, the scout is discarding real events and the prompt is at
fault.
"""
from __future__ import annotations
import json, sys, collections, random, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from util import load_dotenv  # noqa: E402
load_dotenv()
import optimizer, llm  # noqa: E402

# (entry window = weeks the scout admitted it; gap window = exit -> peak, the disputed stretch)
CASES = {
    "BE":    dict(keys=["bloom energy"],                  entry=("2025-09-29", "2026-02-26"), gap=("2026-02-26", "2026-06-22")),
    "CORZ":  dict(keys=["core scientific"],               entry=("2024-04-07", "2024-06-06"), gap=("2024-06-06", "2026-06-18")),
    "NVTS":  dict(keys=["navitas"],                       entry=("2024-11-03", "2025-07-01"), gap=("2025-07-01", "2026-05-26")),
    "BKSY":  dict(keys=["blacksky"],                      entry=("2025-02-01", "2025-07-01"), gap=("2025-07-01", "2026-05-28")),
    "GNENF": dict(keys=["greenland", "tanbreez"],         entry=("2025-11-28", "2026-03-28"), gap=("2026-03-28", "2026-05-06")),
}

SYSTEM = """You classify one financial news article. You are told nothing about what any stock did
afterwards, and you must not speculate about it -- judge ONLY what this article reports.

Answer: does the article report a NEW, DATED, VERIFIABLE CORPORATE OR REGULATORY EVENT?

Counts as an event (verdict "event"):
  a contract or order award, a customer or partnership agreement, an acquisition, an FDA/regulatory
  decision, a permit or licence, an earnings guidance CHANGE, a capacity/plant/expansion decision, a
  government award or policy action naming the company, an index inclusion, a financing that funds a
  named project. It must be reported as something that HAPPENED or was ANNOUNCED, attributable to a
  date, not something an analyst expects.

Does NOT count (verdict "momentum"):
  the stock rose/fell/soared/surged, analyst price targets, upgrades/downgrades, valuation opinions,
  "investors are optimistic", sector or thematic commentary, a rally explained by other companies'
  news, a retrospective "here's why it's up X%", a listicle of stocks to watch.

If the article only RESTATES an event reported earlier and adds no new development, that is
"momentum" -- the news is the price move, not the event.

Return ONLY fenced JSON:
```json
{"verdict":"event|momentum","date":"YYYY-MM-DD or empty","quote":"<=25 words verbatim from the article naming the event, empty if momentum","confidence":"high|medium|low"}
```"""


def pick(arts, keys, a, b, n, rng):
    hits = []
    for x in arts:
        d = str(x.get("published_date") or "")[:10]
        if not (a <= d <= b):
            continue
        blob = ((x.get("title") or "") + " " + str(x.get("text") or x.get("lede") or ""))[:3000].lower()
        if any(k in blob for k in keys):
            hits.append(x)
    rng.shuffle(hits)
    return hits[:n]


def judge(client, art) -> dict:
    body = str(art.get("text") or art.get("lede") or "")[:2200]
    u = f"TITLE: {art.get('title') or ''}\nPUBLISHED: {str(art.get('published_date') or '')[:10]}\nSOURCE: {art.get('domain') or art.get('url') or ''}\n\n{body}"
    try:
        raw = client.complete(SYSTEM, u, use_web_search=False, label="catalyst_recheck",
                              stage="judge", effort="low")
    except Exception as e:  # noqa: BLE001
        return {"verdict": "error", "err": f"{type(e).__name__}: {e}"}
    import agent
    try:
        return agent._extract(raw) or {"verdict": "unparsed"}
    except Exception:  # noqa: BLE001
        return {"verdict": "unparsed"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=14, help="articles per ticker per window")
    ap.add_argument("--model", default="grok4")
    ap.add_argument("--out", default="data/catalyst_recheck.json")
    a = ap.parse_args()

    rng = random.Random(20260822)
    pool = json.loads((ROOT / "data/backtest_3yr_v5/pool.json").read_text())
    arts = pool.get("articles", pool)
    mid, prov = optimizer.resolve_curator_model(a.model)
    client = llm.make_client(prov, mid)
    print(f"judge: {mid} ({prov})   {a.n} articles per window\n", flush=True)

    out = {}
    from concurrent.futures import ThreadPoolExecutor
    for t, c in CASES.items():
        out[t] = {}
        for win in ("entry", "gap"):
            lo, hi = c[win]
            sel = pick(arts, c["keys"], lo, hi, a.n, rng)
            if not sel:
                print(f"  {t:6} {win:5} no articles"); out[t][win] = []; continue
            with ThreadPoolExecutor(max_workers=7) as ex:
                res = list(ex.map(lambda x: judge(client, x), sel))
            recs = [{"title": s.get("title"), "date": str(s.get("published_date") or "")[:10],
                     "url": s.get("url"), **r} for s, r in zip(sel, res)]
            out[t][win] = recs
            ev = sum(1 for r in recs if r.get("verdict") == "event")
            bad = sum(1 for r in recs if r.get("verdict") in ("error", "unparsed"))
            print(f"  {t:6} {win:5} {lo}..{hi}  n={len(recs):2}  event={ev:2} ({100*ev/max(len(recs)-bad,1):3.0f}%)  bad={bad}", flush=True)
    (ROOT / a.out).write_text(json.dumps(out, indent=1))
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
