# What does a smarter model actually buy you?

**Eight LLMs, one decision, 100,000 documents, and a frontier model brought in to grade the work.**

*JMH Data Sciences · August 2026*

---

## The setup

A system reads a rolling corpus of **99,117 financial news articles** and, every month for three
years, makes the same judgment call about each live thesis it is tracking:

> *Is this still true? Has the thing I was waiting for happened yet? Should I still be exposed to it?*

That is 565 judgment calls per run — the kind of repetitive, evidence-weighing decision that
organisations increasingly hand to a language model. A manufacturer deciding whether to pre-buy
raw material ahead of a supply shock is making the same shape of call against a different feed.

The obvious question when you build one of these: **which model should read the documents, and does
paying more for a better one actually pay?**

So I ran the whole three-year pipeline **eight times**, changing exactly one thing each time — the
model that makes that judgment. Same documents, same retrieval, same downstream logic, same
everything else. Prices ranged over **5.3×**, from $5.96 to $31.66 per complete run.

---

## Finding 1 — More spend bought no measurable return

<!-- SBT panel 15: Portfolio value vs LLM spend -->

| model | cost | vs cheapest | wall clock | outcome |
|---|---|---|---|---|
| Claude Sonnet 5 | $31.66 | 5.3× | 70 min | $142,780 |
| Kimi K3 *(low reasoning)* | $27.31 | 4.6× | 107 min | $215,799 |
| Kimi K3 *(high reasoning)* | $25.67 | 4.3× | 138 min | $173,320 |
| GPT-5.6 Luna | $13.46 | 2.3× | 50 min | $142,054 |
| MiniMax M3 | $8.16 | 1.4× | 48 min | $151,642 |
| Grok 4.3 *(high reasoning)* | $6.85 | 1.1× | 47 min | $155,950 |
| Grok 4.3 *(low reasoning)* | $6.42 | 1.1× | 45 min | $272,336 |
| DeepSeek V4 Flash | $5.96 | 1.0× | 181 min | $208,065 |

A **5.3× spread in spend** produced a **1.92× spread in outcome** — and that is the part most
analyses would stop at and misreport.

Before running any of this I measured the **noise floor**: I ran the identical configuration twice,
changing nothing but the model's own sampling randomness. The two runs finished **1.86× apart.**

So the entire spread across eight different models is barely larger than the spread between one model
and *itself*. **On outcome alone, this experiment cannot distinguish any of these models from any
other.** Seven of the eight sit inside the noise band.

Most model bake-offs never measure that band. They report the winner.

**Also worth noting: price and speed are unrelated.** The cheapest model was also the slowest — four
times slower than the fastest.

---

## Finding 2 — Decision *quality* varies sharply, and peaks in the middle

<!-- SBT panels 16 and 17: decision quality vs spend; quality ranked by cost -->

Outcome is a single number per run, hostage to a handful of lucky calls. So I changed the unit of
analysis: instead of eight outcomes, **4,527 individually graded decisions**.

Every decision was scored on **process only**, with no prices and no outcomes in front of the grader:

- **Was the trigger a specific, datable, resolvable event** — a contract award, a ruling, a regulatory
  decision — rather than an open-ended trend like *"AI demand is growing"*?
- **Did the write-up claim more than its own cited sources establish?**
- **Was the keep-or-drop call consistent with the analyst's own stated exit condition?**

A decision is *clean* only if it passes all three.

| model | cost | clean decisions |
|---|---|---|
| **GPT-5.6 Luna** | $13.46 | **63.8%** |
| **Grok 4.3** *(low reasoning)* | **$6.42** | **61.1%** |
| Grok 4.3 *(high reasoning)* | $6.85 | 57.9% |
| Kimi K3 *(high reasoning)* | $25.67 | 52.1% |
| Kimi K3 *(low reasoning)* | $27.31 | 51.7% |
| MiniMax M3 | $8.16 | 43.4% |
| DeepSeek V4 Flash | $5.96 | 42.6% |
| **Claude Sonnet 5** | **$31.66** | **40.8%** ← most expensive, worst |

