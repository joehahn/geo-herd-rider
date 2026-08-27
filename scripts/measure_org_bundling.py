#!/usr/bin/env python3
"""Would an LLM org tagger actually restore COMPANY BUNDLING in the post-handoff era?

Accuracy is not the question -- validate_org_tagger.py answered that (82% right-or-tied against
GKG's 29%, adjudicated blind). The question this answers is the MECHANISM one CLAUDE.md demands:
does tagging CHANGE WHAT THE SCOUT SEES? A tagger that is perfectly accurate but yields 500
one-article companies has restored nothing, because a bundle of one corroborates nothing and is
demoted to the beat path anyway.

So this measures the thing that decides it: how many post-handoff articles move OFF the beat/orphan
path INTO a company bundle of 2+, against today's baseline of ~10% company-key coverage.

DECISION RULE, fixed BEFORE the run so it cannot be fitted afterwards (non-negotiable #5):
    coverage into 2+ bundles  >50%  -> propose the profile knob and re-curate
                              <30%  -> close it; the beat path is the right home
    in between               -> report and decide with the size distribution in hand

Run:  python scripts/measure_org_bundling.py --model deepseek/deepseek-v4-flash
"""
from __future__ import annotations
import argparse, collections, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap_corpus as bs, orgs as _o, lede as _l, llm as _llm, util as _u  # noqa: E402
from validate_org_tagger import SYSTEM, SCHEMA, _blocks, keys_for, json_from  # noqa: E402

_u.load_dotenv()
# EXPLICIT TICKERS ARE FREE AND NEAR-PERFECT, so the tagger is a UNION partner, never a replacement:
# 23% of post-handoff articles name a symbol outright and that path costs nothing and cannot
# hallucinate a company. Measured separately below so its contribution is visible, not assumed.
TICK = re.compile(r'\(([A-Z]{1,5})\)|\b(?:NASDAQ|NYSE|NYSEARCA|AMEX|OTC|TSX)\s*:\s*([A-Z]{1,5})\b')

def sym_keys(a):
    t = (a.get("title") or "") + " " + _l.scout_text(a)
    return {g for m in TICK.finditer(t) for g in m.groups() if g}

