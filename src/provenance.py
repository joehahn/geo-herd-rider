"""provenance.py — what makes a curation THE canonical one, and the gate that enforces it.

THE PROBLEM THIS EXISTS TO KILL. A published dashboard is a claim about a specific book, and the
book is a function of three inputs that live in three different places:

    corpus (data/<pool>/pool.json)  ->  curation (data/<run>/journal.json)  ->  book (the profile)

Nothing tied them together, so every one of them has drifted at least once and the page said nothing:

  * 2026-08-12  FBT's --run default pointed at a 1-year pool; the page rebuilt on 1/3 of the data.
  * 2026-08-19  `cp -R` left a stale journal inside cbt_3yr_v9. CBT and the sweep both ran on a
                curation nobody had produced. Caught only because a reader noticed SBT quoting
                $272K against CBT's $115K for what was supposed to be one run.
  * 2026-08-21  CBT's --run default still said data/cbt_1yr, ~50 commits after the published page
                had moved to the 3-year grok run. A bare rebuild silently produced a 26-event page.
  * 2026-08-21  docs/cbt.html was showing $272,336 against the profile's $112,435 -- not a bug, but
                a page built five hours BEFORE `max_watchlist: 8 -> 6` was committed, and never
                rebuilt. Diagnosing that took a full price-panel investigation to rule out.

Each was found by eye, late, and after someone had already trusted a wrong number.

THE DISTINCTION THAT DOES THE WORK. Profile knobs split cleanly by WHERE THEY ACT:

  CURATION knobs act UPSTREAM of the journal -- which articles are read, which the scout is shown,
  which events open and when they retire. Change one and the existing journal could never have been
  produced under it. The run is INVALID and must be re-curated (LLM cost, hours).

  BOOK knobs act at REPLAY time, over a fixed journal -- sizing, culling, rebalancing. Change one and
  the same curation simply produces a different book. Nothing is invalidated; the page just needs a
  rebuild (seconds, free). `max_watchlist` is the type specimen: watchlist_cap() is called ONLY in
  firehose.backtest, never in the scan path.

Getting that boundary wrong in either direction is expensive. Treat a book knob as curation and every
sizing tweak demands a re-curation. Treat a curation knob as book and you publish a page whose
settings table describes something the journal never ran under -- which is the 2026-08-19 failure.

So the canonical curation is not a path anyone has to remember. It is DERIVED: the run whose recorded
inputs match (canonical corpus, current profile's curation knobs). CANON_RUN below is a pointer for
convenience, and `verify` checks the pointer against that definition on every build.

WHY A SWEEP THAT READS THE NEWS CANNOT BE CONFUSED WITH IT. Such a sweep varies a CURATION knob --
that is what makes it need the news -- so each arm's fingerprint differs from the profile's by
construction, `verify` reports it as non-canonical, and `require_publishable` refuses to let it
overwrite docs/. No naming convention to remember and no discipline required: the arms cannot reach
the published pages even if someone points a builder straight at one.
"""
from __future__ import annotations

