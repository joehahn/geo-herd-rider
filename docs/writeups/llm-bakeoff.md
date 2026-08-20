# What does a smarter model actually buy you?

*Joe Hahn · [JMH Data Sciences](https://jmh-datasciences.com) · August 2026*

Eight LLMs, one decision repeated across 100,000 news articles, and a frontier
model to grade those decisions.

## Why point AI at 100,000 news articles

Every business is exposed to events it did not cause. A supplier's plant goes down. A tariff is
proposed. A safety agency schedules a vote that could pull a rival's product off the shelf. Threats
and openings both, and many are **reported publicly before they reach anyone's numbers**.

The information is not the hard part. But nobody has time to read many thousands of possibly
relevant documents per month to find the twelve that definitely matter to *you*. So: **can a
language model do that reading for you, and then execute the routine actions that would naturally
follow?**

That is what this solution was built to answer. And in this application the solution reads
business news as it is published, flags what could help or hurt, and then keeps deciding what to do
**as the story evolves over time**. This is mundane work, which is exactly where AI earns its
keep: most of what moves a bottom line is mundane.

News is one feed among many. Support tickets, contracts coming up for renewal, incident and safety
reports, regulatory filings, vendor advisories: same problem in different clothes, too much arriving
for anyone to read and a little of it consequential. This experiment uses AI to read a flood of
financial news to optimize a market portfolio, because there the scorecard is unambiguous, but swap
the feed and the same machinery watches your supply chain, your regulators, or your competitors.

## The decision being automated

For each situation this solution is monitoring, it uses AI to revisit three questions on a
schedule. Nothing in them is specific to news; they are what you ask of any live item in a
queue:

- **Is this still true?**: the situation I flagged is still developing, and the reasoning I
wrote down still holds.

- **Has the thing I was waiting for already happened?**: most situations turn on one
identifiable event: a ruling, a signed act, a contract award, a plant restart. Once it happens,
the uncertainty is gone and so is the reason to act. *A manufacturer watching a proposed tariff
cares enormously up to the signing and not at all afterwards, because by then the price has moved.
An on-call engineer watching a spreading failure cares until the fix ships.*

- **Should I still be committed to it?**: should capital, inventory or capacity still be tied
up on the strength of this, or is that commitment now doing nothing.

That is roughly **600 judgment calls per run**, where a **run** means one complete pass of the
system over three years of news: month by month, from scratch, making every call in sequence exactly
as it would have at the time. A run takes under two hours and costs between $6 and $32 depending on
which model is doing the reading.

Which raises the obvious question: **does paying for a better model pay?** So I ran the whole
thing **eight times**, changing exactly one thing each time, the model making those calls. Same
articles, same retrieval, same downstream logic. Prices spanned **5×**.

## 1. Cost tells you nothing about speed

*[chart 1]*

The first surprise is a practical one. **Price and speed are unrelated.** The cheapest model was
the *slowest* by a factor of four: three hours against forty-five minutes. Two models within
seven cents of each other differed by more than two hours of wall clock.

If you are running this hourly against a live feed rather than monthly against an archive, that
difference decides whether the system is usable at all, and it is invisible on a price list.

## 2. A frontier model graded every decision, and quality peaks in the middle

*[chart 2]*

Comparing the models on the portfolio's final value would be close to meaningless: one number per
run, decided by a handful of lucky calls. So I changed the unit of analysis. **Claude Fable 5, the strongest model available and one that never the
strongest model available, and one that never touched the production path, re-read the decisions the
eight working models had made and graded them, blind to which model produced which.**

Each decision was scored on **process only**, with no prices and no outcomes in front of the
grader. Three tests: was the trigger a specific, datable event rather than a vague trend? Did the
write-up claim more than its own cited sources support? Was the keep-or-drop call consistent with the
exit condition the model itself had written down? A decision is **clean** only if it passes all
three.

**The score below is the percentage of that model's ~600 decisions that came back clean.
Higher is better.** Quality separates sharply where the portfolio value could not, a 23-point spread
across the eight. And the curve **peaks in the middle**. The most expensive model finished
*last*. A $6.42 model landed within three points of the leader.

This is not "cheaper is better": the cheapest model is near the bottom too. It is that
**price predicts almost nothing about fitness for a particular job**, and the only way to find out
is to grade the work.

## 3. Knowing *how* a model fails beats knowing *that* it does

*[chart 3]*

Breaking the same grades out by test, higher being better on all three, says three things no
aggregate score can.

**Internal consistency is a solved problem.** Every model scores 93–100%: none of them
contradicts reasoning it wrote down itself. That test can be retired.

**Every model is weakest on the same thing.** Identifying a specific, datable trigger runs
46–66% across eight models from six vendors. When everything fails the same way, **the instructions
are at fault, not the model**, and fixing that is worth more than any model swap.

**One test actually separates the field:** staying inside your sources, 75% to 97%. That is what
the extra money bought, where it bought anything. The most expensive model is the instructive case: it
writes well-evidenced analysis of things that *are not events*. Not a bad model; a
**mismatch between a model's habits and a job's requirements**, invisible on any leaderboard.

## 4. The grader was graded too

*[chart 4]*

Using an LLM to grade LLMs invites an obvious objection, so the design answers it. A cheap model
screened all 4,500 decisions first; Fable 5 then re-read 1,200 of them, both the ones the screen
condemned *and* the ones it cleared, so the correction ran in both directions rather than only
rescuing false accusations.

Then the cheap screen was itself audited against the frontier grader. It agreed
**93%** of the time on consistency and **85%**
on datable triggers, but only **66%** on whether a claim outran its
sources. That is the hardest judgment of the three, and precisely where a cheap grader should not be
trusted. The study's own conclusion, turning up inside its own instrument.

## If you are building something like this

**Grade decisions, not outcomes.** Eight outcomes cannot separate eight models. Four thousand
graded decisions can. One is a sample of one; the other is a sample of thousands.

**Spend frontier money on the judge, not the worker.** The most valuable model in this study
never ran in production. It graded what did.

**Measure inference time, not just price.** A four-fold speed difference decides whether a
system can run at the cadence your business actually needs.

**Expect the answer to be specific to your job, and do not port this leaderboard to yours.**
Best here was mid-priced, worst was the most expensive, runner-up cost $6.42, none of it
predictable from a benchmark, and none of it measured on your documents. What transfers is the
method: run the arms, grade the decisions blind, audit the grader. That costs a few hundred dollars
and answers the question for *your* task, which no published benchmark can.

The whole study cost under $200 and took two days.

## Work with us

JMH Data Sciences builds and evaluates AI systems that make repeated decisions over unstructured
information (news, filings, reports, tickets, claims) where being *approximately right, reliably*
beats being brilliant occasionally.

If you are automating judgment over a document feed and want to know whether it is actually working,
we would like to hear from you.

[jmh-datasciences.com →](https://jmh-datasciences.com)
