# AI to automate routine business decisions: what does a smarter model actually buy you?

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
for anyone to read, with only a small portion of it being consequential. This experiment uses AI to
read a flood of financial news to optimize a market portfolio, because there the scorecard is
unambiguous. But swap the feed and the same machinery watches your supply chain, your regulators, or
your competitors.

## The decisions being automated

The AI reads the stream as it arrives, watching for a just-published event that is about to
affect the business: a ruling, a supply shock, a plant going down, a competitor stumbling. Finding it
once is not the job though, because the situation keeps moving and the reason to act can expire. So
for every situation it is already tracking, the solution revisits three questions on a schedule:

- **Is this still true?**: the situation I flagged is still developing, and the reasoning I
wrote down still holds.

- **Has the thing I was waiting for already happened?**: most situations turn on one
identifiable event: a ruling, a signed act, a contract award, a plant restart. Once it happens,
the uncertainty is gone and so is the reason to act. *A manufacturer watching a proposed tariff
cares enormously up to the signing and not at all afterwards, because by then the price has moved.
An on-call engineer watching a spreading failure cares until the fix ships.*

- **Should I still be committed to it?**: should capital, inventory or capacity still be tied
up on the strength of this, or is that commitment now doing nothing.

That results in roughly **570 AI judgment calls per scan**, where a **scan** means one
complete pass across three years of business news, about 100,000 articles: month by month, from
scratch, making every call in sequence exactly as it would have at the time. In this experiment, a scan takes about 1-3
hours for AI to process and costs about $5-30 depending on which model is doing the reading.

Which raises the obvious question: **does paying for a better model pay?** So I ran the whole
thing **eight times**, changing only the model making those calls: same articles, same retrieval,
same downstream logic. Prices spanned **5×**.

Here are the main findings, all of them specific to this use case.

## 1. Cost says nothing about speed

*[chart 1]*

Each bar indicates how long that AI model took to read and decide upon the
100,000-article corpus and make every call that followed. The models are ordered by price, least
expensive on the left, and the figure under each name is what it costs relative to the cheapest of
the eight.

The first surprise is a practical one. **Price and speed are unrelated.** The cheapest model was
the *slowest* by a factor of four: three hours against forty-five minutes, with two models
within seven cents of each other differing by more than two hours of wall clock.

So if you are running this hourly against a live feed rather than monthly against an archive, that
difference decides which LLM is usable at all, and that factor is invisible on a price list.

## 2. Quality peaks in the middle

*[chart 2]*

This is also a cost-optimization exercise, so our goal is not to crown the best model, but to find
the most capable AI per dollar spent. To do that I used a top-of-the-line frontier model to judge
the other models' decisions, Claude Fable 5, which never touched the production path. **The judge re-read
the decisions the eight working models had made and graded them, blind to which model produced
which.**

Each decision was scored on **process only**, with no post-AI outcomes in front of the judge.
It never saw whether a call made money or lost it, so **a lucky guess earns nothing** and a
well-reasoned call that happened to go wrong loses nothing. Three tests: was the trigger a specific, datable event rather than a vague trend? Did the
AI model's write-up claim more than its own cited sources support? Was the keep-or-drop call consistent with the
exit condition the model itself had written down? A decision is **clean** only if it passes all
three.

**The chart above gives the percentage of each model's ~570 decisions that came back clean.
Higher is better.** Quality separates sharply where the portfolio value could not, a
23-point spread across the eight. And the curve **peaks in the middle**:
**GPT-5.6 Luna** leads at 63.8%, while **Claude Sonnet 5**, the
dearest of the eight at 5.3× the cheapest, finished *last* at
40.8%. **Grok 4.3 LOW reasoning** came within 3 points of the
leader for less than half the leader's cost, and that is the configuration this study picks: nearly the best work on
offer, at a fraction of the price of the models either side of it.

This is not "cheaper is better": the cheapest model is near the bottom too. It is that
**price predicts almost nothing about fitness for a particular job**, and the only way to find out
is to grade the work.

## 3. Knowing *how* a model fails beats knowing *that* it does

*[chart 3]*

Section 2's scores are a synthesis of the three tests detailed here, higher is better.

**In this experiment every model is weakest on the same thing.** The yellow bars score how well
the AI identifies a specific, datable trigger, and that score runs 46–66% across eight models from
six vendors. Internal consistency (blue bars) sits at 93–100% for every one of them. When
everything fails the same way the model is unlikely to be the problem. Rather, the instructions
might be at fault, or a datable trigger may simply be hard to pin down and this close to the
ceiling. Either way that is where to look, and looking there is worth more than any model swap.

**But one test (green bars) separates the field:** staying inside your sources,
75–97%. That is what
the extra money bought, where it bought anything. The most expensive model is the instructive case: it
writes well-evidenced analysis of things that *are not events*. That tells us **the most
expensive model considered here is not well suited to this particular job**, and no leaderboard
would have told you so.

## If you are building something like this

**Grade decisions, not outcomes.** Eight outcomes cannot separate eight models. About 4,500
graded decisions can. One is a sample of one; the other is a sample of thousands.

**Spend frontier money on the judge, not the worker.** The most valuable model in this study
never ran in production. It graded what did.

**Measure inference time, not just price.** A four-fold speed difference decides whether a
system can run at the cadence your business actually needs.

**Expect the answer to be specific to your job, and do not port this leaderboard to yours.**
Best here was mid-priced, worst was the most expensive, runner-up cost 1.1× the
cheapest, none of it predictable from a benchmark, and none of it measured on your documents. What transfers is the
method: run the arms, grade the decisions blind, audit the judge. That costs a few hundred dollars
and answers the question for *your* task, which no published benchmark can.

The whole study cost under $200 and took two days.

## Work with us

JMH Data Sciences builds and evaluates AI systems that make repeated decisions over unstructured
information (news, filings, reports, tickets, claims) where being *approximately right, reliably*
beats being brilliant occasionally.

If you are automating judgment over a document feed and want to know whether it is actually working,
we would like to hear from you.

[jmh-datasciences.com →](https://jmh-datasciences.com)

The code, the corpus and the grading harness are public:
[github.com/joehahn/geo-herd-rider](https://github.com/joehahn/geo-herd-rider).