import hashlib
import collections
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- the canonical inputs
# ONE place. Promoting a new corpus, curation or sweep is an edit here and nothing else; every
# builder default derives from these, and every publish is checked against them.
CANON_CORPUS = "data/backtest_3yr_v5"
# Promoted 2026-08-22 to v18, the first curation at the current config (min_bundle_articles 1,
# max_events 0 = uncapped, max_event_scans 6). Curated with --decisions, as mb2rep was and
# as mb1/mb2/mb3 were not.
# Earlier note, on why --decisions is not optional:
# mb1/mb2/mb3 were not. Proposed-and-culled candidates are persisted nowhere else, so
# without it CBT's funnel loses three bars and panels 12/14/16/17 render empty.
# Earlier note, still true of why v9 was dropped: Same config, but v9 was curated when the corpus held
# 56.9% archived lede; the wayback backfill completed 2026-08-21 and mb1 read ~72%. v9 also
# predates provenance stamping, so 16 of its 24 curation knobs were never recorded -- it passes
# `verify` only because unrecorded knobs cannot be checked. mb1 stamped all 25 at creation.
# NOTE the gap this exposes: corpus_id is path + article count, and enrichment changes NEITHER,
# so nothing here could have told you v9 was stale. That wants a text-state digest.
CANON_RUN = "data/cbt_3yr_v37_nocull"       # v34 -> v37 on 2026-09-10. NO EVENT CULL:
                                      # max_events 0. THIS TURNS THE RANKER OFF ENTIRELY, not just the cull --
                                      # backtest_gdelt.py:388 gates the ranker on `max_events AND picker_model`,
                                      # so a zero cap never constructs it. v37's log carries no 'event-ranker
                                      # ON' line and 0 of its 601 events carry a rank or pct. Said wrongly here
                                      # when v37 was promoted ('still ORDERS every event') and corrected the
                                      # same day off the journal. CONSEQUENCE: CBT plot 4 (score-vs-gain) is
                                      # EMPTY on this curation -- '0 funded events with a rank' -- which is
                                      # correct behaviour for a run with no ranker, not a plotting bug.
                                      # The only surviving cut is max_watchlist. This is the CONTROL that six
                                      # successive LLM rankers had failed against: removing the ranking layer
                                      # entirely gave identical escalator capture (10 vs 10), BETTER
                                      # cancellation (40% vs 47%), events living 44% longer (2.86 vs 1.99
                                      # scans) and 61.8% vs 50.8% resolving on their own terms. Keeping the
                                      # order while dropping the cull keeps the useful half and pays for the
                                      # cheap half -- BUT SEE ABOVE: no ranker ran, so there was no useful half
                                      # to keep. v37 is the pure null. 37 scans, 2023-08 .. 2026-07.
                                      # PRIOR: v33 -> v34 on 2026-09-09. AN EVENT MUST SIT INSIDE A
                                      # PROCEDURE. Six debugging iterations, each read out of the
                                      # events themselves rather than off a scoreboard. The press
                                      # reports acts that have ALREADY happened; this book needs the
                                      # act that has NOT, and the two only connect when the reported
                                      # act sits in a procedure obliging a next step -- an opened
                                      # investigation must conclude, a filed application must be
                                      # decided. Where none exists the scout reached for a theme and
                                      # everything downstream failed.
                                      #   PROMPTS: pending_next must be an act the catalyst's own
                                      #     procedure REQUIRES; both exit branches must be discrete
                                      #     occurrences, not two ends of a dial; the exit points
                                      #     FORWARD, never back at the act the catalyst reports; the
                                      #     structural-driver exception is deleted; the agent may
                                      #     prune peers but not the SUBJECT, and is now TOLD which
                                      #     vehicle that is (`opened_on`).
                                      #   GATE BUGS, all found by reading what the gates ADMITTED:
                                      #     _DATED_PENDING matched `mar` inside "market" and `dec`
                                      #     inside "decision" (a date short-circuits the gate, so
                                      #     any pending_next containing "market" was admitted
                                      #     unread); `financ` matched "financials" as well as
                                      #     "financing"; `upgrad` was missing while `downgrad` was
                                      #     present; and the retired roster checked only the primary
                                      #     ticker, so a retired name walked back in as somebody
                                      #     else's peer -- AMD returned twice on the identical
                                      #     NVIDIA antitrust thesis it had been retired from.
                                      #   SCORING: exit_quality was being scored on a field that does
                                      #     not exist yet -- the cull runs BEFORE the agents write
                                      #     the scan's entries, so a newborn's exit was blank and the
                                      #     judge read the blank arbitrarily. First-scan zeros: 23 in
                                      #     v33, ZERO here.
                                      # THE SWEEP: cancellation median 61.6% -> 38.8%, best 30.3% ->
                                      # 12.7%, median Sharpe 0.71 -> 1.33. Median final rose 1.76x,
                                      # which #6 puts INSIDE the unmeasurable band, and dispersion
                                      # got worse (CV 0.389 -> 0.583) -- so the P&L is not the claim.
                                      # NO CONFIG CHANGE: the profile already sits on the marginal
                                      # optimum for lookback (30) and drop_unfunded (4), and the two
                                      # knobs that differ are a plateau and a noise-width apart.
                                      #
                                      # Previous note, v31 -> v33 on 2026-09-08. THE SAME-TICKER GUARD, RELAXED.
                                      # A candidate whose ticker was already held could never open a
                                      # second event, however unrelated its thesis. Measured on 385
                                      # dropped candidates: 21% were CLEARLY DISTINCT (<20% word
                                      # overlap with the holder's catalyst), 60% were restatements the
                                      # guard was right to kill. The relaxation routes only the
                                      # distinct ones to the matcher, reusing `_restates_resolved`.
                                      # 138 routed over 35 scans: 60 opened a real second event, 54
                                      # were folded back by the matcher, 24 joined a third event --
                                      # INTC's spin-off AND its export-control event, LLY's Wegovy
                                      # label AND orforglipron, KRYS's Vyjuvek AND KB707 PDUFAs.
                                      # ALSO FIXED: evscore stopped being stamped whenever the LLM
                                      # ranker ran, so the two scorers were never on the same events
                                      # (790 / 59 / 0 both). Now 840 entries carry both -- which is
                                      # how we learned evrank ranks forward escalators AT CHANCE
                                      # (|d| < 0.07 across 16 escalator definitions, both runs). The
                                      # d = +1.03 measured on v32 was an artifact of that 59-entry
                                      # sample and does NOT replicate.
                                      # NOT improved, and recorded so it is not re-litigated: the
                                      # no-restatement prompt rule is a null (29% both runs), and the
                                      # span rubric fix made `spans_a_month` MORE constant (95% -> 97%
                                      # TRUE), so its +1 is close to a fixed offset.
                                      # P&L is NOT the reason: median final fell $109K -> $93K (inside
                                      # the band #6 calls unmeasurable) and median cancellation rose
                                      # 53.5% -> 61.6%. Promoted on MECHANISM.
                                      #
                                      # Previous note, v30 -> v31 on 2026-09-07. THE EXPOSURE CLAUSE -- the one
                                          # field a reader uses to decide whether to trust the basket
                                          # -- was written once by the SCOUT (the cheap model) and
                                          # never revisited by the judge. Three changes, no knobs:
                                          #   DIRECTION REQUIRED. Every clause now leads with "gains"
                                          #     or "loses". 0% -> 100% of 952 clauses.
                                          #   THE EVENT AGENT RESTATES IT each scan, so the stage that
                                          #     re-reads the catalyst owns the justification. Clauses
                                          #     describing a NON-vehicle: 341 -> 0 (v30 had ev164 at 18
                                          #     clauses for 5 vehicles, ev209 at 12 for 1).
                                          #   LONG-ONLY GATE, enforced at both stages: a vehicle whose
                                          #     own clause says the catalyst works AGAINST it is
                                          #     dropped, not merely described. Fired 40 times.
                                          #
                                          # WHY: ev432 was the best-justified event in v30 -- dated
                                          # milestones, falsifiable two-sided exit, sources every scan,
                                          # clean resolution -- and its four airlines were attached to
                                          # a Strait of Hormuz closure as "sensitive to fuel price
                                          # increases". They then rose ~50% BECAUSE the threat receded
                                          # and oil fell 19%. The trade won and the recorded reason
                                          # predicted a loss. No gate we had checked anything but FORM.
                                          #
                                          # PROMOTED ON RECORD QUALITY, NOT ON CAPTURE, and the
                                          # difference is stated because the numbers invite the wrong
                                          # read. Escalators AVAILABLE in the pool rose 19 -> 23 of 37
                                          # months, but escalators FUNDED fell 11-in-9 -> 5-in-4 and
                                          # the final $266,604 -> $193,179. That is NOT attributable:
                                          # v30 and v31 are different CURATIONS, not a paired replay,
                                          # and #6 records the same config drawn twice at $117,200 and
                                          # $62,997. The gates removed 40 vehicles of 1,143 slots --
                                          # too small a perturbation to own a 2x swing -- and the loss
                                          # sits at the FUNDING stage, which measured all day as
                                          # ranking escalators at chance. Resolving it needs a re-run
                                          # of v31, not an argument.
                                          #
                                          # STILL OPEN: 17% of vehicles carry no clause at all (was
                                          # 21%), and 3 clauses still say "loses" -- all on events
                                          # culled at birth, which never get an agent read, so the
                                          # reconciliation never touches them.
                                          #
                                          # Previous note, v29 -> v30 on 2026-09-07. Seven knob changes aimed at
                                          # ESCALATOR HARVESTING -- getting names that run >=1.5x in a
                                          # month in front of the optimizer. Four act at replay
                                          # (max_watchlist 12->16, concentration_cap 0.4->0.25,
                                          # min_trade_size 0->0.05, max_stale_scans 8->5) and three at
                                          # curation (max_events 0->24, news_lookback_days 0->31,
                                          # max_silent_scans already 5), which is why this run exists.
                                          #
                                          # THE EVENT-CULL IS BACK ON (max_events 24) so events are
                                          # ranked and compete, and so that ranking path is executable
                                          # and debuggable -- at 0 it never ran.
                                          #
                                          # MEASURED AGAINST v29, book knobs held identical:
                                          #   live POOL                     105 -> 79 names
                                          #   months w/ escalator available  25 -> 19 (close basis)
                                          #   FUNDED escalators            8 in 7 -> 5 in 5 months
                                          #   cull-at-birth                 45% -> 28%
                                          # So the cull did NOT repeat the historic blow-up, but it
                                          # culls at RANDOM with respect to escalator capture. That is
                                          # expected: evscore ties a momentum sort (88th vs 92nd pctile
                                          # against a 24-seed null) and scores velocity 0.0 for any
                                          # event with no prior scan, with no freshness reserve --
                                          # unlike _ranked_cull, which has one for exactly this reason.
                                          # PROMOTED ANYWAY, deliberately: this is the configuration to
                                          # iterate the event ranker against, and the ranker cannot be
                                          # improved while it is switched off.
                                          #
                                          # THE STANDING FINDING, unchanged by any knob tried so far:
                                          # escalators sit in the pool in most months and NEITHER
                                          # selection stage can find them. Trailing price ranks them at
                                          # chance (median rank 56 of 126; top-20 hit rate 17% vs 15.9%
                                          # expected), and capture is flat whether the optimizer is
                                          # handed 12 names, 16, 40 or the whole pool uncapped.
                                          #
                                          # Previous note, v28 -> v29 on 2026-09-07. Two changes, both measured
                                          # BEFORE the run, on v27 and v28 independently:
                                          #   OCCURRENCE GATE on `pending_next` -- a candidate whose
                                          #     pending thing is a price move, a trend or another
                                          #     analyst's opinion ("the realization of the 120%
                                          #     upside", "Canaccord revises IREN rating again") is not
                                          #     an event: nothing can resolve it, so its exit can
                                          #     never fire on the merits. Events whose pending_next
                                          #     names a real occurrence: 91% -> 100%.
                                          #   max_silent_scans 8 -> 5. Events that exit on the MERITS
                                          #     go quiet for a median of ONE scan; events retired by a
                                          #     timer go quiet for EIGHT, piling up at exactly the old
                                          #     cap (96 of v28's 132). Cutting at 5 catches 85% of
                                          #     those for 12% collateral.
                                          # WHAT IT BOUGHT: no event now holds through more than 5
                                          # consecutive silent scans; the blunt 12-scan wall almost
                                          # stopped firing (45 -> 12 events); median event lifetime
                                          # 8 -> 6 scans; silent scans per live event 4.80 -> 3.61.
                                          # WHAT IT DID NOT: the judgment-vs-counter balance is flat
                                          # -- of events that ENDED, 47.0% (v28) vs 47.9% (v29) ended
                                          # on the agent's judgment rather than a timer. v29 makes the
                                          # timer fire sooner; it does not make the agent the thing
                                          # that retires events. "Agent said the thesis is dead" is
                                          # 5% in BOTH. That is the next target and it is a prompt
                                          # change, not a knob.
                                          # COST: 452 events vs 562, nominations 110 -> 64/scan. The
                                          # candidate pool the cull ranks only fell 168 -> 126, so it
                                          # still sees ~21 candidates per watchlist slot.
                                          # ALSO NEW, inert by design: every entry now carries
                                          # `coverage` (evscore over the scan's own pool -- score,
                                          # mentions, source_breadth, author_breadth, superlatives,
                                          # velocity; 1,641 entries, velocity non-zero on 72%).
                                          # NOTHING consumes it yet -- max_events is still 0 and
                                          # _ranked_cull still allocates all 6 slots on recency and
                                          # price momentum. It is stamped so that testing a
                                          # coverage-weighted cull_rank becomes a BOOK-knob replay
                                          # over a fixed journal instead of another curation.
                                          #
                                          # Previous note, v27 -> v28 on 2026-09-06. Adds the two fields that make an
                                          # event explain itself, at no cost to v27's numbers:
                                          #   EXPOSURE -- every vehicle, peers included, states how it
                                          #     is connected. 1,438 clauses over 1,064 vehicle slots;
                                          #     the weak-clause gate leaves 45. Peers were 33% of
                                          #     funded position-days with nothing said for them.
                                          #   MILESTONES {what, kind, when} -- 2,679 recorded, 90%
                                          #     `happened` / 10% `expected`, 96% carrying a real date.
                                          #     A bare string could not tell a development from a
                                          #     forecast, and `when` gives the due-date exit rule
                                          #     something to test for the first time.
                                          # v27's numbers held: pending 57%, dated 20%, contradictory
                                          # 10%, two-sided exits 94%, absorption 0.08, resolved 59%.
                                          #
                                          # PROMOTED KNOWING IT IS NOT FINISHED. 22 of 562 catalysts
                                          # (4%) are an analyst call rather than an occurrence
                                          # ("Goldman Sachs projects gold to reach $5,000 if..."), and
                                          # such an event's exit passes the two-sided test while being
                                          # unfalsifiable ("if/when Fed independence is compromised or
                                          # preserved"). That is the next rule, and the metric limit
                                          # is worth remembering: "offers two alternatives" is not
                                          # "is checkable".
