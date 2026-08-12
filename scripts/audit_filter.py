#!/usr/bin/env python3
"""audit_filter.py — put eyeballs on what the ingest funnel THREW AWAY.

`gkg.pool()` drops rows with only a counter, so the funnel tells you how many articles died at each
stage but nothing about whether they deserved to. A filter nobody audits is a filter trusted blindly,
and the dangerous error is asymmetric: dropping junk costs nothing, dropping one early gem article
costs the whole thesis.

This replays the SAME filters over the CACHED BigQuery rows, so it is free (no BigQuery, no LLM, no
network) and reproducible. It captures what each stage removed and prints a stratified sample for a
human read, plus the full set to JSON for a deeper look.

  python scripts/audit_filter.py --stage spam --sample 60
  python scripts/audit_filter.py --stage all --out data/backtest_1yr/dropped.json

STAGES (in funnel order; each sees only what earlier stages passed):
  blocklist  domain on the profile's mill_block
  spam       headline matched a bot/listicle pattern in retrieval_config.spam_title_patterns
  no_beat    the keyword regex matched somewhere in the GKG Extras blob, but not in this article's
             own headline or URL
  no_org     GKG extracted no SUBJECT company within engine.ontopic_offset characters
  undated    no parseable publish date (these are silently skipped by pool(), uncounted)

Read the sample asking one question: WOULD I HAVE WANTED THIS ARTICLE? A stage is behaving when its
drops are boilerplate you would never trade on. Anything that reads like real reporting on a named
company is a false positive and the pattern needs narrowing.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import gkg  # noqa: E402

STAGES = ("blocklist", "spam", "no_beat", "no_org", "undated")


def window_files(cache_dir: Path, start: str, end: str, chunk_days: int = 7) -> list[Path]:
    """The row files for exactly the chunks `gkg.pool(start, end, chunk_days)` would have queried.

    NECESSARY, not tidiness: the cache accumulates rows from every ingest ever run, and those windows
    OVERLAP -- a 14-day probe and an 8-week backfill both sit inside the 1-year window, so filtering
    by date alone silently audits a superset. (Measured: replaying every file reported 7,132 spam
    drops where the 1-year run actually dropped 4,302.) Reconstructing the exact chunk boundaries is
    the only way the audit matches the funnel it claims to explain."""
    import pandas as pd
    edges = list(pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq=f"{chunk_days}D"))
    if not edges or edges[-1] < pd.Timestamp(end):
        edges.append(pd.Timestamp(end))
    want = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i].date().isoformat(), edges[i + 1].date().isoformat()
        if lo == hi:
            continue
        hits = sorted(cache_dir.glob(f"rows-{lo}-{hi}-*.json"))
        if hits:
            want.append(hits[-1])
    return want


def replay(cache_dir: Path, profile: str | None = None, files: list[Path] | None = None) -> dict[str, list[dict]]:
    """Re-run the funnel over every cached row file, returning {stage: [dropped article, ...]}.

    Mirrors gkg.pool()'s filter ORDER exactly -- an article dropped by an earlier stage is never
    tested by a later one, which is what makes the funnel's counts conditional. Any divergence here
    would make the audit a lie, so this deliberately reuses gkg's own predicates rather than
    reimplementing them."""
    eng = gkg.config()["engine"]
    stoplist = {s.lower() for s in gkg.config()["org_stoplist"]}
    blocked = gkg._mill_block(profile)
    matchers = gkg._beat_matchers()
    out: dict[str, list[dict]] = {s: [] for s in STAGES}
    kept = 0

    files = files if files is not None else sorted(cache_dir.glob("rows-*.json"))
    if not files:
        sys.exit(f"no cached rows under {cache_dir} -- run scripts/ingest.py first")
    for i, f in enumerate(files, 1):
        for r in json.loads(f.read_text()):
            url = r.get("DocumentIdentifier") or ""
            if not url:
                continue
            src = (r.get("SourceCommonName") or "").lower()
            title = gkg._page_title(r.get("Extras")) or gkg._slug_title(url)
            rec = {"title": title, "source": src, "url": url,
                   "date": gkg._gkg_date(r.get("DATE"))}
            if gkg._domain_in(src, blocked):
                out["blocklist"].append(rec); continue
            if not title or gkg._spam_title(title):
                out["spam"].append(rec); continue
            hay = f"{title} {url}"
            if not any(p.search(hay) for _, p in matchers):
                out["no_beat"].append(rec); continue
            if not gkg._subject_orgs(r.get("V2Organizations"), eng["ontopic_offset"], stoplist) \
                    and not gkg._names_ticker(title):     # mirror pool()'s named-ticker rescue
                # DIAGNOSTIC: three different things look identical from the outside -- GDELT found no
                # organisation at all; it found one but too deep in the article (beyond ontopic_offset);
                # or every one it found was on the stoplist. They imply completely different fixes, so
                # record which happened rather than reporting one undifferentiated count.
                raw = r.get("V2Organizations") or ""
                parts = [x for x in raw.split(";") if "," in x]
                deep, stopped = [], []
                for part in parts:
                    nm, off = part.rsplit(",", 1)
                    nm = nm.strip()
                    if not off.strip().isdigit():
                        continue
                    if int(off) > eng["ontopic_offset"]:
                        deep.append((nm, int(off)))
                    elif len(nm) < 4 or any(s in nm.lower() for s in stoplist):
                        stopped.append(nm)
                rec["why"] = ("no_org_at_all" if not parts else
                              "all_beyond_offset" if deep and not stopped else
                              "all_stoplisted" if stopped and not deep else
                              "mixed_deep_and_stoplisted")
                rec["orgs_beyond_offset"] = [f"{n}@{o}" for n, o in deep[:4]]
                rec["orgs_stoplisted"] = stopped[:4]
                out["no_org"].append(rec); continue
            if not rec["date"]:
                out["undated"].append(rec); continue
            kept += 1
        print(f"  replayed {i}/{len(files)} row files", end="\r", file=sys.stderr, flush=True)
    print(f"  replayed {len(files)} row files; {kept:,} would be kept (pre-dedup/syndication)",
          file=sys.stderr)
    return out


def which_pattern(title: str) -> str:
    """Which spam pattern fired -- so a false positive names the rule that has to change."""
    for pat in gkg.config().get("spam_title_patterns", []):
        if re.search(pat, title or ""):
            return pat
    return "(none)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="spam", choices=(*STAGES, "all"))
    ap.add_argument("--sample", type=int, default=60, help="headlines to print for a human read")
    ap.add_argument("--cache-dir", default="data/gkg_cache")
    ap.add_argument("--out", default=None, help="write the full dropped set to this json")
    ap.add_argument("--seed", type=int, default=0, help="fixed so the sample is reproducible")
    ap.add_argument("--start", default="2025-07-04", help="scope to one ingest's chunks (see window_files)")
    ap.add_argument("--end", default="2026-07-03")
    ap.add_argument("--chunk-days", type=int, default=7)
    a = ap.parse_args(argv)

    cdir = ROOT / a.cache_dir
    files = window_files(cdir, a.start, a.end, a.chunk_days)
    print(f"scoped to {len(files)} chunk files for {a.start}..{a.end}", file=sys.stderr)
    dropped = replay(cdir, files=files)
    print("\nDROPPED PER STAGE (conditional: each stage sees only earlier stages' survivors)")
    for s in STAGES:
        print(f"  {len(dropped[s]):>8,}  {s}")

    if a.out:
        Path(a.out).write_text(json.dumps({k: v for k, v in dropped.items()}, default=str))
        print(f"\nfull set -> {a.out}")

    stages = STAGES if a.stage == "all" else (a.stage,)
    rng = random.Random(a.seed)
    for s in stages:
        rows = dropped[s]
        if not rows:
            continue
        print(f"\n{'=' * 100}\n{s.upper()}: {len(rows):,} dropped — random sample of "
              f"{min(a.sample, len(rows))}\n{'=' * 100}")
        by_src = collections.Counter(r["source"] for r in rows)
        print(f"top sources: {', '.join(f'{k} ({v})' for k, v in by_src.most_common(6))}\n")
        for r in rng.sample(rows, min(a.sample, len(rows))):
            print(f"  [{r['source'][:26]:26s}] {r['title'][:96]}")
            if s == "spam":
                print(f"      rule: {which_pattern(r['title'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
