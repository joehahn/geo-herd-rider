#!/usr/bin/env python3
"""Can a CHEAP LLM recover the company bundling that GKG gives us for free?

WHY THIS IS FREE. The post-handoff (websearch) era carries no `orgs`, so 90% of what the live
system reads cannot join a company bundle. The pre-handoff (GKG) era carries `orgs` on 84% of
articles -- so it is a LABELLED TEST SET we already own. Run the tagger on pre-handoff articles,
score it against GKG's own extraction, and we learn whether the tagger is good enough BEFORE
spending anything on the era that needs it.

WHAT "GROUND TRUTH" MEANS HERE, precisely: not truth, but AGREEMENT WITH GKG. The operative
question is "would bundling come out the same?", so the target is the canon key `article_orgs`
returns -- the exact string the bundler groups on -- and not a general notion of correctness.
GKG misses things; a tagger that names a company GKG missed scores as a false positive here and
may be right. That is why disagreements are SAMPLED and printed rather than only counted.

Run:  python scripts/validate_org_tagger.py --n 300 --model deepseek/deepseek-chat-v3.2
"""
from __future__ import annotations
import argparse, collections, json, random, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import bootstrap_corpus as bs, orgs as _o, lede as _l, llm as _llm, util as _u  # noqa: E402

_u.load_dotenv()          # OPENROUTER_API_KEY lives in .env, as it does for every other entry point

SYSTEM = (
    "You extract company names from financial news. For each numbered article you are given a "
    "headline and the opening of the body -- exactly what a reader would see in a search result.\n\n"
    "Return the COMPANIES the article is ABOUT: its subject, the firm whose business or stock the "
    "article concerns. Listed or private both count \u2014 the answer is used to group articles by "
    "subject firm, and a private company is as groupable as a listed one. (Requiring 'publicly "
    "traded' here made the tagger return nothing on three SpaceX articles in the first eval, "
    "correctly following the instruction and scoring as a miss.)\n\n"
    "Rules:\n"
    "- A passing mention is not a subject. 'shares fell alongside the S&P' does not make it about "
    "the S&P.\n"
    "- Never return a country, a government body, an exchange, an index, a regulator, a sector, a "
    "commodity, or a person. 'United States', 'NYSE', 'FDA', 'semiconductors', 'copper' are all "
    "wrong answers.\n"
    "- Never return the publisher, the wire service, or the byline.\n"
    "- Return the company's common name, not its ticker: 'Amgen', not 'AMGN'.\n"
    "- MOST ARTICLES HAVE ONE SUBJECT OR NONE. Returning an empty list is the correct answer for a "
    "policy, macro, commodity or market-roundup story. Do not guess to fill the field."
)

def _blocks(arts, max_chars):
    for i, a in enumerate(arts):
        t = (a.get("title") or "").strip()
        b = _l.scout_text(a)[:max_chars].strip()
        yield f"[{i}] HEADLINE: {t}\nBODY: {b}"

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
          "properties": {"items": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": ["i", "companies"],
              "properties": {"i": {"type": "integer"},
                             "companies": {"type": "array", "items": {"type": "string"}}}}}}}

def json_from(text: str) -> dict:
    """Parse the model's reply whether or not the provider honoured a schema.

    AnthropicClient.complete IGNORES json_schema outright (llm.py: "json_schema/search_query/
    before_date are ignored here"), so a judge run on Anthropic comes back as prose wrapped around
    the object and json.loads dies on char 4. Every judge batch failed this way before this existed.
    OpenRouter DOES honour the schema, so this is a no-op there."""
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise
        return json.loads(text[i:j + 1])