#
# PREVIOUS: data/cbt_3yr_v27_catalyst      # v25 -> v27 on 2026-09-06, promoted on MECHANISM. It is the
                                          # first curation whose catalysts state what has to happen,
                                          # to whom, and whether it already has, and whose exits name
                                          # both outcomes rather than only failure. Measured over the
                                          # same corpus, profile and 37 anchors, cold start, code the
                                          # only difference:
                                          #   catalyst says pending/scheduled      3% -> 59%
                                          #   catalyst carries a date              5% -> 21%
                                          #   self-contradictory on its first scan 25% -> 11%
                                          #   exit offers two alternatives        62% -> 96%
                                          #   exit derived from `pending_next`        -> 88%
                                          #   absorption of unrelated vehicles  0.21 -> 0.05 per event
                                          #   slice precision                  20.5% -> 22.3%
                                          #   cited articles kept in the slice    97% -> 98%
                                          # It also carries the word-boundary ticker fix, the rewritten
                                          # MATCH_SYSTEM, milestones merged in code, and matcher/peer
                                          # decision logging.
                                          #
                                          # THE TWO OBJECTIONS, both checked and both non-issues. 530
                                          # events against 252 is the matcher keeping distinct
                                          # occurrences apart, not splitting one: one-scan events FELL
                                          # to 41% and same-vehicle near-identical catalyst pairs
                                          # stayed at 5. And resolved/finished 72% -> 69% is
                                          # composition (14% still live at run end vs 11%) plus noise
                                          # at n=453; the number that would have mattered, events dying
                                          # on the age cap, is flat at 11%.
                                          #
                                          # NOT a P&L promotion. Per non-negotiable #7 the backtest is
                                          # an upper bound and the forward eval is the verdict; every
                                          # figure above is a mechanism, and none of them is the book.
#
# PREVIOUS: data/cbt_3yr_v25_vehgate      # v24 -> v25 on 2026-08-31: the first curation run with the
                                          # VEHICLE GATE live (agent._named_in -- an event agent may
                                          # only ADD a vehicle the press named in a headline among that
                                          # event's matched articles).
                                          # PROMOTED ON CODE-PROVENANCE. v24 was curated before the
                                          # gate existed and is not reproducible from current code.
                                          # This is the same test used at v22->v23 and v23->v24, and
                                          # applying it here is NOT optional: the v25 book reads
                                          # $271,588 against v24's $797,162, so holding canon at v24
                                          # would be selecting the canonical curation BY ITS P&L --
                                          # exactly the bias non-negotiable #6 exists to prevent.
                                          # THE DROP IS A DIFFERENT DRAW, NOT A REGRESSION, and not the
                                          # gate. The gate refused 4 of 88 vehicle additions and ALL
                                          # FOUR tickers entered the book through another event, so it
                                          # cannot move a book 3x. The two curations share no name in
                                          # their top four (v25: SMCI/MP/NLST/INTC; v24:
                                          # SLV/OUST/STM/RKLB). Same-settings variance of this size is
                                          # already on the record here: $117,200 vs $62,997, and one
                                          # sweep cell $588,538 -> $75,132.
                                          # WHAT THE GATE ACTUALLY BOUGHT: journal integrity at the
                                          # EVENT level, and about nothing at the book level. Measured,
                                          # not asserted.
                                          # PRIOR, v23 -> v24 on 2026-08-30: the first curation run under
                                          # the WIRE-DATELINE lede rule (a paid-wire dateline is not a
                                          # lede, so the article is headline-only). PROMOTED ON
                                          # PROVENANCE, NOT ON RESULTS: v23 was produced by code that
                                          # no longer exists, and the lede rule is a CURATION-path
                                          # change -- it alters what the scout reads.
                                          # WHAT IT IS EVIDENCE FOR, and what it is not. The
                                          # attribution rests on a CONTROLLED paired A/B, not on this
                                          # run: same window (2026-03-28), same scout, old vs new lede
                                          # -- GRDX proposed 1/1 under the old rule (verbatim the
                                          # historical thesis and peers) and 0/3 under the new one.
                                          # This curation is only CONSISTENT with that: it has no
                                          # GRDX and 1 wire-promoted vehicle against v23's 4, but it
                                          # also has 265 events against 347 on a 41-article input
                                          # delta, which is matcher variance, not the fix. The
                                          # discovery gate is byte-identical across both runs (it
                                          # matches on titles, which did not change) and the scout
                                          # actually proposed MORE (1,280 vs 1,219) and admitted MORE
                                          # (1,073 vs 1,050) -- so the event-count drop is clustering
                                          # noise. Do NOT read any book-value difference as the fix.
                                          # PRIOR ENTRY, v22 -> v23 on 2026-08-29: the first curation run
                                          # under the SILENCE CAP (max_silent_scans=8) and the
                                          # first-read retire guard. Unlike the v21->v22 promotion
                                          # the fingerprint DOES move (624c6c2e211c -> a25e2c1839d1):
                                          # max_silent_scans is a new CURATION knob, so v22 could
                                          # not have been produced under this profile.
                                          # BUT check_canon still reports v21/v22/v9 as "matching",
                                          # because a knob absent from an OLD run's stamp is skipped
                                          # rather than counted as a mismatch. Adding a curation knob
                                          # therefore does NOT retroactively invalidate the runs that
                                          # predate it -- they are grandfathered by omission, and only
                                          # the published-page hash catches the drift.
                                          # MECHANISM, the reason it was promoted (never the P&L,
                                          # non-negotiable #6): the quiet-run tail is truncated
                                          # dead at 8. v22 had 32 events running 9-12 consecutive
                                          # ZERO-SOURCE scans -- ev222 held PCG through nine reads
                                          # of "No news on the $1B TMI loan" -- and v23 has none
                                          # past 8. The retire guard stopped 174 events that
                                          # resolve on their OPENING read from banning vehicles
                                          # they never chased.
                                          # COST, measured and NOT a saving: 347 events / 1,372
                                          # agent-scans against v22's 284 / 1,199. The cap alone
                                          # skips ~8% of the budget, but the retire guard hands
                                          # the scout back ~130 tickers it had been barred from
                                          # re-proposing, and those open more events than the cap
                                          # closes. Net +14% scans.
