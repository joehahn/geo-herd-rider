#!/usr/bin/env python3
"""build_writeup.py — the public one-pager: docs/writeups/llm-bakeoff.html

THE LANDING PAGE FOR A LINKEDIN POST, not a dashboard. Everything here is chosen for a reader who
arrives cold, knows nothing about this repo, and will leave in twenty seconds if the first screen
looks like an engineer's workspace:
  * SELF-CONTAINED -- no navigation into the repo, no cross-references to panels they cannot see.
  * PROSE FIRST, chart second. The dashboards assume you already care; this has to earn it.
  * The charts are the SAME data as SBT panels 15-19, redrawn without the internal vocabulary
    (`event_agent_model`, arms, curations) that means nothing outside the project.
  * Ends at jmhdatasciences.com. That is the point of the page.

    python scripts/build_writeup.py
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_fbt_dashboard import CSS, DARK, LIGHT, PLOTLY_CDN  # noqa: E402

SITE = "https://jmhdatasciences.com"


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
  <p class="kicker">JMH Data Sciences · August 2026</p>
  <h1>What does a smarter model actually buy you?</h1>
  <p class="sub">Eight LLMs, one repeated decision, 100,000 documents, and a frontier model
     brought in to grade the work.</p>
</header>

<section>
<h2>The setup</h2>
<p>A system reads a rolling corpus of <b>99,117 financial news articles</b> and, every month for three
years, makes the same judgment call about every thesis it is tracking: <i>is this still true, has the
thing I was waiting for happened, should I still be exposed to it?</i></p>
<p>That is <b>565 judgment calls per run</b> — the repetitive, evidence-weighing decision organisations
increasingly hand to a language model. A manufacturer deciding whether to pre-buy raw material ahead of
a supply shock is making the same shape of call against a different feed.</p>
<p>So I ran the entire three-year pipeline <b>eight times</b>, changing exactly one thing each time: the
model making that judgment. Same documents, same retrieval, same downstream logic. Prices spanned
<b>5.3&times;</b>, from $5.96 to $31.66 per complete run.</p>
</section>

<section>
<h2>1. More spend bought no measurable return</h2>
<div id="c1" class="plot"></div>
<p>A <b>5.3&times; spread in spend</b> produced a <b>1.92&times; spread in outcome</b> — and that is
where most analyses stop and misreport.</p>
<p>Before running any of it I measured the <b>noise floor</b>: the identical configuration, run twice,
differing only in the model's own sampling randomness. Those two runs finished <b>1.86&times; apart</b>
— the shaded band. The entire spread across eight <i>different</i> models is barely wider than the gap
between one model and <i>itself</i>.</p>
<p><b>On outcome alone, this experiment cannot tell these models apart.</b> Most bake-offs never measure
that band. They report the winner.</p>
</section>

<section>
<h2>2. Decision quality varies sharply — and peaks in the middle</h2>
<div id="c2" class="plot"></div>
<p>Outcome is one number per run, hostage to a few lucky calls. So I changed the unit of analysis:
<b>4,527 individually graded decisions</b> instead of eight outcomes.</p>
<p>Each was scored on <b>process only</b>, with no prices and no outcomes in front of the grader. Was the
trigger a specific, datable, resolvable event rather than an open-ended trend? Did the write-up claim
more than its own cited sources establish? Was the keep-or-drop call consistent with the stated exit
condition? A decision is <b>clean</b> only if it passes all three.</p>
<p><b>Quality separates where outcome did not</b> — a 23-point spread, far outside anything noise
explains — and the curve <b>peaks in the middle</b>. The most expensive model finished last. A $6.42
model landed within three points of the leader.</p>
<p>This is <i>not</i> "cheaper is better": the cheapest model is near the bottom too. It is that
<b>price predicts almost nothing about fitness for a specific task</b>.</p>
</section>

<section>
<h2>3. Knowing <i>how</i> a model fails beats knowing <i>that</i> it does</h2>
<div id="c3" class="plot"></div>
<p>Three things fall out, none visible in an aggregate score.</p>
<p><b>Internal consistency is solved.</b> Every model scores 93–100%. That test can be retired — it
costs money and separates nothing.</p>
<p><b>Every model is weakest on the same axis.</b> Datable triggers run 46–66% across eight models from
six vendors. When everything fails the same way, <b>the prompt is at fault, not the model</b> — and
that is worth more than any model swap.</p>
<p><b>One axis separates the field:</b> staying inside your sources, 75% to 97%. That is what the extra
money bought where it bought anything. The most expensive model is the instructive case — it writes
well-evidenced analysis of things that are <i>not events</i>. Not a bad model; a <b>mismatch between a
model's habits and a task's requirements</b>, invisible on any leaderboard.</p>
</section>

<section>
<h2>4. The grader was graded</h2>
<div id="c4" class="plot"></div>
<p>Using an LLM to grade LLMs invites one obvious objection, so the design answers it up front. A
frontier model did the grading, blind to which model produced each decision. A cheap model screened all
4,527 calls and the frontier model re-read <b>1,200</b> — both the ones the screen condemned <i>and</i>
the ones it cleared, so the correction runs in both directions.</p>
<p>Then the screen itself was audited. It agreed with the frontier grader <b>{ja['agree']['consistent']:.0f}%</b>
on consistency and <b>{ja['agree']['dated']:.0f}%</b> on datable triggers, but only
<b>{ja['agree']['supported']:.0f}%</b> on whether a claim exceeded its sources — the hardest judgment,
and exactly where a cheap grader should not be trusted. The study's own thesis, appearing inside its own
instrument.</p>
</section>

<section class="takeaway">
<h2>If you are building one of these</h2>
<p><b>Measure your noise floor first.</b> Run the same configuration twice. That gap is the smallest
difference your evaluation can honestly detect — and most comparisons report differences smaller than
their own noise.</p>
<p><b>Grade decisions, not outcomes.</b> Eight outcomes cannot separate eight models. Four thousand
graded decisions can.</p>
<p><b>Spend frontier money on the judge, not the worker.</b> The most valuable model here never touched
the production path. It graded it.</p>
<p><b>Expect the answer to be task-specific.</b> Best was mid-priced, worst was dearest, runner-up cost
$6.42. None of that is predictable from a leaderboard.</p>
<p class="cost">The whole study cost under $200 and took two days.</p>
</section>

<section class="cta">
<h2>Work with us</h2>
<p>JMH Data Sciences builds and evaluates AI systems that make repeated decisions over unstructured
information — news, filings, reports, tickets, claims — where being <i>approximately right, reliably</i>
matters more than being brilliant occasionally.</p>
<p>If you are automating judgment over a document feed and want to know whether it is actually working,
we would like to hear from you.</p>
<p class="btn"><a href="{SITE}">jmhdatasciences.com &rarr;</a></p>
</section>
"""

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>What does a smarter model actually buy you? · JMH Data Sciences</title>
<meta name="description" content="Eight LLMs, one repeated decision over 100,000 documents, and a
frontier model brought in to grade the work. Spending 5.3x more bought no measurable return.">
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
  const BO = DATA.bo, nm = BO.map(r => r.label + '<br>$' + r.cost.toFixed(2));
  const fin = BO.map(r => r.final), mid = fin.reduce((a,b)=>a+b,0)/fin.length;
  const lo = mid/Math.sqrt({noise}), hi = mid*Math.sqrt({noise});

  // 1. outcome vs spend, with the measured noise band behind it
  Plotly.react('c1', [{{type:'bar', x:nm, y:fin, marker:{{color:'#7dd3fc'}},
      text:fin.map(v=>'$'+Math.round(v/1000)+'K'), textposition:'outside', cliponaxis:false,
      hovertemplate:'%{{x}}<br>$%{{y:,.0f}}<extra></extra>'}}],
    base({{margin:{{l:66,r:16,t:34,b:84}}, showlegend:false,
      shapes:[{{type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:lo, y1:hi, layer:'below',
               fillcolor: dark?'rgba(148,163,184,.22)':'rgba(100,116,139,.16)', line:{{width:0}}}}],
      annotations:[{{xref:'paper', x:.99, xanchor:'right', yref:'y', y:hi, yanchor:'bottom',
        text:'measured noise floor — the SAME setup re-run lands 1.86× apart',
        showarrow:false, font:{{size:10.5}}}}],
      xaxis:{{type:'category', tickfont:{{size:10}}}},
      yaxis:{{gridcolor:p.grid, tickprefix:'$', range:[90000,300000],
             title:{{text:'outcome after 3 years', font:{{size:11}}}}}}}}), CFG);

  // 2. quality vs spend -- horizontal, dearest on top, shade = price
  const byCost = BO.slice().sort((a,b)=>a.cost-b.cost);
  Plotly.react('c2', [{{type:'bar', orientation:'h',
      x:byCost.map(r=>r.clean_2s), y:byCost.map(r=>r.label+'   '+r.mult+'×'),
      marker:{{color:byCost.map(r=>r.cost),
              colorscale:[[0,'#fde68a'],[.45,'#fb923c'],[1,'#b45309']], cmin:0,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
