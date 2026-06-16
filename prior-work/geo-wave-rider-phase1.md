[&larr; herd-wave-rider README](../README.md)

# Prior work — geo-wave-rider Phase 1 (the falsification that motivates this project)

This is the verbatim findings writeup from the predecessor experiment
[`geo-wave-rider`](https://github.com/joehahn/geo-wave-rider), preserved here because it
is the empirical justification for herd-wave-rider's whole design: it proved the naive
"ride the loud telegraph" strategy has no edge after costs, and showed the only flicker
of signal lives in deep causal chains off quieter sources — which is exactly the band
herd-wave-rider targets. Reproduce it in that repo.

---


# geo-wave-rider — Phase 1 findings (null result)

**Date:** 2026-06-16 · **Verdict: NO-GO.** The pre-registered gate is missed on both
criteria. Do not build the optimizer. This is the honest, credible null the SPEC
anticipated — with one genuinely interesting sub-signal flagged for Phase 2.

## Headline: the blanket strategy has no edge after costs

Across all 26 telegraphs (winners + deliberate duds), net of a costs/slippage haircut,
excess return vs SPY over each mapped horizon:

| metric | value | gate | result |
|---|---|---|---|
| median per-event excess | **−0.73%** | > +3% | **FAIL** |
| hit rate | **42.3%** (11/26) | > 55% | **FAIL** |
| mean per-event excess | +1.78% | — | — |
| IQR | −5.43% … +6.63% | — | — |

Mean (+1.78%) exceeds median (−0.73%): the distribution is **right-skewed — a handful
of big winners drag the average up while the typical trade loses.** That is exactly the
"one BWET carrying everything" failure mode the **hit-rate gate exists to catch, and it
caught it.** A blanket "map every telegraph to a trade" rule is not investable.

## The motivating anecdote did not even replicate

The experiment was seeded by BWET ~4x'ing after carriers repositioned near Iran. The
closest telegraph in the set, **GW025 (the June 2025 strikes on Iran), scored −8.2%** —
oil and the shipping/defense basket *faded* as the conflict de-escalated within days and
Hormuz stayed open. The remembered 4x was a different, later episode. This is a direct,
textbook hit on the **survivorship bias** the SPEC named: we remembered the winner and
forgot that the obvious telegraphed trade lost.

## The interesting part: the herd refinement shows a strong gradient

Joe's reframe (edge = being early to where the slow herd is heading; deep, quietly-diffusing
causal chains beat loud megaphone calls) produced the sharpest structure in the data:

**By causal-chain depth (diffusion-lag proxy):**

| chain_depth | n | median excess | hit rate | % drift paths |
|---|---|---|---|---|
| 1 (direct/obvious) | 5 | −0.65% | 40% | 20% |
| 2 | 14 | −3.56% | 29% | 21% |
| 3 (multi-hop) | 7 | **+9.42%** | **71%** | 43% |

**By audience breadth:**

| audience | n | median excess | hit rate |
|---|---|---|---|
| megaphone (top Trump/Musk post) | 14 | **−4.04%** | 21% |
| broad / niche | 12 | **+9.42%** | ~73% |

**By return-path shape (the herd signature):** all **7 `drift` trades were winners**
(median +21%); the 14 `fizzle` trades had median −5.2%. Gradual accrual = the herd
arriving; instant-pop/fizzle = already priced.

The direction is exactly what the herd model predicts: **loud, shallow, obvious calls are
already grazed by the time you read them; edge — if any — lives in deep chains off quieter
sources that the herd is slow to trace.**

## Why this is a signal, not a result — and what it means for Phase 2

Three reasons the herd gradient is **hypothesis-generating, not proof**:

1. **Hindsight contamination.** Mapping was done in-session by a model trained past these
   2024–2026 events. `chain_depth` and `audience_breadth` were assigned by that same
   contaminated mapper, so the gradient could partly reflect *which baskets it chose to
   call "deep,"* not a real market mechanism.
2. **Tiny n.** 26 events; the winning depth-3 cohort is n=7. Not significant.
3. **Confounding windows.** Some horizons caught unrelated catalysts — GW011's 14-day
   window caught Tesla's Q3 earnings pop, not the robotaxi reaction; GW021's window caught
   the late-Jan AI/DeepSeek selloff. The endpoint isn't always attributable to the telegraph.

**Conclusion.** The blanket strategy is a clean NO-GO — stop, don't build the optimizer.
But the diffusion-lag sub-signal is alive and gives Phase 2 a *precise, falsifiable target*
rather than a vague one: **log only deep (chain_depth ≥ 3), quietly-diffusing telegraphs,
live and forward, and test whether they drift.** A live forward logger is also the only way
to remove the hindsight contamination that caps what Phase 1 can claim. If that forward
test holds, there's something real; if it doesn't, the gradient here was an artifact — and
either way we'll know honestly.

## Reproduce

`data/events_mapped.csv` (inputs + mapping) and `data/events_scored.csv` (per-event
results) are committed. Re-run `python src/score.py` to regenerate the report. For an
arms-length mapping, run `python src/map_event.py` with `ANTHROPIC_API_KEY` set — that runs
the scripted blind agent with live `before:`-date web search instead of this in-session pass.
