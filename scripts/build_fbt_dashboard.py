#!/usr/bin/env python3
"""build_fbt_dashboard.py — the Firehose Backtest (FBT) dashboard: docs/fbt.html

EYEBALLS ON THE TOP OF THE FUNNEL. This page judges the NEWS GATHERING, upstream of any LLM. Nothing
here involves the curator, the optimizer, or a return: it answers "is the raw material any good?"
before a single token is spent reasoning over it. Every panel is built to make a WEAKNESS visible —
each carries an explicit verdict badge, and the page leads with what is going badly.

Named for GHR's own vocabulary: this repo says FIREHOSE (117 uses in code and docs) and
never 'retriever' (0 uses) -- that was PWR's word, imported by mistake when this page was
first built. Borrowed from PWR's equivalent (docs/retrieval_pwr.html): the completeness-over-time framing (per month /
week / day / day-of-week), lede-source composition, source utilization, per-beat productivity, and
per-author bylines. Added for GHR, because our funnel differs:
  - the INGEST FUNNEL itself (rows scanned -> each filter -> corpus), which is where GHR's
    theme/org/syndication gating lives and PWR has no equivalent of;
  - TEXT COVERAGE BY AGE, because GHR's fast-lede arm decays with article age and PWR's
    archive-only corpus does not;
  - SYNDICATION, because collapsing duplicates is how GHR isolates the single-outlet (under-the-
    radar) band that its whole thesis rests on.

Reads data/<run>/pool.json + retrieval_stats.json. Render-only: no LLM, no network, no cost, so the
retrieval-iteration loop (edit retrieval_config.json -> re-ingest from cache -> re-render) is free.

    python scripts/build_fbt_dashboard.py                 # the canonical corpus -> docs/fbt.html
    python scripts/build_fbt_dashboard.py --run data/backtest_1yr \\
        --out docs_preview/fbt_1yr.html                   # any other corpus: NOT to docs/
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))       # gkg: specialty/blocklist domain matching
sys.path.insert(0, str(ROOT / "scripts"))   # dash_nav: shared cross-page nav

import dash_nav  # noqa: E402  shared cross-page nav (Backtest | Bootstrap | Forwardtest)
import provenance as _canon  # noqa: E402  canonical-inputs gate
import gkg as _gkg  # noqa: E402  specialty/blocklist domain matching, shared with the pipeline
import orgs as _orgs  # noqa: E402  entity normalisation + grouping, the same code the scout uses

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"
README = "https://github.com/joehahn/geo-herd-rider/blob/main/README.md"
_REPO = "https://github.com/joehahn/geo-herd-rider/blob/main"
CONFIG_URL = f"{_REPO}/retrieval_config.json"
PROFILE_URL = f"{_REPO}/investor_profile.backtest.md"
GKG_URL = f"{_REPO}/src/gkg.py"
SOURCES_URL = f"{_REPO}/news_sources.md"


def _LINK(href: str, label: str) -> str:
    return f'<a href="{href}"><code>{label}</code></a>' 

# Validated with the dataviz skill's checker (scripts/validate_palette.js).
# categorical light #2a78d6/#eb6834/#1baf7a -> all checks pass (worst adjacent CVD dE 9.2)
# categorical dark  #3987e5/#d95926/#199e70 -> all checks pass (worst adjacent CVD dE 9.4)
# ordinal blue 5-step -> monotone L, all adjacent dL >= 0.06, light end 2.06:1 vs surface
LIGHT = {"surface": "#fcfcfb", "text": "#0b0b0b", "text2": "#52514e", "grid": "#e6e5e1",
         "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a",
         "ord": ["#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"]}
DARK = {"surface": "#1a1a19", "text": "#ffffff", "text2": "#c3c2b7", "grid": "#33322f",
        "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70",
        "ord": ["#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"]}
# Status palette is FIXED (never themed) and always ships with a label, never colour alone.
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _gem_beats() -> set:
    cfg = json.loads((ROOT / "retrieval_config.json").read_text())
    return {b["query"] for b in cfg["gem_beats"]}


def _load(run: Path):
    arts = json.loads((run / "pool.json").read_text())
    meta = {k: v for k, v in arts.items() if k != "articles"} if isinstance(arts, dict) else {}
    arts = arts.get("articles", arts) if isinstance(arts, dict) else arts
    stats = {}
    sp = run / "retrieval_stats.json"
    if sp.exists():
        stats = json.loads(sp.read_text())

    # LIVE OVERLAY of an in-flight Wayback backfill. pool.json is only rewritten when a backfill pass
    # FINISHES -- days away for a full corpus -- but wayback_cache.json is checkpointed every 20
    # lookups. Reading the cache directly means every FBT rebuild shows the backfill's CURRENT state
    # instead of a snapshot frozen at the last completed pass. fetch=False: apply what is cached, never
    # hit the network (this dashboard stays render-only and free).
    wb = run / "wayback_cache.json"
    if wb.exists():
        before = sum(1 for a in arts if a.get("lede"))
        try:
            import lede as _lede
            _lede.enrich_wayback(arts, meta.get("end") or "", cache_path=str(wb),
                                 fetch=False, per_article=True)
        except Exception as e:  # noqa: BLE001 -- a cache mid-write is expected; show the stale view
            print(f"  wayback overlay skipped ({type(e).__name__}: {e})", file=sys.stderr)
        stats["wayback_overlay"] = _backfill_progress(wb, arts, sum(1 for a in arts if a.get("lede")) - before)
    return arts, meta, stats


def _backfill_progress(wb: Path, arts: list, newly_applied: int) -> dict:
    """How far the in-flight coverage backfill has got. `target` is the job's actual scope: it runs
    --misses-only, so it is chasing articles with NO text at all, not the whole corpus."""
    cache = json.loads(wb.read_text())
    resolved = len(cache)
    hits = sum(1 for v in cache.values() if v)
    target = sum(1 for a in arts if not a.get("lede") and not a.get("lede_live"))
    return {"resolved": resolved, "hits": hits,
            "hit_pct": round(100 * hits / resolved, 1) if resolved else 0.0,
            "remaining": target, "newly_applied": newly_applied,
            "mtime": datetime.fromtimestamp(wb.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "running": (datetime.now().timestamp() - wb.stat().st_mtime) < 600}


# --------------------------------------------------------------------------- panel data
def audits(run: Path) -> dict:
    """Blind-judge verdicts per stage, from scripts/judge_dropped.py --strong-only.

    WITHOUT THESE THE FUNNEL IS A VOLUME CHART. It shows a stage removing 80,026 articles and says
    nothing about whether it was right to -- and for two stages the answer is "43% of the time, no".
    A dashboard that reports what a filter DID but never whether it did it WELL is only doing half
    the job it exists for. Missing files degrade to no annotation rather than a crash."""
    import json as _j
    out = {}
    for f in run.glob("rebase_*.json"):
        try:
            d = _j.loads(f.read_text())
            out[d.get("stage", f.stem)] = d.get("fp_rate_pct")
        except Exception:  # noqa: BLE001
            continue
    fn = run / "judge_corpus_fn_strong.json"
    if fn.exists():
        try:
            out["_corpus_junk"] = _j.loads(fn.read_text()).get("junk_rate_pct")
        except Exception:  # noqa: BLE001
            pass
    return out


def funnel_rows(stats: dict, n_corpus: int, run: Path) -> list[tuple[str, int, str]]:
    """(stage, articles remaining, note) down the WHOLE ingest funnel.

    STARTS AT THE TOP, not at the first Python filter. Three filters run inside the BigQuery WHERE
    clause and therefore never reach a counter: English-origin, the market-theme gate, and the beat
    keyword match. Together they discard 99.8% of GKG, and the largest of them -- English-origin --
    appeared nowhere in this dashboard at all until it was asked for. A funnel that begins after its
    three biggest stages is not a funnel, it is a flattering excerpt.

    Those three counts are SCALED from sampled weeks (prefilter_scale.json) because counting them
    across the full year would re-scan the wide theme column. The scaling is validated: its beat-
    keyword estimate lands within 0.5% of the exactly-measured row count, which is the first bar the
    engine actually reports.

    RECONCILED, as before: rows with no URL, no parseable date, or a URL already seen are skipped
    without a counter, so the residual is shown rather than left to make the arithmetic fail."""
    import json as _json
    g = stats.get("gkg", {})
    rows = int(g.get("rows_scanned", 0))
    if not rows:
        return []
    out: list[tuple[str, int, str]] = []
    pre = run / "prefilter_scale.json"
    if pre.exists():
        s = _json.loads(pre.read_text())
        out += [
            ("GKG rows in the window", s["all"], "every article GDELT indexed, all languages (est.)"),
            ("− non-English origin", s["en"], f"{s['all'] - s['en']:,} dropped (est.) · TranslationInfo"),
            ("− no market theme", s["theme"], f"{s['en'] - s['theme']:,} dropped (est.) · engine.market_themes"),
            ("− no beat keyword in title/URL", s["kw"],
             f"{s['theme'] - s['kw']:,} dropped (est.) · beat keywords"),
        ]
    out.append(("GKG rows scanned", rows, "exact, from here down"))
    aud = audits(run)
    for name, n, src, key in [
            ("mill_block domain", g.get("dropped_blocklist", 0), "investor profile", "blocklist"),
            # "bot/listicle" named 2 of the 17 hard patterns. EIGHT are institutional-filing churn
            # (13F, Form 4, "buys N shares of", "stake raised by", "grows position in"), and the rest
            # are earnings transcripts, chart-signal templates and quote pages. The label now says so.
            ("boilerplate headline", g.get("dropped_spam", 0),
             "listicles · 13F/insider filings · earnings transcripts · chart signals", "spam"),
            # "beat only in Extras" named a GDELT column, which means nothing without the schema. What
            # it does: BigQuery can only grep the whole page blob, so it keeps a row if a beat word
            # appears ANYWHERE on the page -- a sidebar link, a related-stories strip. Python then
            # re-checks against this article's own headline + URL, and drops it if the word was only
            # page furniture. Coarse query, exact re-check; this bar is where the over-fetch is repaid.
            ("beat word not in the headline or URL", g.get("dropped_no_beat", 0),
             "beat keywords, re-checked against the article's own title (BigQuery matched the whole page)",
             "no_beat"),
            ("no subject company", g.get("dropped_no_org", 0), "ontopic_offset + org_stoplist", "no_org")]:
        rows -= int(n)
        fp = aud.get(key)
        # the WRONGLY-dropped count is the number that matters; a bare FP % under-reads on a big stage
        note = f"{int(n):,} dropped · {src}"
        if fp is not None:
            note += f" · {fp:.0f}% wrongly ⇒ ~{int(int(n) * fp / 100):,} real lost"
        out.append((f"− {name}", rows, note))
    syn = int(g.get("dropped_syndicated", 0))
    resid = rows - syn - n_corpus
    if resid:
        out.append(("− duplicate URL / undated", rows - resid, f"{resid:,} dropped (uncounted residual)"))
        rows -= resid
    resc = int(g.get("rescued_named_ticker", 0))
    out.append(("= CORPUS (after syndication collapse)", rows - syn,
                f"{syn:,} syndicated copies folded" + (f" · {resc:,} rescued by named ticker" if resc else "")))
    return out




def verdicts(arts, stats, gem, n_beats: int, aud: dict) -> list[dict]:
    """The headline judgements. Each is (label, value, status, why) -- the page leads with these so a
    reader sees what is WRONG before they see a pretty time series."""
    n = len(arts)
    days = {a.get("published_date", "")[:10] for a in arts if a.get("published_date")}
    lede = sum(1 for a in arts if a.get("lede_live") or a.get("lede"))
    auth = sum(1 for a in arts if a.get("author"))
    beats = collections.Counter(q for a in arts for q in (a.get("queries") or []))
    gem_n = sum(beats.get(q, 0) for q in gem)
    srcs = collections.Counter(a.get("source", "") for a in arts)
    # SPECIALTY-DESK REACH. These are the outlets hand-picked in the profile as the ones carrying the
    # early call; the forward path restricts a whole search pass to them. GKG cannot do that (BigQuery
    # has no per-domain pass), so here they are simply measured: how much of the corpus each supplied,
    # INCLUDING the ones that supplied nothing. Omitting the zeroes would hide the finding.
    _spec = [s.lower() for s in _gkg._specialty()]
    _seen = {a.get("source", "").lower() for a in arts}
    spec_counts = []
    for d in _spec:
        n_d = sum(v for s, v in srcs.items() if _gkg._domain_in(s, [d]))
        spec_counts.append((d, n_d))
    spec_counts.sort(key=lambda kv: kv[1])
    spec_total = sum(n for _, n in spec_counts)
    top10 = 100 * sum(c for _, c in srcs.most_common(10)) / max(n, 1)
    # CLEAN (WAYBACK) TEXT. `lede` is the as-of-date archived body; `lede_live` is today's page,
    # which is look-ahead BIASED. backtest_gdelt.py marks the clean arm as the ONLY one whose numbers
    # are quotable, so this share is the ceiling on how much of any backtest figure is defensible --
    # which makes it worth a tile of its own.
    clean_n = sum(1 for a in arts if a.get("lede"))
    clean_pct = 100 * clean_n / max(n, 1)

    def st(v, good, warn):      # higher is better
        return "good" if v >= good else ("warning" if v >= warn else "critical")

    return [
        dict(label="Corpus", value=f"{n:,}", sub=f"{len(days)} days · {n / max(len(days), 1):.0f}/day",
             status="good", why="Articles kept after every filter, and how many that is per day."),
        # "Signal in the corpus" DROPPED 2026-08-16. It reported a blind-judged junk rate from an
        # audit file that no current run produces, so it rendered as an em dash / "not measured" while
        # still showing a GREEN status -- a tile claiming health from a number it did not have. If the
        # audit is ever revived the tile can come back with it; an empty tile on the status strip is
        # worse than no tile, because the strip is read as a checklist.
        dict(label="Has body text", value=f"{100 * lede / max(n, 1):.0f}%", sub=f"{lede:,} of {n:,}",
             status=st(100 * lede / max(n, 1), 85, 60),
             why="Share with real article text. The rest reach the curator as a headline only."),
        dict(label="Has byline", value=f"{100 * auth / max(n, 1):.0f}%", sub=f"{auth:,} named authors",
             status=st(100 * auth / max(n, 1), 60, 30),
             why="Share with a named human author. Wire and PR copy have none by design."),
        # REPLACED the "Volume floor" tile 2026-08-16. That one compared a thin day (p10) against a
        # typical day and sat permanently at CRITICAL -- but the thinnest 10% of days were 71 Sundays
        # and 39 Saturdays out of ~112. Pooling weekends with weekdays makes the distribution bimodal,
        # so p10 lands in the weekend cluster and the median in the weekday one, and the ratio measures
        # the gap BETWEEN two populations rather than any retrieval shortfall. Split properly it was
        # healthy either way: weekdays 73%, weekends 58%. A permanently-red tile that is measuring the
        # calendar trains you to ignore the light.
        dict(label="Clean text", value=f"{clean_pct:.0f}%",
             sub=f"{clean_n:,} of {n:,} via wayback",
             status=st(clean_pct, 75, 50),
             why="Fraction of articles whose text was retrieved via wayback."),
        dict(label="Beats firing", value=f"{len(beats)}/{n_beats}",
             sub=f"{n_beats - len(beats)} beat(s) returned nothing all year",
             status=st(100 * len(beats) / max(n_beats, 1), 100, 85),
             why="Standing weekly searches that returned at least one article this year."),
        dict(label="Early-framing beats", value=f"{gem_n:,}", sub=f"{100 * gem_n / max(n, 1):.1f}% of corpus",
             status=st(100 * gem_n / max(n, 1), 15, 8),
             why="Articles from the 10 under-the-radar beats — the ones this strategy bets on."),
        dict(label="Source spread", value=f"{len(srcs):,}", sub=f"top-10 = {top10:.0f}% of corpus",
             status=st(100 - top10, 65, 50),
             why="Distinct outlets in the corpus, and how much the ten largest supply."),
    ]


# Shared page CSS. Lifted to module scope 2026-08-11 so the SBT dashboard renders identically
# instead of maintaining a second, drifting copy of the same tokens.
CSS = f""":root {{
  --surface:{LIGHT['surface']}; --card:#ffffff; --text:{LIGHT['text']}; --text2:{LIGHT['text2']};
  --grid:{LIGHT['grid']}; --line:#e6e5e1;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --surface:{DARK['surface']}; --card:#222220; --text:{DARK['text']}; --text2:{DARK['text2']};
    --grid:{DARK['grid']}; --line:#33322f;
  }}
}}
:root[data-theme="dark"] {{
  --surface:{DARK['surface']}; --card:#222220; --text:{DARK['text']}; --text2:{DARK['text2']};
  --grid:{DARK['grid']}; --line:#33322f;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--surface); color:var(--text);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 80px; }}
header h1 {{ font-size:26px; margin:0 0 4px; letter-spacing:-.01em; }}
.sub {{ color:var(--text2); font-size:14px; margin:0 0 6px; }}
.nav {{ font-size:13px; color:var(--text2); border-bottom:1px solid var(--line);
  padding-bottom:12px; margin-bottom:22px; }}
.nav a {{ color:var(--text2); }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(238px,1fr)); gap:12px; margin-bottom:30px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:13px 14px; }}
.tile-h {{ display:flex; align-items:center; gap:7px; margin-bottom:5px; }}
.dot {{ font-size:11px; }}
.tl {{ font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--text2); flex:1; }}
.badge {{ font-size:10px; text-transform:uppercase; letter-spacing:.05em; border:1px solid;
  border-radius:20px; padding:1px 7px; }}
