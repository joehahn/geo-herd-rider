# STYLE.md: how the docs in this repo are written

Extracted from the existing corpus: this README, `docs/writeups/llm-bakeoff.md`, and the
`portfolio-wave-rider` and `diplomacy-A2A` READMEs. These are descriptive rules, not aspirations. Each one is a pattern already in the writing. Claude checks a draft against this file before
showing it.

## Voice

1. **Bold lead-in, then the explanation.** The most distinctive habit in this corpus. `**Where the
   news comes from.** The firehose has two modes, and they must use different sources, because…`
   Use it to open a paragraph that makes a claim. Do not use it more than once per paragraph.
2. **"This solution" is the subject for mechanism; "we/our" only for a stance.** Mechanism is impersonal: *this solution trades only US-listed stocks*. First person appears where the author is staking a claim: *our model of the market*, *our edge is simply to be reading*. Author-voiced
   write-ups use *I* throughout. Never "we" for a mechanism.
3. **Long sentences, joined.** The rhythm is conversational: clauses strung with `and`, `while`,
   `, so`. Do not write clipped punchy fragments; that is not this voice.
4. **Define a coined term inline, at first use, in the same sentence.** `a *thesis* being the
   specific catalyst driving the ticker`. Never forward-reference a glossary.
5. **The concrete instance follows the abstraction immediately**, often italicized. *A manufacturer
   watching a proposed tariff cares enormously up to the signing and not at all afterwards.*
6. **The caveat rides with the number, not in a footnote.** `roughly +40% to +900% at the current config, all hindsight upper bounds`.
7. **Negative results get the same plain treatment as positive ones.** *The most expensive one
   finished last.* *GDX is the deliberate negative control.* No softening.
8. **Second person when describing the reader's own situation.** *swap the feed and the same
   machinery watches your supply chain.*
9. **Headings are declarative or a question**, in sentence case: *why reading beats reasoning*,
   *what does a smarter model actually buy you?*, *If you are building something like this*. Never a
   corporate noun-phrase heading (*Architecture Overview*, *Key Features*).
10. **Bulleted lists start each bullet with a bolded term**, then a period or a colon, then the
    explanation.
11. **Tables for three or more parallel items** with a comparison across them.
12. **Plot captions**: a plain noun-phrase title, then two to four short simple sentences.
13. **Emoji only inside mermaid diagram nodes.** Never in prose, never in a heading.

## Banned

- **The em-dash.** It is the single loudest tell that a machine wrote the sentence, so this repo does
  not use one anywhere. Every aside can be carried by a comma, a colon, parentheses, a semicolon, or
  a full stop, and picking which one is a real editorial decision rather than a default. Where an
  aside genuinely resists all five, the sentence wanted splitting.
- Marketing adjectives: *powerful, seamless, robust, cutting-edge, comprehensive, elegant*. The
  strongest word this corpus reaches for is **load-bearing**.
- Filler openers: *It's worth noting that*, *In this section we will*, *Let's dive in*.
- Hedging adverbs: *arguably, essentially, fundamentally, quite, rather* (as an intensifier).
- The *not just X, but Y* construction, and rule-of-three lists used for rhythm rather than because
  there are exactly three things.
- A closing paragraph that restates the section that just ended.
- **Past-tense process narration.** See the next section.

## No chronology

State every finding as a **property of the current design**, never as a story about how it was
reached. This is a hard rule, and it is greppable: a draft must contain no *we found*, *we tried*,
*originally*, *was retired*, *after some experimentation*, *it turned out*, *at first*.

- Wrong: *a single-scan baseline caught the right themes but late, then seeding the early articles
  jumped recall from 0% to 92%, which proved retrieval was the wall.*
- Right: *retrieval, not reasoning, is the binding constraint.*

The finding survives; the timeline does not. Readers who want the history can read the commits.

## Evidence

- **Every number traces to a repo source**: a built dashboard, `src/provenance.py`, a stamped
  `provenance.json`. Numbers come from Python, never from an LLM, and never from memory.
- **No tuned parameter values in published docs.** See `HOLDBACK.md`. Naming a value both leaks the
  tuning and guarantees the doc goes stale the next time it moves; describe what the knob governs
  instead.
- **Historical performance is always labelled an upper bound**, in the same sentence as the figure.

## Delivery

Draft in a diff against the existing file. Keep the author's sentence unless it is factually wrong.
Mark every wholly new paragraph `NEW` so the author reads only what changed.
