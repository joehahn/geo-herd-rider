#!/usr/bin/env python3
"""build_writeup.py — the public one-pager: docs/writeups/llm-bakeoff.html

THE LANDING PAGE FOR A LINKEDIN POST, not a dashboard. Everything here is chosen for a reader who
arrives cold, knows nothing about this repo, and will leave in twenty seconds if the first screen
looks like an engineer's workspace:
  * SELF-CONTAINED -- no navigation into the repo, no cross-references to panels they cannot see.
  * PROSE FIRST, chart second. The dashboards assume you already care; this has to earn it.
  * The charts are the SAME data as SBT panels 15-19, redrawn without the internal vocabulary
    (`event_agent_model`, arms, curations) that means nothing outside the project.
  * Ends at jmh-datasciences.com. That is the point of the page.

    python scripts/build_writeup.py
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_fbt_dashboard import CSS, DARK, LIGHT, PLOTLY_CDN  # noqa: E402

# THE HYPHEN IS LOAD-BEARING. This was published as jmhdatasciences.com -- a domain that does not
# exist (NXDOMAIN) -- because the URL was inferred from the phrase "JMH Data Sci website" instead
# of being asked for. Every reader who clicked the byline or the closing button got a browser
# error, on the one page whose entire purpose is to send them here.
SITE = "https://jmh-datasciences.com"


def _markdown(body: str) -> str:
    """Render the same body as Markdown, so the .md and the .html cannot tell different stories.

    They already did: the .md was written by hand alongside the page, so when the page's opening was
    rewritten the .md kept publishing the superseded version. One source, two renderings, no drift.
    Handles only the tags this page actually uses; a chart div becomes a bracketed placeholder,
    because a Markdown reader cannot see Plotly.
    """
    import re
    t = body
    t = re.sub(r'<div id="c(\d)"[^>]*></div>', lambda m: f"\n*[chart {m.group(1)}]*\n", t)
    t = re.sub(r'<p class="kicker">(.*?)</p>', r"*\1*\n", t, flags=re.S)
    t = re.sub(r"<h1>(.*?)</h1>", r"# \1\n", t, flags=re.S)
    t = re.sub(r'<p class="sub">(.*?)</p>', r"\1\n", t, flags=re.S)
    t = re.sub(r"<h2>(.*?)</h2>", r"\n## \1\n", t, flags=re.S)
    t = re.sub(r"<h3>(.*?)</h3>", r"\n### \1\n", t, flags=re.S)
    t = re.sub(r"<li>(.*?)</li>", r"- \1\n", t, flags=re.S)
    t = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n", t, flags=re.S)
    t = re.sub(r"<b>(.*?)</b>", r"**\1**", t, flags=re.S)
    t = re.sub(r"<i>(.*?)</i>", r"*\1*", t, flags=re.S)
    t = re.sub(r'<a href="(.*?)"[^>]*>(.*?)</a>', r"[\2](\1)", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    for a, b in (("&mdash;", "\u2014"), ("&ndash;", "\u2013"), ("&times;", "\u00d7"),
                 ("&plusmn;", "\u00b1"), ("&minus;", "\u2212"), ("&rarr;", "\u2192"),
                 ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")):
        t = t.replace(a, b)
    t = "\n".join(" ".join(ln.split()) for ln in t.splitlines())
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    # The <header> puts the byline above the <h1>, which is right on a rendered page and wrong in
    # Markdown, where the H1 is the document's name. Swap them back.
    lines = t.split("\n")
    nz = [i for i, ln in enumerate(lines[:6]) if ln.strip()]
    if len(nz) >= 2 and lines[nz[0]].startswith("*") and lines[nz[1]].startswith("# "):
        lines[nz[0]], lines[nz[1]] = lines[nz[1]], lines[nz[0]]
    return "\n".join(lines) + "\n"


def main() -> int:
    bo = sorted(json.loads((ROOT / "data/bakeoff_summary.json").read_text()), key=lambda r: r["cost"])
    ja = json.loads((ROOT / "data/judge_audit.json").read_text())
    lo = min(r["cost"] for r in bo)
    import re as _re
    for r in bo:
        r["label"] = r["disp"].replace("<br>", " ")
        # a STRING, not a float: round(1.0, 1) serialises as 1.0 and JS prints it "1", so the
        # cheapest model read "cost 1x" beside seven neighbours reading "cost 1.1x", "cost 4.6x".
        r["mult"] = f"{r['cost'] / lo:.1f}"
        # "Grok 4.3 LOW reasoning" -> base "Grok 4.3", reason "LOW". Chart 1 puts the base name and
        # the cost under the bar and the reasoning level INSIDE it, so the axis carries one short
        # line per model instead of a name long enough that Plotly angles it.
        _m = _re.search(r"\b(LOW|HIGH) reasoning", r["label"])
        r["reason"] = _m.group(1) if _m else ""
        r["base"] = _re.sub(r"\s*(LOW|HIGH) reasoning\s*", "", r["label"]).strip()
    payload = {"bo": bo, "ja": ja}
    # DECISION COUNTS, DERIVED. These were hand-written in four places and had drifted to three
    # different values (600/scan, 4,500 total, "four thousand") against an actual 4,527 and 566.
    # Rounded here for prose, computed once, interpolated everywhere.
    n_tot = sum(r["decisions"] for r in bo)
    n_arm = round(round(n_tot / len(bo)) / 10) * 10          # 566 -> 570
    n_tot_r = f"{round(n_tot, -2):,}"                        # 4,527 -> 4,500
    n_judged = f"{ja['n_tier2'] + ja['n_tier3']:,}"
    # WHO LED, WHO COST MOST, WHO CAME CLOSE. Named in the prose rather than left to the chart, and
    # derived so a re-run of the bake-off cannot leave the sentence naming last month's winner.
    _rank = sorted(bo, key=lambda r: -r["clean_2s"])
    best, runner = _rank[0], _rank[1]
    dearest = max(bo, key=lambda r: r["cost"])
    spread = round(_rank[0]["clean_2s"] - _rank[-1]["clean_2s"])
    gap = round(best["clean_2s"] - runner["clean_2s"])
    _ratio = runner["cost"] / best["cost"]
    ratio_txt = ("less than half the leader's cost" if _ratio < 0.5
                 else f"{_ratio:.0%} of the leader's cost")
    # Per-test ranges across the eight arms, for the section-3 prose. Hardcoded before, and the kind
    # of number that silently goes stale the next time an arm is added.
    def _rng(k):
        v = [r[k] for r in bo]
        return f"{min(v):.0f}\u2013{max(v):.0f}%"
    r_dated, r_supported, r_consistent = _rng("dated_adj"), _rng("supported_adj"), _rng("consistent_adj")
    noise = 1.86

    body = f"""
