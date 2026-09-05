# Curator Backtest (CBT) curation — 2023-08-11

- **Run.** `data/cbt_3yr_v25_vehgate` · fingerprint `a25e2c1839d1`
- **Corpus.** `data/backtest_3yr_v5`
- **Replayed under.** `investor_profile.backtest.md`
- **Where every line below comes from.** The curation journal and this build's frozen price panel. No LLM ran to write this report.

- **Period.** 2023-08-11 → 2023-09-10 · book +2.6% · SPY -0.0%
- **Events.** 0 live this scan · 0 funded · 0 unfunded
- **Held with no live thesis.** 2
- **Opened.** ev1
- **Exited.** ev1, of which 1 opened and exited in this same scan
- **Anchors.** BIL 31.0% (always_include, outside the watchlist and not an event)

## Funded

*No funded position rests on a thesis that was live at this anchor. What the book holds is listed below.*

## Exited at this scan

- **ev1** · LLY, NVO · 1 scan · catalyst RESOLVED: Novo Nordisk Select study results already released and priced · never funded

## Held with no live thesis

- **AMZN 29.0%** · (no event) last said live on (never). The watchlist is sticky, so the position outlived the thesis.
- **GOOGL 40.0%** · (no event) last said live on (never). The watchlist is sticky, so the position outlived the thesis.

## Not funded, the three that came closest

*Every live event was funded.*

## Funded against passed over

- funded vehicles, equal weight: **+2.6%**
- unfunded live events: unpriced

---

*How to read one event.* **Catalyst** is the specific, datable thing the curator is WAITING FOR, written in the present tense and usually not yet true; the header says whether it is still pending or has resolved, and the event lives or dies by it. **Milestones** are dated waypoints already observed on the way to it, which is what lets an agent notice that a due date has passed. **Scan N** counts how many times an agent has re-read that thesis, so a high N against an unchanged assessment is a thesis nothing is confirming. The money is this event's share of realised P&L: a ticker claimed by several live events splits its gain equally between them. **Exits when** is the condition the curator committed to at the outset, so an event whose exit cannot be dated is a theme wearing an event's clothes, and only the `max_event_scans` age cap will ever end it.

