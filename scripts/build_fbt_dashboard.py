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
import re
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))       # gkg: specialty/blocklist domain matching
sys.path.insert(0, str(ROOT / "scripts"))   # dash_nav: shared cross-page nav

import bootstrap_corpus as _bc  # noqa: E402  owns the query-tag convention (beat / engine)
import lede as _lede  # noqa: E402  the ONE provenance definition, shared with the curator pages
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
# s4 ADDED 2026-08-24. A fourth categorical hue was needed once green stopped being available for
# ordinary series: under the CBT colour convention green means GAIN and red means LOSS, so `s3` is
# reserved and any panel with 3+ non-polarity series had nothing left to reach for. Purple validated
# with the dataviz palette checker against both surfaces -- ALL CHECKS PASS as {s1, s2, s4} in light
# (#2a78d6/#eb6834/#7c5cd6) and dark (#3987e5/#d95926/#9b7ce8). #a78bfa was tried first for dark and
# FAILED the lightness band at 0.709.
LIGHT = {"surface": "#fcfcfb", "text": "#0b0b0b", "text2": "#52514e", "grid": "#e6e5e1",
         "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#7c5cd6",
         "ord": ["#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"]}
DARK = {"surface": "#1a1a19", "text": "#ffffff", "text2": "#c3c2b7", "grid": "#33322f",
        "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#9b7ce8",
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




def beat_counts(arts) -> collections.Counter:
    """Articles per BEAT, normalised and de-duplicated per article.

    NOT a count of raw query tags. See bootstrap_corpus.beat_of: Anthropic tags every search with
    `before:<date>`, so the raw counter holds one key per beat PER DAY. Counting those made the
    "Beats firing" tile read 160/43 -- 160 distinct tags against 43 configured beats, i.e. "-117
    beats returned nothing" -- while every per-beat total simultaneously missed its Anthropic half."""
    c = collections.Counter()
    for a in arts:
        c.update(_bc.beats_of(a))
    return c


def verdicts(arts, stats, gem, all_beats, aud: dict, handoff: str = "") -> list[dict]:
    """The headline judgements. Each is (label, value, status, why) -- the page leads with these so a
    reader sees what is WRONG before they see a pretty time series.

    `handoff` (bootstrap only) SCOPES THE THREE TEXT TILES to the pre-handoff era. `lede`, `author`
    and the wayback arm are GKG-side fields that websearch articles do not carry at all, so averaged
    over a corpus that is a fifth websearch they describe neither era -- "has body text 77%" would be
    reporting the era MIX, and would move whenever the forward half grows. Scoped, they are the same
    GKG measurement the FBT page makes, over an honest denominator."""
    n = len(arts)
    days = {a.get("published_date", "")[:10] for a in arts if a.get("published_date")}
    # the population the text tiles are computed over: pre-handoff only when there is a handoff
    txt = [a for a in arts if (a.get("published_date") or "")[:10] < handoff] if handoff else arts
    n_txt = max(len(txt), 1)
    txt_of = f" of the {len(txt):,} pre-handoff" if handoff else f" of {n:,}"
    lede = sum(1 for a in txt if a.get("lede_live") or a.get("lede"))
    auth = sum(1 for a in txt if a.get("author"))
    beats = beat_counts(arts)
    n_beats = len(all_beats)
    # only CONFIGURED beats count as "firing" -- the corpus also carries beats from earlier
    # vocabularies and a handful of free-form Anthropic searches, and scoring those against today's
    # 43-beat denominator is what produced a negative silent-beat count.
    fired = sum(1 for b in all_beats if beats.get(b))
    retired = sum(1 for b in beats if b not in all_beats)
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
    clean_n = sum(1 for a in txt if a.get("lede"))
    clean_pct = 100 * clean_n / n_txt

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
        dict(label="Has body text", value=f"{100 * lede / n_txt:.0f}%", sub=f"{lede:,}{txt_of}",
             status=st(100 * lede / n_txt, 85, 60),
             why="Share with real article text. The rest reach the curator as a headline only."
                 + (" GKG era only — websearch articles carry no lede." if handoff else "")),
        dict(label="Has byline", value=f"{100 * auth / n_txt:.0f}%", sub=f"{auth:,} named authors",
             status=st(100 * auth / n_txt, 60, 30),
             why="Share with a named human author. Wire and PR copy have none by design."
                 + (" GKG era only — websearch articles carry no author." if handoff else "")),
        # REPLACED the "Volume floor" tile 2026-08-16. That one compared a thin day (p10) against a
        # typical day and sat permanently at CRITICAL -- but the thinnest 10% of days were 71 Sundays
        # and 39 Saturdays out of ~112. Pooling weekends with weekdays makes the distribution bimodal,
        # so p10 lands in the weekend cluster and the median in the weekday one, and the ratio measures
        # the gap BETWEEN two populations rather than any retrieval shortfall. Split properly it was
        # healthy either way: weekdays 73%, weekends 58%. A permanently-red tile that is measuring the
        # calendar trains you to ignore the light.
        dict(label="Clean text", value=f"{clean_pct:.0f}%",
             sub=f"{clean_n:,}{txt_of} via wayback",
             status=st(clean_pct, 75, 50),
             why="Fraction of articles whose text was retrieved via wayback."
                 + (" GKG era only — the wayback pass never ran on the websearch half."
                    if handoff else "")),
        dict(label="Beats firing", value=f"{fired}/{n_beats}",
             sub=(f"{n_beats - fired} configured beat(s) returned nothing"
                  + (f" · {retired} retired/ad-hoc also present" if retired else "")),
             status=st(100 * fired / max(n_beats, 1), 100, 85),
             why="Configured standing searches that returned at least one article. Counted per "
                 "beat, not per query tag — Anthropic dates every search, so one beat arrives "
                 "under a different tag each day."),
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
    """One EXPLICITLY numbered panel, rendered straight to HTML.

    Kept as-is because build_cbt_dashboard.py and build_sbt_dashboard.py import this name and pass
    a leading number. Changing this signature in place on 2026-08-24 broke both of them -- they are
    separate entry points, so nothing in an FBT/FBS build exercises them. FBT/FBS itself uses
    `panel_rec` + `render_panels` (positional numbering); see that docstring for why."""
    t = (f'<details class="tbl"><summary>data table</summary>{table}</details>') if table else ""
    return (f'<section class="panel"><h2>{num}. {esc(title)}</h2><p class="lead">{lead}</p>'
            f'<div id="{div_id}" class="plot" style="height:{height}px"></div>{t}</section>')


def panel_rec(title: str, lead: str, div_id: str, height: int, table: str = "",
              side: bool = False, width: int = 0) -> dict:
    """One panel, NOT yet numbered. `render_panels` assigns the number from list position.

    Panels used to carry a hard-coded number, which meant dropping one silently renumbered every
    panel after it while the prose kept pointing at the OLD numbers ("see panel 10"). Numbering by
    position and writing cross-references as `@@div-id@@` makes both correct by construction --
    the FBS arm drops five panels the FBT arm keeps, so the two pages number differently."""
    return {"title": title, "lead": lead, "id": div_id, "h": height, "t": table,
            "side": side, "w": width or height}


def render_panels(items: list[dict]) -> str:
    idx = {it["id"]: i for i, it in enumerate(items, 1)}
    out = []
    for i, it in enumerate(items, 1):
        lead = re.sub(r"@@([a-z0-9-]+)@@", lambda m: str(idx.get(m.group(1), "?")), it["lead"])
        t = (f'<details class="tbl"><summary>data table</summary>{it["t"]}</details>') if it["t"] else ""
        # SIDE LAYOUT: caption left, SQUARE plot right. For a panel whose shape carries the
        # meaning -- a scatter is read as a cloud, and a 16:9 box stretches one axis against the
        # other -- the plot wants to be square, which leaves the caption a column of its own.
        # Wraps to stacked below ~880px, so the square survives on a phone.
        if it.get("side"):
            out.append(
                f'<section class="panel"><h2>{i}. {esc(it["title"])}</h2>'
                f'<div style="display:flex;gap:26px;align-items:flex-start;flex-wrap:wrap">'
                f'<p class="lead" style="flex:1 1 260px;max-width:340px;margin:0">{lead}</p>'
                f'<div id="{it["id"]}" class="plot" style="flex:0 1 {it["w"]}px;'
                f'width:min({it["w"]}px,100%);height:{it["h"]}px"></div></div>{t}</section>')
            continue
        out.append(f'<section class="panel"><h2>{i}. {esc(it["title"])}</h2><p class="lead">{lead}</p>'
                   f'<div id="{it["id"]}" class="plot" style="height:{it["h"]}px"></div>{t}</section>')
    return "".join(out)


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
        org_tagger = bootstrap_corpus.profile_org_tagger()
        arts, meta = bootstrap_corpus.load(org_tagger=org_tagger)
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
    import ast as _ast
    era = None
    if bootstrap:
        import datetime as _dt
        _H = meta["handoff"]
        _by_day = collections.Counter()
        _pre_days, _post_days = collections.Counter(), collections.Counter()
        _chars = {"pre": [], "post": []}
        _clen = collections.defaultdict(list)      # per-day scout-visible text lengths
        for _a in arts:
            _d = (_a.get("published_date") or "")[:10]
            if not _d:
                continue
            _by_day[_d] += 1
            (_pre_days if _d < _H else _post_days)[_d] += 1
            # len(scout_text), NOT len(snippet). The raw pool keeps GKG text in `lede` and leaves
            # `snippet` as a short leftover, so the old `len(snippet)` reported the pre-handoff era
            # at 82 characters when the scout is handed 157 -- and the panel prose drew a 3.7x
            # conclusion from a 1.9x fact. See lede.scout_text.
            #
            # ARTICLES WITH BODY TEXT ONLY. A headline-only article contributes 0, and mixing those
            # zeros into a length distribution makes the median describe NEITHER population: on
            # 2026-08-25, 25 of 105 articles carry no body and the median of the mixture is 178
            # while the median of the articles that HAVE text is 893. Reporting 178 as "what the
            # scout is handed" would be the exact failure this panel was built to expose. How many
            # articles arrive headline-only is a real question and it has its own panel (p-provtime).
            _n = len(_lede.scout_text(_a))
            if _n:
                _clen[_d].append(_n)
                _chars["pre" if _d < _H else "post"].append(_n)
        _days = sorted(_by_day)
        # calendar-complete axis: a MISSING day must draw as a hole, not be skipped. Two days were
        # lost on 2026-07-11/12 when cron did not fire, and a day-index axis would hide that.
        _d0, _d1 = _dt.date.fromisoformat(_days[0]), _dt.date.fromisoformat(_days[-1])
        _cal = [( _d0 + _dt.timedelta(days=i)).isoformat() for i in range((_d1 - _d0).days + 1)]
        _post_cal = [d for d in _cal if d >= _H]
        # PULL HEALTH IS A COLLECTION-DATE QUESTION, not a publication-date one. Both live here so
        # the difference is explicit: `_zero` below is publication-side (a day nothing was PUBLISHED
        # under), while the `_pull_*` fields are collection-side (a morning the cron did or did not
        # FIRE). Scoring cron health on publication dates hides exactly the failure the panel exists
        # to catch -- 2026-08-15's cron never ran, yet its publication bucket holds 4 articles that
        # later pulls backfilled through Anthropic's week-long lookback, so it reads as a thin day
        # rather than a dead one.
        _pull_days = set(meta.get("pull_days") or [])
        _pull_kept = meta.get("pull_kept") or {}
        # AMBER MEANS "POOR FOR THIS KIND OF DAY". Two medians -- weekday and weekend -- taken over
        # the pulls that actually FIRED (a missed cron is red and must not drag the bar it is judged
        # against down). Weekend is Sat/Sun by ISO weekday; the corpus runs ~38/day then against
        # ~120 on weekdays, so one flat median mislabels almost every weekend as under-collection.
        def _is_we(d: str) -> bool:
            return date.fromisoformat(d).isoweekday() >= 6

        def _med(vals: list[int]) -> float:
            v = sorted(vals)
            return v[len(v) // 2] if v else 0.0

        _pull_med = {
            "weekday": _med([_pull_kept.get(d, 0) for d in _post_cal
                             if d in _pull_days and not _is_we(d)]),
            "weekend": _med([_pull_kept.get(d, 0) for d in _post_cal
                             if d in _pull_days and _is_we(d)]),
        }

        def _pull_thr(d: str) -> float:
            """Half the median for THIS day-type; falls back to the other if a type has no pulls."""
            m = _pull_med["weekend" if _is_we(d) else "weekday"] or \
                _pull_med["weekday" if _is_we(d) else "weekend"]
            return round(0.5 * m, 1)
        _pull_missing = [d for d in _post_cal if d not in _pull_days]          # cron did not fire
        _pull_dead = [d for d in _post_cal if d in _pull_days and not _pull_kept.get(d)]
        _zero = [d for d in _post_cal if _by_day.get(d, 0) == 0]
        _lastpull = max(_pull_days) if _pull_days else (_post_cal[-1] if _post_cal else None)
        def _q(v):
            v = sorted(v)
            return {"med": v[len(v) // 2], "p10": v[len(v) // 10], "p90": v[9 * len(v) // 10]} if v else {}
        # THE CEILING IS THE POINT, so it is measured rather than described. `cap` is the share of
        # the day sitting on that day's modal length: a natural spread scores near zero, a day being
        # truncated scores near 1 and the IQR band collapses onto the median. That is how the
        # [:300] ingest cap reads on this panel, and how its 2026-08-24 removal reads as a step.
        def _dstat(v):
            v = sorted(v)
            if not v:
                return None
            _mode = collections.Counter(v).most_common(1)[0][0]
            return {"med": v[len(v) // 2], "p25": v[len(v) // 4], "p75": v[3 * len(v) // 4],
                    "n": len(v), "cap": round(sum(1 for x in v if x == _mode) / len(v), 3),
                    "mode": _mode}
        _cd = [(d, _dstat(_clen.get(d) or [])) for d in _cal]
        _chars_day = {
            "cal":  [d for d, r in _cd if r],
            "med":  [r["med"] for _d, r in _cd if r],
            "p25":  [r["p25"] for _d, r in _cd if r],
            "p75":  [r["p75"] for _d, r in _cd if r],
            "n":    [r["n"] for _d, r in _cd if r],
            "cap":  [r["cap"] for _d, r in _cd if r],
            "mode": [r["mode"] for _d, r in _cd if r],
        }
        # ---- ORG TAGGER: three questions, one payload -----------------------------------------
        # Websearch articles arrive with no `orgs`, so the tagger fills them and company bundling
        # works post-handoff as it already does on GKG. It runs from cron every morning, which means
        # it can fail QUIETLY -- a missed day is a day of thinner bundles with nothing on screen.
        # These are the three numbers that say whether it is working, and each is here because a
        # specific failure happened while building it:
        #   coverage  -- distinguishes "tagger said NO COMPANY" (correct, ~16%) from "tagger was
        #                NEVER ASKED" (a real gap). Reporting them together cried wolf permanently.
        #   mem/art   -- bundle memberships per article. The number that actually predicts whether
        #                bundling works: at 0.59 the curation got worse, at 1.46 it did not. GKG
        #                runs at 1.17, drawn as the benchmark.
        #   sizes     -- the singleton/multi split against GKG's, i.e. "does this look like the
        #                corpus the backtest was validated on".
        _tags = {}
        try:
            import org_tagger as _otg, orgs as _og
            _tcache = _otg.load_cache(org_tagger) if org_tagger else {}
            _cn = _og.build_canon(arts)
            _tmp = _og.ticker_map(arts, _cn)
            _byday = collections.defaultdict(lambda: [0, 0, 0])   # company / no-company / unseen
            for _a in arts:
                _d = (_a.get("published_date") or "")[:10]
                if not _d or _d < _H:
                    continue
                if _og.article_orgs(_a, _cn, _tmp):
                    _byday[_d][0] += 1
                elif _a.get("url") in _tcache:
                    _byday[_d][1] += 1
                else:
                    _byday[_d][2] += 1
            _days = [d for d in _cal if d >= _H]
            def _mem(g):
                if not g:
                    return 0.0
                return round(sum(len(_og.article_orgs(x, _cn, _tmp)) for x in g) / len(g), 3)
            _bywin = collections.defaultdict(list)
            for _a in arts:
                _d = (_a.get("published_date") or "")[:10]
                if _d:
                    _bywin[_d].append(_a)
            _pre_arts = [x for x in arts if (x.get("published_date") or "")[:10] < _H]
            _post_arts = [x for x in arts if (x.get("published_date") or "")[:10] >= _H]
            def _sizes(g):
                return _og.size_histogram(_og.group(g, canon=_cn, tmap=_tmp), pct=True)
            _tags = {
                "on": bool(org_tagger), "model": org_tagger or None,
                "cache": (_otg.cache_path(org_tagger).name if org_tagger else None),
                "days": _days,
                "company": [_byday[d][0] for d in _days],
                "nocomp":  [_byday[d][1] for d in _days],
                "unseen":  [_byday[d][2] for d in _days],
                "mem_days": _days,
                "mem": [_mem(_bywin.get(d, [])) for d in _days],
                "mem_gkg": _mem(_pre_arts), "mem_post": _mem(_post_arts),
                "sizes_gkg": _sizes(_pre_arts), "sizes_post": _sizes(_post_arts),
                "size_labels": [b[2] for b in _og.SIZE_BUCKETS],
            }
        except Exception as _e:  # noqa: BLE001 -- a panel must never take the page down
            # PRINTED, not just recorded. The first version referenced an out-of-scope name, raised
            # NameError, and this handler turned that into "tagging is off" -- three panels silently
            # absent on a page whose whole job is to say whether tagging is working.
            import sys as _s
            print(f"  FBS: org-tagger panels unavailable ({type(_e).__name__}: {_e})",
                  file=_s.stderr, flush=True)
            _tags = {"on": False, "error": f"{type(_e).__name__}: {_e}"}
        era = {
            "handoff": _H, "cal": _cal, "counts": [_by_day.get(d, 0) for d in _cal],
            "pre_n": sum(_pre_days.values()), "post_n": sum(_post_days.values()),
            "pre_per_day": round(sum(_pre_days.values()) / max(len(_pre_days), 1), 1),
            "post_per_day": round(sum(_post_days.values()) / max(len(_post_days), 1), 1),
            "zero_days": _zero, "n_missing": sum(1 for d in _post_cal if d not in _by_day),
            "last_day": _lastpull, "post_days": len(_post_cal),
            # collection-side pull health
            "pull_missing": _pull_missing, "pull_dead": _pull_dead,
            "pull_n": len(_post_cal) - len(_pull_missing),
            "chars_pre": _q(_chars["pre"]), "chars_post": _q(_chars["post"]),
            "tags": _tags,
            # PER-DAY, because the two era medians hide the two things that matter most: the
            # post-handoff median sits ON an ingest ceiling rather than describing the source, and
            # that ceiling MOVED. A median cannot show either; a time series shows both at a glance.
            "chars_day": _chars_day,
            # what the scout is handed, per era, AFTER lede.apply has filled snippet
            # trailing three weeks BY COLLECTION DATE: (day, articles kept, cron fired?)
            # (day, articles kept, cron fired?, the threshold amber is judged against).
            # PER DAY-TYPE, not one flat median. A weekend collects about a third of a weekday --
            # measured on this corpus, ~38 vs ~120 -- so a single median taken across mixed days sits
            # near the weekday level and half of it still lands ABOVE a normal Saturday. Every
            # weekend then draws amber, six of them in a trailing three weeks, which trains the
            # reader to ignore the colour that is supposed to mean "this pull under-collected".
            "trailing": [(d, _pull_kept.get(d, 0), d in _pull_days, _pull_thr(d))
                         for d in _post_cal[-21:]],
            "pull_med": _pull_med,
        }
        # ---- SOURCE OVERLAP and BEAT FIRING across the seam --------------------------------------
        # NORMALISE THE HOSTNAME FIRST. The two gatherers disagree: 58% of websearch sources carry a
        # "www." prefix and 0% of GKG's do, so a raw comparison reported 17 shared sources when the
        # true figure is 58, and 14% of post-handoff articles from a shared source when it is 41%.
        # That is a display defect, not a functional one -- specialty_allow and mill_block match by
        # SUBSTRING, so both lists hit identically either way (verified: 521 of 2,144 post articles
        # match specialty_allow under both spellings). It still corrupts every per-source count on
        # this page, the per-source panel included, by splitting a publisher into two rows.
        import re as _re
        _nsrc = lambda a: _re.sub(r"^www\.", "", (a.get("source") or "?").strip().lower())
        _sp, _sq = collections.Counter(), collections.Counter()
        _bp, _bq = collections.Counter(), collections.Counter()
        for _a in arts:
            _d = (_a.get("published_date") or "")[:10]
            if not _d:
                continue
            _isPre = _d < _H
            (_sp if _isPre else _sq)[_nsrc(_a)] += 1
            _q = _a.get("queries")
            if isinstance(_q, str):
                try: _q = _ast.literal_eval(_q)
                except Exception: _q = []
            # NORMALISE. Comparing raw tags across the seam compares GKG's bare beat names against
            # Anthropic's dated ones, which invents a "new" beat per beat per day and simultaneously
            # reports a beat as STOPPED when it fired the whole time under a dated tag. Both errors
            # ran on this panel: 117 phantom new beats, and a false blind-spot call on the FDA beat.
            for _b in {_bc.beat_of(_x) for _x in (_q or []) if _bc.beat_of(_x)}:
                (_bp if _isPre else _bq)[_b] += 1
        _shared = set(_sp) & set(_sq)
        _postN = sum(_sq.values()) or 1
        era["src"] = {
            "n_pre": len(_sp), "n_post": len(_sq), "shared": len(_shared),
            "post_from_shared": round(100 * sum(_sq[x] for x in _shared) / _postN, 1),
            "www_post": round(100 * sum(1 for a in arts
                              if (a.get("published_date") or "")[:10] >= _H
                              and (a.get("source") or "").startswith("www.")) / _postN, 0),
            "top_new": [[x, _sq[x]] for x, _ in _sq.most_common(60) if x not in _shared][:12],
            "top_shared": [[x, _sq[x], _sp.get(x, 0)] for x, _ in _sq.most_common(60)
                           if x in _shared][:12]}
        era["beat"] = {
            "n_pre": len(_bp), "n_post": len(_bq), "shared": len(set(_bp) & set(_bq)),
            "stopped": [[b, _bp[b]] for b in sorted(set(_bp) - set(_bq))],
            "new_only": len(set(_bq) - set(_bp))}

    comp = None
    if bootstrap:
        # An assembled corpus has no ingest funnel -- there is no BigQuery scan to narrow. What matters
        # instead is COMPOSITION: how much came from each era, which ENGINE fetched the websearch half,
        # and where the seam is. Same panel slot, honest content, rather than a funnel chart with
        # nothing to put in it. VERTICAL + LINEAR, deliberately: the two things the panel has to say
        # are "the GKG era is ~4x the websearch era" and "Anthropic is a sliver of the websearch era",
        # and both are RATIOS -- a log axis would flatten the first and a horizontal bar buries the
        # engine stack in a 200px label gutter. The 15 spam drops SHOULD look like nothing.
        comp = {"handoff": meta["handoff"],
                "total": meta["n_gkg"] + meta["n_websearch"],
                "gkg": meta["n_gkg"], "websearch": meta["n_websearch"],
                "tavily": meta["n_tavily"], "anthropic": meta["n_anthropic"],
                "both": meta["n_both"], "spam": meta["spam_dropped"]}
        fun = []
    else:
        fun = funnel_rows(stats, n, run)

    import difflib
    bymonth = collections.defaultdict(
        lambda: {"n": 0, "text": 0, "auth": 0, "clean": 0, "live": 0, "none": 0, "both": 0, "div": 0,
                 # the search class and its per-engine split; zero for a GKG-only month
                 "search": 0, "search_tavily": 0, "search_anthropic": 0,
                 "search_both": 0, "search_unknown": 0})
    bycal: collections.Counter = collections.Counter()   # calendar months, for panel 2's top row
    # WEEKLY BINS ON THE BOOTSTRAP, MONTHLY ON THE 3-YEAR CORPUS. The handoff is 2026-07-28, which
    # falls MID-MONTH, so a calendar-month bar blends 27 days of GKG with 4 of websearch -- and the
    # provenance panel's whole claim is that the handoff is a CLEAN SUBSTITUTION. Monthly bars
    # cannot show that; they show a partial one, in the single bar that matters most. Weekly gives
    # ~22 bars over the bootstrap's five months and puts the seam inside one week instead of one
    # month. FBT keeps months: three years weekly is 156 bars.
    def _bin(ds: str) -> str:
        if not bootstrap:
            return ds[:7]
        _d = _dt.date.fromisoformat(ds)
        return (_d - _dt.timedelta(days=_d.weekday())).isoformat()   # Monday of that week

    for a in arts:
        _ds = (a.get("published_date") or "")[:10]
        if not _ds:
            continue
        m = _bin(_ds)
        d = bymonth[m]
        d["n"] += 1
        # THE TOP ROW OF PANEL 2 IS LABELLED "per month" AND MUST BE MONTHS. Everything else on this
        # page bins through _bin(), which is WEEKLY on the bootstrap (see its comment) -- so on FBS
        # `months` holds Monday keys and the "per month" row was drawing the "per ISO week" row again
        # under the wrong label. It only LOOKED empty rather than duplicated because the stacked mix
        # series it actually draws are keyed by calendar month and found nothing at a Monday key.
        # Counted separately here so the three rows are genuinely month / week / day. Identical to
        # `months` on FBT, where _bin already returns ds[:7].
        bycal[_ds[:7]] += 1
        d["text"] += 1 if (a.get("lede_live") or a.get("lede")) else 0
        d["auth"] += 1 if a.get("author") else 0
        # PROVENANCE, not just presence. `clean` counts articles read from an AS-OF archived capture;
        # `live` counts today's page, which is look-ahead-biased. A backtest number is only defensible
        # to the extent it rests on the clean band, so the two are never merged into one "has text".
        # SEARCH is its own class -- shared with lede.apply and the curator dashboards via
        # lede.provenance(), because a third private copy of this rule is how the first two
        # diverged. Without it every websearch article lands in `none`, which reads as "a bare
        # headline" about an article carrying a 300-1500 char engine snippet.
        _pv = _lede.provenance(a)
        d["clean" if _pv == "archived" else
          "live" if _pv == "live page" else
          "search" if _pv == "search snippet" else "none"] += 1
        if _pv == "search snippet":
            d[f"search_{a.get('engine') or 'unknown'}"] += 1
        if a.get("lede") and a.get("lede_live"):     # the bias sample: both arms on the same article
            d["both"] += 1
            if difflib.SequenceMatcher(None, a["lede"], a["lede_live"]).ratio() <= 0.80:
                d["div"] += 1
    months = sorted(bymonth)
    cal_keys = sorted(bycal)

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

    # SOURCE MIX PER BUCKET, at all three resolutions. Bootstrap only: the point is to see WHICH
    # retrieval path supplied each day, so the era seam and the engine mix read off the same bars the
    # coverage panel already draws -- which is what made the standalone seam panel redundant.
    # Series are exclusive and sum to the flat count, so the stack equals the plain total.
    def _series(a):
        return "gkg" if a.get("era") != "websearch" else a.get("engine", "tavily")
    MIX = ("gkg", "tavily", "both", "anthropic")
    mix = {k: {"d": collections.Counter(), "w": collections.Counter(), "m": collections.Counter()}
           for k in MIX}
    mix_ready = bootstrap and any(a.get("pull_date") for a in arts)
    if bootstrap:
        for a in arts:
            d = (a.get("published_date") or "")[:10]
            if not d:
                continue
            k = _series(a)
            if k not in mix:
                continue
            mix[k]["d"][d] += 1
            mix[k]["m"][d[:7]] += 1
            try:
                _dt_ = date.fromisoformat(d)
                mix[k]["w"][(_dt_ - timedelta(days=_dt_.weekday())).isoformat()] += 1
            except ValueError:
                pass

    # THE UNSETTLED TAIL. Anthropic's gather looks back a week, so an article published today is
    # usually caught by a LATER pull -- measured on this corpus, Tavily is same-day for 100% of what
    # it finds while Anthropic's p90 lag is 6 days. The most recent published-dates therefore have
    # not finished collecting their Anthropic share, and shading them stops the tail being read as
    # Anthropic going dark. Measured, not hard-coded, so it tracks the real gather config.
    settle = None
    if bootstrap and mix_ready:
        _lag = sorted((date.fromisoformat(a["pull_date"])
                       - date.fromisoformat(a["published_date"][:10])).days
                      for a in arts
                      if a.get("era") == "websearch" and a.get("engine") != "tavily"
                      and a.get("pull_date") and a.get("published_date"))
        _lag = [d for d in _lag if 0 <= d <= 30]         # a mis-parsed date must not blow up the band
        if _lag:
            # MAX, not a percentile. The band's job is to cover every publication-date that can still
            # gain articles, and UNDER-covering is the dangerous direction -- an unshaded day the
            # reader trusts is the failure. Max cannot run away here because the gather's lookback is
            # a hard ceiling on the lag (a 7-day lookback can only reach 6 days back from its anchor),
            # so on this corpus max == p90 == 6 and the band is COMPLETE rather than probabilistic.
            _win = _lag[-1]
            _end = date.fromisoformat(meta["end"])
            settle = {"from": (_end - timedelta(days=_win)).isoformat(), "days": _win,
                      "anchor": meta["end"]}

    beats = beat_counts(arts)   # normalised per-beat article counts; see beat_counts()
    cfg = json.loads((ROOT / "retrieval_config.json").read_text())
    all_beats = [b["query"] for b in cfg["gem_beats"]] + [b["query"] for b in cfg["coverage_beats"]]
    tiles = "".join(tile(v) for v in verdicts(arts, stats, gem, all_beats, audits(run),
                                              handoff=meta.get("handoff", "") if bootstrap else ""))
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
    _BUCK = _orgs.SIZE_BUCKETS        # the ONE definition; see orgs.SIZE_BUCKETS
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
            # NORMALISE FIRST. bundle_beat looks the query up in the parent map, and a raw Anthropic
            # tag carries `before:<date>`, so it misses every entry and falls through to itself --
            # which split one beat's bundle into one bundle PER DAY. 93 of the labels on this panel
            # were dated fragments, and 30 of them were spurious one-article bundles.
            _beat[_gkg.bundle_beat(_bc.beat_of(q))] += 1
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
        "comp": comp,          # bootstrap only -- panel 1 draws composition instead of a funnel
        "backfill": {**(stats.get("wayback_overlay") or {}),
                     "m": months,
                     "none": [bymonth[m]["none"] for m in months],
                     "clean": [bymonth[m]["clean"] for m in months]},
        "prov": {"m": months,
                 # the bin the handoff falls in, computed the same way the bars are, so the dashed
                 # line lands ON a bar instead of near one
                 "hoff": (_bin(meta["handoff"]) if bootstrap else meta.get("handoff", "")[:7])
                         if meta.get("handoff") else "",
                 "clean": [bymonth[m]["clean"] for m in months],
                 "live": [bymonth[m]["live"] for m in months],
                 "search": [bymonth[m]["search"] for m in months],
                 # the search band SPLIT BY ENGINE: the question "is Anthropic supplying text
                 # Tavily does not" is a COUNT question and answerable; the P&L version is not
                 # (all websearch evidence so far comes from a single scan).
                 "search_tavily": [bymonth[m]["search_tavily"] for m in months],
                 "search_anthropic": [bymonth[m]["search_anthropic"] for m in months],
                 "search_both": [bymonth[m]["search_both"] for m in months],
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
        "monthly": {"m": cal_keys, "n": [bycal[m] for m in cal_keys]},
        # bootstrap only -- per-bucket counts for each retrieval path, same keys/order as above
        "settle": settle,
        "mix": ({k: {"d": [mix[k]["d"][x] for x in day_keys],
                     "w": [mix[k]["w"][x] for x in week_keys],
                     "m": [mix[k]["m"][x] for x in cal_keys]} for k in MIX} if bootstrap else None),
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

    panels = render_panels([
        panel_rec(("How the bootstrap corpus is assembled" if bootstrap
                  else "Filtering the ingestion funnel"),
              ((f"A CLEAN CUT at <b>{meta.get('handoff','?')}</b>, never a blend: GKG + wayback before "
                f"it, the daily websearch pull only after it — the forward test this leads to runs on "
                f"websearch alone, and blending would make the bootstrap richer than the thing it "
                f"predicts. The websearch half is itself a union of two engines: Tavily fetched "
                f"<b>{100 * (comp['tavily'] + comp['both']) / max(comp['websearch'], 1):.0f}%</b> of it "
                f"and is the only one that reaches the Dow Jones desks, Anthropic the rest, and they "
                f"overlap on <b>{100 * comp['both'] / max(comp['websearch'], 1):.1f}%</b> — all but "
                f"disjoint, so a silently dead engine is a hole, not a rounding error. Engine is "
                f"<b>inferred</b> from the query tags, not recorded (<code>bootstrap_corpus.engine_of</code>). "
                f"<br><br><b>Log y-axis</b> — the only way 9 and 15 are visible beside 9,269. "
                f"One bar per source, so every height is that source's own count."
                if bootstrap and comp else
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
                 f"{_LINK(PROFILE_URL, 'investor_profile.backtest.md')}.")),
              "p-funnel", 420 if bootstrap else 460),
        panel_rec("Coverage over time",
              "The same corpus at three resolutions: per month, per ISO week, and per day. Month "
              "shows whether any stretch of calendar is under-served, week is the scout's actual "
              "cadence, and day exposes the individual holes the coarser views average away."
              + ((f" Bars are stacked by which path fetched the article — blue is GKG + wayback "
                  f"(<b>{era['pre_per_day']}/day</b>) before the dashed handoff, orange Tavily and "
                  f"purple Anthropic (<b>{era['post_per_day']}/day</b>) after it. Anthropic searches "
                  f"a week back rather than a day, so the shaded tail has not finished filling and "
                  f"is not a gap." if era else "") if bootstrap else "")
              + " Flat and gap-free is the goal.",
              "p-vol", 700),
        panel_rec("Articles by day of week",
              "Where the volume sits across the week. A weekend trough is normal market-news "
              "behaviour, not a retrieval fault — this panel exists so a real gap is distinguishable "
              "from the ordinary weekly cycle.",
              "p-dow", 300),
        # THE LEDE/WAYBACK FAMILY -- FBT ONLY. All five measure the wayback text backfill, which by
        # construction exists only before the handoff: websearch articles carry no `lede`, `author`
        # or `text_miss` field at all, so on the bootstrap corpus every one of these plots a GKG-era
        # quantity that cliffs to zero in its last month for a structural reason, not a retrieval
        # one. FBT already measures the same machinery over the full 3-year GKG corpus, where it is
        # the point of the page. What the curator reads AFTER the handoff is panel `p-chars`, which
        # is a real panel again as of 2026-08-27 -- this comment pointed at one that had been folded
        # into p-provtime's prose as a single median, and that median was both mismeasured and, after
        # the 2026-08-24 ingest-cap removal, three days stale.
        ] + ([] if bootstrap else [
            panel_rec("Body text vs time",
                  "Fraction of articles containing text rather than just a bare headline. Older articles "
                  "score worse because their links have rotted.",
                  "p-text", 360),
            panel_rec("Lede provenance by month",
                  "Counts of where each month's text came from. <b>Clean</b> is the Wayback copy pulled "
                  "from archive.org as written when crawled soon after publication. <b>Biased</b> is the "
                  "current text (pulled via the GKG-provided url) which may have been edited since. "
                  "<b>None</b> is a bare headline.",
                  "p-prov", 380),
            panel_rec("Wayback hit rate (of URLs attempted)",
                  "A Wayback pass fills the articles that reached the curator as a bare headline — dead "
                  "links, 5xx, paywalls. Grey is the remaining hole per month, green is text recovered. "
                  "The hole concentrates in the oldest months, where links have rotted, and in the "
                  "NEWEST, where archive.org has not crawled yet. This panel reads the backfill's cache "
                  "directly rather than the corpus file, so it moves on every rebuild instead of waiting "
                  "for the multi-day pass to finish."
                  "<br><br><b>Mind the denominator.</b> The headline percentage here is hits &divide; URLs "
                  "<b>ATTEMPTED</b> — only the articles that lacked text and were queued for Wayback. It "
                  "is NOT the share of the corpus carrying text, which is the stat tile above and panel @@p-prov@@. "
                  "The two are easily confused because they currently sit a tenth of a point apart by "
                  "coincidence: the hit rate is 63.3% of attempted URLs, while live-page text happens to "
                  "be 63.4% of the corpus. Corpus text coverage is 87% (23% archived + 63% live).",
                  "p-backfill", 360),
            panel_rec("Live vs archived text divergence",
                  "Most of this corpus was read from the article's page <b>as it looks today</b>, "
                  "because that is ~2,600&times; faster than pulling an archived copy. The risk: today's "
                  "page may no longer be what ran on the decision date."
                  "<br><br>A month-stratified sample was fetched BOTH ways — today's page and the "
                  "archive.org snapshot from on-or-before that date. This plots the share where the two "
                  "differ materially: a spot check on the ~87% we could not afford to fetch cleanly. Flat "
                  "and low means today's page is a faithful stand-in at every age.",
                  "p-drift", 360),
            panel_rec("Why text is missing",
                  "The recorded reason each headline-only article failed, straight from the fetch — all "
                  "code, no AI. <b>url_recycled</b> is a real test: the page loaded and produced text, but "
                  "shared too few words with the stored headline, so that URL now serves a different "
                  "story. <b>removed</b> is HTTP 404/410. <b>blocked_or_paywalled</b> is 401/403 — a "
                  "paywall and a bot-wall are indistinguishable to an anonymous client, so they share one "
                  "honest label. <b>no_text_on_page</b> is a 200 with no prose; audited separately, 82% "
                  "are genuine walls and 18% our parser failing.",
                  "p-miss", 340),
        ]) + [
        panel_rec("Articles per beat",
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
        panel_rec("Articles per source",
              "The 50 largest outlets, plus everything else folded into one grey bar. A sweep "
              "dominated by a handful of publishers inherits their editorial priorities; a long tail "
              "means genuine breadth.",
              "p-src", 1050),
        panel_rec("Specialty-desk reach",
              "The outlets hand-picked in "
              f"{_LINK(PROFILE_URL, 'investor_profile.backtest.md')} as <code>specialty_allow</code> — "
              "the desks expected to carry the early call. On the forward path a whole search pass is "
              "restricted to them; BigQuery cannot do that, so here they are simply measured. Desks "
              "with <b>zero</b> articles are shown, not omitted — that is the finding.",
              "p-spec", 620),
        panel_rec("News replication",
              "The same story often runs on many sites at once; ingest merges those copies and "
              "counts how many outlets ran each one (plot 1 calls this step <i>syndication</i>). Most "
              "sit at 1 — a story only one outlet carried is one the market probably has not priced "
              "yet. Widest this year: 122 outlets. Log axis.",
              "p-syn", 320),
        panel_rec("Articles per author",
              "The 25 most frequent named writers, with the no-byline bucket shown in grey for scale. "
              "Wire copy, PR releases and the publisher's own name are excluded by design, so a "
              "byline here is a real person. Most of the corpus has none — that is normal for market "
              "news, not a fault. Log axis.",
              "p-auth", 640),
        panel_rec("Articles per bundle",
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
        # "The handoff seam" RETIRED 2026-08-24. It was a daily bar chart of the calendar
        # coloured by era -- the same bars panel @@p-vol@@ draws, which now stacks by
        # retrieval path and carries the handoff line, so the seam is visible there at all
        # three resolutions instead of only one. Its two load-bearing facts (the per-day
        # rates and the 07-25..07-29 confound) moved into that panel's prose.
        # REPLACES the per-era chars panel. That drew the same fact as two bars and stated it as
        # "0% archived, 0% live", which is true and reads as a text blackout; the corpus-side truth
        # is a PROVENANCE FLIP, and a flip is a time series, not a pair of eras. The char medians it
        # carried are one sentence, kept below.
        panel_rec("Text provenance over time",
              "Where each week\u2019s article text came from. <b>Archived</b> is a Wayback copy taken "
              "near publication, <b>live page</b> is today\u2019s version which may have been edited "
              "since, and <b>search snippet</b> is what the engine returned when we pulled it. At the "
              "handoff the archived and live bands go to zero and search takes over the whole bar \u2014 "
              "a clean swap, not a gap. The search band is split by engine because Tavily and Anthropic "
              "barely overlap, so each is worth paying for.",
              "p-provtime", 400),
        panel_rec("Article length over time",
              "Each day\u2019s median article length in characters; the band is the middle half. "
              "The flat 300 stretch is our own ingest cap rather than shorter news \u2014 a truncated "
              "day is one where the band collapses onto the line. The GKG era runs at a median of "
              f"<b>{era['chars_pre'].get('med','?')}</b> characters, and after we removed that cap on "
              "2026-08-24 the websearch era runs at <b>900</b>. Headline-only articles are left out, "
              "since counting their zero would describe neither population.",
              "p-chars", 400),
        panel_rec("Org tagging, per day",
              "Websearch articles arrive with no company tags, so we add them each morning after the "
              "pull. Green is an article tagged with at least one company, grey is one the tagger read "
              "and correctly found no company in (a macro or roundup story), red is one it never saw. "
              "Red is the only bad colour \u2014 it means a morning the tagging did not run.",
              "p-tagcov", 340),
        panel_rec("Bundle memberships per article",
              "How many company bundles the average article joins \u2014 the number that decides "
              "whether bundling works at all. An article that joins none is read alone with no "
              "corroboration. The dashed line is what GKG gives the backtest, so the websearch era is "
              "healthy when the solid line sits near or above it.",
              "p-tagmem", 340),
        panel_rec("Bundle sizes: websearch vs backtest",
              "Bundles holding one article, two, three and so on, as a share of all bundles in each "
              "era. This is the shape check: if the two eras have the same profile then the curator "
              "reads the websearch corpus the same way it reads the backtest one. A websearch bar "
              "taller on the left means bundles are too thin.",
              "p-tagsize", 340),
        panel_rec("Daily pull health",
              "The operational card: is the morning cron actually firing? Each bar is one morning's "
              "pull over the trailing three weeks. <b>Red</b> is a cron that did not fire or "
              "collected nothing; <b>amber</b> is a pull that ran but under-collected. "
              f"There are TWO dotted medians because there are two kinds of day: weekday "
              f"<b>{era['pull_med']['weekday']:.0f}</b> and weekend "
              f"<b>{era['pull_med']['weekend']:.0f}</b> articles, and each bar is judged against "
              "half of its OWN. A single median across both sits at the weekday level, so half of "
              "it still lands above a normal Saturday and every weekend drew amber \u2014 six of "
              "them in a trailing three weeks, which teaches the reader to ignore the one colour "
              "that is supposed to mean something. "
              "<b>This is the one panel keyed to COLLECTION date</b> \u2014 every other panel here "
              "buckets by publication date, which is the wrong axis for this question: a morning the "
              "cron missed still fills its publication bucket from later pulls (Anthropic searches a "
              "week back), so it reads as a thin day rather than a dead one.<br><br>"
              f"<b>Last pull: {era['last_day']}.</b> Mornings the cron fired since the handoff: "
              f"<b>{era['pull_n']} of {era['post_days']}</b>. "
              + (f"<b>Missed entirely: {', '.join(era['pull_missing'])}</b> \u2014 no pull file was "
                 f"written, so nothing was collected that morning."
                 if era['pull_missing'] else "None missed.")
              + (f" Pulls that ran but collected nothing: <b>{len(era['pull_dead'])}</b>."
                 if era['pull_dead'] else " Every pull that ran collected something.")
              + " A silent gap is the failure mode this panel exists to catch \u2014 the corpus "
                "stops growing and every downstream number keeps looking reasonable.",
              "p-pull", 340),
        panel_rec("Which publishers the forward half reads",
              f"Only <b>{era['src']['shared']}</b> hostnames appear in both eras, and just "
              f"<b>{era['src']['post_from_shared']}%</b> of post-handoff articles come from a "
              "publisher GKG also carried. Amber bars are the biggest websearch sources GKG NEVER "
              "had \u2014 Reuters, WSJ, Barron\u2019s and the rest of the mainstream desks; blue "
              "bars are the ones both eras share, with the GKG count beside them.<br><br>"
              "<b>This confounds any forward-vs-backtest comparison.</b> A difference in results "
              "between the two halves may be the method or may simply be a different set of "
              f"publishers. Note also that <b>{era['src']['www_post']:.0f}%</b> of websearch sources "
              "carry a <code>www.</code> prefix and none of GKG\u2019s do; the counts here are "
              "normalised, but the RAW strings are not, which splits a publisher into two rows on "
              "panel @@p-src@@. It is display-only \u2014 <code>specialty_allow</code> and "
              "<code>mill_block</code> match by substring and hit identically either way.",
              "p-srcera", 420),
        panel_rec("Which beats survive the handoff",
              f"<b>{era['beat']['shared']}</b> of the {era['beat']['n_pre']} pre-handoff beats still "
              f"fire after it, and <b>{era['beat']['new_only']}</b> more appear that GKG never used "
              "\u2014 the websearch gather runs a wider standing-query vocabulary. So the beat set "
              "EXPANDED rather than rotated, which is the reassuring reading.<br><br>"
              + (f"<b>{len(era['beat']['stopped'])} beat(s) stopped entirely:</b> "
                 + ", ".join(f"<code>{b}</code> ({n:,} pre-articles)"
                             for b, n in era['beat']['stopped'][:3])
                 + ". A beat that produced articles before the handoff and none after is a forward "
                   "BLIND SPOT \u2014 the thesis it was written to catch is no longer being looked "
                   "for."
                 if era['beat']['stopped'] else
                 "No beat stopped at the handoff \u2014 everything GKG reached is still covered."),
              "p-beatera", 380),
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
// DRAW ONLY WHAT THIS PAGE HAS. The two arms ship different panel sets (FBS drops the lede/wayback
// family, FBT has no era panels), and Plotly.react on an id that is not in the DOM THROWS -- which
// aborts draw() and leaves every panel after it blank. That exact failure blanked the whole FBS
// page once already. Missing div -> silently skip; every call below goes through here.
function react(id, data, layout, cfg) {{
  if (document.getElementById(id)) Plotly.react(id, data, layout, cfg);
}}

function draw() {{
  const p = pal();

  // 1a. BOOTSTRAP: corpus COMPOSITION, not a funnel -- VERTICAL bars on a LINEAR axis, with the
  //     websearch bar stacked by which engine fetched it. Vertical because the two readings the panel
  //     owes the reader are both ratios (GKG era vs websearch era; Tavily vs Anthropic inside it) and
  //     height differences read as ratios where a 200px label gutter and a log axis do not. Linear
  //     because a log axis would flatten the 4x era gap AND lie about stacked segment heights; the
  //     15 spam drops are SUPPOSED to look like nothing. Only ONE date appears anywhere here: the
  //     handoff, which is the only one the composition depends on.
  if (DATA.comp) {{
    const c = DATA.comp, H = c.handoff;
    // ONE BAR PER SOURCE, not a stack. A stack cannot survive the log axis: the three engine
    // boundaries land at log10(2064/2073/2232), which is 0.8% of the bar's height, so the segments
    // collapse into the 2px borders and the engine split -- the thing this panel exists to show --
    // disappears. Flat bars are each sized by their own value, so log renders all five honestly,
    // 9 and 15 included. The websearch three are bracketed by a tinted band and named once above it.
    const X = ['GKG + wayback<br>before ' + H, 'Tavily only', 'both engines', 'Anthropic only',
               'title-spam dropped'];
    const V = [c.gkg, c.tavily, c.both, c.anthropic, c.spam];
    const C = [p.s1, p.s2, p.text2, p.s4, p.text2];
    const NOTE = ['pre-handoff: GKG + the wayback lede backfill',
                  'websearch: Tavily reached it, Anthropic did not',
                  'websearch: BOTH engines reached it',
                  'websearch: Anthropic reached it, Tavily did not',
                  'excluded from the ' + c.total.toLocaleString() + ' -- the backtest filter, re-applied'];
    react('p-funnel', [{{
      type:'bar', x:X, y:V,
      marker:{{color:C, line:{{width:2, color:p.surface}}}},
      text:V.map(v=>v.toLocaleString()), textposition:'outside',
      textfont:{{color:p.text2, size:12.5}}, cliponaxis:false,
      customdata:NOTE, hovertemplate:'%{{y:,}} articles<br>%{{customdata}}<extra></extra>'
    }}], base(p, {{bargap:0.4, margin:{{l:70, r:24, t:52, b:62}},
        shapes:[{{  // the bracket: these three bars are one era, subdivided
          type:'rect', xref:'x', x0:0.5, x1:3.5, yref:'paper', y0:0, y1:1,
          fillcolor:p.grid, opacity:0.4, line:{{width:0}}, layer:'below'}}],
        annotations:[{{
          x:2, xref:'x', y:1.0, yref:'paper', yanchor:'bottom', yshift:8, showarrow:false,
          font:{{size:11.5, color:p.text2}},
          text:'websearch daily, from ' + H + ' \u2014 <b>' + c.websearch.toLocaleString()
               + '</b> articles, a union of two engines'}}],
        xaxis:{{type:'category', gridcolor:'rgba(0,0,0,0)', linecolor:p.grid, tickfont:{{size:11.5}}}},
        // LOG, requested: it is what makes 9 and 15 visible at all next to 9,269. Each bar is its
        // own value, so nothing here is a misleading share of a whole. Range starts at 1 because a
        // log axis has no zero, and clears the tallest bar so its label is not clipped.
        yaxis:{{type:'log', gridcolor:p.grid, zerolinecolor:p.grid,
                range:[0, Math.log10(c.gkg) + 0.18],
                title:{{text:'articles (log scale)', font:{{size:11}}}}}}
    }}), CFG);
  }} else {{
  // 1b. funnel — horizontal bars, ordinal blue (magnitude down a fixed sequence of stages)
  const f = DATA.funnel, nst = f.labels.length;
  react('p-funnel', [{{
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
  }}

  // 2. volume at three resolutions -- SMALL MULTIPLES, one shared measure (articles), never a
  //    second y-scale on one frame. Each row is its own subplot with its own count axis.
  // the handoff, marked on all three resolutions. Bootstrap-only -- a single-pool FBT corpus has no
  // seam to mark, so DATA.comp gates it. All three subplots are DATE axes, so the date goes in raw.
  const HOFF = DATA.comp ? DATA.comp.handoff : null;
  const hline = ax => ({{type:'line', xref:ax, x0:HOFF, x1:HOFF, yref:ax.replace('x','y') + ' domain',
    y0:0, y1:1, line:{{color:p.text2, width:1.5, dash:'dash'}}, layer:'above'}});
  // STACKED BY RETRIEVAL PATH when the corpus has more than one. This is what retired the standalone
  // seam panel: the era cut and the engine mix are properties of the SAME bars this panel already
  // draws, so drawing them twice was two panels disagreeing waiting to happen. The four series are
  // exclusive and sum to the flat total, so every bar is still the day's article count.
  const MIX = DATA.mix, SRC = MIX ? [
    ['gkg',       'GKG + wayback', p.s1],
    ['tavily',    'Tavily',        p.s2],
    ['both',      'both engines',  p.text2],
    ['anthropic', 'Anthropic',     p.s4]] : [];
  const volTrace = (res, xs, ax, unit) => MIX
    ? SRC.map(([k, name, col]) => ({{
        type:'bar', name:name, x:xs, y:MIX[k][res], legendgroup:k, showlegend:ax === 'x',
        // 2px surface gap between stacked segments -- but NOT on the daily row, where 119 bars
        // are only a few px wide each and a 2px border would eat the bar it is separating
        marker:{{color:col, line:{{width:res === 'd' ? 0 : 2, color:p.surface}}}},
        xaxis:ax, yaxis:ax.replace('x','y'),
        hovertemplate:'%{{x}}<br>%{{y:,}} from ' + name + '<extra>' + unit + '</extra>'}}))
    : [{{type:'bar', x:xs, y:res === 'm' ? DATA.monthly.n : res === 'w' ? DATA.weekly.n : DATA.daily.n,
        marker:{{color:p.s1}}, xaxis:ax, yaxis:ax.replace('x','y'),
        hovertemplate:'%{{x}}<br>%{{y:,}} articles<extra>' + unit + '</extra>'}}];
  react('p-vol', [].concat(
    volTrace('m', DATA.monthly.m, 'x',  'month'),
    volTrace('w', DATA.weekly.d,  'x2', 'week'),
    volTrace('d', DATA.daily.d,   'x3', 'day')
  ), base(p, {{
    grid:{{rows:3, columns:1, pattern:'independent', roworder:'top to bottom'}},
    barmode:'stack', showlegend:!!MIX,
    legend:{{orientation:'h', y:1.10, x:0, font:{{size:11}}, traceorder:'normal'}},
    margin:{{l:64,r:24,t:MIX ? 46 : 26,b:58}}, bargap:0.15,
    shapes: (HOFF ? ['x','x2','x3'].map(hline) : []).concat(
      // the unsettled tail, daily row only -- at month and week resolution it is a sliver of one bar
      DATA.settle ? [{{type:'rect', xref:'x3', x0:DATA.settle.from, x1:DATA.daily.d[DATA.daily.d.length-1],
        yref:'y3 domain', y0:0, y1:1, fillcolor:p.text2, opacity:0.10, line:{{width:0}},
        layer:'below'}}] : []),
    annotations:(HOFF ? [{{  // named once, on the top row -- three copies would just be noise
      x:HOFF, xref:'x', xanchor:'left', yref:'y domain', y:1, yanchor:'top', showarrow:false,
      font:{{size:10.5, color:p.text2}}, text:' handoff ' + HOFF}}] : []).concat(
      // PURPLE, the Anthropic series colour (p.s4), not the neutral text grey. The label names one
      // series, so it should be readable as belonging to that series rather than to the chart
      // furniture -- the shaded band it annotates is the tail Anthropic has not backfilled yet.
      DATA.settle ? [{{x:DATA.settle.from, xref:'x3', xanchor:'right', yref:'y3 domain', y:1,
        yanchor:'top', showarrow:false, font:{{size:10, color:p.s4}},
        text:'Anthropic still filling \u2192 '}}] : []).concat([
      {{text:'per month', x:0, xref:'paper', y:1.0,  yref:'paper', showarrow:false,
        font:{{size:11.5, color:p.text2}}, xanchor:'left'}},
      {{text:'per ISO week', x:0, xref:'paper', y:0.635, yref:'paper', showarrow:false,
        font:{{size:11.5, color:p.text2}}, xanchor:'left'}},
      {{text:'per day', x:0, xref:'paper', y:0.27, yref:'paper', showarrow:false,
        font:{{size:11.5, color:p.text2}}, xanchor:'left'}}
    ]),
    xaxis:{{gridcolor:p.grid}},  yaxis:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}},
    xaxis2:{{gridcolor:p.grid}}, yaxis2:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}},
    // NAME THE AXIS. All three rows bucket by PUBLICATION date, not by the day the article was
    // fetched -- that is what makes the panel a statement about news-calendar coverage, and it is
    // also the only axis both eras share (GKG's half came out of one BigQuery batch and has no
    // meaningful per-day collection date). It is also why the tail is unsettled: a slow engine's
    // articles arrive under an OLD publication date. Titled on the bottom row only, for the stack.
    xaxis3:{{gridcolor:p.grid,
             title:{{text:'publication date — the day the news ran, not the day it was fetched',
                     font:{{size:11}}}}}},
    yaxis3:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}}
  }}), CFG);

  // 4. body text vs article age -- connected dots (a trend over an ordered axis), single series so
  //    no legend box; the title names it. Markers >= 8px with a surface ring so overlaps stay legible.
  const M = DATA.months, share = M.n.map((tot,i)=>100*M.text[i]/tot);
  react('p-text', [{{
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
  react('p-prov', [
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
  react('p-backfill', [
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
  react('p-drift', [{{
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
  react('p-miss', [{{
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
  react('p-group', [
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
  react('p-beats', [
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
  react('p-src', [{{
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
  react('p-spec', [{{
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
  react('p-syn', [{{
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
  react('p-dow', [{{
    type:'bar', x:DATA.dow.k, y:DATA.dow.n, marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
    hovertemplate:'%{{x}}<br>%{{y:,}} articles<extra></extra>'
  }}], base(p, {{yaxis:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}}}}), CFG);

  // 8. authors
  const A = DATA.authors;
  react('p-auth', [{{
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
  // ---- FBS-ONLY PANELS. Guarded on DATA.era, which only the bootstrap build emits.
  const ERA = DATA.era;
  if (ERA) {{
    // text provenance over time -- stacked COUNTS by month, with the search band split by engine.
  // Stacked because the classes are EXCLUSIVE and sum to the month's article count, so the bar's
  // height stays the corpus and the composition is the story. The handoff line is the same helper
  // every other time panel uses, so the flip lines up with the volume panel above it.
  {{
    const PR = DATA.prov;
    const _sb = [
      ['clean',            'archived (as-of)',   ST.good],
      ['live',             'live page',          p.s2],
      ['search_tavily',    'search \u00b7 Tavily',    p.s4],
      ['search_anthropic', 'search \u00b7 Anthropic', p.s1],
      ['search_both',      'search \u00b7 both',      p.s3],
      ['none',             'headline only',      p.grid],
    ].filter(([k]) => (PR[k] || []).some(v => v > 0));
    react('p-provtime', _sb.map(([k, name, col]) => ({{
      type:'bar', name:name, x:PR.m, y:PR[k],
      marker:{{color:col, line:{{width:2, color:p.surface}}}},
      hovertemplate:'%{{x}}<br>%{{y:,}} ' + name + '<extra></extra>'}})),
      base(p, {{barmode:'stack', showlegend:true,
        legend:{{orientation:'h', y:1.13, x:0, font:{{size:11}}}},
        margin:{{l:64,r:24,t:40,b:52}},
        shapes: (PR.hoff || HOFF) ? [{{type:'line', xref:'x',
          x0:(PR.hoff || HOFF.slice(0,7)), x1:(PR.hoff || HOFF.slice(0,7)),
          yref:'paper', y0:0, y1:1, line:{{color:p.text2, width:1.5, dash:'dash'}}}}] : [],
        xaxis:{{gridcolor:p.grid}},
        yaxis:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}}}}), CFG);
  }}

    // HOW MUCH TEXT THE SCOUT IS HANDED, per day. Median line + IQR band, because the BAND is the
    // finding: a truncated day has no spread, so the band closing onto the line IS the ceiling. The
    // cap-share strip underneath states the same fact numerically for anyone who reads bands
    // charitably. Drawn on a date axis so it aligns with every other time panel and the handoff dash.
    {{
      const C = ERA.chars_day || {{cal:[]}};
      if (C.cal.length) {{
        react('p-chars', [
          {{type:'scatter', name:'middle half', x:C.cal.concat(C.cal.slice().reverse()),
            y:C.p75.concat(C.p25.slice().reverse()), fill:'toself', fillcolor:p.s1,
            opacity:0.16, line:{{width:0}}, hoverinfo:'skip', showlegend:true}},
          {{type:'scatter', name:'median chars', x:C.cal, y:C.med, mode:'lines',
            line:{{color:p.s1, width:2}},
            customdata:C.cal.map((d,i) => [C.n[i], Math.round(C.cap[i]*100), C.mode[i],
                                           C.p25[i], C.p75[i]]),
            hovertemplate:'%{{x}}<br>median <b>%{{y}}</b> chars  '
              + '(middle half %{{customdata[3]}}\u2013%{{customdata[4]}})<br>'
              + '%{{customdata[0]}} articles<br>'
              + '%{{customdata[1]}}% sit exactly on %{{customdata[2]}} chars<extra></extra>'}},
          // the ceiling strip: only days where a single length dominates are worth ink
          {{type:'scatter', name:'share pinned to one length', x:C.cal, y:C.cal.map(() => 0),
            mode:'markers', yaxis:'y2',
            marker:{{size:C.cap.map(v => 3 + 9*v), color:C.cap.map(v => v >= 0.5 ? ST.warning : p.grid),
                     line:{{width:0}}}},
            customdata:C.cap.map(v => Math.round(v*100)),
            hovertemplate:'%{{x}}<br>%{{customdata}}% of the day pinned to one length<extra></extra>'}},
        ], base(p, {{showlegend:true,
          legend:{{orientation:'h', y:1.13, x:0, font:{{size:11}}}},
          margin:{{l:64,r:24,t:40,b:56}},
          shapes: (HOFF ? [{{type:'line', xref:'x', x0:HOFF, x1:HOFF, yref:'paper', y0:0, y1:1,
                            line:{{color:p.text2, width:1.5, dash:'dash'}}}}] : []).concat([
            {{type:'line', xref:'x', x0:'2026-08-24', x1:'2026-08-24', yref:'paper', y0:0, y1:1,
              line:{{color:ST.good, width:1.5, dash:'dot'}}}}]),
          annotations:(HOFF ? [{{x:HOFF, xref:'x', yref:'paper', y:1.02, yanchor:'bottom',
                                 showarrow:false, font:{{size:10, color:p.text2}},
                                 text:'handoff'}}] : []).concat([
            {{x:'2026-08-24', xref:'x', yref:'paper', y:1.02, yanchor:'bottom', showarrow:false,
              font:{{size:10, color:ST.good}}, text:'[:300] cap removed'}}]),
          xaxis:{{type:'date', gridcolor:p.grid}},
          yaxis:{{gridcolor:p.grid, rangemode:'tozero',
                  title:{{text:'characters of body text', font:{{size:11}}}}}},
          yaxis2:{{overlaying:'y', side:'right', range:[-1, 1], visible:false}}}}), CFG);
      }}
    }}

    // ---- ORG TAGGER, three panels. Guarded on TG.on so a page built with tagging off simply
    // draws nothing rather than three empty axes claiming a broken pipeline.
    {{
      const TG = ERA.tags || {{}};
      if (TG.on && (TG.days || []).length) {{
        react('p-tagcov', [
          {{type:'bar', name:'tagged with a company', x:TG.days, y:TG.company,
            marker:{{color:ST.good, line:{{width:2,color:p.surface}}}},
            hovertemplate:'%{{x}}<br>%{{y}} tagged<extra></extra>'}},
          {{type:'bar', name:'read, no company in it', x:TG.days, y:TG.nocomp,
            marker:{{color:p.grid, line:{{width:2,color:p.surface}}}},
            hovertemplate:'%{{x}}<br>%{{y}} no company<extra></extra>'}},
          {{type:'bar', name:'NEVER TAGGED', x:TG.days, y:TG.unseen,
            marker:{{color:ST.critical, line:{{width:2,color:p.surface}}}},
            hovertemplate:'%{{x}}<br>%{{y}} never tagged<extra></extra>'}},
        ], base(p, {{barmode:'stack', showlegend:true,
          legend:{{orientation:'h', y:1.14, x:0, font:{{size:11}}}},
          margin:{{l:60,r:20,t:44,b:56}},
          xaxis:{{type:'date', gridcolor:p.grid}},
          yaxis:{{gridcolor:p.grid, title:{{text:'articles', font:{{size:11}}}}}}}}), CFG);

        react('p-tagmem', [
          {{type:'scatter', name:'websearch era', x:TG.mem_days, y:TG.mem, mode:'lines',
            line:{{color:p.s1, width:2}},
            hovertemplate:'%{{x}}<br>%{{y:.2f}} bundles per article<extra></extra>'}},
        ], base(p, {{showlegend:true,
          legend:{{orientation:'h', y:1.14, x:0, font:{{size:11}}}},
          margin:{{l:60,r:20,t:44,b:56}},
          shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y',
                    y0:TG.mem_gkg, y1:TG.mem_gkg,
                    line:{{color:p.text2, width:1.5, dash:'dash'}}}}],
          annotations:[{{xref:'paper', x:0.01, yref:'y', y:TG.mem_gkg, yanchor:'bottom',
                         showarrow:false, font:{{size:10, color:p.text2}},
                         text:'GKG / backtest: ' + TG.mem_gkg.toFixed(2)}}],
          xaxis:{{type:'date', gridcolor:p.grid}},
          yaxis:{{gridcolor:p.grid, rangemode:'tozero',
                  title:{{text:'bundles joined per article', font:{{size:11}}}}}}}}), CFG);

        const _lab = TG.size_labels;
        react('p-tagsize', [
          {{type:'bar', name:'backtest (GKG)', x:_lab, y:TG.sizes_gkg,
            marker:{{color:p.grid, line:{{width:2,color:p.surface}}}},
            hovertemplate:'%{{x}} article(s)<br>%{{y}}% of GKG bundles<extra></extra>'}},
          {{type:'bar', name:'websearch (tagged)', x:_lab, y:TG.sizes_post,
            marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
            hovertemplate:'%{{x}} article(s)<br>%{{y}}% of websearch bundles<extra></extra>'}},
        ], base(p, {{barmode:'group', showlegend:true,
          legend:{{orientation:'h', y:1.14, x:0, font:{{size:11}}}},
          margin:{{l:60,r:20,t:44,b:56}},
          xaxis:{{type:'category', title:{{text:'articles in the bundle', font:{{size:11}}}}}},
          yaxis:{{gridcolor:p.grid, ticksuffix:'%',
                  title:{{text:'share of that era\u2019s bundles', font:{{size:11}}}}}}}}), CFG);
      }}
    }}

    // trailing three weeks -- the operational card. A ZERO day is drawn in the critical colour so a
    // silent cron failure is the loudest thing on the panel.
    // BY COLLECTION DATE (see the era block): each bar is one morning's pull. A morning the cron
    // never ran has no bar to colour -- zero height is invisible -- so it gets a full-height tinted
    // band instead, which is the only state on this panel that is an outright failure.
    // r[3] is the per-day AMBER THRESHOLD, computed server-side against this day-type's own median
    // (see _pull_thr). Amber therefore means "poor for a day like this", not "poor for a Tuesday"
    // applied to a Sunday -- which is what a single flat median was doing to every weekend.
    const T=ERA.trailing, tx=T.map(r=>r[0]), ty=T.map(r=>r[1]), tf=T.map(r=>r[2]),
          tt=T.map(r=>r[3] || 0);
    const MED=ERA.pull_med || {{}};
    react('p-pull', [{{
      type:'bar', x:tx, y:ty,
      marker:{{color:ty.map((v,i) => !tf[i] ? ST.critical
                       : (v===0 ? ST.critical : (v < tt[i] ? ST.warning : p.s1))),
               line:{{width:2,color:p.surface}}}},
      customdata:tx.map((d,i) => (tf[i] ? 'cron fired' : 'CRON DID NOT FIRE — no pull file for this day')
                        + ' · amber below ' + tt[i]),
      hovertemplate:'%{{x}}<br>%{{y}} articles collected<br>%{{customdata}}<extra></extra>'}}],
      base(p, {{margin:{{l:60,r:20,t:24,b:74}},
        // one dotted line PER DAY-TYPE: a single line across both was a median of neither.
        shapes:[['weekday', MED.weekday], ['weekend', MED.weekend]]
          .filter(([, m]) => m > 0)
          .map(([, m]) => ({{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:m, y1:m,
                             line:{{color:p.text2, width:1.2, dash:'dot'}}}})).concat(
          tx.map((d,i) => tf[i] ? null : ({{type:'rect', xref:'x', x0:i-0.5, x1:i+0.5,
            yref:'y domain', y0:0, y1:1, fillcolor:ST.critical, opacity:0.13,
            line:{{width:0}}, layer:'below'}})).filter(Boolean)),
        annotations:tx.map((d,i) => tf[i] ? null : ({{x:i, xref:'x', yref:'y domain', y:1,
          yanchor:'top', showarrow:false, font:{{size:10, color:ST.critical}},
          text:'cron<br>missed'}})).filter(Boolean),
        xaxis:{{type:'category', tickangle:-45, tickfont:{{size:9}},
                title:{{text:'collection date — the morning the pull ran', font:{{size:11}}}}}},
        yaxis:{{gridcolor:p.grid, title:{{text:'articles collected', font:{{size:11}}}}}}}}), CFG);
  }}

  if (ERA && ERA.src) {{
    const S1=ERA.src.top_shared, S2=ERA.src.top_new;
    react('p-srcera', [
      {{type:'bar', orientation:'h', name:'in BOTH eras', x:S1.map(r=>r[1]), y:S1.map(r=>r[0]),
        marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
        customdata:S1.map(r=>r[2]),
        hovertemplate:'%{{y}}<br>%{{x:,}} post-handoff<br>%{{customdata:,}} pre-handoff<extra></extra>'}},
      {{type:'bar', orientation:'h', name:'websearch ONLY (GKG never had it)',
        x:S2.map(r=>r[1]), y:S2.map(r=>r[0]),
        marker:{{color:ST.warning, line:{{width:2,color:p.surface}}}},
        hovertemplate:'%{{y}}<br>%{{x:,}} post-handoff<br>absent from GKG<extra></extra>'}}
    ], base(p, {{barmode:'group', showlegend:true,
        legend:{{orientation:'h', y:1.10, x:0, font:{{size:11}}}},
        margin:{{l:150,r:24,t:38,b:44}},
        yaxis:{{automargin:true, tickfont:{{size:10}}}},
        xaxis:{{gridcolor:p.grid, title:{{text:'post-handoff articles', font:{{size:11}}}}}}}}), CFG);

    const B=ERA.beat;
    react('p-beatera', [{{
      type:'bar', x:['shared by both','websearch only','stopped at handoff'],
      y:[B.shared, B.new_only, B.stopped.length],
      marker:{{color:[p.s1, ST.warning, ST.critical], line:{{width:2,color:p.surface}}}},
      text:[B.shared, B.new_only, B.stopped.length], textposition:'outside',
      textfont:{{size:12,color:p.fg}}, cliponaxis:false,
      hovertemplate:'%{{x}}<br>%{{y}} beats<extra></extra>'}}],
      base(p, {{margin:{{l:60,r:20,t:26,b:52}},
        yaxis:{{gridcolor:p.grid, title:{{text:'beats', font:{{size:11}}}}}}}}), CFG);
  }}

}}
draw();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
</script>
</body></html>"""

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dash_nav.stamp(doc))
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
    # book here. --bootstrap is exempt from the CORPUS half: FBS renders the assembled bootstrap
    # corpus by design, which is a different corpus on purpose rather than a drifted one.
    #
    # THE INTERPRETER CHECK IS NOT EXEMPT, and used to be by accident. It lived inside this same
    # `if not a.bootstrap` block, so exempting FBS from the corpus check silently exempted it from
    # the interpreter check too -- and docs/fbs.html IS a published page. The two questions are
    # orthogonal: WHICH CORPUS you render has nothing to do with WHICH PYTHON renders it. Caught
    # 2026-08-24 after a whole session of FBS builds ran on the system 3.9.6 (numpy 2.0.2, pandas
    # 2.3.3) instead of .venv 3.12.13 (numpy 2.4.6, pandas 3.0.3) with nothing warning. It was
    # harmless THAT time -- FBS runs no optimizer, and the two builds diffed to the timestamp alone
    # -- but that is luck, not a guarantee, and it is the same hole that let CBT and SBT disagree by
    # 36% on 2026-08-21. cbt/sbt already check unconditionally; this was the only outlier.
    _p = []
    _iv = _canon.check_interpreter()
    if _iv:
        _p.append(_iv)
    if not a.bootstrap and a.run != _canon.CANON_CORPUS:
        _p.append(f"corpus is {a.run}, canonical is {_canon.CANON_CORPUS}")
    _canon.require_publishable(out, "FBS" if a.bootstrap else "FBT", _p)
    build(ROOT / a.run if not Path(a.run).is_absolute() else Path(a.run),
          ROOT / out if not Path(out).is_absolute() else Path(out),
          bootstrap=a.bootstrap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