def keys_for(names, canon):
    """LLM names -> the SAME canon keys the bundler groups on, via the same normalise+canon path
    article_orgs uses. Comparing raw strings would score 'Amgen Inc.' against 'amgen' as a miss."""
    out = []
    for n in names or []:
        k = _o.normalise(n)
        if not k:
            continue
        k = canon.get(k, k)
        if k not in out:
            out.append(k)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--model", default="deepseek/deepseek-chat-v3.2")
    ap.add_argument("--provider", default="openrouter")
    ap.add_argument("--max-chars", type=int, default=800)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--stratum", default="all", choices=("all", "bundled", "top"),
                    help="which population to draw from: the whole corpus, articles GKG could bundle "
                         "at all, or articles in a company bundle of 2+ that reaches a scout call")
    ap.add_argument("--out", default="data/org_tagger_eval.json")
    ap.add_argument("--adjudicate", action="store_true",
                    help="judge every GKG/tagger disagreement blind — the only way this eval means "
                         "anything, since GKG is a noisy label rather than ground truth")
    ap.add_argument("--judge", default="claude-sonnet-4-6")
    ap.add_argument("--judge-provider", default="anthropic")
    ap.add_argument("--from-detail", action="store_true",
                    help="reuse the cached per-article tagger output and only (re-)run the judge")
    a = ap.parse_args()

    arts, _meta = bs.load()
    H = bs.HANDOFF
    pre = [x for x in arts if (x.get("published_date") or "")[:10] < H and
           ((x.get("title") or "").strip() or _l.scout_text(x).strip())]
    canon = _o.build_canon(arts)
    tmap = _o.ticker_map(arts, canon)
    # STRATA. A uniform draw over the corpus is not the population this capability serves: most of
    # the corpus is tail, while the articles that reach the scout are the ones sitting in real
    # multi-article company bundles. `bundled` = GKG produced a usable key at all; `top` = that key
    # names a bundle of 2+ articles, i.e. one that survives to a scout call and can corroborate.
    if a.stratum != "all":
        _grp = _o.group(arts, canon=canon, tmap=tmap)
        _big = {k for k, v in _grp.items() if len(v) >= 2}
        def _in(x):
            ks = _o.article_orgs(x, canon, tmap)
            return bool(ks) if a.stratum == "bundled" else any(k in _big for k in ks)
        pre = [x for x in pre if _in(x)]
        print(f"stratum {a.stratum!r}: {len(pre):,} of the pre-handoff era qualifies", flush=True)
    random.seed(a.seed)
    samp = random.sample(pre, min(a.n, len(pre)))
    truth = [_o.article_orgs(x, canon, tmap) for x in samp]
    print(f"sample {len(samp)} pre-handoff articles · {sum(1 for t in truth if t)} carry a GKG key "
          f"({100*sum(1 for t in truth if t)/len(samp):.0f}%) · model {a.model}", flush=True)

    got: dict[int, list] = {}
    _cache = Path(a.out.replace(".json", "_detail.json"))
    if a.from_detail and _cache.exists():
        # SAME seed -> same sample -> same order, so index alignment holds. Lets a failed judge pass
        # be retried for the judge's cost alone instead of re-running the tagger over the corpus.
        got = {i: d["llm"] for i, d in enumerate(json.loads(_cache.read_text()))}
        print(f"reusing {len(got)} cached tagger results from {_cache} (no tagger spend)", flush=True)
    # RESOLVE THE ALIAS. This script passes a short name; make_client needs the model id. Fixed in
    # src/org_tagger.py hours ago and not back-ported here, so `--model grok4` 400'd on all 91 calls
    # and the run reported "0 disagreements" rather than "the tagger never ran".
    import org_tagger as _ot
    _mid, _mprov = _ot.resolve(a.model)
    cli = None if got else _llm.make_client(a.provider or _mprov, _mid)
    t0 = time.time()
    for s in ([] if got else range(0, len(samp), a.batch)):
        chunk = samp[s:s + a.batch]
        user = ("\n\n".join(_blocks(chunk, a.max_chars)) +
                f"\n\nReturn one entry per article, i = 0..{len(chunk)-1}.")
        # A DEAD BATCH IS A MISSING ANSWER, NOT AN EMPTY ONE. It used to fall through and leave
        # every article in the batch with no entry, which scored identically to the tagger saying
        # "no company" -- so 2 failed batches out of 6 put ~50 articles into the wrong column and
        # made a run look 15 points worse than it was. Now: retry the batch in smaller pieces, and
        # whatever still has no answer is EXCLUDED from scoring and reported as coverage.
        for _sub, _lo in [(chunk, 0)] if True else []:
            pass
        _todo = [(0, chunk)]
        for _attempt in range(3):
            _next = []
            for _off, _ch in _todo:
                _u = ("\n\n".join(_blocks(_ch, a.max_chars)) +
                      f"\n\nReturn one entry per article, i = 0..{len(_ch)-1}.")
                try:
                    r = cli.complete(SYSTEM, _u, use_web_search=False, label="org-tagger-eval",
                                     stage="scout", json_schema=SCHEMA, effort="low")
                    for it in (json_from(r).get("items") or []):
                        i = int(it.get("i", -1))
                        if 0 <= i < len(_ch):
                            got[s + _off + i] = keys_for(it.get("companies"), canon)
                except Exception as e:  # noqa: BLE001
                    print(f"  batch {s//a.batch}+{_off} attempt {_attempt}: "
                          f"{type(e).__name__}: {str(e)[:90]}", file=sys.stderr)
                    if len(_ch) > 1:                     # split and retry: smaller asks parse better
                        _h = len(_ch) // 2
                        _next += [(_off, _ch[:_h]), (_off + _h, _ch[_h:])]
            if not _next:
                break
            _todo = _next
        print(f"  {min(s+a.batch, len(samp))}/{len(samp)}  {time.time()-t0:.0f}s", flush=True)

    detail = [{"title": x.get("title"), "body": _l.scout_text(x)[:a.max_chars],
               "gkg": sorted(T), "llm": sorted(got.get(i, [])), "answered": i in got}
              for i, (x, T) in enumerate(zip(samp, truth))]
    _miss = [i for i, d in enumerate(detail) if not d["answered"]]
    if _miss:
        print(f"\n  !! {len(_miss)} of {len(detail)} articles got NO tagger answer after retries — "
              f"EXCLUDED from scoring, not counted as an empty answer", flush=True)
    detail = [d for d in detail if d["answered"]]
    tp = fp = fn = 0
    exact = both = 0
    rec_hits = collections.Counter()
    only_llm, only_gkg = [], []
    for i, d0 in enumerate(detail):
        x, T = {"title": d0["title"]}, d0["gkg"]
        G, L = set(T), set(d0["llm"])
        if T:                                   # articles GKG could bundle: the scoreable ones
            both += 1
            tp += len(G & L); fp += len(L - G); fn += len(G - L)
            if G == L:
                exact += 1
            rec_hits["any"] += 1 if (G & L) else 0
            for k in (L - G):
                only_llm.append((x.get("title"), sorted(G), k))
            for k in (G - L):
                only_gkg.append((x.get("title"), k, sorted(L)))
        else:                                   # GKG had nothing: potential RECOVERY, unscoreable
            rec_hits["gkg_blank"] += 1
            if L:
                rec_hits["llm_named"] += 1
                only_llm.append((x.get("title"), [], sorted(L)[0]))

    if not detail:
        print("\n  !! NO ARTICLE GOT A TAGGER ANSWER — nothing to score. The tagger did not run "
              "(model id? key? balance?). Refusing to print an accuracy over an empty set.",
              file=sys.stderr)
        return
    P = tp / max(tp + fp, 1); R = tp / max(tp + fn, 1)
    print(f"\n=== agreement with GKG on the {both} articles GKG could bundle ===")
    print(f"  precision {P:.3f}   recall {R:.3f}   F1 {2*P*R/max(P+R,1e-9):.3f}")
    print(f"  exact same key set:   {exact}/{both} = {100*exact/max(both,1):.1f}%")
    print(f"  at least one key hit: {rec_hits['any']}/{both} = {100*rec_hits['any']/max(both,1):.1f}%")
    print(f"\n=== the {rec_hits['gkg_blank']} articles GKG left blank ===")
    print(f"  tagger named a company on {rec_hits['llm_named']} "
          f"({100*rec_hits['llm_named']/max(rec_hits['gkg_blank'],1):.0f}%) — recovery or noise, "
          f"see the sample below")
    random.seed(5)
    print("\n--- tagger said a company GKG did not (is it a GKG miss, or a tagger error?) ---")
    for t, g, k in random.sample(only_llm, min(12, len(only_llm))):
        print(f"  {str(t)[:74]!r}\n      GKG={g or '[]'}   tagger added: {k}")
    print("\n--- GKG said a company the tagger did not ---")
    for t, k, l in random.sample(only_gkg, min(8, len(only_gkg))):
        print(f"  {str(t)[:74]!r}\n      GKG={k}   tagger={l or '[]'}")
    adj = {}
    if a.adjudicate:
        # WHY THIS PHASE EXISTS. The samples above are not close calls -- GKG labels a Rocket Lab
        # article `spacex` and an Alphabet article `spacex`. Scoring against GKG therefore charges
        # the tagger for being RIGHT, and precision/recall against a noisy label says nothing about
        # whether bundling would improve. So every disagreement goes to a stronger judge that sees
        # the article and two UNLABELLED candidate answers in randomised order. The judge cannot
        # tell which side is GKG, so it cannot be biased toward the incumbent.
        pairs = [(i, d) for i, d in enumerate(detail)
                 if set(d["gkg"]) != set(d["llm"]) and (d["gkg"] or d["llm"])]
        print(f"\n=== adjudicating {len(pairs)} disagreements, blind, with {a.judge} ===", flush=True)
        jcli = _llm.make_client(a.judge_provider, a.judge)
        rnd = random.Random(a.seed + 1)
        flip = {i: rnd.random() < 0.5 for i, _ in pairs}   # True => option A is the TAGGER
        # SCHEMA DELIBERATELY UNUSED. Passing json_schema sets require_parameters:True on the
        # OpenRouter path, and a model without structured-output support then 404s with "No
        # endpoints found" on every batch -- qwen3-235b does exactly that. The Anthropic path
        # ignores the schema outright. So the judge asks for JSON in the prompt and json_from()
        # extracts it, which works on every provider. Kept for documentation of the shape.
        JS = {"type": "object", "additionalProperties": False, "required": ["items"],
              "properties": {"items": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["i", "winner"],
                  "properties": {"i": {"type": "integer"},
                                 "winner": {"type": "string", "enum": ["A", "B", "both", "neither"]}}}}}}
        JSYS = ("You judge which of two automatic taggers better identified the SUBJECT COMPANY of a "
                "news article -- the firm the article is actually about.\n\n"
                "Answer 'A' or 'B' for the better list, 'both' if the two are equally acceptable "
                "namings of the same subject, or 'neither' if both are wrong or the article has no "
                "subject company (a macro, policy, commodity or market-roundup story).\n"
                "An empty list is the CORRECT answer when the article has no subject company. "
                "A country, index, exchange, regulator, sector or commodity is never a subject "
                "company. Judge only from the text shown.")
        for s0 in range(0, len(pairs), a.batch):
            ch = pairs[s0:s0 + a.batch]
            blocks = []
            for i, d in ch:
                A, B = (d["llm"], d["gkg"]) if flip[i] else (d["gkg"], d["llm"])
                blocks.append(f"[{i}] HEADLINE: {d['title']}\nBODY: {d['body'][:500]}\n"
                              f"  Option A: {A or '(no company)'}\n  Option B: {B or '(no company)'}")
            try:
                r = jcli.complete(JSYS, "\n\n".join(blocks) + "\n\nJudge each article by its number. "
                                  'Reply with JSON ONLY, no prose: '
                                  '{"items":[{"i":<article number>,"winner":"A"|"B"|"both"|"neither"}]}',
                                  use_web_search=False, label="org-tagger-judge", stage="scout",
                                  effort="low")   # NO json_schema on purpose -- see JS's comment
                for it in (json_from(r).get("items") or []):
                    i = int(it.get("i", -1)); w = it.get("winner")
                    if i in flip and w:
                        adj[i] = ({"A": "tagger", "B": "gkg"}.get(w, w) if flip[i]
                                  else {"A": "gkg", "B": "tagger"}.get(w, w))
            except Exception as e:  # noqa: BLE001
                print(f"  judge batch {s0//a.batch}: {type(e).__name__}: {str(e)[:110]}", file=sys.stderr)
            print(f"  judged {min(s0+a.batch, len(pairs))}/{len(pairs)}", flush=True)
        v = collections.Counter(adj.values())
        nj = sum(v.values())
        # THE SUMMARY IS ONLY MEANINGFUL IF THE JUDGE ACTUALLY RAN. When every batch failed, this
        # block used to print "tagger right or tied on 33/150 = 22.0%" -- a number computed by
        # treating 117 unjudged disagreements as losses for BOTH sides. Same missing-vs-empty bug
        # as the tagger path had, one level up. Refuse instead.
        if nj < 0.9 * len(pairs):
            print(f"\n  !! JUDGE COVERAGE {nj}/{len(pairs)} — TOO LOW TO REPORT. The accuracy lines "
                  f"are suppressed: an unjudged disagreement is not a loss, and scoring it as one "
                  f"understates BOTH sides. Fix the judge and re-run with --from-detail.",
                  flush=True)
            Path(a.out.replace(".json", "_detail.json")).write_text(json.dumps(
                [{**d, "verdict": adj.get(i)} for i, d in enumerate(detail)], indent=1))
            return
        print(f"\n  of {nj} adjudicated disagreements:")
        for k in ("tagger", "gkg", "both", "neither"):
            print(f"    {k:8} wins {v[k]:4}  ({100*v[k]/max(nj,1):5.1f}%)")
        agree = sum(1 for d in detail if set(d["gkg"]) == set(d["llm"]))
        ok = agree + v["tagger"] + v["both"]
        print(f"\n  === what this actually measures ===")
        print(f"  tagger is right or tied on {ok}/{len(detail)} = {100*ok/len(detail):.1f}% of articles")
        print(f"  GKG    is right or tied on {agree+v['gkg']+v['both']}/{len(detail)} = "
              f"{100*(agree+v['gkg']+v['both'])/len(detail):.1f}%")
        print(f"  (they agree outright on {agree}; {v['neither']} are cases where BOTH are wrong)")
        random.seed(9)
        _t=[(i,detail[i]) for i in adj if adj[i]=="tagger"]; _g=[(i,detail[i]) for i in adj if adj[i]=="gkg"]
        print("\n--- judge preferred the TAGGER ---")
        for i,d in random.sample(_t, min(6,len(_t))):
            print(f"  {str(d['title'])[:70]!r}\n      tagger={d['llm']}   gkg={d['gkg']}")
        print("\n--- judge preferred GKG ---")
        for i,d in random.sample(_g, min(6,len(_g))):
            print(f"  {str(d['title'])[:70]!r}\n      tagger={d['llm']}   gkg={d['gkg']}")
    Path(a.out.replace(".json", "_detail.json")).write_text(json.dumps(
        [{**d, "verdict": adj.get(i)} for i, d in enumerate(detail)], indent=1))
    Path(a.out).write_text(json.dumps(
        {"model": a.model, "n": len(samp), "scoreable": both, "precision": round(P, 4),
         "recall": round(R, 4), "exact": exact, "gkg_blank": rec_hits["gkg_blank"],
         "llm_named_on_blank": rec_hits["llm_named"], "max_chars": a.max_chars,
         "seed": a.seed,
         "judge": a.judge if a.adjudicate else None,
         "adjudicated": {k: v for k, v in collections.Counter(adj.values()).items()}}, indent=1))
    print(f"\nwrote {a.out}")

if __name__ == "__main__":
    main()