def dist(bundles, arts_n, label):
    sz = collections.Counter(len(v) for v in bundles.values())
    big = {k: v for k, v in bundles.items() if len(v) >= 2}
    in2 = len({id(x) for v in big.values() for x in v})
    print(f"\n  {label}")
    print(f"    companies identified      {len(bundles):5}   of which 2+ articles: {len(big)}")
    print(f"    articles in a 2+ bundle   {in2:5}  = {100*in2/max(arts_n,1):5.1f}% of the window")
    print(f"    bundle sizes: " + "  ".join(f"{k}:{sz[k]}" for k in sorted(sz)[:8])
          + (f"  ... max {max(sz)}" if sz else ""))
    if big:
        top = sorted(big.items(), key=lambda kv: -len(kv[1]))[:8]
        print(f"    biggest: {[(k, len(v)) for k, v in top]}")
    return in2

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek/deepseek-v4-flash")
    ap.add_argument("--provider", default="openrouter")
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--max-chars", type=int, default=800)
    ap.add_argument("--limit", type=int, default=0, help="0 = the whole post-handoff window")
    ap.add_argument("--out", default="data/org_bundling_effect.json")
    a = ap.parse_args()

    arts, _m = bs.load()
    H = bs.HANDOFF
    post = [x for x in arts if (x.get("published_date") or "")[:10] >= H]
    if a.limit:
        post = post[:a.limit]
    canon = _o.build_canon(arts)
    tmap = _o.ticker_map(arts, canon)
    N = len(post)
    print(f"post-handoff window: {N:,} articles  ({H} .. )  model {a.model}", flush=True)

    # --- BASELINE: what the live system does today -------------------------------------------
    base = collections.defaultdict(list)
    for x in post:
        for k in _o.article_orgs(x, canon, tmap):
            base[k].append(x)
    b_in2 = dist(base, N, "BASELINE — article_orgs() as the live system runs it")

    # --- the free half, measured on its own ---------------------------------------------------
    only_sym = collections.defaultdict(list)
    for x in post:
        for k in sym_keys(x):
            only_sym[k].append(x)
    dist(only_sym, N, "explicit ticker regex alone (free, no LLM)")

    # --- the tagger ---------------------------------------------------------------------------
    cli = _llm.make_client(a.provider, a.model)
    got: dict[int, list] = {}
    t0 = time.time()
    for s in range(0, N, a.batch):
        todo = [(0, post[s:s + a.batch])]
        for _att in range(3):                     # missing != empty; see validate_org_tagger
            nxt = []
            for off, ch in todo:
                u = ("\n\n".join(_blocks(ch, a.max_chars)) +
                     f"\n\nReturn one entry per article, i = 0..{len(ch)-1}.")
                try:
                    r = cli.complete(SYSTEM, u, use_web_search=False, label="org-bundling",
                                     stage="scout", json_schema=SCHEMA, effort="low")
                    for it in (json_from(r).get("items") or []):
                        i = int(it.get("i", -1))
                        if 0 <= i < len(ch):
                            got[s + off + i] = keys_for(it.get("companies"), canon)
                except Exception as e:  # noqa: BLE001
                    print(f"  batch {s}+{off} att{_att}: {type(e).__name__}: {str(e)[:80]}",
                          file=sys.stderr)
                    if len(ch) > 1:
                        h = len(ch) // 2
                        nxt += [(off, ch[:h]), (off + h, ch[h:])]
            if not nxt:
                break
            todo = nxt
        if (s // a.batch) % 20 == 0:
            print(f"  {min(s+a.batch,N)}/{N}  {time.time()-t0:.0f}s", flush=True)

    miss = [i for i in range(N) if i not in got]
    print(f"\n  tagger answered {N-len(miss):,}/{N:,} articles"
          + (f"  ({len(miss)} UNANSWERED — excluded, not counted as empty)" if miss else ""))

    tag = collections.defaultdict(list)
    for i, x in enumerate(post):
        for k in got.get(i, []):
            tag[k].append(x)
    t_in2 = dist(tag, N, "TAGGER alone")

    uni = collections.defaultdict(list)
    for i, x in enumerate(post):
        ks = set(got.get(i, [])) | {canon.get(_o.normalise(s), _o.normalise(s))
                                    for s in sym_keys(x) if _o.normalise(s)}
        for k in ks:
            uni[k].append(x)
    u_in2 = dist(uni, N, "UNION (tagger + explicit tickers) — the proposed configuration")

    print(f"\n=== THE DECISION NUMBER ===")
    print(f"  articles reaching a company bundle of 2+ (what the scout can corroborate):")
    print(f"    today            {b_in2:5}  = {100*b_in2/N:5.1f}%")
    print(f"    with the tagger  {u_in2:5}  = {100*u_in2/N:5.1f}%   "
          f"({(u_in2/max(b_in2,1)):.1f}x)")
    rule = ("PROPOSE the knob and re-curate" if u_in2 / N > 0.50 else
            "CLOSE it — the beat path is the right home" if u_in2 / N < 0.30 else
            "IN BETWEEN — decide with the size distribution")
    print(f"  pre-registered rule says: {rule}")
    Path(a.out).write_text(json.dumps(
        {"n": N, "model": a.model, "baseline_in2": b_in2, "tagger_in2": t_in2,
         "union_in2": u_in2, "baseline_pct": round(100*b_in2/N, 2),
         "union_pct": round(100*u_in2/N, 2), "unanswered": len(miss),
         "verdict": rule}, indent=1))
    print(f"\nwrote {a.out}")

if __name__ == "__main__":
    main()
