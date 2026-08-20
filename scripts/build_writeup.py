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
    for r in bo:
        r["label"] = r["disp"].replace("<br>", " ")
        r["mult"] = round(r["cost"] / lo, 1)
    payload = {"bo": bo, "ja": ja}
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

<h2>The decision being automated</h2>
<p>For each situation this solution is monitoring, it uses AI to revisit three questions on a
schedule. Nothing in them is specific to news; they are what you ask of any live item in a
queue:</p>
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
<p>That is roughly <b>600 judgment calls per run</b>, where a <b>run</b> means one complete pass of the
system over three years of news: month by month, from scratch, making every call in sequence exactly
as it would have at the time. A run takes under two hours and costs between $6 and $32 depending on
which model is doing the reading.</p>
<p>Which raises the obvious question: <b>does paying for a better model pay?</b> So I ran the whole
thing <b>eight times</b>, changing exactly one thing each time, the model making those calls. Same
articles, same retrieval, same downstream logic. Prices spanned <b>5&times;</b>.</p>
</section>

<section>
<h2>1. Cost tells you nothing about speed</h2>
<div id="c1" class="plot"></div>
<p>The first surprise is a practical one. <b>Price and speed are unrelated.</b> The cheapest model was
the <i>slowest</i> by a factor of four: three hours against forty-five minutes. Two models within
seven cents of each other differed by more than two hours of wall clock.</p>
<p>If you are running this hourly against a live feed rather than monthly against an archive, that
difference decides whether the system is usable at all, and it is invisible on a price list.</p>
</section>

<section>
<h2>2. A frontier model graded every decision, and quality peaks in the middle</h2>
<div id="c2" class="plot"></div>
<p>Comparing the models on the portfolio's final value would be close to meaningless: one number per
run, decided by a handful of lucky calls. So I changed the unit of analysis. <b>Claude Fable 5, the strongest model available and one that never the
strongest model available, and one that never touched the production path, re-read the decisions the
eight working models had made and graded them, blind to which model produced which.</b></p>
<p>Each decision was scored on <b>process only</b>, with no prices and no outcomes in front of the
grader. Three tests: was the trigger a specific, datable event rather than a vague trend? Did the
write-up claim more than its own cited sources support? Was the keep-or-drop call consistent with the
exit condition the model itself had written down? A decision is <b>clean</b> only if it passes all
three.</p>
<p><b>The score below is the percentage of that model's ~600 decisions that came back clean.
Higher is better.</b> Quality separates sharply where the portfolio value could not, a 23-point spread
across the eight. And the curve <b>peaks in the middle</b>. The most expensive model finished
<i>last</i>. A $6.42 model landed within three points of the leader.</p>
<p>This is not "cheaper is better": the cheapest model is near the bottom too. It is that
<b>price predicts almost nothing about fitness for a particular job</b>, and the only way to find out
is to grade the work.</p>
</section>

<section>
<h2>3. Knowing <i>how</i> a model fails beats knowing <i>that</i> it does</h2>
<div id="c3" class="plot"></div>
<p>Breaking the same grades out by test, higher being better on all three, says three things no
aggregate score can.</p>
<p><b>Internal consistency is a solved problem.</b> Every model scores 93–100%: none of them
contradicts reasoning it wrote down itself. That test can be retired.</p>
<p><b>Every model is weakest on the same thing.</b> Identifying a specific, datable trigger runs
46–66% across eight models from six vendors. When everything fails the same way, <b>the instructions
are at fault, not the model</b>, and fixing that is worth more than any model swap.</p>
<p><b>One test actually separates the field:</b> staying inside your sources, 75% to 97%. That is what
the extra money bought, where it bought anything. The most expensive model is the instructive case: it
writes well-evidenced analysis of things that <i>are not events</i>. Not a bad model; a
<b>mismatch between a model's habits and a job's requirements</b>, invisible on any leaderboard.</p>
</section>

<section>
<h2>4. The grader was graded too</h2>
<div id="c4" class="plot"></div>
<p>Using an LLM to grade LLMs invites an obvious objection, so the design answers it. A cheap model
screened all 4,500 decisions first; Fable 5 then re-read 1,200 of them, both the ones the screen
condemned <i>and</i> the ones it cleared, so the correction ran in both directions rather than only
rescuing false accusations.</p>
<p>Then the cheap screen was itself audited against the frontier grader. It agreed
<b>{ja['agree']['consistent']:.0f}%</b> of the time on consistency and <b>{ja['agree']['dated']:.0f}%</b>
on datable triggers, but only <b>{ja['agree']['supported']:.0f}%</b> on whether a claim outran its
sources. That is the hardest judgment of the three, and precisely where a cheap grader should not be
trusted. The study's own conclusion, turning up inside its own instrument.</p>
</section>

<section class="takeaway">
<h2>If you are building something like this</h2>
<p><b>Grade decisions, not outcomes.</b> Eight outcomes cannot separate eight models. Four thousand
graded decisions can. One is a sample of one; the other is a sample of thousands.</p>
<p><b>Spend frontier money on the judge, not the worker.</b> The most valuable model in this study
never ran in production. It graded what did.</p>
<p><b>Measure inference time, not just price.</b> A four-fold speed difference decides whether a
system can run at the cadence your business actually needs.</p>
<p><b>Expect the answer to be specific to your job, and do not port this leaderboard to yours.</b>
Best here was mid-priced, worst was the most expensive, runner-up cost $6.42, none of it
predictable from a benchmark, and none of it measured on your documents. What transfers is the
method: run the arms, grade the decisions blind, audit the grader. That costs a few hundred dollars
and answers the question for <i>your</i> task, which no published benchmark can.</p>
<p class="cost">The whole study cost under $200 and took two days.</p>
</section>