CANON_BOOTSTRAP_RUN = "data/cbs_v12"   # the curation behind docs/cbs.html. v11 -> v12 on 2026-09-06,
                                      # re-seeded from the promoted CANON_RUN (cbt_3yr_v27_catalyst):
                                      # v11 inherited its live theses from v25, so the live book was
                                      # running gates and prompts that had since been replaced. Also
                                      # the first bootstrap curated at today's scan-path code, which
                                      # clears the drift v11 carried.
                                      # PREVIOUS: data/cbs_v11
                                      # v10 -> v11 on 2026-09-02: THE SEED JOURNAL, not the corpus.
                                      # Every bootstrap since cbs_v3 was seeded from
                                      # cbt_3yr_v21_evscans12 while CANON_RUN advanced v22 -> v23 ->
                                      # v24 -> v25, so the live book inherited its live theses from a
                                      # curation that predates the ticker-resolver fix, the SILENCE
                                      # CAP, the first-read retire guard and the vehicle gate.
                                      # WHAT THAT COST, measured on cbs_v10: 29 events predating its
                                      # own first scan, 27 with no matching catalyst in the canonical
                                      # journal ("Potential 479% upside in biotech sector" among
                                      # them), and 60 tickers reaching the live watchlist only
                                      # through those. ev202 (IXHL, "FDA approves sleep apnea drug")
                                      # ran TWELVE consecutive zero-source entries -- the exact shape
                                      # max_silent_scans exists to kill, which v21 did not have --
                                      # then aged out at scan 1 here, and STILL took 40% of the
                                      # 2026-08-25 published recommendation, because retiring an
                                      # event writes no thesis_live=False decision row and
                                      # max_stale_scans (8) outlives a 5-scan bootstrap.
                                      # check_canon now compares this run's stamped --seed-journal to
                                      # CANON_RUN, which is the check that would have caught it.
                                      # ADDED 2026-09-01. It was hard-coded as "data/cbs_v9" in
                                      # build_cbt_dashboard.py AND check_canon.py -- the second time
                                      # this file's own rule ("do NOT hard-code a run or corpus path
                                      # in a builder again") was broken, and it meant promoting a new
                                      # bootstrap curation took edits in two unrelated scripts.
                                      # v9 -> v10 on 2026-09-01: v9 was curated 2026-08-28 under
                                      # agent.py 832e6b02bc72 / org_tagger.py 77114340e28b, both of
                                      # which have since moved. org_tagger decides which companies an
                                      # article is ABOUT, so that drift changes what the scout sees --
                                      # a re-scan, not a rebuild. Seed journal deliberately UNCHANGED
                                      # (cbt_3yr_v21_evscans12) so code is the only variable; that it
                                      # is not CANON_RUN is a separate open question.
CANON_SWEEP = "data/sweep_cbt_3yr_v37_nocull.json"       # 2026-09-10, over the promoted v37.
                                      # 5,880 cells, BOOK knobs only, replay over the frozen journal.
                                      # THE risk_aversion AXIS WAS RESTORED for this sweep -- [0.0, 0.5, 1.0,
                                      # 2.0, 5.0, 10.0, 16.0]. An earlier trim had cut 0.5/1.0/2.0/3.0 as
                                      # 'known bad', which hid the fact that the axis is monotone and that the
                                      # profile was sitting at its WORST end. Do not trim a swept axis to the
                                      # values a previous sweep liked.
                                      # IT MOVED FOUR KNOBS: max_watchlist 8 -> 16, concentration_cap
                                      # 0.25 -> 0.6, drop_unfunded_weeks 2 -> 4, risk_aversion 1.0 -> 10.0.
                                      # All four are BOOK knobs, so this promotion is a REBUILD, not a
                                      # re-curation. Per-knob evidence is in investor_profile.backtest.md.
                                      # PRIOR: 2026-09-09, over the promoted v34 curation.
                                      # 5,040 cells, BOOK knobs only, replay over the frozen journal.
                                      # AGAIN NO CONFIG CHANGE, and this time the marginals AGREE
                                      # with the profile rather than contradicting themselves as
                                      # v33's did: lookback_period_days peaks cleanly at 30 (the
                                      # profile's value) and drop_unfunded_weeks is monotone to 4
                                      # (also the profile's). max_watchlist 12-30 is a plateau and
                                      # concentration_cap 0.25 vs 0.4 is a noise width. The profile's
                                      # own cell is $293,265 at 31.4% cancelled, Sharpe 1.83 --
                                      # comfortably better than the grid median on all three.
                                      #
                                      # Previous note, 2026-09-08, over the promoted v33 curation.
                                      # 5,040 cells, BOOK knobs only, replay over the frozen journal.
                                      # NO CONFIG CHANGE came out of it: the best-region view and the
                                      # marginals DISAGREE on concentration_cap and drop_unfunded_weeks,
                                      # which marks those as interaction artifacts of one journal. Only
                                      # lookback_period_days shows a clean peak (30d, 48.5% cancelled
                                      # vs 58-73% either side) and the profile is already there.
                                      #
                                      # Previous note, 2026-09-07b, over the promoted v31 curation.
                                      # Re-swept because CANON_RUN advanced v30 -> v31; a sweep over a
                                      # superseded journal is the drift check_canon exists to catch.
                                      #
                                      # Previous note, 2026-09-07, over the promoted v30 curation.
                                      # 5,040 cells, ZERO LLM cost -- the curation is fixed and only
                                      # BOOK_KNOBS vary, so this is a replay grid, not a re-curation.
                                      # FIVE CURATIONS STALE before this: CANON_SWEEP still pointed at
                                      # the v25 sweep while CANON_RUN advanced v26 -> v30, which is the
                                      # drift check_canon exists to catch and did.
                                      # READ IT FOR THE SIZING KNOBS. The grid ranks on cancellation and
                                      # Sharpe and CANNOT see escalator capture -- the metric the current
                                      # work is actually optimising -- and on that metric our own replays
                                      # point the other way on max_watchlist (capture rises monotonically
                                      # with width; Sharpe-ranked grids prefer narrow). Do not read a
                                      # max_watchlist recommendation off this file without checking it
                                      # against escalator capture first.
                                      #
                                      # Previous note, 2026-09-01c, over the promoted v25 curation.
                                      # _gate2: the resolved-entry gate NARROWED to fire only when ALL of
                                      # a ticker's catalysts are resolved. _gate blocked on ANY resolved
                                      # row, which refused names that still had a live catalyst -- 24 of
                                      # 239 resolved (scan, ticker) pairs, 10%. _gate and _box are kept on
                                      # disk: same 5,040-cell grid, so all three are comparable.
                                      # _gate: adds the RESOLVED-ENTRY gate (firehose.backtest -- a name
                                      # whose catalyst the curator flagged resolved may KEEP a position
                                      # but may not OPEN one). Supersedes _box, kept on disk as the only
                                      # record of the book WITHOUT the gate. Same 5,040-cell grid, so the
                                      # two are directly comparable cell-for-cell.
                                      # _box: the FIRST sweep priced under box-bound sizing. The
                                      # predecessor (sweep_cbt_3yr_v25_vehgate.json, 2026-08-31) is kept
                                      # but is NOT comparable to this one -- it was computed while
                                      # min_trade_size renormalized concentration_cap away, so its cap
                                      # axis was inert. Its grid also differed: min_trade_size has been
                                      # REMOVED (pinned at 0.0) and four axes extended past edges the
                                      # old grid could not see over. 7,200 cells -> 5,040.
                                      # NAMED AFTER THE RUN IT SWEPT, ending a numbering that had drifted
                                      # into a trap: every sweep_vN on disk was swept over a DIFFERENT
                                      # run than its number suggests (sweep_v24 -> v22, sweep_v25 -> v23,
                                      # sweep_v26 -> v24), so `sweep_v25` and `cbt_3yr_v25_vehgate` look
                                      # like a pair and are TWO curations apart. The historical files are
                                      # left alone -- renaming them would break references already in
                                      # commit messages and profile comments, and would not stop the next
                                      # mistake. check_canon.py now VERIFIES the pairing from the sweep's
                                      # own `run` field, which does. Same 7,200-cell BOOK-knob grid.
                                      # PRIOR: v25 -> v26 on 2026-08-30, swept over
                                      # cbt_3yr_v24_wirelede curation. Same 7,200-cell BOOK-knob
                                      # grid; no CURATION knob varies, so SBT still describes the
                                      # one canonical curation.
                                      # PRIOR: v24 -> v25 on 2026-08-30, swept over
                                      # cbt_3yr_v23_silence curation (the silence cap + first-read
                                      # retire guard). Same 7,200-cell BOOK-knob grid as v24, which
                                      # swept cbt_3yr_v22_resolver and is kept on disk for the
                                      # before/after. The grid touches no CURATION knob, so SBT
                                      # still describes the one canonical curation.