<header>
  <p class="kicker">Joe Hahn · <a href="{SITE}">JMH Data Sciences</a> · August 2026</p>
  <h1>AI to automate routine business decisions: what does a smarter model actually buy you?</h1>
  <p class="sub">Eight LLMs, one decision repeated across 100,000 news articles, and a frontier
     model to grade those decisions.</p>
</header>

<section>
<h2>Why point AI at 100,000 news articles</h2>
<p>Every business is exposed to events it did not cause. A supplier's plant goes down. A tariff is
proposed. A safety agency schedules a vote that could pull a rival's product off the shelf. Threats
and openings both, and many are <b>reported publicly before they reach anyone's numbers</b>.</p>
<p>The information is not the hard part. But nobody has time to read many thousands of possibly
relevant documents per month to find the twelve that definitely matter to <i>you</i>. So: <b>can a
language model do that reading for you, and then execute the routine actions that would naturally
follow?</b></p>
<p>That is what this solution was built to answer. And in this application the solution reads
business news as it is published, flags what could help or hurt, and then keeps deciding what to do
<b>as the story evolves over time</b>. This is mundane work, which is exactly where AI earns its
keep: most of what moves a bottom line is mundane.</p>
<p>News is one feed among many. Support tickets, contracts coming up for renewal, incident and safety
reports, regulatory filings, vendor advisories: same problem in different clothes, too much arriving
for anyone to read, with only a small portion of it being consequential. This experiment uses AI to
read a flood of financial news to optimize a market portfolio, because there the scorecard is
unambiguous. But swap the feed and the same machinery watches your supply chain, your regulators, or
your competitors.</p>

