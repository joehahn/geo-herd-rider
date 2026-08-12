#!/usr/bin/env python3
"""audit_extractor.py — is `no_text_on_page` a paywall, or our extractor failing?

`no_text_on_page` is the only INFERRED label in the lede-miss breakdown. Every other reason is a
fact the server told us (404, 403, 429). This one means the server returned 200 OK and
`wayback._extract_lede` found no prose -- and from the outside, an extractor bug looks exactly like
a genuine paywall interstitial. The distinction matters far beyond these articles: the same
extractor runs on all 38,896, so a systematic parse failure here implies silent losses everywhere.

Re-fetches a sample, KEEPS the raw HTML, and asks a model to read the page and say which it is.
The model is given the page text only -- never told what our extractor concluded, or that a filter
was involved -- so it cannot simply agree with us.

    python scripts/audit_extractor.py --limit 150
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import lede as lede_mod  # noqa: E402
import llm  # noqa: E402
import wayback  # noqa: E402
from optimizer import load_financial_model, resolve_stage_models  # noqa: E402
from util import load_dotenv  # noqa: E402

SYSTEM = """You are shown the visible text of a web page, with markup stripped. Decide what kind of
page it is.

ARTICLE  = a news story is present. The body may be short, truncated, or preceded by navigation
           junk, but actual reporting prose about an event is there to read.
WALL     = no reporting is present. The page is a paywall or subscription prompt, a cookie/consent
           interstitial, a bot-check, a login screen, a "page not found", or pure navigation and
           boilerplate with no story.

Judge only what you can see. If reporting prose is present anywhere in the text, answer ARTICLE.

Return ONLY JSON: {"v":[{"i":1,"a":"ARTICLE","w":"3-8 word reason"},...]}, one entry per page."""


def visible_text(html: str, cap: int = 1200) -> str:
    """Markup-stripped page text -- what a reader would see, which is what the judge should rule on."""
    h = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", html or "")
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(h)).strip()[:cap]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default="data/backtest_1yr/pool.json")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="data/backtest_1yr/judge_extractor.json")
    a = ap.parse_args(argv)
    load_dotenv()

    arts = json.loads((ROOT / a.pool).read_text())["articles"]
    pool = [x for x in arts if x.get("text_miss") == "no_text_on_page"]
    print(f"{len(pool):,} articles labelled no_text_on_page", flush=True)
    rows = random.Random(a.seed).sample(pool, min(a.limit, len(pool)))

    print(f"re-fetching {len(rows)} pages (raw HTML kept) ...", flush=True)
    def grab(x):
        h = lede_mod._fetch_html(x["url"])
        return {**x, "html_len": len(h or ""), "text": visible_text(h) if h else "",
                # what OUR extractor gets now -- if this is non-empty the label was simply stale
                "extract_now": (wayback._extract_lede(h) or "") if h else ""}
    with ThreadPoolExecutor(max_workers=16) as ex:
        got = [r for r in ex.map(grab, rows) if r["text"]]
    print(f"  {len(got)} pages fetched with visible text ({len(rows) - len(got)} unreachable now)",
          flush=True)
    recovered = [g for g in got if g["extract_now"]]
    if recovered:
        print(f"  NOTE {len(recovered)} now extract fine on a retry -- transient, not a parse failure")

    todo = [g for g in got if not g["extract_now"]]
    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    _, (eid, eprov) = resolve_stage_models(fm)
    cli = llm.make_client(eprov, eid)
    print(f"judging {len(todo)} pages our extractor still cannot parse -> {eid}", flush=True)

    N = 8                                    # page text is long; keep batches small
    batches = [todo[i:i + N] for i in range(0, len(todo), N)]
    verdicts: list = [None] * len(todo)
    def one(bi):
        b = batches[bi]
        body = "\n\n".join(f"{i + 1}. {r['text'][:900]}" for i, r in enumerate(b))
        try:
            txt = cli.complete(SYSTEM, body, use_web_search=False, label=f"extract-{bi}",
                               stage="agent", effort="low")
            s = txt[txt.index("{"):txt.rindex("}") + 1]
            for v in json.loads(s).get("v", []):
                k = int(v.get("i", 0)) - 1
                if 0 <= k < len(b):
                    verdicts[bi * N + k] = {"a": str(v.get("a", "")).upper(), "w": v.get("w", "")}
        except Exception as e:  # noqa: BLE001
            print(f"    batch {bi} failed ({type(e).__name__})", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(one, range(len(batches))))

    judged = [(t, v) for t, v in zip(todo, verdicts) if v]
    art = [(t, v) for t, v in judged if v["a"] == "ARTICLE"]
    print(f"\n{'=' * 92}")
    print(f"no_text_on_page audit: {len(pool):,} labelled · {len(rows)} sampled · {len(judged)} judged")
    print(f"  EXTRACTOR FAILURE (real article we could not parse): {len(art):,} "
          f"({100 * len(art) / max(len(judged), 1):.1f}%)")
    print(f"  genuine wall (paywall/consent/bot-check)           : {len(judged) - len(art):,} "
          f"({100 * (len(judged) - len(art)) / max(len(judged), 1):.1f}%)")
    if recovered:
        print(f"  transient (extracts fine on retry)                : {len(recovered):,}")
    print(f"\n  -> implied corpus-wide extractor loss: "
          f"~{int(len(pool) * len(art) / max(len(judged), 1)):,} articles")
    for t, v in art[:12]:
        print(f"\n  [{t.get('source','')[:24]:24s}] {str(t.get('title'))[:78]}")
        print(f"       {v['w']}  ::  {t['text'][:140]}")
    Path(ROOT / a.out).write_text(json.dumps(
        {"n_labelled": len(pool), "n_sampled": len(rows), "n_judged": len(judged),
         "extractor_failures": len(art), "transient": len(recovered),
         "failure_rate_pct": round(100 * len(art) / max(len(judged), 1), 1),
         "examples": [{"title": t.get("title"), "source": t.get("source"), "url": t.get("url"),
                       "why": v["w"], "text": t["text"][:300]} for t, v in art]}, indent=1))
    print(f"\n-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