# --------------------------------------------------------------------------- the knob partition
# UPSTREAM of the journal. Changing any of these invalidates an existing curation.
CURATION_KNOBS = frozenset({
    "model", "scout_model", "event_agent_model", "event_agent_effort",
    "org_tagger_model",                        # fills `orgs` at ingest -> changes which company
                                               # bundles exist -> changes what the scout is shown

    "picker_model", "picker_effort",           # backtest_gdelt uses the picker for the max_events cap
    "retrieval_engine", "discovery_filter",
    "news_cap", "news_lookback_days", "event_news_cap",
    "relevance_filter", "relevance_keep",
    "scout_articles_per_call", "max_article_chars",
    "min_bundle_articles",
    "max_events", "max_new_events",
    "curator_memory_weeks", "max_event_scans", "max_silent_scans",
    # exit_patience_scans / max_stale_scans WERE here until 2026-08-22 and were MISFILED. The test
    # is not what a knob sounds like, it is WHERE IT IS CALLED: both are read only by
    # firehose._watch_clocks, which is called only by firehose._stateful_watch, which is called only
    # inside firehose.backtest() -- the replay path. Proven empirically, not by reading: holding the
    # v18 journal FIXED and varying only those two produced books from $95,170 to $345,968. A
    # curation knob cannot do that, because the journal never changes. Misfiling them meant any
    # change to exit behaviour would have demanded an unnecessary ~$7 re-curation, and it hid two
    # free levers. They are BOOK_KNOBS; see the max_watchlist type specimen.
    "rebalance_period",                        # sets the scan cadence -> which weeks exist at all
    "specialty_allow", "mill_block",           # source filters applied as the corpus is read
})

# REPLAY-time only. Free to change; the page just needs a rebuild.
BOOK_KNOBS = frozenset({
    "max_watchlist", "max_agents",             # max_agents is the legacy alias (firehose.watchlist_cap)
    "exit_patience_scans", "max_stale_scans",  # firehose._watch_clocks, reached only from backtest()
    "concentration_cap", "risk_aversion",
    "lookback_period_days", "optimizer_lookback_days",
    "min_trade_size", "t_update_days", "risk_free_rate", "min_dollar_volume_usd", "exclude_young_reverse_split",
    "min_vol_pctile",                          # quiet floor, applied in backtest() beside the liquidity gate
    "initial_investment_usd",
    "always_include", "starter_watchlist", "defensive_ticker",
    "cull_rank", "cull_fresh_slots", "cull_fresh_scans",
    "drop_unfunded_weeks", "unfunded_reentry_on_new_catalyst", "unfunded_cooldown_weeks",
})

# FORWARD-only, and inert in the backtest (CLAUDE.md: gather_model is the live web-search stage).
# Excluded from the fingerprint so a forward retrieval change does not falsely invalidate a backtest
# curation -- but still classified, so it counts toward the completeness check below.
FORWARD_ONLY_KNOBS = frozenset({"gather_model"})


def check_interpreter() -> str:
    """Refuse to publish from an interpreter other than the project venv.

    CLAUDE.md specifies Python 3.12 + .venv, but nothing enforced it, and every dashboard built on
    2026-08-21 used the SYSTEM python (3.9.6, numpy 2.0.2, pandas 2.3.3) while every sweep ran under
    .venv (3.12.13, numpy 2.4.6, pandas 3.0.3). Same journal, same profile, same frozen price panel,
    byte-identical inputs -- and the mean-variance optimiser returned $54,960 on one stack and
    $40,498 on the other, a 36% gap. CBT and SBT therefore disagreed all day about the same book for
    a reason no amount of checking the DATA could ever have found; it took hashing every input,
    proving them equal, and only then looking at the interpreter.

    Numerical libraries are part of the provenance. Returns "" when fine, else the complaint.
    """
    import sys
    venv = REPO_ROOT / ".venv"
    if not venv.exists():
        return ""                       # no venv in this checkout; nothing to enforce against
    try:
        inside = Path(sys.prefix).resolve() == venv.resolve()
    except Exception:  # noqa: BLE001
        return ""
    if inside:
        return ""
    return (f"running under {sys.executable} (Python {sys.version.split()[0]}), not the project venv. "
            f"Numerical results DIFFER between stacks -- rebuild with .venv/bin/python.")


def check_partition_covers_profile() -> list[str]:
    """Every profile knob must be classified. Returns the unclassified ones.

    This is the part that keeps the mechanism honest a year from now. A knob nobody has classified is
    a knob whose blast radius nobody has decided, and the failure mode is silent: it would simply be
    left out of the fingerprint, so changing it would invalidate a curation without anything noticing.
    Builders call this and refuse to publish while it is non-empty, which turns "add a knob" into
    "decide where the knob acts" -- the question CLAUDE.md already says to ask before adding one.
    """
    import optimizer
    known = CURATION_KNOBS | BOOK_KNOBS | FORWARD_ONLY_KNOBS
    return sorted(set(optimizer._FINANCIAL_MODEL_DEFAULTS) - known)


