"""check_forward_reach.py — for each MISSED ground-truth article (Tavily backtest didn't retrieve it),
test whether the FORWARD engine (Anthropic web_search, restricted to the article's domain) can reach it.

This is a REACHABILITY fact (is the article in Anthropic's index / behind a wall Tavily can't cross), NOT
a recall claim (it does NOT say the forward would surface it unprompted at the time). Look-ahead-safe: we
only check whether a KNOWN article is reachable, we don't do timed discovery. Writes url->bool to
data/gt_forward_reachable.json; the dashboard marks reachable misses as green (forward-reachable) vs orange.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from util import load_dotenv  # noqa: E402

load_dotenv()
import anthropic  # noqa: E402

OUT = REPO / "data" / "gt_forward_reachable.json"
_WS = "web_search_20260209"


def _norm(u: str) -> str:
    p = urlparse(u)
    return (p.netloc.replace("www.", "") + p.path).rstrip("/").lower()


def main():
    res = json.loads((REPO / "data" / "retrieval_backtest.json").read_text())
    missed = {}
    for g, c in res["charts"].items():
        for a in c["ground_truth"]:
            if not a["detected"]:
                missed.setdefault(_norm(a["url"]), a)
    print(f"{len(missed)} distinct missed GT articles to check\n")
    cl = anthropic.Anthropic()
    reach = json.loads(OUT.read_text()) if OUT.exists() else {}
    for a in missed.values():
        if a["url"] in reach:
            continue
        dom = urlparse(a["url"]).netloc.replace("www.", "")
        try:
            r = cl.messages.create(model="claude-sonnet-4-6", max_tokens=700,
                tools=[{"type": _WS, "name": "web_search", "max_uses": 2, "allowed_domains": [dom]}],
                messages=[{"role": "user", "content": f'Search {dom} for this exact article and report its url if found: "{a["title"]}"'}])
            urls = []
            for b in r.content:
                if getattr(b, "type", "") == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                    urls += [_norm(x.url) for x in b.content
                             if getattr(x, "type", "") == "web_search_result" and getattr(x, "url", None)]
            found = _norm(a["url"]) in urls
        except Exception as e:  # noqa: BLE001
            found = False
            print(f"  ERR {dom}: {e}")
        reach[a["url"]] = found
        print(f"  {'REACH' if found else 'no   '} {dom:22} {a['title'][:44]}")
        OUT.write_text(json.dumps(reach, indent=1))   # checkpoint each
    n = sum(1 for v in reach.values() if v)
    print(f"\nforward-reachable: {n}/{len(reach)}  -> {OUT}")


if __name__ == "__main__":
    main()
