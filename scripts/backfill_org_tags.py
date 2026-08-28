#!/usr/bin/env python3
"""backfill_org_tags.py — fill the org-tagger cache for a corpus. Idempotent, resumable, additive.

There is no difference between "backfill history" and "tag today's pull": both are org_tagger.tag()
over a list of articles, and it only sends the ones that are BOTH missing an org key and absent from
the cache. So running this twice costs nothing the second time, and running it after a new day of
news tags only that day.

The cache is keyed by (model, PROMPT_VERSION) and appended to, never rewritten. Fixing the prompt
means bumping org_tagger.PROMPT_VERSION, which starts a new file and leaves the old answers on disk
to compare against -- the corpus itself is never touched, so no curation is invalidated by tagging.

  python scripts/backfill_org_tags.py --corpus bootstrap --model deepseek4
  python scripts/backfill_org_tags.py --corpus data/backtest_3yr_v5 --model deepseek4
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import orgs as _o, org_tagger as _ot, optimizer as _op, util as _u  # noqa: E402

_u.load_dotenv()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="bootstrap",
                    help="'bootstrap' for the assembled bootstrap corpus, else a path with pool.json")
    ap.add_argument("--model", default=None,
                    help="alias, e.g. deepseek4. Default: org_tagger_model from the forward profile")
    ap.add_argument("--post-handoff-only", action="store_true",
                    help="bootstrap only: skip the GKG half, which already has orgs on 82%%")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="re-tag articles ALREADY in the cache (append-only: the new answer wins, "
                         "the old one stays on disk to diff against)")
    a = ap.parse_args()

    short = a.model or _op.load_financial_model("investor_profile.forward.md").get("org_tagger_model")
    res = _op.resolve_org_tagger_model({"org_tagger_model": short})
    if not res:
        print("org_tagger_model is off and no --model given — nothing to do.\n"
              "Set it in investor_profile.forward.md or pass --model deepseek4.")
        return 1
    model_id, provider = res

    if a.corpus == "bootstrap":
        import bootstrap_corpus as bs
        arts, meta = bs.load()
        if a.post_handoff_only:
            arts = [x for x in arts if (x.get("published_date") or "")[:10] >= meta["handoff"]]
    else:
        pool = json.loads((ROOT / a.corpus / "pool.json").read_text())
        arts = pool.get("articles", pool) if isinstance(pool, dict) else pool
    if a.limit:
        arts = arts[:a.limit]

    canon = _o.build_canon(arts)
    cache = _ot.load_cache(short)
    need = arts if a.refresh else [x for x in arts
                                   if not _o.article_orgs(x, canon, None)
                                   and x.get("url") not in cache]
    print(f"corpus {a.corpus}: {len(arts):,} articles · {len(cache):,} already cached · "
          f"{len(need):,} to tag  ->  {_ot.cache_path(short).name}")
    if a.dry_run or not need:
        print("nothing to do." if not need else "dry run — nothing sent.")
        return 0
    t0 = time.time()
    out = _ot.tag(arts, short, provider, batch=a.batch, workers=a.workers, canon=canon,
                  refresh=a.refresh)
    print(f"\n{json.dumps(out, indent=1)}\n  {time.time()-t0:.0f}s")
    # A RUN THAT ANSWERED NOTHING IS A FAILURE, NOT A QUIET SUCCESS. Every batch failing (a bad
    # model id, a dead key, an expired balance) used to exit 0, so the cron line would log its
    # success message and the tags would simply never appear. Tested with a bogus model: 5 sent,
    # 0 answered, exit 0. Non-zero now, which the cron step reports as "tolerated" -- visible in
    # the log without ever taking the pull down with it.
    if out.get("sent") and not out.get("answered"):
        print(f"  FAILED: {out['sent']} articles sent, none answered — the tagger is not working "
              f"(model/key/balance?). Nothing was cached.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