# --------------------------------------------------------------------------- fingerprint
def check_beat_vocabulary(corpus: str | Path | None = None) -> list[str]:
    """Every beat reference must resolve to a LIVE beat. Returns the complaints.

    THE HOLE THIS CLOSES. retrieval_config.json is not in the curation fingerprint, and it cannot
    simply be added: it is an INGEST-time input, and the corpus -- not the config -- is what a replay
    reads. But a beat name is also a JOIN KEY. Every article carries the beat name it was retrieved
    under, and the curator later intersects those tags with the CONFIGURED names
    (agent._gem_beats() & queries) and looks them up in beat_parent. Rename a beat and the join
    silently matches nothing, which is indistinguishable from a beat that found nothing.

    Measured on 2026-08-24: renaming three beats stripped the gem-score bonus from 8,077 of 20,941
    gem-scored articles (38.6%) in any re-curation over the existing corpus, and dropped one beat out
    of its bundle parent -- while the fingerprint matched, the frozen journal was untouched, every
    published number was unchanged, and check_canon reported ALL CONSISTENT. Nothing failed loudly.

    Two invariants, both pure reads:
      1. every beat_parent key and value names a live beat;
      2. every distinct query tag in the corpus resolves, via gkg.canon_beat, to a live beat.
    Invariant 2 is the one with teeth -- it counts the ORPHANED ARTICLES, so a rename shows up as
    thousands of articles the curator can no longer score rather than as a config diff nobody reads.
    The fix for a legitimate rename is an entry in retrieval_config's `beat_renames`; the fix for a
    changed QUERY is to retire the old beat and add a new one, because it is not the same search."""
    out: list[str] = []
    try:
        import gkg as _g
    except Exception as e:  # noqa: BLE001 -- never let this check crash the report
        return [f"could not import gkg to check the beat vocabulary: {e}"]
    live = {b["query"] for b in _g.beats()}

    bp = _g.beat_parent()
    for k, v in bp.items():
        if _g.canon_beat(k) not in live:
            out.append(f"beat_parent KEY does not name a live beat: {k!r}")
        if _g.canon_beat(v) not in live:
            out.append(f"beat_parent VALUE does not name a live beat: {v!r} (parent of {k!r})")

    path = Path(corpus or (REPO_ROOT / CANON_CORPUS))
    pool = path if path.suffix == ".json" else path / "pool.json"
    if not pool.exists():
        return out
    try:
        d = json.loads(pool.read_text())
        arts = d.get("articles", d) if isinstance(d, dict) else d
    except Exception as e:  # noqa: BLE001
        return out + [f"could not read {pool} to check beat tags: {e}"]
    orphan_arts: dict = collections.Counter()
    for a in arts:
        if not isinstance(a, dict):
            continue
        for q in {_g.canon_beat(x) for x in (a.get("queries") or [])}:
            if q and q not in live:
                orphan_arts[q] += 1
    if orphan_arts:
        n = sum(orphan_arts.values())
        top = ", ".join(f"{q!r} ({c:,})" for q, c in orphan_arts.most_common(3))
        out.append(f"{len(orphan_arts)} corpus beat tag(s) resolve to NO live beat, orphaning "
                   f"{n:,} article-tags in {pool.parent.name}: {top}"
                   + ("" if len(orphan_arts) <= 3 else f", +{len(orphan_arts)-3} more")
                   + ". Add a `beat_renames` entry, or retire-and-add if the query itself changed.")
    return out


def _norm(v):
    """Normalise for comparison: lists become sorted tuples so ordering is not a false difference."""
    if isinstance(v, (list, tuple)):
        return sorted(str(x) for x in v)
    return v


def corpus_id(corpus: str | Path) -> dict:
    """Identify a corpus by path, article count AND TEXT STATE.

    THE TEXT STATE IS NOT DECORATION, it is the part that works. Path plus article count was the
    original fingerprint and it is blind to the change that matters most: `ingest.py --wayback`
    rewrites pool.json IN PLACE, filling `lede` from archive.org without adding or removing a single
    article. Measured on 2026-08-21, the backfill moved the canonical corpus from 56.9% to 75.1%
    archived lede while the count sat at 99,117 both sides. So data/cbt_3yr_v9 -- curated against the
    thinner text -- kept verifying as canonical for as long as anyone cared to ask, and the staleness
    was found by REASONING about it rather than by any check here. That is the failure this module
    exists to make impossible, reappearing one level down.

    `clean` / `live` / `none` are the three lede provenances FBT plots, so a corpus that has been
    re-enriched fingerprints differently from the one a curation actually read.
    """
    p = REPO_ROOT / corpus if not Path(corpus).is_absolute() else Path(corpus)
    rel = str(Path(corpus)) if not Path(corpus).is_absolute() else str(p.relative_to(REPO_ROOT))
    out = {"path": rel, "articles": None, "text": None}
    pool = p / "pool.json"
    if pool.exists():
        try:
            d = json.loads(pool.read_text())
            arts = d.get("articles", d) if isinstance(d, dict) else d
            out["articles"] = len(arts)
            clean = sum(1 for a in arts if a.get("lede"))
            live = sum(1 for a in arts if a.get("lede_live") and not a.get("lede"))
            out["text"] = {"clean": clean, "live": live, "none": len(arts) - clean - live}
        except Exception:  # noqa: BLE001 -- an unreadable pool is reported as unknown, not fatal
            pass
    return out


def corpus_id_from_articles(arts: list, label: str, **extra) -> dict:
    """corpus_id() for a corpus that is ASSEMBLED IN MEMORY and has no pool.json on disk.

    The bootstrap corpus is deliberately never materialised -- "a copy of two sources is a third
    thing that can drift from both" -- so corpus_id() found no pool.json, left `articles` and `text`
    as None, and stamped the path as "(gdelt-live)". A curation whose recorded inputs do not describe
    what produced it is precisely the drift this module exists to prevent, so the bootstrap
    identifies itself by the same three things a corpus dir does -- count and text state -- plus the
    span and the ingest stamp, which are what make a bootstrap corpus what it is.

    Measures TEXT STATE identically to corpus_id, so a bootstrap curated before a wayback backfill
    fingerprints differently from one curated after."""
    clean = sum(1 for a in arts if a.get("lede"))
    live = sum(1 for a in arts if a.get("lede_live") and not a.get("lede"))
    out = {"path": label, "articles": len(arts),
           "text": {"clean": clean, "live": live, "none": len(arts) - clean - live}}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


# INGEST-OWNED knobs that no longer live in the investor profile. They are still part of what a
# curation is a function of -- moving a parameter to its proper owner must not change what the
# fingerprint MEANS -- so curation_key reads their VALUES from retrieval_config.json instead.
# Because the values were carried over unchanged, the canonical fingerprint is unchanged too, which
# is the test that the move was a relocation and not an edit.
INGEST_OWNED = frozenset({"specialty_allow", "mill_block"})


def _ingest_knob(k: str):
    try:
        return json.loads((REPO_ROOT / "retrieval_config.json").read_text()).get(k)
    except Exception:  # noqa: BLE001
        return None