<h2>The decisions being automated</h2>
<p>The AI reads the stream as it arrives, watching for a just-published event that is about to
affect the business: a ruling, a supply shock, a plant going down, a competitor stumbling. Finding it
once is not the job though, because the situation keeps moving and the reason to act can expire. So
for every situation it is already tracking, the solution revisits three questions on a schedule:</p>
<ul>
  <li><b>Is this still true?</b>: the situation I flagged is still developing, and the reasoning I
      wrote down still holds.</li>
  <li><b>Has the thing I was waiting for already happened?</b>: most situations turn on one
      identifiable event: a ruling, a signed act, a contract award, a plant restart. Once it happens,
      the uncertainty is gone and so is the reason to act. <i>A manufacturer watching a proposed tariff
      cares enormously up to the signing and not at all afterwards, because by then the price has moved.
      An on-call engineer watching a spreading failure cares until the fix ships.</i></li>
  <li><b>Should I still be committed to it?</b>: should capital, inventory or capacity still be tied
      up on the strength of this, or is that commitment now doing nothing.</li>
</ul>
<p>That results in roughly <b>{n_arm} AI judgment calls per scan</b>, where a <b>scan</b> means one
complete pass across three years of business news, about 100,000 articles: month by month, from
scratch, making every call in sequence exactly as it would have at the time. In this experiment, a scan takes about 1-3
hours for AI to process and costs about $5-30 depending on which model is doing the reading.</p>
<p>Which raises the obvious question: <b>does paying for a better model pay?</b> So I ran the whole
thing <b>eight times</b>, changing only the model making those calls: same articles, same retrieval,
same downstream logic. Prices spanned <b>5&times;</b>.</p>
<p>Here are the main findings, all of them specific to this use case.</p>
</section>

<section>
<h2>1. Cost says nothing about speed</h2>
<div id="c1" class="plot"></div>
<p>Each bar indicates how long that AI model took to read and decide upon the
100,000-article corpus and make every call that followed. The models are ordered by price, least
expensive on the left, and the figure under each name is what it costs relative to the cheapest of
the eight.</p>
<p>The first surprise is a practical one. <b>Price and speed are unrelated.</b> The cheapest model was
the <i>slowest</i> by a factor of four: three hours against forty-five minutes, with two models
within seven cents of each other differing by more than two hours of wall clock.</p>
<p>So if you are running this hourly against a live feed rather than monthly against an archive, that
difference decides which LLM is usable at all, and that factor is invisible on a price list.</p>
</section>

<section>
<h2>2. Quality peaks in the middle</h2>
<div id="c2" class="plot"></div>
<p>This is also a cost-optimization exercise, so my goal is not to crown the best model, but to find
the most capable AI per dollar spent. To do that I used a top-of-the-line frontier model to judge
the other models' decisions, Claude Fable 5, which never touched the production path. <b>The judge re-read
the decisions the eight working models had made and graded them, blind to which model produced
which.</b></p>
<p>Each decision was scored on <b>process only</b>, with no post-AI outcomes in front of the judge.
It never saw whether a call made money or lost it, so <b>a lucky guess earns nothing</b> and a
well-reasoned call that happened to go wrong loses nothing. Three tests: was the trigger a specific, datable event rather than a vague trend? Did the
AI model's write-up claim more than its own cited sources support? Was the keep-or-drop call consistent with the
exit condition the model itself had written down? A decision is <b>clean</b> only if it passes all
three.</p>
<p><b>The chart above gives the percentage of each model's ~{n_arm} decisions that came back clean.
Higher is better.</b> Quality separates sharply where the portfolio value could not, a
{spread}-point spread across the eight. And the curve <b>peaks in the middle</b>:
<b>{best['label']}</b> leads at {best['clean_2s']:.1f}%, while <b>{dearest['label']}</b>, the
dearest of the eight at {dearest['mult']}&times; the cheapest, finished <i>last</i> at
{min(r['clean_2s'] for r in bo):.1f}%. <b>{runner['label']}</b> came within {gap} points of the
leader for {ratio_txt}, and that is the configuration this study picks: nearly the best work on
offer, at a fraction of the price of the models either side of it.</p>
<p>This is not "cheaper is better": the cheapest model is near the bottom too. It is that
<b>price predicts almost nothing about fitness for a particular job</b>, and the only way to find out
is to grade the work.</p>