.tv {{ font-size:27px; font-weight:600; letter-spacing:-.02em; }}
.ts {{ font-size:12.5px; color:var(--text2); margin-bottom:7px; }}
.tw {{ font-size:12.5px; color:var(--text2); line-height:1.5; }}
.panel {{ margin:0 0 34px; }}
.panel h2 {{ font-size:17px; margin:0 0 5px; font-weight:600; }}
.lead {{ color:var(--text2); font-size:13.5px; margin:0 0 12px; max-width:80ch; }}
.plot {{ background:var(--card); border:1px solid var(--line); border-radius:10px; }}
details.tbl {{ margin-top:9px; font-size:13px; color:var(--text2); }}
details.tbl summary {{ cursor:pointer; }}
table {{ border-collapse:collapse; margin-top:9px; width:100%; font-size:12.5px; }}
th,td {{ text-align:left; padding:5px 9px; border-bottom:1px solid var(--line); }}
th {{ color:var(--text2); font-weight:600; }}
.params {{ width:auto; }} .params td:first-child {{ color:var(--text2); }}
.scroll {{ overflow-x:auto; }}
footer {{ margin-top:44px; padding-top:16px; border-top:1px solid var(--line);
  font-size:12.5px; color:var(--text2); }}"""


# Shared page CSS. Lifted to module scope 2026-08-11 so the SBT dashboard renders identically
# instead of maintaining a second, drifting copy of the same tokens.
# --------------------------------------------------------------------------- html helpers
def esc(s) -> str:
    return html.escape(str(s), quote=True)


def tile(v: dict) -> str:
    c = STATUS[v["status"]]
    icon = {"good": "●", "warning": "▲", "serious": "▲", "critical": "■"}[v["status"]]
    return (f'<div class="tile"><div class="tile-h"><span class="dot" style="color:{c}">{icon}</span>'
            f'<span class="tl">{esc(v["label"])}</span>'
            f'<span class="badge" style="color:{c};border-color:{c}">{v["status"]}</span></div>'
            f'<div class="tv">{esc(v["value"])}</div><div class="ts">{esc(v["sub"])}</div>'
            f'<div class="tw">{esc(v["why"])}</div></div>')


def panel(num: int, title: str, lead: str, div_id: str, height: int, table: str = "") -> str:
    t = (f'<details class="tbl"><summary>data table</summary>{table}</details>') if table else ""
    return (f'<section class="panel"><h2>{num}. {esc(title)}</h2><p class="lead">{lead}</p>'
            f'<div id="{div_id}" class="plot" style="height:{height}px"></div>{t}</section>')


def table_html(headers: list[str], rows: list[list]) -> str:
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


# --------------------------------------------------------------------------- build
def build(run: Path, out: Path, bootstrap: bool = False) -> None:
    """Render the corpus-health dashboard.

    `bootstrap=True` renders FBS (docs/fbs.html) off src/bootstrap_corpus instead of a single pool.json.
    SAME panels on purpose: the questions that matter about a corpus -- how much of it has text, which
    beats it reaches, how deep the provenance goes -- do not change because the corpus was assembled
    from two eras, and a forked copy of 870 lines of panel code would drift from this one within a week
    (which is precisely how the beat vocabulary drifted). One builder, two corpora."""
    if bootstrap:
        import bootstrap_corpus
        arts, meta = bootstrap_corpus.load()
        stats = {}                       # no retrieval_stats.json: the corpus is assembled, not ingested
        print(f"  {bootstrap_corpus.describe(meta)}", flush=True)
    else:
        arts, meta, stats = _load(run)
    _PAGE = "fbs.html" if bootstrap else "fbt.html"
    _NAME = "Firehose Bootstrap (FBS)" if bootstrap else "Firehose Backtest (FBT)"
    # The sub-line must say WHICH corpus, and for FBS where the two eras meet -- a reader who cannot see
    # the handoff on the page will read the seam as a retrieval change.
    _SRC = (f"GKG+wayback &rarr; {meta.get('handoff','?')} &rarr; websearch daily "
            f"({meta.get('n_gkg',0):,} + {meta.get('n_websearch',0):,})") if bootstrap else "GKG on BigQuery"
    gem = _gem_beats()
    n = len(arts)
    dates = sorted(a.get("published_date", "")[:10] for a in arts if a.get("published_date"))
    win = f"{dates[0]} → {dates[-1]}" if dates else "?"

    # ---- panel data ------------------------------------------------------
    # Does the funnel actually HAVE its upstream stages and its accuracy annotations? The prose below
    # is written from these flags rather than asserting a fixed story: the old text claimed BOTH
    # ("figures are scaled from four sampled weeks", "each stage carries how often it was wrong")
    # while rendering NEITHER -- prefilter_scale.json does not exist for this corpus and no drop audit
    # has been run against it. A panel that describes content it is not showing is worse than one that
    # admits the gap, because the reader cannot tell the funnel is missing its three largest stages.
    _has_prefilter = (run / "prefilter_scale.json").exists()
    _has_audit = (bool(audits(run)) if not bootstrap else False)
    # ---- FBS-ONLY: THE SEAM. Everything else on this page aggregates the corpus into one blob, which
    # is exactly wrong for the bootstrap: it is deliberately TWO regimes with a clean cut, and only the
    # post-handoff half resembles production. These three payloads split at the handoff so the
    # forward-use half can be read on its own.
    era = None
    if bootstrap:
        import datetime as _dt
        _H = meta["handoff"]
        _by_day = collections.Counter()
        _pre_days, _post_days = collections.Counter(), collections.Counter()
        _chars = {"pre": [], "post": []}
        for _a in arts:
            _d = (_a.get("published_date") or "")[:10]
            if not _d:
                continue
            _by_day[_d] += 1
            (_pre_days if _d < _H else _post_days)[_d] += 1
            _chars["pre" if _d < _H else "post"].append(len(_a.get("snippet") or ""))
        _days = sorted(_by_day)
        # calendar-complete axis: a MISSING day must draw as a hole, not be skipped. Two days were
        # lost on 2026-07-11/12 when cron did not fire, and a day-index axis would hide that.
        _d0, _d1 = _dt.date.fromisoformat(_days[0]), _dt.date.fromisoformat(_days[-1])
        _cal = [( _d0 + _dt.timedelta(days=i)).isoformat() for i in range((_d1 - _d0).days + 1)]
        _post_cal = [d for d in _cal if d >= _H]
        _zero = [d for d in _post_cal if _by_day.get(d, 0) == 0]
        _lastpull = _post_cal[-1] if _post_cal else None
        def _q(v):
            v = sorted(v)
            return {"med": v[len(v) // 2], "p10": v[len(v) // 10], "p90": v[9 * len(v) // 10]} if v else {}
        era = {
            "handoff": _H, "cal": _cal, "counts": [_by_day.get(d, 0) for d in _cal],
            "pre_n": sum(_pre_days.values()), "post_n": sum(_post_days.values()),
            "pre_per_day": round(sum(_pre_days.values()) / max(len(_pre_days), 1), 1),
            "post_per_day": round(sum(_post_days.values()) / max(len(_post_days), 1), 1),
            "zero_days": _zero, "n_missing": sum(1 for d in _post_cal if d not in _by_day),
            "last_day": _lastpull, "post_days": len(_post_cal),
            "chars_pre": _q(_chars["pre"]), "chars_post": _q(_chars["post"]),
            # what the scout is handed, per era, AFTER lede.apply has filled snippet
            "trailing": [(d, _by_day.get(d, 0)) for d in _cal[-21:]],
        }

    if bootstrap:
        # An assembled corpus has no ingest funnel -- there is no BigQuery scan to narrow. What matters
        # instead is COMPOSITION: how much came from each era, and where the seam is. Same panel slot,
        # honest content, rather than a funnel chart with nothing to put in it.
        fun = [(f"GKG + wayback (to {meta['handoff']})", meta["n_gkg"],
                f"{meta['start']} → {meta['handoff']}, ~3 months of retrospective depth"),
               (f"websearch daily (from {meta['handoff']})", meta["n_websearch"],
                f"{meta['handoff']} → {meta['end']}, deduped by URL; grows every morning"),
               ("title-spam dropped from the websearch era", meta["spam_dropped"],
                "the backtest's filter re-applied, so both eras are filtered to one standard")]
    else:
        fun = funnel_rows(stats, n, run)

    import difflib
    bymonth = collections.defaultdict(
        lambda: {"n": 0, "text": 0, "auth": 0, "clean": 0, "live": 0, "none": 0, "both": 0, "div": 0})
    for a in arts:
        m = (a.get("published_date") or "")[:7]
        if not m:
            continue
        d = bymonth[m]
        d["n"] += 1
        d["text"] += 1 if (a.get("lede_live") or a.get("lede")) else 0
        d["auth"] += 1 if a.get("author") else 0
        # PROVENANCE, not just presence. `clean` counts articles read from an AS-OF archived capture;
        # `live` counts today's page, which is look-ahead-biased. A backtest number is only defensible
        # to the extent it rests on the clean band, so the two are never merged into one "has text".
        if a.get("lede"):
            d["clean"] += 1
        elif a.get("lede_live"):
            d["live"] += 1
        else:
            d["none"] += 1
        if a.get("lede") and a.get("lede_live"):     # the bias sample: both arms on the same article
            d["both"] += 1
            if difflib.SequenceMatcher(None, a["lede"], a["lede_live"]).ratio() <= 0.80:
                d["div"] += 1
    months = sorted(bymonth)

    byday = collections.Counter(a.get("published_date", "")[:10] for a in arts if a.get("published_date"))
    day_keys = sorted(byday)
    byweek = collections.Counter()
    for d, c in byday.items():
        try:                                    # ISO week, labelled by its Monday for a real time axis
            dt = date.fromisoformat(d)
            byweek[(dt - __import__("datetime").timedelta(days=dt.weekday())).isoformat()] += c
        except ValueError:
            pass
    week_keys = sorted(byweek)
    dow = collections.Counter()
    for d, c in byday.items():
        try:
            dow[DOW[date.fromisoformat(d).weekday()]] += c
        except ValueError:
            pass

    beats = collections.Counter(q for a in arts for q in (a.get("queries") or []))
    cfg = json.loads((ROOT / "retrieval_config.json").read_text())
    all_beats = [b["query"] for b in cfg["gem_beats"]] + [b["query"] for b in cfg["coverage_beats"]]
    tiles = "".join(tile(v) for v in verdicts(arts, stats, gem, len(all_beats), audits(run)))
    beat_rows = sorted(((q, beats.get(q, 0), q in gem) for q in all_beats), key=lambda r: r[1])

    srcs = collections.Counter(a.get("source", "") for a in arts)
    # SPECIALTY-DESK REACH. These are the outlets hand-picked in the profile as the ones carrying the
    # early call; the forward path restricts a whole search pass to them. GKG cannot do that (BigQuery
    # has no per-domain pass), so here they are simply measured: how much of the corpus each supplied,
    # INCLUDING the ones that supplied nothing. Omitting the zeroes would hide the finding.
    _spec = [s.lower() for s in _gkg._specialty()]
    _seen = {a.get("source", "").lower() for a in arts}
    spec_counts = []
    for d in _spec:
        n_d = sum(v for s, v in srcs.items() if _gkg._domain_in(s, [d]))
        spec_counts.append((d, n_d))
    spec_counts.sort(key=lambda kv: kv[1])
    spec_total = sum(n for _, n in spec_counts)
    TOP_SRC = 50
    top_src = srcs.most_common(TOP_SRC)
    tail = sum(c for _, c in srcs.most_common()[TOP_SRC:])

    syn = collections.Counter(min(int(a.get("syndication") or 1), 10) for a in arts)

    authors = collections.Counter(a["author"] for a in arts if a.get("author"))
    miss_reasons = dict(sorted(collections.Counter(
        a.get("text_miss") for a in arts if a.get("text_miss")).items(), key=lambda kv: -kv[1]))

    # ---- ENTITY GROUPING: what the scout's ticker-bundles are made of ---------------------------
    # Built with the SAME orgs module the curator runs, so this measures the real thing rather than a
    # reimplementation that could drift from it. Two questions the panel answers:
    #   1. how much of the corpus can be bundled at all (an article with no usable subject org joins
    #      no group and reaches the scout only via the unclustered catch-all), and
    #   2. of what IS grouped, how much sits in singleton bundles -- where grouping is structurally
    #      incapable of corroborating anything, because there is nothing to corroborate with.
    _canon = _orgs.build_canon(arts)
    _tmap = _orgs.ticker_map(arts, _canon)
    _grp = _orgs.group(arts, canon=_canon, tmap=_tmap)
    _noorg = [a for a in arts if not _orgs.article_orgs(a, _canon, _tmap)]
    _BUCK = [(1, 1, "1"), (2, 3, "2-3"), (4, 10, "4-10"), (11, 50, "11-50"), (51, 10 ** 9, "51+")]
    _gsz, _asz = [], []
    for lo, hi, lab in _BUCK:
        gs = [k for k, v in _grp.items() if lo <= len(v) <= hi]
        _gsz.append(len(gs))
        _asz.append(sum(len(_grp[k]) for k in gs))
    # WHERE THE NO-COMPANY ARTICLES NOW GO. They used to reach the scout alone (or not at all); they
    # are now bundled by BEAT -- the standing search that ingested them -- so every one of them has a
    # topical home. This is the whole point of the panel: the bar that used to be a hole is now a
    # bundle class, and it is drawn beside the company bundles so the two can be compared.
    # TYPICAL SLICES PER WINDOW, for the bar labels. A beat bundle is date-sliced per curation, so a
    # corpus-total bar alone implies the scout reads it as one block -- it does not. Estimated from
    # the beat's article count over the run's span, at the same ~30-article budget the scout packs to.
    _span_days = 1
    try:
        _ds = sorted({(a.get("published_date") or "")[:10] for a in arts if a.get("published_date")})
        _span_days = max(1, (date.fromisoformat(_ds[-1]) - date.fromisoformat(_ds[0])).days)
    except Exception:  # noqa: BLE001
        pass
    _beat = collections.Counter()
    for a in _noorg:
        for q in (a.get("queries") or []):
            _beat[_gkg.bundle_beat(q)] += 1
            break                                   # median beats/article is 1; count each once
    _orphan_left = sum(1 for a in _noorg if not (a.get("queries") or []))
    labels = [b[2] for b in _BUCK] + ["beat\nbundles", "no bundle\nat all"]
    _gsz = _gsz + [len(_beat), 0]
    _asz = _asz + [sum(_beat.values()), _orphan_left]
    # NAMED bundles for the horizontal bar: the biggest of each kind, so the panel says WHICH
    # bundles carry the corpus rather than only how the sizes are distributed.
    _named = ([(f"{k}", len(v), "company") for k, v in _grp.items()]
              + [(f"{b}", n, "beat") for b, n in _beat.items()])
    _named.sort(key=lambda t: -t[1])
    _TOP_N = 100
    _top = _named[:_TOP_N]
    _rest = _named[_TOP_N:]
    # THE TAIL, as one bar. 22k bundles cannot be listed, but leaving them out entirely would let the
    # panel imply the top 100 IS the corpus -- they are 22,263 bundles holding a quarter of it, nearly
    # all singletons. Drawn as one aggregate in grey and labelled as a COUNT of bundles, not a bundle,
    # so it is not mistaken for one.
    # slices/window = (articles in a 30-day window) / 30-article call budget, rounded up
    def _slices(n):
        per_win = n * 30.0 / _span_days
        return max(1, int(-(-per_win // 30)))
    bundles = {"q": [(t[0] + (f"  ×{_slices(t[1])}" if t[2] == "beat" and _slices(t[1]) > 1 else ""))
                     for t in _top],
               "n": [t[1] for t in _top],
               "kind": [t[2] for t in _top],
               "rest_n": len(_rest), "rest_a": sum(t[1] for t in _rest),
               "rest_singletons": sum(1 for t in _rest if t[1] == 1),
               "top_n": _TOP_N, "top_a": sum(t[1] for t in _top),
               "n_company": len(_grp), "n_beat": len(_beat),
               "a_company": sum(len(v) for v in _grp.values()), "a_beat": sum(_beat.values())}
    grouping = {"labels": labels, "groups": _gsz, "articles": _asz,
                "n_entities": len(_grp), "no_org": len(_noorg),
                "no_org_seen": sum(_beat.values()), "no_org_blind": _orphan_left,
                "n": len(arts), "n_beats": len(_beat),
                "biggest": sorted(((len(v), k) for k, v in _grp.items()), reverse=True)[:12]}

    # ---- payload ---------------------------------------------------------
    payload = {
        "grouping": grouping,
        "bundles": bundles, "era": era,
        "funnel": {"labels": [r[0] for r in fun], "values": [r[1] for r in fun],
                   "notes": [r[2] for r in fun]},
        "backfill": {**(stats.get("wayback_overlay") or {}),
                     "m": months,
                     "none": [bymonth[m]["none"] for m in months],
                     "clean": [bymonth[m]["clean"] for m in months]},
        "prov": {"m": months,
                 "clean": [bymonth[m]["clean"] for m in months],
                 "live": [bymonth[m]["live"] for m in months],
                 "none": [bymonth[m]["none"] for m in months]},
        "drift": {"m": months, "both": [bymonth[m]["both"] for m in months],
                  "div": [bymonth[m]["div"] for m in months],
                  "pct": [round(100 * bymonth[m]["div"] / bymonth[m]["both"], 1)
                          if bymonth[m]["both"] else None for m in months]},
        "months": {"m": months, "n": [bymonth[m]["n"] for m in months],
                   "text": [bymonth[m]["text"] for m in months],
                   "auth": [bymonth[m]["auth"] for m in months]},
        "daily": {"d": day_keys, "n": [byday[d] for d in day_keys]},
        "weekly": {"d": week_keys, "n": [byweek[w] for w in week_keys]},
        "monthly": {"m": months, "n": [bymonth[m]["n"] for m in months]},
        "dow": {"k": DOW, "n": [dow.get(k, 0) for k in DOW]},
        "beats": {"q": [r[0] for r in beat_rows], "n": [r[1] for r in beat_rows],
                  "gem": [r[2] for r in beat_rows]},
        "sources": {"s": [s for s, _ in top_src] + ([f"({len(srcs) - TOP_SRC:,} other outlets)"] if tail else []),
                    "n": [c for _, c in top_src] + ([tail] if tail else [])},
        "spec": {"d": [d for d, _ in spec_counts], "n": [n for _, n in spec_counts],
                 "total": spec_total, "corpus": n,
                 "zero": sum(1 for _, x in spec_counts if x == 0)},
        "syn": {"k": [str(k) if k < 10 else "10+" for k in sorted(syn)],
                "n": [syn[k] for k in sorted(syn)]},
        "miss": {"k": list(miss_reasons), "n": list(miss_reasons.values())},
        # The no-byline bar belongs here, not in a footnote: it is by far the largest bucket, and a
        # per-author chart that hides it implies bylines are the norm when they are the minority.
        "authors": {"a": ["(no byline)"] + [a for a, _ in authors.most_common(25)],
                    "n": [sum(1 for x in arts if not x.get("author"))]
                         + [c for _, c in authors.most_common(25)]},
    }

    g = stats.get("gkg", {})
    params = [
        # THE CORPUS PATH, first row. Every number below is derived from one directory on disk and
        # the page never said which, so a reader could not tell a 1-year corpus from a 3-year one
        # -- the exact confusion that let CBT render a whole dashboard off the wrong pool.
        # Repo-RELATIVE, matching CBT. The absolute path leaks a home directory into a page that
        # gets shared, and the part a reader needs is which pool under data/, not where it lives.
        ("Corpus", str(Path(run).resolve()).replace(str(ROOT) + "/", "")),
        ("Window", win), ("Articles", f"{n:,}"),
        ("Discovery engine", "GKG on BigQuery (gdelt-bq.gdeltv2.gkg_partitioned)"),
        ("Rows scanned", f"{g.get('rows_scanned', 0):,}"),
        ("BigQuery", f"{g.get('billed_gb', 0):.0f} GB billed over {g.get('queries', 0)} queries"),
        ("Discovery time", f"{g.get('elapsed_s', 0):.0f}s"),
        ("Beats", f"{len(cfg['gem_beats'])} early-framing + {len(cfg['coverage_beats'])} sector-coverage"),
        ("Beat vocabulary", _LINK(CONFIG_URL, "retrieval_config.json")
         + " — every beat's search query, its GKG keyword atoms, the spam patterns and the engine knobs"),
        ("Domain steering", _LINK(PROFILE_URL, "investor_profile.backtest.md")
         + " — specialty_allow (gem pass) and mill_block (the blocklist stage above)"),
        ("Lede arm", "live (today's page) — look-ahead-BIASED, prototype only"),
    ]
    # values may carry a trusted <a> built by _LINK, so only the KEY is escaped here
    ptable = "".join(f"<tr><td>{esc(k)}</td><td>{v if '<a ' in str(v) else esc(v)}</td></tr>"
                     for k, v in params)

    panels = "".join([
        panel(1, ("How the bootstrap corpus is assembled" if bootstrap
                  else "Filtering the ingestion funnel"),
              (f"A CLEAN CUT at {meta.get('handoff','?')}, not a blend: GKG + the wayback lede backfill "
               f"before it, the daily websearch pull ONLY after it. GKG is not used past the handoff even "
               f"though it has coverage there, because the forward test this leads to runs on websearch "
               f"alone — blending would make the bootstrap richer than the thing it predicts. The seam is "
               f"nearly invisible by volume (~101/day before, ~97/day after), but the eras are otherwise "
               f"<b>97.7% disjoint by URL</b>, so treat any metric that moves at the handoff as a corpus "
               f"change first and a signal second. Defined in src/bootstrap_corpus.py."
               if bootstrap else
               "Each bar is what REMAINS; the arithmetic reconciles to the corpus count. "
               "<b>Log x-axis</b>."
               + ("<br><br>The top four stages run inside the BigQuery query, scaled from four sampled weeks. "
                  "Everything below &ldquo;GKG rows scanned&rdquo; is exact."
                  if _has_prefilter else
                  "<br><br><b>STARTS PART-WAY DOWN.</b> Three filters run first, inside the BigQuery "
                  "<code>WHERE</code>, and are not shown: English-origin, market-theme, beat-keyword. They "
                  "drop most of GDELT before a row is fetched. Uncounted because their drops are never "
                  "retrieved.")
               + ("<br><br>Each bar also carries how often it was <b>wrong</b>, from a re-read of its drops."
                  if _has_audit else
                  "")
               + ""
               + f"<br><br>Configured in {_LINK(CONFIG_URL, 'retrieval_config.json')} and "
                 f"{_LINK(PROFILE_URL, 'investor_profile.backtest.md')}."),
              "p-funnel", 460),
        panel(2, "Coverage over time",
              "The same corpus at three resolutions: per month, per ISO week, and per day. Month shows "
              "whether any stretch of calendar is under-served; week is the scout's actual cadence, so "
              "a thin week is a thin decision; day exposes individual holes the coarser views average "
              "away. Flat and gap-free is the goal.",
              "p-vol", 700),
        panel(3, "Articles by day of week",
              "Where the volume sits across the week. A weekend trough is normal market-news "
              "behaviour, not a retrieval fault — this panel exists so a real gap is distinguishable "
              "from the ordinary weekly cycle.",
              "p-dow", 300),
        panel(4, "Body text vs time",
              "Fraction of articles containing text rather than just a bare headline. Older articles "
              "score worse because their links have rotted.",
              "p-text", 360),
        panel(5, "Lede provenance by month",
              "Counts of where each month's text came from. <b>Clean</b> is the Wayback copy pulled "
              "from archive.org as written when crawled soon after publication. <b>Biased</b> is the "
              "current text (pulled via the GKG-provided url) which may have been edited since. "
              "<b>None</b> is a bare headline.",
              "p-prov", 380),
        panel(6, "Wayback hit rate (of URLs attempted)",
              "A Wayback pass fills the articles that reached the curator as a bare headline — dead "
              "links, 5xx, paywalls. Grey is the remaining hole per month, green is text recovered. "
              "The hole concentrates in the oldest months, where links have rotted, and in the "
              "NEWEST, where archive.org has not crawled yet. This panel reads the backfill's cache "
              "directly rather than the corpus file, so it moves on every rebuild instead of waiting "
              "for the multi-day pass to finish."
              "<br><br><b>Mind the denominator.</b> The headline percentage here is hits &divide; URLs "
              "<b>ATTEMPTED</b> — only the articles that lacked text and were queued for Wayback. It "
              "is NOT the share of the corpus carrying text, which is the stat tile above and panel 5. "
              "The two are easily confused because they currently sit a tenth of a point apart by "
              "coincidence: the hit rate is 63.3% of attempted URLs, while live-page text happens to "
              "be 63.4% of the corpus. Corpus text coverage is 87% (23% archived + 63% live).",
              "p-backfill", 360),
        panel(7, "Live vs archived text divergence",
              "Most of this corpus was read from the article's page <b>as it looks today</b>, "
              "because that is ~2,600&times; faster than pulling an archived copy. The risk: today's "
              "page may no longer be what ran on the decision date."
              "<br><br>A month-stratified sample was fetched BOTH ways — today's page and the "
              "archive.org snapshot from on-or-before that date. This plots the share where the two "
              "differ materially: a spot check on the ~87% we could not afford to fetch cleanly. Flat "
              "and low means today's page is a faithful stand-in at every age.",
              "p-drift", 360),
        panel(8, "Why text is missing",
              "The recorded reason each headline-only article failed, straight from the fetch — all "
              "code, no AI. <b>url_recycled</b> is a real test: the page loaded and produced text, but "
              "shared too few words with the stored headline, so that URL now serves a different "
              "story. <b>removed</b> is HTTP 404/410. <b>blocked_or_paywalled</b> is 401/403 — a "
              "paywall and a bot-wall are indistinguishable to an anonymous client, so they share one "
              "honest label. <b>no_text_on_page</b> is a 200 with no prose; audited separately, 82% "
              "are genuine walls and 18% our parser failing.",
              "p-miss", 340),
        panel(9, "Articles per beat",
              "A <b>beat</b> is one standing weekly search; all 46 live in "
              f"{_LINK(CONFIG_URL, 'retrieval_config.json')}, each with a plain-English query (used "
              "verbatim on the forward path) and keyword atoms (matched against headline and URL "
              "here). Sectors and superlatives are just coverage beats, not separate files."
              "<br><br><b>Orange</b>: the 10 early-framing beats — coverage while a name is still "
              "under-the-radar, what this strategy bets on. <b>Blue</b>: the 36 sector-coverage "
              "beats; one superlative beat among them is the corpus's largest single source. Three "
              "early-framing beats sit at the bottom, contributing nothing."
              f"<br><br>Outlet preferences live in {_LINK(PROFILE_URL, 'investor_profile.backtest.md')}, "
              f"sources in {_LINK(SOURCES_URL, 'news_sources.md')}. The English-origin gate (plot 1) "
              "is still in code — a wart.",
              "p-beats", 900),
        panel(10, "Articles per source",
              "The 50 largest outlets, plus everything else folded into one grey bar. A sweep "
              "dominated by a handful of publishers inherits their editorial priorities; a long tail "
              "means genuine breadth.",
              "p-src", 1050),
        panel(11, "Specialty-desk reach",
              "The outlets hand-picked in "
              f"{_LINK(PROFILE_URL, 'investor_profile.backtest.md')} as <code>specialty_allow</code> — "
              "the desks expected to carry the early call. On the forward path a whole search pass is "
              "restricted to them; BigQuery cannot do that, so here they are simply measured. Desks "
              "with <b>zero</b> articles are shown, not omitted — that is the finding.",
              "p-spec", 620),
        panel(12, "News replication",
              "The same story often runs on many sites at once; ingest merges those copies and "
              "counts how many outlets ran each one (plot 1 calls this step <i>syndication</i>). Most "
              "sit at 1 — a story only one outlet carried is one the market probably has not priced "
              "yet. Widest this year: 122 outlets. Log axis.",
              "p-syn", 320),
        panel(13, "Articles per author",
              "The 25 most frequent named writers, with the no-byline bucket shown in grey for scale. "
              "Wire copy, PR releases and the publisher's own name are excluded by design, so a "
              "byline here is a real person. Most of the corpus has none — that is normal for market "
              "news, not a fault. Log axis.",
              "p-auth", 640),
        panel(14, "Articles per bundle",
              "Articles are bundled before the scout reads them, so a ticker\u2019s move-signal and "
              f"its driver arrive together. The <b>{bundles['top_n']} largest</b> bundles are named; "
              f"the grey bar at the bottom is every other bundle combined \u2014 "
              f"<b>{bundles['rest_n']:,}</b> of them holding <b>{bundles['rest_a']:,}</b> articles, "
              f"of which {bundles['rest_singletons']:,} hold a single article and can corroborate "
              "nothing. <b>Blue</b> is a COMPANY bundle, every article about one firm. <b>Green</b> "
              "is a BEAT bundle, the fallback for articles naming no usable company: grouped by the "
              "standing search that found them, so they get a topical home instead of being read "
              f"alone ({bundles['n_beat']} beats, {bundles['a_beat']:,} articles). "
              "<b>A green bar is a corpus total, not one scout call.</b> Beat bundles are "
              "DATE-SLICED per curation because a beat is a theme rather than one story, so unlike a "
              "company bundle it may be split; the <b>\u00d7N</b> on a label is how many slices that "
              "beat typically becomes in one window (median slice ~20 articles). Blue bars are also "
              "corpus totals \u2014 a company bundle is filtered to the curation window before it is "
              "read, and is never split.",
              "p-group", 1500),
    ] + ([
        panel(15, "The handoff seam",
              f"The bootstrap is deliberately TWO regimes with a clean cut at <b>{meta['handoff']}</b>, "
              "not a blend: GKG with the wayback backfill before it, the daily websearch pull after. "
              "Every other panel on this page aggregates the two, which is exactly wrong for judging "
              "forward readiness \u2014 only the post-handoff half resembles production.<br><br>"
              f"Blue is GKG ({era['pre_per_day']}/day), amber is websearch "
              f"({era['post_per_day']}/day). The axis is CALENDAR-COMPLETE, so a day the pull missed "
              "draws as a hole rather than being skipped. <b>Known confound:</b> the run-up across "
              "07-25..07-29 (15 &rarr; 33 &rarr; 48 &rarr; 85 &rarr; 102) is unexplained, plausibly a "
              "Tavily quota or config change, so any before/after comparison spanning that date is "
              "not a retrieval result.",
              "p-seam", 380),
        panel(16, "What the curator actually reads, per era",
              "The sharpest difference on this page, and it is not visible anywhere else. Post-handoff "
              "articles carry <b>no lede at all</b> \u2014 0% archived, 0% live \u2014 so the scout "
              "sees only the search snippet. Pre-handoff articles get a wayback lede written into the "
              "same field.<br><br>"
              f"Median characters the scout is handed: <b>{era['chars_pre'].get('med','?')}</b> before "
              f"the handoff, <b>{era['chars_post'].get('med','?')}</b> after. The forward test "
              "therefore reads a DIFFERENT amount of text per article than the backtest that "
              "validated it \u2014 in this corpus more, because a Tavily snippet is longer than a raw "
              "GKG one, but capped and never enriched. If the wayback backfill is what makes the "
              "backtest work, the forward era does not have it.",
              "p-chars", 360),
        panel(17, "Daily pull health",
              "The operational card: is the morning cron actually firing? Bars are the trailing three "
              "weeks; the dashed line is the post-handoff median. "
              f"<b>Last day in the corpus: {era['last_day']}.</b> "
              f"Zero-article days since the handoff: <b>{len(era['zero_days'])}</b>"
              + (f" ({', '.join(era['zero_days'][:5])})" if era['zero_days'] else "")
              + f". Days absent entirely: <b>{era['n_missing']}</b>. "
              "Two days were already lost on 2026-07-11/12 when cron did not fire, and a silent gap "
              "is the failure mode this panel exists to catch \u2014 the corpus simply stops growing "
              "and every downstream number keeps looking reasonable.",
              "p-pull", 340),
    ] if bootstrap and era else []))

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_NAME}</title>
<script src="{PLOTLY_CDN}"></script>
<style>
{CSS}
</style></head><body>
<div class="wrap">
{dash_nav.render(_PAGE)}
<header>
  <h1>{_NAME}</h1>
  <p class="sub">{esc(win)} &middot; {n:,} articles &middot; {_SRC}</p>
</header>
<div class="tiles">{tiles}</div>
<section class="panel"><h2>Parameters</h2>
  <div class="scroll"><table class="params">{ptable}</table></div></section>
{panels}

</div>
<script>
const DATA = {json.dumps(payload)};
const L = {json.dumps(LIGHT)}, D = {json.dumps(DARK)}, ST = {json.dumps(STATUS)};
function pal() {{
  const dark = document.documentElement.dataset.theme === 'dark' ||
    (document.documentElement.dataset.theme !== 'light' &&
     window.matchMedia('(prefers-color-scheme: dark)').matches);
  return dark ? D : L;
}}
function base(p, extra) {{
  return Object.assign({{
    paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
    font:{{color:p.text2, size:12, family:'-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif'}},
    margin:{{l:60,r:24,t:14,b:44}},
    xaxis:{{gridcolor:p.grid, zerolinecolor:p.grid, linecolor:p.grid}},
    yaxis:{{gridcolor:p.grid, zerolinecolor:p.grid, linecolor:p.grid}},
    hoverlabel:{{bgcolor:p.surface, bordercolor:p.grid, font:{{color:p.text}}}},
    showlegend:false
  }}, extra || {{}});
}}
const CFG = {{displayModeBar:false, responsive:true}};

function draw() {{
  const p = pal();

  // 1. funnel — horizontal bars, ordinal blue (magnitude down a fixed sequence of stages)
  const f = DATA.funnel, nst = f.labels.length;
  Plotly.react('p-funnel', [{{
    type:'bar', orientation:'h', x:f.values, y:f.labels,
    marker:{{color:f.labels.map((_,i)=>p.ord[Math.min(Math.floor(i*p.ord.length/nst), p.ord.length-1)]),
             line:{{width:2, color:p.surface}}}},
    text:f.values.map((v,i)=>v.toLocaleString()+'  —  '+f.notes[i]),
    textposition:'outside', textfont:{{color:p.text2, size:11.5}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}} remaining<extra></extra>'
  }}], base(p, {{margin:{{l:220,r:190,t:10,b:36}},
      yaxis:{{autorange:'reversed', gridcolor:'rgba(0,0,0,0)', linecolor:p.grid, tickfont:{{size:12}}}},
      xaxis:{{type:'log', gridcolor:p.grid, zerolinecolor:p.grid,
              title:{{text:'articles remaining (log scale)', font:{{size:11}}}}}}
  }}), CFG);

  // 2. volume at three resolutions -- SMALL MULTIPLES, one shared measure (articles), never a
  //    second y-scale on one frame. Each row is its own subplot with its own count axis.
  Plotly.react('p-vol', [
    {{type:'bar', x:DATA.monthly.m, y:DATA.monthly.n, marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
      xaxis:'x', yaxis:'y', hovertemplate:'%{{x}}<br>%{{y:,}} articles<extra>month</extra>'}},
    {{type:'bar', x:DATA.weekly.d, y:DATA.weekly.n, marker:{{color:p.s1}},
      xaxis:'x2', yaxis:'y2', hovertemplate:'week of %{{x}}<br>%{{y:,}} articles<extra>week</extra>'}},
    {{type:'bar', x:DATA.daily.d, y:DATA.daily.n, marker:{{color:p.s1}},
      xaxis:'x3', yaxis:'y3', hovertemplate:'%{{x}}<br>%{{y}} articles<extra>day</extra>'}}
  ], base(p, {{
    grid:{{rows:3, columns:1, pattern:'independent', roworder:'top to bottom'}},
    margin:{{l:64,r:24,t:26,b:40}}, bargap:0.15,
    annotations:[
      {{text:'per month', x:0, xref:'paper', y:1.0,  yref:'paper', showarrow:false,
        font:{{size:11.5, color:p.text2}}, xanchor:'left'}},
      {{text:'per ISO week', x:0, xref:'paper', y:0.635, yref:'paper', showarrow:false,
        font:{{size:11.5, color:p.text2}}, xanchor:'left'}},
      {{text:'per day', x:0, xref:'paper', y:0.27, yref:'paper', showarrow:false,
        font:{{size:11.5, color:p.text2}}, xanchor:'left'}}
    ],
    xaxis:{{gridcolor:p.grid}},  yaxis:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}},
    xaxis2:{{gridcolor:p.grid}}, yaxis2:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}},
    xaxis3:{{gridcolor:p.grid}}, yaxis3:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}}
  }}), CFG);

  // 4. body text vs article age -- connected dots (a trend over an ordered axis), single series so
  //    no legend box; the title names it. Markers >= 8px with a surface ring so overlaps stay legible.
  const M = DATA.months, share = M.n.map((tot,i)=>100*M.text[i]/tot);
  Plotly.react('p-text', [{{
    type:'scatter', mode:'lines+markers', x:M.m, y:share,
    line:{{color:p.s1, width:2}},
    marker:{{color:p.s1, size:10, line:{{width:2, color:p.surface}}}},
    cliponaxis:false,
    customdata:M.m.map((_,i)=>[M.text[i], M.n[i]]),
    hovertemplate:'%{{x}}<br>%{{y:.1f}}% have body text<br>%{{customdata[0]:,}} of %{{customdata[1]:,}}<extra></extra>'
  }}], base(p, {{margin:{{l:60,r:30,t:26,b:44}},
      // autoscaled, NOT anchored at 0: the series lives in a ~58-80% band, and forcing a 0-100
      // axis compresses the whole decay into the top fifth of the panel where the slope -- the
      // entire point of the chart -- is unreadable.
      yaxis:{{gridcolor:p.grid, ticksuffix:'%', autorange:true,
              title:{{text:'articles with body text', font:{{size:11}}}}}},
      xaxis:{{gridcolor:p.grid, title:{{text:'publication month  (left = ~1 year old at fetch, right = days old)',
              font:{{size:11}}}}}}}}), CFG);

  // 5. lede provenance -- stacked COUNTS. Green=clean is the status "good" colour deliberately: this
  //    is the only band a quotable number may rest on. Orange=biased, grey=none.
  const PR = DATA.prov;
  Plotly.react('p-prov', [
    {{type:'bar', name:'clean (as-of archive)', x:PR.m, y:PR.clean,
      marker:{{color:ST.good, line:{{width:2,color:p.surface}}}},
      hovertemplate:'%{{x}}<br>%{{y:,}} clean<extra></extra>'}},
    {{type:'bar', name:'biased (live page)', x:PR.m, y:PR.live,
      marker:{{color:p.s2, line:{{width:2,color:p.surface}}}},
      hovertemplate:'%{{x}}<br>%{{y:,}} biased<extra></extra>'}},
    {{type:'bar', name:'none (headline only)', x:PR.m, y:PR.none,
      marker:{{color:p.grid, line:{{width:2,color:p.surface}}}},
      hovertemplate:'%{{x}}<br>%{{y:,}} headline-only<extra></extra>'}}
  ], base(p, {{barmode:'stack', showlegend:true,
      legend:{{orientation:'h', y:1.14, x:0, font:{{size:11.5}}}},
      margin:{{l:64,r:24,t:36,b:44}},
      yaxis:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}},
      xaxis:{{gridcolor:p.grid}}}}), CFG);

  // 6. wayback backfill -- reads the backfill's own cache, so this moves on every rebuild instead
  //    of waiting for the multi-day pass to rewrite pool.json. Stacked: recovered vs still missing.
  const BF = DATA.backfill;
  Plotly.react('p-backfill', [
    {{type:'bar', name:'text recovered from archive.org', x:BF.m, y:BF.clean,
      marker:{{color:ST.good, line:{{width:2, color:p.surface}}}},
      hovertemplate:'%{{x}}<br>%{{y:,}} recovered<extra></extra>'}},
    {{type:'bar', name:'still headline-only', x:BF.m, y:BF.none,
      marker:{{color:p.grid, line:{{width:2, color:p.surface}}}},
      hovertemplate:'%{{x}}<br>%{{y:,}} still missing<extra></extra>'}}
  ], base(p, {{barmode:'stack', showlegend:true,
      legend:{{orientation:'h', y:1.14, x:0, font:{{size:11}}}},
      margin:{{l:64,r:24,t:40,b:60}},
      yaxis:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}},
      xaxis:{{gridcolor:p.grid}}}}), CFG);

  // 7. measured drift -- connected dots over an ordered axis, single series (no legend box; the
  //    title names it). Nulls where a month had no both-arms sample rather than an implied zero.
  const DR = DATA.drift;
  Plotly.react('p-drift', [{{
    type:'scatter', mode:'lines+markers+text', x:DR.m, y:DR.pct, connectgaps:false,
    line:{{color:p.s1, width:2}},
    marker:{{color:p.s1, size:10, line:{{width:2, color:p.surface}}}},
    text:DR.pct.map(v=>v===null?'':v.toFixed(0)+'%'), textposition:'top center',
    textfont:{{color:p.text2, size:11}}, cliponaxis:false,
    customdata:DR.m.map((_,i)=>[DR.div[i], DR.both[i]]),
    hovertemplate:'%{{x}}<br>%{{y:.1f}}% diverged<br>%{{customdata[0]}} of %{{customdata[1]}} sampled<extra></extra>'
  }}], base(p, {{margin:{{l:60,r:30,t:26,b:44}},
      yaxis:{{gridcolor:p.grid, ticksuffix:'%', rangemode:'tozero',
              title:{{text:'live text differing from archive', font:{{size:11}}}}}},
      xaxis:{{gridcolor:p.grid, title:{{text:'publication month', font:{{size:11}}}}}}}}), CFG);

  // 5. why text is missing -- measured reasons, horizontal bars, labelled (never colour alone)
  const MS = DATA.miss;
  Plotly.react('p-miss', [{{
    type:'bar', orientation:'h', x:MS.n.slice().reverse(), y:MS.k.slice().reverse(),
    marker:{{color:p.s2, line:{{width:2, color:p.surface}}}},
    text:MS.n.slice().reverse().map(v=>v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:11}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'
  }}], base(p, {{margin:{{l:190,r:90,t:14,b:40}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', tickfont:{{size:11.5}}, automargin:true}},
      // log: the reasons span ~3 decades (thousands down to single digits) and a linear axis
      // renders everything below the top two as a zero-length stub
      xaxis:{{type:'log', gridcolor:p.grid,
              title:{{text:'headline-only articles (log scale)', font:{{size:11}}}}}}}}), CFG);

  // 3b. ARTICLES PER BUNDLE -- same form as the per-beat panel below it, so the two read as a
  //     pair: this is what the scout is handed, that is where the corpus came from. Horizontal bars
  //     because the labels are company and beat NAMES, which do not fit on an x-axis. Two named
  //     series rather than a colour map, so the legend carries the distinction.
  const BU = DATA.bundles;
  const _ci = BU.kind.map((k,i)=>[k,i]).filter(t=>t[0]==='company').map(t=>t[1]).reverse();
  const _bi = BU.kind.map((k,i)=>[k,i]).filter(t=>t[0]==='beat').map(t=>t[1]).reverse();
  Plotly.react('p-group', [
    {{type:'bar', orientation:'h', name:'company bundle', x:_ci.map(i=>BU.n[i]), y:_ci.map(i=>BU.q[i]),
      marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
      text:_ci.map(i=>BU.n[i].toLocaleString()), textposition:'outside',
      textfont:{{color:p.text2, size:10.5}}, cliponaxis:false,
      hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'}},
    {{type:'bar', orientation:'h', name:'beat bundle (no company named)',
      x:_bi.map(i=>BU.n[i]), y:_bi.map(i=>BU.q[i]),
      marker:{{color:ST.good, line:{{width:2,color:p.surface}}}},
      text:_bi.map(i=>BU.n[i].toLocaleString()), textposition:'outside',
      textfont:{{color:p.text2, size:10.5}}, cliponaxis:false,
      hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'}},
    {{type:'bar', orientation:'h',
      name:'all '+BU.rest_n.toLocaleString()+' smaller bundles combined',
      x:[BU.rest_a], y:['\u2014 '+BU.rest_n.toLocaleString()+' other bundles \u2014'],
      marker:{{color:p.grid, line:{{width:2,color:p.surface}}}},
      text:[BU.rest_a.toLocaleString()], textposition:'outside',
      textfont:{{color:p.text2, size:10.5}}, cliponaxis:false,
      hovertemplate:'%{{x:,}} articles across '+BU.rest_n.toLocaleString()+
                    ' bundles ('+BU.rest_singletons.toLocaleString()+
                    ' hold a single article)<extra></extra>'}}
  ], base(p, {{margin:{{l:300,r:90,t:34,b:44}}, showlegend:true,
      legend:{{orientation:'h', y:1.06, x:0, font:{{size:11.5}}}},
      barmode:'overlay',
      yaxis:{{gridcolor:'rgba(0,0,0,0)', tickfont:{{size:10.5}}, automargin:true}},
      // log x: the largest bundle is ~500x the smallest shown, and on a linear axis everything
      // below the top three renders as a stub.
      xaxis:{{type:'log', gridcolor:p.grid,
             title:{{text:'articles in the bundle (log scale)', font:{{size:11}}}}}}}}), CFG);

  // 4. beat productivity — gem vs coverage as two named series (identity, never colour alone:
  //    the legend plus the y-axis label both name the beat)
  const B = DATA.beats;
  const gi = B.q.map((_,i)=>i).filter(i=>B.gem[i]), ci = B.q.map((_,i)=>i).filter(i=>!B.gem[i]);
  Plotly.react('p-beats', [
    {{type:'bar', orientation:'h', name:'sector-coverage beat (36)', x:ci.map(i=>B.n[i]), y:ci.map(i=>B.q[i]),
      marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
      text:ci.map(i=>B.n[i].toLocaleString()), textposition:'outside',
      textfont:{{color:p.text2, size:10.5}}, cliponaxis:false,
      hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'}},
    {{type:'bar', orientation:'h', name:'early-framing beat (10, the thesis)', x:gi.map(i=>B.n[i]),
      y:gi.map(i=>B.q[i]), marker:{{color:p.s2, line:{{width:2,color:p.surface}}}},
      text:gi.map(i=>B.n[i].toLocaleString()), textposition:'outside',
      textfont:{{color:p.text2, size:10.5}}, cliponaxis:false,
      hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'}}
  ], base(p, {{margin:{{l:330,r:90,t:10,b:40}}, showlegend:true,
      legend:{{orientation:'h', y:1.045, x:0, font:{{size:11.5}}}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', tickfont:{{size:11}}, automargin:true}},
      // log: beats span 7,010 down to 3, so a linear axis hides every starving beat -- which is
      // precisely what this panel exists to show
      xaxis:{{type:'log', gridcolor:p.grid,
              title:{{text:'articles over the window (log scale)', font:{{size:11}}}}}}}}), CFG);

  // 5. sources
  const S = DATA.sources;
  Plotly.react('p-src', [{{
    type:'bar', orientation:'h', x:S.n.slice().reverse(), y:S.s.slice().reverse(),
    marker:{{color:S.s.slice().reverse().map(s=>s.startsWith('(')?p.grid:p.s1),
             line:{{width:2,color:p.surface}}}},
    text:S.n.slice().reverse().map(v=>v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:10}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'
  }}], base(p, {{margin:{{l:200,r:60,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', tickfont:{{size:10}}, automargin:true}},
      xaxis:{{type:'log', autorange:true, gridcolor:p.grid,
              title:{{text:'articles (log scale)', font:{{size:11}}}}}}}}), CFG);

  // 10. specialty-desk reach. Zero-yield desks are drawn as status-critical, with a visible marker,
  //     because a bar of length zero on a log axis is invisible -- and those are the whole point.
  const SP = DATA.spec;
  Plotly.react('p-spec', [{{
    type:'bar', orientation:'h', x:SP.n.map(v=>v===0?null:v), y:SP.d,
    marker:{{color:SP.n.map(v=>v===0?ST.critical:p.s3), line:{{width:2,color:p.surface}}}},
    text:SP.n.map(v=>v===0?'0 — never reached':v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:10.5}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{text}}<extra></extra>'
  }}], base(p, {{margin:{{l:210,r:130,t:14,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', tickfont:{{size:10.5}}, automargin:true}},
      xaxis:{{type:'log', autorange:true, gridcolor:p.grid,
              title:{{text:'articles (log scale) — ' + SP.zero + ' of ' + SP.d.length +
                     ' desks returned nothing', font:{{size:11}}}}}}}}), CFG);

  // 6. syndication
  Plotly.react('p-syn', [{{
    type:'bar', x:DATA.syn.k, y:DATA.syn.n,
    marker:{{color:DATA.syn.k.map(k=>k==='1'?p.s3:p.s1), line:{{width:2,color:p.surface}}}},
    text:DATA.syn.n.map(v=>v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:11}}, cliponaxis:false,
    hovertemplate:'carried by %{{x}} outlet(s)<br>%{{y:,}} stories<extra></extra>'
  }}], base(p, {{yaxis:{{type:'log', gridcolor:p.grid, title:{{text:'stories (log)', font:{{size:11}}}}}},
                // type:'category' is REQUIRED, not cosmetic. The x values are '1'..'9' plus '10+';
                // left to auto-detect, Plotly reads the nine numeric-looking strings as numbers,
                // makes the axis linear, and then silently DROPS the one bar it cannot place -- the
                // 10+ bucket, which is the whole point of having a bucket.
                xaxis:{{type:'category', title:{{text:'outlets carrying the story', font:{{size:11}}}},
                        gridcolor:p.grid}}}}), CFG);

  // 7. day of week
  Plotly.react('p-dow', [{{
    type:'bar', x:DATA.dow.k, y:DATA.dow.n, marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
    hovertemplate:'%{{x}}<br>%{{y:,}} articles<extra></extra>'
  }}], base(p, {{yaxis:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}}}}), CFG);

  // 8. authors
  const A = DATA.authors;
  Plotly.react('p-auth', [{{
    type:'bar', orientation:'h', x:A.n.slice().reverse(), y:A.a.slice().reverse(),
    marker:{{color:A.a.slice().reverse().map(s=>s.startsWith('(')?p.grid:p.s3),
             line:{{width:2,color:p.surface}}}},
    text:A.n.slice().reverse().map(v=>v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:10}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'
  }}], base(p, {{margin:{{l:200,r:70,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', tickfont:{{size:10}}, automargin:true}},
      // log: the no-byline bucket is ~1000x the busiest writer, so a linear axis renders every
      // author as a zero-length stub against it
      xaxis:{{type:'log', autorange:true, gridcolor:p.grid,
              title:{{text:'articles (log scale)', font:{{size:11}}}}}}}}), CFG);
}}
  // ---- FBS-ONLY PANELS. Guarded on DATA.era, which only the bootstrap build emits.
  const ERA = DATA.era;
  if (ERA) {{
    const H = ERA.handoff;
    const pre = ERA.cal.map((d,i) => d <  H ? ERA.counts[i] : null);
    const post= ERA.cal.map((d,i) => d >= H ? ERA.counts[i] : null);
    Plotly.react('p-seam', [
      {{type:'bar', name:'GKG + wayback', x:ERA.cal, y:pre,
        marker:{{color:p.s1}}, hovertemplate:'%{{x}}<br>%{{y}} articles<extra>GKG</extra>'}},
      {{type:'bar', name:'websearch daily', x:ERA.cal, y:post,
        marker:{{color:ST.warning}}, hovertemplate:'%{{x}}<br>%{{y}} articles<extra>websearch</extra>'}}
    ], base(p, {{barmode:'overlay', showlegend:true,
        legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
        margin:{{l:60,r:20,t:38,b:44}},
        shapes:[{{type:'line', x0:H, x1:H, yref:'paper', y0:0, y1:1,
                  line:{{color:p.text2, width:1.5, dash:'dash'}}}}],
        annotations:[{{x:H, xanchor:'left', yref:'paper', y:0.98, yanchor:'top', showarrow:false,
                       font:{{size:10.5, color:p.text2}}, text:' handoff'}}],
        xaxis:{{type:'category', tickangle:-45, tickfont:{{size:8}}, nticks:22}},
        yaxis:{{gridcolor:p.grid, title:{{text:'articles that day', font:{{size:11}}}}}}}}), CFG);

    // chars the scout is handed, per era -- p10/median/p90 as a grouped bar
    const CP=ERA.chars_pre, CQ=ERA.chars_post;
    Plotly.react('p-chars', [
      {{type:'bar', name:'GKG + wayback', x:['p10','median','p90'], y:[CP.p10,CP.med,CP.p90],
        marker:{{color:p.s1}}, text:[CP.p10,CP.med,CP.p90], textposition:'outside',
        textfont:{{size:11,color:p.fg}}, cliponaxis:false,
        hovertemplate:'%{{x}}: %{{y}} chars<extra>GKG</extra>'}},
      {{type:'bar', name:'websearch daily', x:['p10','median','p90'], y:[CQ.p10,CQ.med,CQ.p90],
        marker:{{color:ST.warning}}, text:[CQ.p10,CQ.med,CQ.p90], textposition:'outside',
        textfont:{{size:11,color:p.fg}}, cliponaxis:false,
        hovertemplate:'%{{x}}: %{{y}} chars<extra>websearch</extra>'}}
    ], base(p, {{barmode:'group', showlegend:true,
        legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
        margin:{{l:64,r:20,t:38,b:44}},
        yaxis:{{gridcolor:p.grid, title:{{text:'characters the scout reads', font:{{size:11}}}}}}}}), CFG);

    // trailing three weeks -- the operational card. A ZERO day is drawn in the critical colour so a
    // silent cron failure is the loudest thing on the panel.
    const T=ERA.trailing, tx=T.map(r=>r[0]), ty=T.map(r=>r[1]);
    const med=ERA.post_per_day;
    Plotly.react('p-pull', [{{
      type:'bar', x:tx, y:ty,
      marker:{{color:ty.map(v => v===0 ? ST.critical : (v < med*0.5 ? ST.warning : p.s1)),
               line:{{width:2,color:p.surface}}}},
      hovertemplate:'%{{x}}<br>%{{y}} articles<extra></extra>'}}],
      base(p, {{margin:{{l:60,r:20,t:24,b:60}},
        shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:med, y1:med,
                  line:{{color:p.text2, width:1.2, dash:'dot'}}}}],
        xaxis:{{type:'category', tickangle:-45, tickfont:{{size:9}}}},
        yaxis:{{gridcolor:p.grid, title:{{text:'articles pulled', font:{{size:11}}}}}}}}), CFG);
  }}

draw();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
</script>
</body></html>"""

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    print(f"wrote {out}  ({len(doc) / 1024:.0f} KB, {n:,} articles)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # --run IS the corpus dir here (this page reads its pool.json). The default has drifted twice:
    # to data/backtest_1yr, which silently rebuilt the page on 1/3 of the data (caught 2026-08-12),
    # and then to data/backtest_3yr while the curator had already moved to the _v5 pool. It comes
    # from dash_nav now, so the corpus is named in ONE place for every page that reads one.
    ap.add_argument("--run", default=_canon.CANON_CORPUS)
    ap.add_argument("--out", default="")
    ap.add_argument("--bootstrap", action="store_true",
                    help="render FBS (docs/fbs.html) off the assembled bootstrap corpus "
                         "(src/bootstrap_corpus) instead of a single run's pool.json")
    a = ap.parse_args(argv)
    out = a.out or ("docs/fbs.html" if a.bootstrap else "docs/fbt.html")
    # THE GATE. FBT describes ONE corpus, so that is the whole check -- there is no curation and no
    # book here. --bootstrap is exempt: FBS renders the assembled bootstrap corpus by design, which
    # is a different corpus on purpose rather than a drifted one.
    if not a.bootstrap:
        _p = []
        _iv = _canon.check_interpreter()
        if _iv:
            _p.append(_iv)
        if a.run != _canon.CANON_CORPUS:
            _p.append(f"corpus is {a.run}, canonical is {_canon.CANON_CORPUS}")
        _canon.require_publishable(out, "FBT", _p)
    build(ROOT / a.run if not Path(a.run).is_absolute() else Path(a.run),
          ROOT / out if not Path(out).is_absolute() else Path(out),
          bootstrap=a.bootstrap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