def curation_key(fm: dict, corpus: "str | Path | dict", arm: str = "fuller") -> dict:
    """The inputs a curation is a function of. Two runs with equal keys are the same experiment.

    `corpus` may be a path OR an already-built identity dict (corpus_id_from_articles), for a corpus
    that is assembled in memory and has no pool.json to point at."""
    knobs = {k: _norm(fm.get(k) if k not in INGEST_OWNED else _ingest_knob(k))
             for k in sorted(CURATION_KNOBS)}
    key = {"corpus": corpus if isinstance(corpus, dict) else corpus_id(corpus),
           "arm": arm, "knobs": knobs}
    key["hash"] = hashlib.sha256(
        json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return key


# --- CODE DRIFT: which scan-path files, and which drifts have been reviewed -------------------
# curator_code_id() has stamped these hashes on every run since it was written, and NOTHING EVER
# COMPARED THEM. On 2026-09-01 both live curations turned out to have drifted -- cbt_3yr_v25_vehgate
# on agent.py + firehose.py, cbs_v9 on all three -- while check_canon printed ALL CONSISTENT,
# because it only ever compared profile KNOBS. Caught by eye, late, which is the failure this whole
# module exists to stop.
#
# CURATION_CRITICAL is the pure scan path: a change here alters what the scout and the agents SEE or
# DECIDE, so the journal could not be reproduced. src/firehose.py is deliberately NOT in it -- that
# file holds scan() AND backtest(), so its hash alone cannot say which half moved, and every gate
# added recently (liquidity floor, death-spiral, resolved-entry) lives in the replay half.
CURATION_CRITICAL = ("src/agent.py", "src/org_tagger.py")

# Drift that has been LOOKED AT and accepted, per run, with the reason. Anything not listed here is
# reported as unreviewed. This is an explicit decision with a paper trail, not a suppression: the
# entry has to say WHY the journal is still trustworthy under the new code.
ACCEPTED_CODE_DRIFT: dict[str, dict[str, str]] = {
    "data/cbt_3yr_v28_exposure": {
        "src/agent.py":
            "No drift expected: v28 was curated at the commit that promoted it. This slot is kept so "
            "the NEXT serialisation-only edit has a home, and so nobody accepts a behavioural change "
            "here by habit -- read the diff before adding a note.",
    },
    "data/cbt_3yr_v27_catalyst": {
        "src/agent.py":
            "SERIALISATION ONLY, accepted 2026-09-06 (the day it was curated). The edits after this "
            "curation are jsonable_events() -- one place that knows `vehicles` and `names` are sets, "
            "replacing three writers of which two let a set fall through to json.dumps(default=str) "
            "and wrote its repr -- and as_set(), which recovers a set from whatever a round-trip "
            "left behind. as_set is reached from _filter_event only at FILTER_VERSION 3, which is "
            "OFF, and jsonable_events changes what is WRITTEN, never what is decided. On a run that "
            "starts from an empty journal, as this one did, the new `names` assignment is identical "
            "to the old setdefault().add(). So this journal IS what today's code produces.",
    },
    # src/agent.py's acceptance for cbt_3yr_v25_vehgate was REMOVED on 2026-09-05. It read "the
    # journal is what today's gate-free code would produce", and _filter_event's ticker test moved
    # to word boundaries that day, which changes the article slice every event-agent reads. That is
    # a real behaviour change: this curation genuinely CANNOT be reproduced by today's code, and
    # check_canon should say so until it is re-curated. The stamp records which filter version each
    # run used (`code.filter_version`), so a report still reconstructs the slice its agents saw.
    "data/cbt_3yr_v25_vehgate": {
        "_src/agent.py_retired_2026-09-05":
            "95a7a21 REVERTS the vehicle gate. This run curated at f9f0c47, where the gate was "
            "present but fired 0 TIMES across 37 scans (the _prev baseline bug), so the journal is "
            "what today's gate-free code would produce. Accepted 2026-09-01. ALSO carries the "
            "matcher/consolidation decision logging added 2026-09-05: both new call sites only READ "
            "state and hand it to picker_log, which is a no-op unless --decisions is passed, so no "
            "prompt, input or branch the curator takes is altered and this journal is still what "
            "today's code produces. Accepted 2026-09-05.",
        "src/firehose.py":
            "liquidity floor, death-spiral exclusion and the resolved-entry gate all act in "
            "backtest() at the funding gate -- replay-side. scan() is untouched. Accepted 2026-09-01.",
    },
}


def code_drift(run: str | Path) -> dict:
    """Compare a run's stamped scan-path code digest against the working tree.

    Returns {file: (stamped, now, state)} where state is one of match / accepted / DRIFTED, plus
    `critical` listing unreviewed drift in CURATION_CRITICAL files. Missing stamp -> {}."""
    run = str(run).rstrip("/")
    pf = REPO_ROOT / run / "provenance.json"
    if not pf.exists():
        return {}
    stamped = json.loads(pf.read_text()).get("code", {})
    if not stamped:
        return {}
    now = curator_code_id()
    ok = ACCEPTED_CODE_DRIFT.get(run, {})
    out, critical = {}, []
    for rel in sorted(k for k in stamped if k not in ("git", "dirty")):
        was, isnow = stamped[rel], now.get(rel)
        if was == isnow:
            state = "match"
        elif rel in ok:
            state = "accepted"
        else:
            state = "DRIFTED"
            if rel in CURATION_CRITICAL:
                critical.append(rel)
        out[rel] = (was, isnow, state)
    return {"files": out, "critical": critical, "accepted": ok,
            "git": (stamped.get("git"), now.get("git"))}


def curator_code_id() -> dict:
    """A digest of the CURATOR CODE that produced a curation -- the prompts and the gates.

    WHY THIS EXISTS. `curation_key` hashes profile knobs + corpus + arm, and that is the right key
    for "could this curation have been produced under this profile". But a curation is also a
    function of the SCOUT PROMPT and the code-side gates, and those live in src/agent.py, which the
    fingerprint cannot see. On 2026-08-22 the retired-ticker guard was rewritten from a categorical
    ban into a raised evidentiary bar -- a change that alters which tickers the scout may propose
    while leaving every profile knob untouched. The re-curation would have fingerprinted IDENTICALLY
    to the run it was meant to be compared against, and the only thing separating them would have
    been a directory name and somebody's memory. That is the exact failure mode CLAUDE.md's
    provenance section catalogues ("Every one was caught by eye, late").

    RECORDED, NOT HASHED. This is deliberately NOT folded into the fingerprint: doing so would make
    every existing stamp mismatch on the next whitespace edit to agent.py, and would conflate "ran
    under a different config" (which must block a publish) with "ran under different code" (which
    must be visible but is often intended). It is written alongside so a comparison of two runs can
    always answer "same code?" without anybody having to remember.
    """
    import subprocess
    out = {}
    # src/org_tagger.py ADDED 2026-08-28, and it should never have been missing. cbs_v7 and cbs_v8
    # are two curations of one corpus produced from COMPLETELY DIFFERENT tag sets -- a subject-only
    # prompt and an extraction prompt, 0.59 vs 1.46 bundle memberships per article -- and they
    # stamped IDENTICAL hashes AND identical code digests. Only a directory name separated them,
    # which is verbatim the failure this function's docstring was written about. The tagger's prompt
    # is a curator input exactly as the scout prompt is; it just lives in a file nobody listed here.
    for rel in ("src/agent.py", "src/firehose.py", "src/org_tagger.py"):
        f = REPO_ROOT / rel
        if f.exists():
            out[rel] = hashlib.md5(f.read_bytes()).hexdigest()[:12]
    # WHICH ADMISSION RULE THE EVENT-AGENTS' NEWS SLICE WAS BUILT WITH. A file hash says the code
    # changed; this says WHICH BEHAVIOUR ran, and src/curation_report.py needs that to reconstruct
    # the slice an agent actually read. A curation stamped before this key existed is version 1.
    try:
        import agent as _a
        out["filter_version"] = int(getattr(_a, "FILTER_VERSION", 1))
    except Exception:  # noqa: BLE001 -- provenance must import without the curator's dependencies
        pass
    try:
        out["git"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                                    capture_output=True, text=True, timeout=10).stdout.strip() or "?"
        out["dirty"] = bool(subprocess.run(["git", "status", "--porcelain", "src"], cwd=REPO_ROOT,
                                           capture_output=True, text=True, timeout=10).stdout.strip())
    except Exception:  # noqa: BLE001 -- provenance must never sink a curation
        out["git"] = "?"
    return out


def stamp(run_dir: str | Path, fm: dict, corpus: "str | Path | dict", arm: str = "fuller",
          argv: list[str] | None = None, note: str = "") -> Path:
    """Write run_dir/provenance.json. Called by the curation producer as the run is created."""
    run = REPO_ROOT / run_dir if not Path(run_dir).is_absolute() else Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    rec = curation_key(fm, corpus, arm)
    rec["code"] = curator_code_id()
    # BOOK KNOBS ARE RECORDED BUT NOT FINGERPRINTED. The hash covers CURATION_KNOBS only, and that is
    # correct -- changing a book knob needs a rebuild, not a re-curation, so it must not invalidate an
    # existing journal. But the stamp claims to record "the EFFECTIVE config", and it was silently
    # omitting half of it: max_stale_scans, set per-arm on the 2026-08-26 cadence sweep, was nowhere
    # in the file, so a later reader (and sweep_optimizer, which needs it to replay an arm under the
    # settings it was curated with) had to parse argv to find out. Separate key, so nothing that reads
    # `knobs` or the hash changes behaviour.
    rec["book_knobs"] = {k: _norm(fm.get(k)) for k in sorted(BOOK_KNOBS)}
    # WHAT ACTUALLY RAN, not what was typed. `knobs` records the ALIAS ("llama4"), which is the
    # right fingerprint input -- but it cannot answer "which model produced this curation" if the
    # alias table is ever re-pointed, and before 2026-08-28 an unknown alias silently resolved to
    # mimo, so a stamp could name a model that never ran. Outside `knobs`, so the hash is unchanged
    # and no existing curation is invalidated by recording it.
    try:
        import optimizer as _op
        (_s_id, _s_p), (_e_id, _e_p) = _op.resolve_stage_models(fm)
        rec["models_resolved"] = {"scout": f"{_s_p}:{_s_id}", "event_agent": f"{_e_p}:{_e_id}"}
        _g = _op.resolve_gather_model(fm)
        rec["models_resolved"]["gather"] = f"{_g[1]}:{_g[0]}"
        _o = _op.resolve_org_tagger_model(fm)
        rec["models_resolved"]["org_tagger"] = f"{_o[1]}:{_o[0]}" if _o else None
        if _o:
            # WHICH TAGS, not just which tagger. The cache filename carries the prompt hash, so
            # this is the one string that distinguishes two curations tagged by the same model
            # under different prompts.
            import org_tagger as _ot
            rec["models_resolved"]["org_tagger_cache"] = _ot.cache_path(
                fm.get("org_tagger_model")).name
    except Exception as _e:  # noqa: BLE001 -- provenance enrichment, never a gate on writing a stamp
        rec["models_resolved"] = {"error": f"{type(_e).__name__}: {_e}"}
    rec["argv"] = argv or []
    if note:
        rec["note"] = note
    f = run / "provenance.json"
    f.write_text(json.dumps(rec, indent=1, sort_keys=True, default=str))
    return f


def verify(run_dir: str | Path, fm: dict, corpus: str | Path | None = None,
           arm: str = "fuller") -> dict:
    """Compare a run's recorded inputs against the current profile + canonical corpus.

    Returns {ok, reason, diffs, unverifiable}. `unverifiable` names knobs the run never recorded --
    only ever non-empty for runs stamped before this module existed, and reported rather than
    assumed equal, because silently passing what was never checked is how the original bug survived.
    """
    run = REPO_ROOT / run_dir if not Path(run_dir).is_absolute() else Path(run_dir)
    want = curation_key(fm, corpus or CANON_CORPUS, arm)
    f = run / "provenance.json"
    if not f.exists():
        return {"ok": False, "reason": "unstamped", "diffs": [], "unverifiable": sorted(CURATION_KNOBS),
                "detail": f"{run.name} has no provenance.json, so what it ran under is unknown."}
    got = json.loads(f.read_text())
    diffs, unver, unver_corpus = [], [], False
    gc, wc = got.get("corpus") or {}, want["corpus"]
    if gc.get("path") != wc.get("path"):
        diffs.append(("corpus", gc.get("path"), wc.get("path")))
    elif gc.get("articles") != wc.get("articles"):
        diffs.append(("corpus articles", gc.get("articles"), wc.get("articles")))
    elif gc.get("text") != wc.get("text"):
        # A run stamped before text state was recorded reports None -- unverifiable, not a mismatch.
        if gc.get("text") is None:
            unver_corpus = True
        else:
            def _pct(t):
                n = sum(t.values()) or 1
                return f"{100*t['clean']/n:.1f}% clean / {100*t['live']/n:.1f}% live"
            diffs.append(("corpus TEXT STATE (re-enriched since this run)",
                          _pct(gc["text"]), _pct(wc["text"])))
    if got.get("arm") != want["arm"]:
        diffs.append(("lede arm", got.get("arm"), want["arm"]))
    gk = got.get("knobs") or {}
    for k in sorted(CURATION_KNOBS):
        if k not in gk:                      # never recorded -> reported, never assumed equal
            unver.append(k); continue
        if _norm(gk[k]) != want["knobs"][k]:
            diffs.append((k, gk[k], want["knobs"][k]))
    ok = not diffs
    if unver_corpus:
        unver.append("corpus text state (not recorded by this run)")
    return {"ok": ok, "reason": "" if ok else "curation-knob mismatch", "diffs": diffs,
            "unverifiable": unver, "hash_run": got.get("hash"), "hash_want": want["hash"]}


# --------------------------------------------------------------------------- the publish gate
PUBLISHED = {"docs/cbt.html", "docs/fbt.html", "docs/sbt.html", "docs/fbs.html"}


def is_published(out: str | Path) -> bool:
    p = Path(out)
    try:
        rel = p.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return str(rel) in PUBLISHED


def require_publishable(out: str | Path, page: str, problems: list[str]) -> None:
    """Refuse to overwrite a PUBLISHED page when its inputs are not the canonical ones.

    A hard stop, and only for docs/. Building an off-canon page is a normal, useful thing to do --
    an old curation, a bake-off arm, a news-reading sweep -- so anything written elsewhere is waved
    through with a warning. What is not normal is that page becoming the published one, which is the
    step every incident above had in common. Redirect with --out, or fix the inputs.
    """
    import sys
    if not problems:
        return
    body = "\n".join(f"     - {p}" for p in problems)
    if not is_published(out):
        print(f"  !! {page}: NOT the canonical book --\n{body}\n"
              f"     Writing to {out} anyway (not a published page).", file=sys.stderr)
        return
    raise SystemExit(
        f"\nREFUSING to publish {out}.\n"
        f"  {page} would describe a book that is not the canonical one:\n{body}\n\n"
        f"  The canonical book is: corpus {CANON_CORPUS}, curation {CANON_RUN},\n"
        f"  curation knobs as set in investor_profile.backtest.md.\n\n"
        f"  Either re-curate/rebuild so the inputs match, or send this build somewhere else:\n"
        f"      --out docs_preview/{Path(out).name}\n")