</section>

<section>
<h2>3. Knowing <i>how</i> a model fails beats knowing <i>that</i> it does</h2>
<div id="c3" class="plot"></div>
<p>Section 2's scores are a synthesis of the three tests detailed here, higher is better.</p>
<p><b>In this experiment every model is weakest on the same thing.</b> The yellow bars score how well
the AI identifies a specific, datable trigger, and that score runs {r_dated} across eight models from
six vendors. Internal consistency (blue bars) sits at {r_consistent} for every one of them. When
everything fails the same way the model is unlikely to be the problem. Rather, the instructions
might be at fault, or a datable trigger may simply be hard to pin down and this close to the
ceiling. Either way that is where to look, and looking there is worth more than any model swap.</p>
<p><b>But one test (green bars) separates the field:</b> staying inside your sources,
{r_supported}. That is what
the extra money bought, where it bought anything. The most expensive model is the instructive case: it
writes well-evidenced analysis of things that <i>are not events</i>. That tells us <b>the most
expensive model considered here is not well suited to this particular job</b>, and no leaderboard
would have told you so.</p>
</section>

<section class="takeaway">
<h2>If you are building something like this</h2>
<p><b>Grade on decisions, not outcomes.</b> Eight outcomes cannot separate eight models, but
{n_tot_r} graded decisions can.</p>
<p><b>Spend frontier money on the judge and not the worker.</b> The most valuable model in this
study never ran in production, rather it graded what did.</p>
<p><b>Measure inference time, not just price.</b> A four-fold speed difference can decide whether a
system can run at the cadence your business actually needs.</p>
<p><b>Transfer the method used here to your use case, rather than my results.</b> My scores depend upon my
prompts and my documents. The recipe is what carries over: run the document corpus through every
candidate model, grade the AI decisions blind, and audit those grades with a separate judge.</p>
<p class="cost">This study took two days to quantify and $200 in inference costs.</p>
</section>

<section class="cta">
<h2>Work with us</h2>
<p>JMH Data Sciences builds and evaluates AI systems that make repeated decisions over unstructured
information (news, filings, reports, tickets, claims) where being <i>approximately right, reliably</i>
beats being brilliant occasionally.</p>
<p>If you are automating judgment over a document feed and want to know whether it is actually working,
we would like to hear from you.</p>
<p class="btn"><a href="{SITE}">jmh-datasciences.com &rarr;</a></p>
<!-- THE REPO LINK SITS BELOW THE CTA ON PURPOSE. This page has one job and one exit; a GitHub link
     placed higher would leak a cold reader into an engineer's workspace before they convert. Down
     here it serves the one reader who has already read the whole thing and wants to check the work,
     which for a technical audience is a credibility signal rather than a distraction. -->