<section class="cta">
<h2>Work with us</h2>
<p>JMH Data Sciences builds and evaluates AI systems that make repeated decisions over unstructured
information (news, filings, reports, tickets, claims) where being <i>approximately right, reliably</i>
beats being brilliant occasionally.</p>
<p>If you are automating judgment over a document feed and want to know whether it is actually working,
we would like to hear from you.</p>
<p class="btn"><a href="{SITE}">jmh-datasciences.com &rarr;</a></p>
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
const CFG = {{displayModeBar:false, responsive:true}};
function draw() {{
  const dark = matchMedia('(prefers-color-scheme: dark)').matches
            && document.documentElement.getAttribute('data-theme') !== 'light';
  const p = dark ? D : L;
  const base = extra => Object.assign({{paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
    font:{{color:p.fg, size:12}}, hoverlabel:{{bgcolor:p.surface, font:{{color:p.fg}}}}}}, extra);
  const BO = DATA.bo, nm = BO.map(r => r.label + '<br>cost ' + r.mult + '\u00d7');

  // 1. COST vs INFERENCE TIME. Was portfolio value with a noise band behind it; that chart argued
  // about an outcome the piece then tells you to ignore, so it undercut its own next section. Wall
  // clock is a fact a reader can act on and it is genuinely uncorrelated with price -- the cheapest
  // model is the slowest by 4x, which no price list shows.
  Plotly.react('c1', [{{type:'bar', x:nm, y:BO.map(r=>r.minutes), marker:{{color:'#22d3ee'}},
      text:BO.map(r=>Math.round(r.minutes)+' min'), textposition:'outside', cliponaxis:false,
      hovertemplate:'%{{x}}<br>%{{y:.0f}} minutes per run<extra></extra>'}}],
    base({{margin:{{l:62,r:16,t:20,b:84}}, showlegend:false,
      xaxis:{{type:'category', tickfont:{{size:10}}}},
      yaxis:{{gridcolor:p.grid, ticksuffix:' min', rangemode:'tozero',
             title:{{text:'time to analyze the 100,000-article corpus',
                    font:{{size:11}}}}}}}}), CFG);

  // 2. quality vs spend -- horizontal, dearest on top, shade = price
  const byCost = BO.slice().sort((a,b)=>a.cost-b.cost);
  Plotly.react('c2', [{{type:'bar', orientation:'h',
      x:byCost.map(r=>r.clean_2s), y:byCost.map(r=>r.label+'   cost '+r.mult+'×'),
      marker:{{color:byCost.map(r=>r.cost),
              colorscale:'Plasma', reversescale:true, cmin:0,
              cmax:Math.max(...byCost.map(r=>r.cost)),
              colorbar:{{title:{{text:'cost per<br>run', font:{{size:10}}}}, tickprefix:'$',
                        thickness:9, len:.6}}}},
      text:byCost.map(r=>r.clean_2s.toFixed(0)+'%'), textposition:'outside', cliponaxis:false,
      hovertemplate:'%{{y}}<br>%{{x:.1f}}%% of decisions clean<extra></extra>'}}],
    base({{margin:{{l:196,r:60,t:14,b:46}}, showlegend:false,
      xaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[0,78],
             title:{{text:'decisions clean on all 3 tests', font:{{size:11}}}}}},
      yaxis:{{automargin:true, tickfont:{{size:11}}}}}}), CFG);

  // 3. the three tests, broken out
  const mk = (n,k,c) => ({{type:'bar', name:n, x:nm, y:BO.map(r=>r[k]), marker:{{color:c}},
      text:BO.map(r=>r[k].toFixed(0)+'%'), textposition:'outside', cliponaxis:false,
      textfont:{{size:9.5}}, hovertemplate:'%{{x}}<br>'+n+' %{{y:.0f}}%<extra></extra>'}});
  Plotly.react('c3', [mk('datable trigger','dated_adj','#fbbf24'),
                      mk('claims within sources','supported_adj','#34d399'),
                      mk('internally consistent','consistent_adj','#60a5fa')],
    base({{barmode:'group', margin:{{l:56,r:16,t:38,b:84}},
      legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
      xaxis:{{type:'category', tickfont:{{size:10}}}},
      yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[35,108],
             title:{{text:'pass rate', font:{{size:11}}}}}}}}), CFG);

  // 4. the grader auditing itself
  const A = DATA.ja.agree, ax = ['consistent','dated','supported'];
  Plotly.react('c4', [{{type:'bar', x:ax.map(k=>k==='dated'?'datable trigger':
                          k==='supported'?'claims within sources':'internally consistent'),
      y:ax.map(k=>A[k]), marker:{{color:['#60a5fa','#fbbf24','#34d399']}},
      text:ax.map(k=>A[k].toFixed(0)+'%'), textposition:'outside', cliponaxis:false,
      hovertemplate:'%{{x}}<br>cheap screen agreed %{{y:.1f}}%% of the time<extra></extra>'}}],
    base({{margin:{{l:56,r:16,t:16,b:52}}, showlegend:false,
      xaxis:{{type:'category'}},
      yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[0,108],
             title:{{text:'cheap screen agreed with the frontier grader', font:{{size:11}}}}}}}}), CFG);
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