**Quality separates where outcome did not** — a 23-point spread, far outside anything noise explains.
And the curve **peaks in the middle**. The most expensive model finished last. A $6.42 model landed
within three points of the leader, at a fifth of the price of the worst.

**This is not "cheaper is better."** The cheapest model is also near the bottom. The finding is that
**price predicts almost nothing about fitness for a specific task**, and the only way to know is to
grade the work.

---

## Finding 3 — Knowing *how* a model fails is worth more than knowing *that* it does

<!-- SBT panel 18: Where each model actually fails -->

| model | datable trigger | claims within sources | internally consistent |
|---|---|---|---|
| GPT-5.6 Luna | 66% | 97% | 100% |
| Grok 4.3 *(low)* | 66% | 94% | 96% |
| Grok 4.3 *(high)* | 60% | 90% | 100% |
| Kimi K3 *(high)* | 59% | 93% | 99% |
| MiniMax M3 | 59% | 80% | 93% |
| Kimi K3 *(low)* | 57% | 91% | 99% |
| DeepSeek V4 Flash | 52% | 75% | 99% |
| Claude Sonnet 5 | 46% | 90% | 99% |

Three things fall out, and none is visible in an aggregate score:

**Internal consistency is solved.** Every model scores 93–100%. No model contradicts its own stated
reasoning. That test can be retired — it costs money to run and separates nothing.

**Every model is weakest on the same axis.** *Datable trigger* runs 46–66% across eight models from
six vendors. When every model fails the same way, **the prompt is at fault, not the model** — and
that is the highest-value fix available, worth more than any model swap.

**One axis separates the field: staying inside your sources.** 75% to 97%. That is what the extra
money bought where it bought anything — a model that stops asserting more than its evidence carries.

Claude Sonnet 5 is the instructive case. It writes **well-evidenced analysis (90%) of things that are
not events (46%)**. That is not a bad model; it is a **mismatch between a model's habits and a task's
requirements** — invisible in any benchmark, and exactly what an evaluation like this exists to catch.

---

## Finding 4 — The grader was graded

<!-- SBT panel 19: the full comparison table -->

Using an LLM to grade LLMs invites one obvious objection, so the design answers it up front:

- **A frontier model did the grading** (Claude Fable 5), blind to which model produced each decision.
- **Two tiers.** A cheap model screened all 4,527 calls; the frontier model re-read **1,200** of them
  — both the ones the screen condemned *and* the ones it cleared, so the correction runs in both
  directions rather than only rescuing false accusations.
- **The screen was itself audited.** It agreed with the frontier grader **93%** on consistency, **85%**
  on datable triggers, and only **67%** on whether a claim exceeded its sources — the hardest
  judgment, and precisely where a cheap grader should not be trusted. That is this study's own thesis
  appearing inside its own instrument.
- **Contamination was tested, not assumed.** Decisions about the best-performing assets were graded
  *slightly harsher*, not softer — so no hindsight leaked into a process-only rubric.

---

## What this means if you are building one of these

**Measure your noise floor before you measure anything else.** Run the same configuration twice. The
gap between those two runs is the smallest difference your evaluation can honestly detect. Most
model comparisons report differences smaller than their own noise.

**Grade decisions, not outcomes.** Eight outcomes cannot separate eight models. Four thousand graded
decisions can. Outcome is one sample; process is thousands.

**Spend frontier money on the judge, not the worker.** The most valuable model in this study never
touched the production path. It graded it.

**Expect the answer to be task-specific.** The best model here was mid-priced, the worst was the most
expensive, and the runner-up cost $6.42. None of that is predictable from a leaderboard.

The whole study cost **under $200** and took two days.

---

## Work with us

JMH Data Sciences builds and evaluates AI systems that make repeated decisions over unstructured
information — news, filings, reports, tickets, claims — where being *approximately right, reliably*
matters more than being brilliant occasionally.

If you are automating judgment over a document feed and want to know whether it is actually working,
we would like to hear from you.

**→ [jmhdatasciences.com](https://jmhdatasciences.com)**

---

<sub>Methodology and every underlying number are published in full, including the runs that failed and
the two analysis bugs found and corrected along the way.</sub>