<p class="repo">The code, the corpus and the grading harness are public:
<a href="https://github.com/joehahn/geo-herd-rider">github.com/joehahn/geo-herd-rider</a>.</p>
</section>
"""

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- THE <title> CARRIES THE TOPIC, THE <h1> CARRIES THE HOOK. They are read in different places:
     the title tag is a browser tab, a search result and a shared-link card, where "what does a
     smarter model actually buy you?" alone gives no clue what the page is about. The h1 is read
     after the reader has already arrived, where a topical prefix only dilutes the question. -->
<title>AI to automate routine business decisions: what does a smarter model actually buy you? · JMH Data Sciences</title>
<meta name="description" content="Eight LLMs, one repeated decision over 100,000 documents, and a
frontier model brought in to grade the work. Spending 5.3x more bought no measurable return.">
<!-- OPEN GRAPH. This page exists to be shared on LinkedIn, which builds its preview card from og:*
     and falls back to title/description only if they are absent. No og:image: the charts are Plotly,
     rendered in the browser, so there is no static image to point at. -->
<meta property="og:type" content="article">
<meta property="og:title" content="AI to automate routine business decisions: what does a smarter model actually buy you?">
<meta property="og:description" content="Eight LLMs, one repeated decision over 100,000 documents, and a
frontier model brought in to grade the work. Spending 5.3x more bought no measurable return.">
<meta property="og:site_name" content="JMH Data Sciences">
<meta name="twitter:card" content="summary">
<script src="{PLOTLY_CDN}"></script>
<style>{CSS}
body {{ max-width: 860px; margin: 0 auto; padding: 28px 20px 60px; line-height: 1.62; }}
.repo {{ font-size: .92rem; opacity: .68; margin-top: 26px; }}
.kicker {{ text-transform: uppercase; letter-spacing: .09em; font-size: 12px; opacity: .7; margin: 0 0 6px; }}
h1 {{ font-size: 2.05rem; line-height: 1.18; margin: 0 0 10px; }}
.sub {{ font-size: 1.12rem; opacity: .85; margin: 0 0 6px; }}
header {{ border-bottom: 1px solid rgba(127,127,127,.28); padding-bottom: 22px; margin-bottom: 30px; }}
section {{ margin: 0 0 42px; }}
h2 {{ font-size: 1.32rem; margin: 34px 0 12px; }}
/* EXPLICIT HEIGHT IS LOAD-BEARING. The inherited .plot rule sets only background/border, and a
   div with no height collapses to 0px -- Plotly then draws into a zero-height box and the page
   renders with four invisible charts and no error anywhere. The dashboards never hit this
   because panel() passes a height per panel. */
.plot {{ width: 100%; height: 430px; margin: 18px 0 22px; }}
#c2 {{ height: 480px; }}   /* horizontal, 8 categories -- needs more vertical room */
p {{ margin: 0 0 13px; }}
.takeaway {{ border-left: 3px solid #34d399; padding-left: 18px; }}
.cost {{ opacity: .72; font-size: .95rem; }}
.cta {{ border-top: 1px solid rgba(127,127,127,.28); padding-top: 26px; }}
.btn a {{ display: inline-block; margin-top: 8px; padding: 11px 20px; border-radius: 9px;
          background: #34d399; color: #06281c; font-weight: 650; text-decoration: none; }}
</style></head><body>
{body}
<script>
const DATA = {json.dumps(payload)};
const L = {json.dumps(LIGHT)}, D = {json.dumps(DARK)};
// The mode bar is on for one button only: "download as PNG", at 3x scale and 1500x850, so a chart
// can be dropped into a deck without being re-drawn. Everything else is stripped -- zoom and lasso
// on a static bar chart are noise for this audience.
const CFG = {{responsive:true, displaylogo:false,
  modeBarButtonsToRemove:['zoom2d','pan2d','select2d','lasso2d','zoomIn2d','zoomOut2d',
                          'autoScale2d','resetScale2d','toggleSpikelines',
                          'hoverClosestCartesian','hoverCompareCartesian'],
  toImageButtonOptions:{{format:'png', filename:'llm-bakeoff', scale:3,
                        width:1500, height:850}}}};
function draw() {{
  const dark = matchMedia('(prefers-color-scheme: dark)').matches
            && document.documentElement.getAttribute('data-theme') !== 'light';
  const p = dark ? D : L;
  const base = extra => Object.assign({{paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
    font:{{color:p.fg, size:13.5}}, hoverlabel:{{bgcolor:p.surface, font:{{color:p.fg}}}}}}, extra);
  const BO = DATA.bo, nm = BO.map(r => r.label + '<br>cost ' + r.mult + '\u00d7');
  // Chart 1 plots against an INDEX, not the name. Once the reasoning level moves inside the bar the
  // two Grok arms carry the same label, and identical category strings make Plotly merge them into
  // one bar. Indexed x with explicit ticktext keeps eight bars and lets the labels repeat.
  const ix = BO.map((_, i) => i);
  const nm1 = BO.map(r => r.base + '<br>cost ' + r.mult + '\u00d7');

  // 1. COST vs INFERENCE TIME. Was portfolio value with a noise band behind it; that chart argued
  // about an outcome the piece then tells you to ignore, so it undercut its own next section. Wall
  // clock is a fact a reader can act on and it is genuinely uncorrelated with price -- the cheapest
  // model is the slowest by 4x, which no price list shows.
  Plotly.react('c1', [{{type:'bar', x:ix, y:BO.map(r=>r.minutes), marker:{{color:'#22d3ee'}},
      text:BO.map(r=>Math.round(r.minutes)+' min'), textposition:'outside', cliponaxis:false,
      customdata:BO.map(r=>r.label),
      hovertemplate:'%{{customdata}}<br>%{{y:.0f}} minutes per scan<extra></extra>'}}],
    base({{margin:{{l:76,r:16,t:20,b:92}}, showlegend:false,
      xaxis:{{tickmode:'array', tickvals:ix, ticktext:nm1, tickangle:0,
             range:[-0.6, BO.length-0.4], tickfont:{{size:11.5}}, zeroline:false}},
      yaxis:{{gridcolor:p.grid, ticksuffix:' min', rangemode:'tozero',
             title:{{text:'analysis time', font:{{size:13}}, standoff:14}}}},
      annotations:BO.map((r,i)=> r.reason ? {{x:i, y:r.minutes/2, text:r.reason+'<br>reasoning',
             showarrow:false, font:{{size:10.5, color:'#083344'}}}} : null).filter(Boolean)}}), CFG);

  // 2. quality vs spend -- horizontal, dearest on top, shade = price
  const byCost = BO.slice().sort((a,b)=>a.cost-b.cost);
  Plotly.react('c2', [{{type:'bar', orientation:'h',
      x:byCost.map(r=>r.clean_2s), y:byCost.map(r=>r.label+'   cost '+r.mult+'×'),
      marker:{{color:byCost.map(r=>r.cost),
              // COST RAMP: light blue cheap -> deep red costly. Plasma ran yellow-to-purple, which
              // carries no intuition about price; blue-to-red does, and the lightness falls
              // monotonically along it so the order survives greyscale and colour blindness.
              colorscale:[[0,'#cfe3f2'],[0.28,'#8ab6da'],[0.55,'#c193ac'],[0.8,'#d05f5f'],[1,'#8f1d1d']], cmin:0,
              cmax:Math.max(...byCost.map(r=>r.cost)),
              colorbar:{{title:{{text:'cost per<br>scan', font:{{size:12}}}}, tickprefix:'$',
                        thickness:9, len:.6}}}},
      text:byCost.map(r=>r.clean_2s.toFixed(0)+'%'), textposition:'outside', cliponaxis:false,
      hovertemplate:'%{{y}}<br>%{{x:.1f}}%% of decisions clean<extra></extra>'}}],
    base({{margin:{{l:196,r:60,t:14,b:46}}, showlegend:false,
      xaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[0,78],
             title:{{text:'decisions clean on all 3 tests', font:{{size:13}}, standoff:14}}}},
      yaxis:{{automargin:true, tickfont:{{size:12}}}}}}), CFG);

  // 3. the three tests, broken out
  const mk = (n,k,c) => ({{type:'bar', name:n, x:nm, y:BO.map(r=>r[k]), marker:{{color:c}},
      text:BO.map(r=>r[k].toFixed(0)+'%'), textposition:'outside', cliponaxis:false,
      textfont:{{size:9.5}}, hovertemplate:'%{{x}}<br>'+n+' %{{y:.0f}}%<extra></extra>'}});
  Plotly.react('c3', [mk('datable trigger','dated_adj','#fbbf24'),
                      mk('claims within sources','supported_adj','#34d399'),
                      mk('internally consistent','consistent_adj','#60a5fa')],
    base({{barmode:'group', margin:{{l:56,r:16,t:38,b:84}},
      legend:{{orientation:'h', y:1.16, x:0, font:{{size:12.5}}}},
      xaxis:{{type:'category', tickfont:{{size:11.5}}}},
      yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[35,108],
             title:{{text:'pass rate', font:{{size:13}}, standoff:14}}}}}}), CFG);

}}
draw();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
</script></body></html>"""
    out = ROOT / "docs/writeups/llm-bakeoff.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"  wrote {out}")
    md = out.with_suffix(".md")
    md.write_text(_markdown(body))
    print(f"  wrote {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
