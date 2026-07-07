# geo-herd-rider

**Author:** Joe Hahn  
**Email:** jmh.datasciences@gmail.com  
**Date:** 2026-Jun-23 <br>
**branch:** main

**Our model of the market.** Two groups move a price. The **smart money** (insiders and genuinely expert investors) have a real edge, they get to move first and they reap the greatest rewards. Then the **slow herd** arrives late to pile in and flatten the opportunity. We are neither. We have no inside information and no deep-investor edge, but we do have **data** (news, posts, reports, prediction markets) and **AI to manage and interpret that data**. Our play is to use that data's leading indicators to infer *where the smart money is already heading* and position us **between the smart money and the herd**. But because we must first discern where the smart money is headed, we inevitably arrive a bit late, but with the goal of arriving early enough to capture some of the move before the slow herd arrives and prices it away. And just as we ride in ahead of the herd, we also ride out as it shows up. Once the herd piles in and flattens the opportunity, that position has done its work and so we pivot off to the next event whose opportunity is still un-grazed.

**The core idea.** We don't reason out a causal chain to *find* the next winner — the financial press already publishes the answer, by ticker, naming the winner **early** (while it's still under the radar) and then repeatedly, more loudly, as the move builds. For example, the niche tanker-freight ETF (BWET) was named in print as a standout trade — *"the best-performing ETF of 2026 … flown under the radar"* — weeks before it tripled again. Our edge is simply to be **reading**: enter when the press names a ticker on a *live* thesis — a *thesis* being the specific catalyst driving the ticker (here, a war spiking tanker freight rates), *live* while that catalyst is unresolved — ride while the thesis holds, and exit when the catalyst resolves. AI is never used to predict *how big* a move will be — only which ticker or tickers to monitor, and whether its thesis still holds, while a non-AI mechanical optimizer sizes it.

**What this repo does.** Walking week by week, an LLM reads the news firehose, extracts the US-listed tickers the press explicitly **names** as thesis-driven movers, and curates a watchlist. A standard portfolio optimizer then decides **how much to hold of each name** — sizing them from their recent returns and volatility (using the same math that robo-advisors utilize). A position is **held while its driving catalyst is live** and **dropped when the driver behind the rise goes away** (ceasefire signed, chokepoint reopens). This whole solution is then backtested against a curated set of about a dozen historical thesis-driven events and the tickers (designated as **gems**) that they drove.

## How it works, at a glance

This solution is one short assembly line that loops weekly. It reads the news firehose to spot the **events** the press is flagging. Each event is driven by a **catalyst** — a discrete cause such as a war, an election, or a supply shock — and that catalyst causes specific tickers (which we designate as **gems** which are named explicitly by the journalists covering the event) to rise. A gem's **thesis** is just *why* it's rising — the claim that this catalyst is driving this ticker. A **scout** discovers the events and writes each gem's thesis; a **matcher** groups each week's named tickers into the events already in flight; and then an **event agent** **tracks each event over time** — an event can last weeks, months, or years, and the gem that best expresses it can *change* as it unfolds. We invest in a gem while its thesis is **live** (the catalyst still active/unresolved) and **exit** (drop the position) when the catalyst **resolves** (the war ends, the chokepoint reopens, the bill is signed) — and it is the **event agent that writes this exit call**, arguing each week whether the catalyst has already happened and dropping the position the moment it has. A **plain optimizer** (never the AI) then sizes whatever is held.

```mermaid
flowchart TD
    S["📰 Firehose<br/>gathers last week's pool of news articles<br/>via web search"]
    S --> SC

    subgraph CUR["🧠 AI Curator"]
      direction TB
      SC["🔍 Scout<br/>scans news to discover rising gems named by the press & writes each gem's thesis — the catalyst statement driving it"]
      MA["🧩 Matcher<br/>assigns each gem to an event, pre-existing or new"]
      AG["🟢/⚪ Event agent<br/>determines whether the catalyst is still alive or resolved/exited; scores its conviction; also picks which gem(s) best express the event"]
      SC --> MA --> AG
    end

    E["🎯 Watchlist<br/>gathers the top events' gems (ranked by conviction) for possible funding"]
    W["⚖️ Optimizer<br/>derives optimal portfolio distribution across watchlist, and<br/>parks idle capital in SPY or a gold hedge when no gem qualifies"]
    U["🧑 User<br/>adjusts portfolio at brokerage"]

    AG --> E
    AG -. "resolved catalysts are remembered so scout won't re-chase the hype" .-> SC
    E --> W --> U
    U -. "↻ back to Firehose, weekly" .-> X(( ))
    style X fill:none,stroke:none

    classDef bet fill:#fae3e0,stroke:#c0392b;
    class E bet
```

The whole assembly line **runs once per `rebalance_days` (default 7 = weekly)** and marches week by week across the era. Each pass re-reads the firehose, the event agents re-ask *"is this event's thesis still live, or has it resolved?"*, each agent then names the gem or gems that best express the event it is monitoring (and those gems can change over time), and then the optimizer rebalances the portfolio — **sizing is mechanical; the AI never sets the position sizes** (it only names tickers and the hold/exit call). An event isn't rediscovered from scratch each week: its agent remembers what it concluded last week (its prior-week note), and the position stays on (a "sticky hold") through quiet weeks — so each event is tracked continuously until its agent calls the exit. The exit is **resolution-driven, not crowd-driven** (we drop on the catalyst *resolving* — war ends, bill passes). Each week the agent argues the devil's-advocate case that the catalyst has *already happened* and then answers a forced binary — *has the catalyst resolved, yes or no?* — and a yes drops the position **immediately** (a resolved catalyst is definitive, so it exits at once rather than waiting out the sticky-hold), even if the coverage is still loud. Between these hard exits, each agent's **[conviction](#conviction-how-its-scored-and-how-it-decays) (1–10)** rides up on fresh milestones and **decays** on silence or a priced-in market — the soft signal that feeds the competitive cull and the always-on **[SPY/gold floors](#the-signal-and-its-jobs)** (both explained in detail below). And once a catalyst resolves it is **remembered**: the scout is told which catalysts have already resolved (over the last `curator_memory_weeks`, default 8) so it won't **re-open the same ticker on lingering hype** after the catalyst is done (a ceasefire already signed isn't a fresh catalyst).

The red highlighted box is where our advantage comes from: the press has already flagged a live catalyst (the **event**) and named the tickers that express it (its **gem(s)**), so we never have to predict the winner ourselves — this solution just reads the ticker named by the press and rides it while its thesis holds.

The sections below explain each box in greater detail — the [Firehose](#the-news-firehose-why-reading-beats-reasoning), the [AI Curator](#inside-the-curator-scout--event-agents) (its scout, matcher, event agents), and the [watchlist and optimizer](#the-signal-and-its-jobs).

### How the core pieces fit together

The above pipeline shows how the inputs and outputs are managed, while the following describes how an agent uses those inputs to monitor an event's datastream with an eye towards portfolio optimization. The main concept: one **event** (which is described by one **catalyst**) is managed by an **AI agent** (which is the durable unit that this solution tracks) that maintains a **basket** of same-catalyst tickers. An example could be a rearmament catalyst → Rheinmetall + BAE + Saab + Thales, keeping in mind that the basket of tickers can evolve over time until the event's catalyst is resolved. Multiple event-agents can run concurrently, with each proposing its basket, with the surviving agents' tickers gathered into the watchlist that the mechanical optimizer then sizes into a portfolio in a way that also tends to downweight the weaker tickers. Note that each agent's ticker-peers must share the *same* catalyst, which prevents a basket from acquiring unrelated gems.

- An **event** is flagged by the **scout**, and it is the real-world thing that is unfolding, the storyline that this solution is tracking (e.g. "Hormuz blockade").
- A **catalyst** is the event's spine that is documented by the **scout**. It is the continuous driver that runs through the entire event, preferably one that will ultimately resolve, with that resolution known as the **exit**. For the "Hormuz blockade" event the spine is *Iran's push to close the Strait of Hormuz*, and that event resolves with a *ceasefire*. An event can have multiple tickers associated with it, and they all share the same catalyst.
- **Milestones** are the vertebrae on the spine, they are the developments along the way (*protests in Iran → a US carrier group to the Med → strikes on Iran → the Hormuz closure itself*) that keeps the catalyst *live* and feeds the event's **conviction**. The **event-agent** tracks the milestones and its conviction week to week.
- **Conviction** is the event-agent's weekly rating (1–10) of how live and still-under-owned the catalyst is. Conviction is driven by the news. A fresh milestone can lift the agent's conviction, while silence, or news that the market has priced the move in, will cause conviction to decay over time.
- An **exit** can be declared by the agent that is monitoring the event, and it is where the milestone spine ends since the catalyst is resolved (e.g. the war ends, the bill is signed, the chokepoint reopens). Example: a ceasefire triggering an exit from the "Hormuz blockade" event. When the agent calls its own exit, its basket of tickers is no longer communicated to the live watchlist, which means the optimizer will not consider those exiting tickers at the next portfolio rebalance. However this solution still preserves a memory of the resolved catalyst to prevent the scout from re-chasing that event as its news winds down during subsequent weeks.
- A **thesis** is the statement that connects the event to one or more tickers (e.g. this catalyst is causing that ticker to rise), and the thesis is authored by the **scout**.
- A **gem** is an in-demand ticker that benefits from the event. The **scout** names the gem or gems, and the **matcher** merges every ticker that names the same catalyst into ONE event (so that upticks by RNMBY and RHMTY and LMT are regarded as a single defense event rather than three distinct events) and assigns those same-event gems to the event's **basket**, which can evolve as the event unfolds since the event is pinned to the catalyst and not to any particular tickers. Each **event-agent** proposes its basket, and the agents are ranked by conviction (against each other and versus the always-on SPY/gold floors detailed below). Since this solution manages at most `max_agents` (currently 7) concurrent event-agents, only the top-ranked survive (the lower-conviction agents fade away), and the surviving agents pour their tickers into the **watchlist**. A mathematical portfolio **optimizer** then assigns weights to those tickers in a way that tends to zero-out the weakest tickers.
- This solution provides two kinds of agents: (i) the **event-agents** described above that have fluid baskets; and (ii) the **always-on floor agents**, namely an ever-present **SPY floor agent** as well as the **configurable defensive agent** that by default favors GLD. These always-on single-ticker agents have a **fixed conviction** (default 5), which gives the optimizer the opportunity to fund safe harbors like SPY and/or GLD during moments when the event-agents are all low-conviction.

### Conviction: how it's scored, and how it decays

Every week each agent rates its event's **conviction** (1–10). A high conviction score means that the event has a **fresh, concrete, still-under-owned** catalyst that is delivering new milestones (a signed contract, a funding round, an escalation) that are driving towards resolution, while a low score means the event's driver is spent. Conviction is **remembered**: each agent sees its *own prior-week score* (a one-step memory carried in its journal note) and nudges it from there, so that the event's conviction trajectory is continuous and is not re-rolled from scratch each week.

Three forces move it — one hard, two soft:

- **Hard exit — the catalyst *resolves*.** The devil's-advocate binary (*has the catalyst already happened?*) — a *yes* drops the position **immediately** (war ends, bill signed, chokepoint reopens), regardless of conviction or how loud the coverage still is. This is the only *instant* exit.
- **Priced-in decay — the market caught up.** When coverage flips from *"early / under-owned"* to *"fully valued / consensus"* while the catalyst is still structurally live, the agent steps conviction down toward 3–4 (`catalyst_resolved` stays *false* — it's a fade, not a resolution).
- **Silence decay — the firehose goes quiet.** On **each weekly refresh** (every `rebalance_days`) with **no fresh coverage** of the event's trend, conviction steps **down by 1 from the prior score** — so sustained silence **compounds** week over week toward the cull floor, while a single fresh trend-story **resets it back up**. This rests on the firehose thesis: the press covers live trends *loudly*, so silence is itself evidence the thesis is fading.

The soft decays never sell directly — they lower a name's **standing** until the competitive cull acts on it (next section). A faded event's falling score eventually drops it below a stronger event, or below one of the always-on floor agents, and only then is it evicted.

## The news firehose: why reading beats reasoning

This solution doesn't screen all tickers to discover gems. The financial press already does that work and names the ticker, repeatedly, early while it's under the radar and then louder as the move builds. Here is BWET's news-history during the runup to the 2026 Iran war:

| Date | Outlet | Framing | from this date → peak |
|---|---|---|---|
| **Mar 4** | etf.com | *"best-performing ETF of 2026 … flown under the radar"* | **~3.2×** |
| Mar 20 | ETF.com | *"skyrocketing … still flying under the radar"* | ~2.3× |
| Apr 9 | Business Times | *"a 1,300% rally … an Iran war gauge"* | ~1.5× |
| Apr 25 | CNBC | *"up over 600% … better than oil or energy stocks"* | mainstream |

The progression in that last column, from "under the radar" to "everyone piling in", traces a gem moving from the smart money to the slow herd, and reading it early is the whole point. This solution enters the gems the press names on a live thesis and exits on thesis decay. The question "when to drop BWET?" answers itself: the position is dropped when the catalyst resolves (the Strait of Hormuz reopens, a ceasefire is signed) and freight rates roll over, not when the coverage merely gets crowded.

**Where the news comes from.** The firehose has two modes, and they must use different news sources, because reading historical news is a fundamentally different problem from reading this week's:

- **Live use (running the solution going forward, week to week).** The firehose is Anthropic web search, not a bulk download of every article published that week. Instead the curator answers a single question, *which tickers is the press naming as thesis-driven movers this week?*, by running its own web searches for exactly that, reading the headline and snippet of each result, and returning the tickers the press flags. From that one question Claude spawns its own follow-up searches (no fixed list; it adapts to whatever's live that week), capping every search to news dated today or earlier.

- **Backtest (replaying history to score this solution).** Here a normal web search is poison: searching old news today silently re-imports the future. Its date filters leak post-cutoff articles, its results are ranked by what later became famous, and it returns today's edited page. The goal is to assemble a representative news pool that is neither poisoned (no look-ahead) nor incomplete (it must include the early, under-the-radar phase, where the edge lives). That is why this solution uses GDELT, Wayback, and seeds:
  - **GDELT** is the only date-honest discovery index: it has server-enforced date bounds, and results ordered by date rather than relevance, so a gem's early article isn't boosted because it later mooned. GDELT is queried with a fixed list of 23 beats (superlatives, macro beats, the GICS sector sweep, and a small thesis-driven theme layer), never a ticker symbol:
    ```
    superlatives:  "best performing stock"  "biggest gainers"  "best performing etf"
    macro beats:   geopolitics  war  shipping  tariffs  "interest rates"
    sectors:       "technology stocks"  "energy stocks"  "financial stocks"
                   "healthcare stocks"  "industrial stocks"  "materials stocks"
                   "consumer stocks"  "utility stocks"  "real estate stocks"
                   "telecom stocks"
    themes:        cryptocurrency  "space stocks"  "robotics stocks"
                   "quantum stocks"  "nuclear stocks"
    ```
    The first three groups are gem-agnostic by construction, while the 10-beat sector sweep is derived from the 11 GICS sectors but with consumer staples and discretionary merged into a single consumer search term, plus market-wide superlatives, so that nothing is privileged. The themes group covers non-GICS asset classes and emerging-tech areas where gems emerge but the sector sweep is too coarse (quantum) or doesn't reach at all (crypto).
  - **But GDELT catches a gem late, not early.** It monitors a mostly-mainstream source list and surfaces a story only once it has propagated across those outlets, so the niche, low-readership early write-ups (the "(BWET) … under the radar" pieces) are under-indexed or absent, and a gem usually enters GDELT only after it has gone mainstream. GDELT also returns headlines only, and a headline names the theme, rarely the ticker.
  - So **Wayback** is used to patch the headline gap: for each URL GDELT did return, it fetches that page's as-of-date archived lede (which usually names the ticker). But it can't conjure URLs GDELT never returned, so GDELT plus Wayback is itself incomplete and largely misses the early trajectory.
  - **Seeds** fill exactly that hole — but honesty first about *what they are*: the seeds are **synthetic, hand-authored catalyst descriptions, not retrieved articles.** Each is a short title + snippet *written* to represent what an early under-the-radar write-up of a **real** event (China's rare-earth curbs, the Strait-of-Hormuz war risk, Germany's debt-brake vote) would have said — tagged with a plausible outlet name but **no real URL** (20 of the 21 seed entries have a blank URL) — and injected at the event's true date. The *events* are real; the *articles* are fabricated. And they are written in the very "still-early / under-owned / smart-money-first" framing the scout is built to reward. (Honest caveat, now sharper: because the seeds are hand-authored knowing which gems won **and** pre-framed to clear the gate, any seeded-backtest return is a hindsight **upper bound** — the seeds *grant* the early naming, they do not prove the live firehose would have retrieved it. Replacing them with genuine dated articles is on the todo list; the forward paper trade remains the only clean test.)

  **Why the forward-looking live use does not utilize GDELT + Wayback + seeds:** during live use, the firehose is Anthropic web search, which rides a general-purpose web index. It is far broader than GDELT's news monitors (it reaches the niche trade press), it returns the content snippet rather than just the headline, and it indexes fresh pages within days. So a just-published under-the-radar write-up is reachable as it appears, before it goes mainstream, with no seeding needed.

The ticker that motivates this project is **BWET**. In the 2026 Iran war it ran ~8× from its spark (Iran's late-December 2025 currency collapse and mass protests, which drew Trump's "armada" toward the Gulf) to its May peak, while SPY sat flat. The edge isn't knowing BWET will run 8×, it's reading the article that names it early enough to ride the back half (still ~3× from the first "under-the-radar" write-up). The May plateau is the three-tier model in one line: as the press turned toward peace, smart money rotated out while the slow herd kept backfilling.

![BWET vs SPY across the 2026 Iran war](assets/bwet_vs_spy.png)

## Live dashboard

[**A landing page of per-gem scans**](https://joehahn.github.io/geo-herd-rider/) — one dashboard per hidden-gem event ([BWET](https://joehahn.github.io/geo-herd-rider/bwet/), [MP](https://joehahn.github.io/geo-herd-rider/mp/), …), each showing value vs SPY, allocation over time, cumulative $-gain per holding, the **event agent-journal arc** (week-by-week hindsight / read / exit-state, for spotting anchoring or missed exits), a firehose log, retrieval-health, the curator **model** used, and an LLM-cost panel. Each portfolio is the **event-first agent** finding that gem in a **realistic, noisy GDELT news firehose**, with **Wayback** recovering the as-of-date ticker-naming ledes GDELT's headlines omit (look-ahead-clean) and **synthetic seeds** (hand-authored catalyst descriptions, not retrieved articles) standing in for the niche early pieces GDELT never indexes, injected at their true dates (the one retrieval shortcut; see Status). Each is a **hindsight upper bound** (seeded early naming + a model trained past the events), not a promise — the ceiling the mechanics can reach on clean inputs. A [**parameter-sweep dashboard**](https://joehahn.github.io/geo-herd-rider/sweeps/) leads with a **7-model LLM bake-off** (sum Final Curated value per curator model, ordered by cost) and then plots the sum (across all gems) of Final Curated Portfolio value vs sizing knobs (`concentration_cap`, `lookback_period_days`, `min_trade_size`, `risk_aversion`) against the flat Sum-SPY benchmark. Rebuild all with `python scripts/build_dashboard.py --all`.

## The signal, and its jobs

One source, three jobs — plus mechanical sizing:

- **Read** — *what's worth owning.* The news firehose — the tickers the press explicitly **names** as thesis-driven movers. The human never picks. (High-reach posts via `trump_feed.py` are a *roadmapped* second source — point-in-time-sliceable and wired into the legacy single-scan path, but not yet read by the event-first engine.)
- **Enter** — *the press names it on a live thesis.* The human never sets the trade; the curator just reports which tickers the press is naming as live movers.
- **Exit** — *is the thesis still live?* Hold while the driving catalyst is active; drop it when the press says it's resolving. Mainstream hype ("up 600%, everyone piling in") is **not thesis death** — only the catalyst resolving ends the hold.
- **Sizing** — mechanical (the ⚖️ **Optimizer** box). A standard mean-variance optimizer weights whatever watchlist results, tuned only by `investor_profile.md`. Two gates shape that watchlist first. A **conviction cull** (`max_agents`, currently 7) keeps only the top-N events by conviction — a *relative* cull, so a faded event is evicted only once a stronger one needs its slot. And **always-on floor agents** compete in that same ranking: an ever-present **SPY floor agent** (`spy_agent_conviction`, currently 5) and an optional **defensive agent** (`defensive_agent_conviction` + `defensive_ticker`, currently gold / GLD at 5) — a live event-agent must *out-rank* them to hold a slot, so when its conviction fades the capital rotates to SPY (rides the market) or the defensive asset (a hedge) instead of riding the gem down or sitting in cash (see [Conviction](#conviction-how-its-scored-and-how-it-decays)). *The gold floor is a drawdown-reducer whose backtest edge leans on gold's 2026 rally — a forward-test candidate, not a validated win, and off-able via the knob; it auto-skips gems of the same theme (GDX) to avoid double-counting.* The LLM never touches the numbers, and a schema guardrail (below) drops any magnitude it tries to emit.

Cadence is **one knob** (`rebalance_days`, default 7 = weekly): it sets both how often the firehose re-scans/re-optimizes *and* the trailing news window each scan reads — they're the same thing ("the news since the last scan"). A position persists across scans via a [sticky hold](agent_design.md#sticky-hold-hysteresis-current) (it exits on confirmed thesis death or prolonged silence), so coverage gaps don't churn it.

Scope is **US-listed stocks, ADRs, ETFs and ETNs** (e.g. BWET is an ETN) — so a foreign event (a war, an election) is captured via its US-listed proxy (e.g. YPF / ARGT for Argentina), which is both how the US press names it and what a retail brokerage can trade. A **live ticker resolver** handles the foreign case: when the scout names a company it can't confidently map to a US symbol (e.g. *Rheinmetall*), a scoped web-search call resolves it to the US-listed ADR (*Rheinmetall → RNMBY*) — look-ahead-safe because a name↔ticker mapping is a static fact and only the symbol is kept, so a real thesis is never dropped for lack of a ticker. A **code guard** still drops any *unresolved* foreign-exchange suffix (`CSL.AX`, `7203.T`, …), so a foreign listing can't slip into the book unmapped. **Options and futures are excluded on principle**: they'd require a strike / expiry / leverage call (i.e. *magnitude*), which the mechanical optimizer can't size and the no-magnitude guardrail forbids — commodity and rate exposure comes via ETFs/ETNs instead. (Admissibility rule in [`agent_design.md`](agent_design.md).)

## Inside the curator: scout → event agents

**Each week the engine discovers, then fans out.** Discovery poses a single question to the firehose — *which tickers is the press naming as thesis-driven movers this week?* — and surfaces a few candidate events (a scout call reads the whole week's coverage; you can't target-search an event you haven't found yet, so discovery must be broad). The engine then **fans out one agent per live event** — the new candidates plus every event already being held — each running in parallel: it pulls *its own* event's news, reads its full journal arc since entry, writes a hindsight self-critique, and makes the hold-or-exit call. The live events' current tickers become the watchlist the optimizer sizes. Next week, repeat.

The curator runs in one of two modes, both feeding the same optimizer — the two leftmost paths in the diagram:

- **Single scan** (the baseline) — one LLM call per week reads the whole firehose and emits the watchlist. Simple and cheap, but it tends to *tunnel on the loudest gem* and grab thematic noise.
- **Scout → event agents** (the current engine) — a **scout** reads the firehose to *discover* candidate events; then every held event gets **its own agent** that, each week:
  1. pulls news **targeted to that event** (its own catalyst — including resolution signals like a ceasefire);
  2. reads its **full journal arc since entry** (the catalyst it entered on, the vehicle's evolution, every prior read) and writes a weekly **`hindsight`** self-critique of last week's call *before* deciding — a Reflexion-style step to break repeat-the-same-mistake inertia;
  3. runs an explicit **exit-on-resolution** check against the whole arc — flip to exit the week the specific catalyst *resolves* (bill signed, approval granted, deal closed, chokepoint reopens), even if the stock is still rising and a broader theme lingers (crowding alone is never an exit);
  4. writes a new note: a short assessment, the **`thesis_live` / exit** call (the *only* thing that drives the hold/exit), and hot-linked sources.

  The live events become the watchlist; the optimizer sizes. The journal (`data/windows/agent_journals.json`) is the human-readable audit trail. Discovery is aggregate (you can't target-search an event you haven't found); only *monitoring* a held event uses its own targeted search — so it doesn't bias what we discover.

  **Multiple tickers per event — the peer-basket.** One catalyst usually has several credible vehicles (a European-rearmament shock lifts Rheinmetall *and* BAE / Saab / Thales; a rare-earth curb lifts several miners). Rather than force the agent to pre-pick the single winner and throw the rest away, the **scout** names the purest vehicle as the primary `ticker` **and** lists its direct same-catalyst peers in a `peers` field; those peers join that one event's vehicle set. The **event agent** then proposes the **whole basket**, and the **mechanical mean-variance optimizer sizes them and drops the weak ones** — the LLM never forecasts *which* peer wins (non-negotiable #1: it names candidates, sizing is mechanical). Hard guard: a peer must share the **same catalyst** — a name driven by a *different* catalyst is a separate event, never a peer — so a basket **structurally cannot drift** into an unrelated gem. (A naive "just propose more names" version *did* drift across catalysts and lost ~45% of return; anchoring peers inside one vetted catalyst is the fix.) A/B honesty: on RNMBY the basket formed cleanly (`RNMBY + BAE + Saab + Thales`) yet the *single* purest name still won (+251% vs +235%) — baskets help when the best vehicle is **ambiguous**, not when it's already the clear winner.

  **The catalyst gate — the hard filter, and its anticipation clause.** The scout names a ticker only when the press ties it to a *specific, datable, resolvable* catalyst — a war/chokepoint, an export ban or tariff, a named bill, a regulatory approval, a supply shock — and it **rejects pure theme/momentum** ("AI-power demand", "rising gold demand", "safe-haven flows"), which has no resolution and rides through every crash. That named resolution is exactly what later flips the position to EXIT. A recent, surgically-tested refinement admits one more class: **anticipation of a specific *dated future event*** — a national election, an FDA/PDUFA date, a scheduled vote, a court-ruling date — where the name is demonstrably rising *ahead* of the event and the **known date is the exit**. This lets the curator ride a run *into* a fixed event and sell the news: **MicroStrategy** rode Bitcoin *in anticipation of* the pro-crypto 2024 election — entering in September and exiting at the November vote — a trade the un-refined gate declined (it read the pre-election rise as momentum). The refinement stays surgical (validated in isolation: dated-election anticipation passes 6/6, a dateless Bitcoin-demand control 0/6), and it's a **validated prototype, not yet swept across all gems** (a shared prompt change, forward-test-gated). The clean flip side is **GDX** (gold miners): a ~3× run that was a diffuse macro theme with *no* discrete catalyst until a late gold-specific tariff, so the gate correctly declines it early and catches it only at the blow-off top — the deliberate **negative control** that proves the gate isn't merely permissive.

  *Implementation note — two agent engines:* **`--agent`** is ticker-keyed (the original: one journal per ticker); **`--event-first`** makes the **event** first-class (`agent.run_event_agent_scans`) — an LLM **matcher** groups this week's tickers into existing events (so RNMBY/RHMTY/LMT collapse into *one* defense event), and the event agent holds that event's **same-catalyst basket**, which can *evolve* week to week. The ticker-keyed engine stays as the A/B baseline. The 13-gem run showed why this matters — it fragmented single events across many tickers (RNMBY and RHMTY are the same company under two ADRs); event-first is the fix. See [`agent_design.md`](agent_design.md).

**Guardrail, machine-enforced.** This isn't a polite instruction the model could ignore — it's structural. Every LLM stage must return JSON matching a fixed Pydantic schema (`SCOUT_SCHEMA`, `EVENT_AGENT_SCHEMA`) whose fields are only `ticker`, `thesis`, `thesis_live`, `catalyst_resolved`, and the like — there is **no field for a price target, magnitude, weight, or position size**. The schema is set `extra='ignore'`, so if the model volunteers a number anyway ("buy 8% of BWET"), that field is *silently dropped* before anything downstream sees it. The LLM therefore *cannot* size even if it tries — it has nowhere to put a number; the mechanical optimizer sets every weight. The LLM picks composition and the *when-to-exit* call only. (It may *attribute* a figure to the press — "press cites ~600% YTD" — but never forecasts its own.)

## Models — one seam, pick by need

Every LLM call routes through a provider-agnostic seam (`src/llm.py`), so the same pipeline runs on Anthropic (Opus/Sonnet/Haiku) or any OpenRouter model via the **`model:` knob in `investor_profile.md`** (`optimizer.resolve_curator_model` maps `mimo|sonnet4|sonnet5|opus|llama4|deepseek|grok4` → provider + id), with structured-output JSON schemas keeping cheap models' output clean. Each scan stamps a `<scan>.meta.json` sidecar so every dashboard shows *which* model produced that book. A **7-model bake-off** (top plot on the sweeps page) re-scans each model's **6 gems** under the current prompts and re-scores them on shared price panels. The honest read: by **summed value DeepSeek tops it ($947k) — but entirely via *sprawl***, winning only on the two dirtiest gems (RNMBY's dial-up basket and GDX, the *negative control* it should decline), while **Sonnet-5 wins the four cleanly-caught gems** (SMR/BWET/MP/GEO+MSTR) and catches all six (DeepSeek and Llama-4 miss RNMBY's early anticipation). So the sum **rewards sprawl** under a permissive `max_agents`, and *precision*, not gross value, is the honest test. The **chosen curator is Sonnet-5**, picked for its **selectivity + early-anticipation catch** — few, high-conviction events, catching a gem while its catalyst is still forming (RNMBY, MSTR). The cheap open-weights (DeepSeek/Llama-4/MiMo, ~$2–4 per 6-gem scan vs Sonnet-5's ~$75) can top the *sum* by naming many names and funding a few big winners, but are leakier and less accurate on the clean gems. The live curator is the **`model:` knob** in `investor_profile.md` (currently Sonnet-5). Every call's cost is priced into `data/llm_costs.csv`.

## Harvesting the distribution, not one gem

Event-driven runs are heavy-tailed: BWET is a tail outlier, and below it sit progressively more numerous, smaller analogs. So the objective is to **harvest the distribution** — reliably ride the many medium-tier events — not to time one jackpot. The system is therefore measured against a locked multi-event test set (`data/fixtures/gems.json`, window 2022-09 → present, US-listed incl. ADRs/ETFs), balanced across **verticals** (AI, nuclear, crypto, healthcare, defense, shipping, EM-energy, materials, consumer, precious-metals) and **geopolitical types** (war ×2, election, trade-war):

> CVNA ~100× · PLTR 32× · NVDA 17× · SMR 16× · SMCI 14×↘ · MSTR 13× · HIMS 11× · RNMBY 8× · BWET ~8× · MP 6.5× · YPF 4.4× · GDX 3.5× · URA 3.2× — plus PTON (a slow-fizzle *negative control* for the exit engine).

This measures **recall** (how many gems the firehose catches) and the **exit engine** (does it cut a decaying thesis); **precision** (false positives — does it also grab hyped names that fizzle?) is measured separately by the realistic GDELT-noise run.

## Status

The firehose pipeline is built end-to-end and runs over historical news; below is what it scores so far and how those numbers should be read.

**Pipeline.** `firehose.py` runs the single-scan curator; `agent.py` runs the scout→event-agent curator (the current engine). Both hand the live watchlist to the reused mean-variance optimizer (`investor_profile.md` knobs); `scripts/run_harness.py` scores either against the gem set; the dashboard renders the portfolio. Every LLM call is priced into `data/llm_costs.csv`.

**Results so far.**
- *The solution today (6 gems, event-first engine, live config):* the curator reads a real GDELT+Wayback+seeds firehose and — gated to **discrete, datable, resolvable catalysts** plus the dated-anticipation refinement — names the gem the press flags while it's still early; the mechanical optimizer sizes it against always-on SPY/gold floors; a **resolution-driven exit** drops it when the catalyst resolves. All six gems run on **news-derived seeds** (not synthetic). Five are **caught and ridden to a clean exit** — **SMR** (ADVANCE Act → signing exit, the textbook arc), **BWET** (Hormuz → ceasefire), **MP** (rare-earth curbs + DoD deal), **GEO+MSTR** (2024 election, MSTR entering early via the anticipation gate), **RNMBY** (rearmament) — while **GDX is the deliberate negative control**, a diffuse theme the gate correctly declines until a late blow-off top. Per-gem returns run roughly **+40% to +900%** at the current config — all **hindsight upper bounds**, settled on the [sweeps dashboard](https://joehahn.github.io/geo-herd-rider/sweeps/).
- *Curator = Sonnet-5, confirmed by the 6-gem × 7-model bake-off:* DeepSeek tops the raw *sum* ($947k) but only by **sprawling** on the two dirtiest gems (RNMBY dial-up, GDX negative control); **Sonnet-5 wins the four cleanly-caught gems and catches all six**, so it stays the pick for selectivity + the early-anticipation catch. (The sum rewards sprawl — *precision* is the better test. Cost: ~$170 of the ~$180 run was the 3 Anthropic models — Sonnet-5 $75, Opus $66; the open-weights cost pennies.)
- *How we got here (the load-bearing findings):* a single-scan baseline caught the right *themes* but late and via the wrong *vehicle* (early-recall 0%, +42% vs SPY +98%); **seeding the early articles jumped recall 0% → 92%**, proving **retrieval, not reasoning, is the wall**; a per-event agent with a resolution-aware exit then rode BWET the full window (**+189–224%** vs +87%); and the **event-first engine** (one first-class event + a deterministic same-ticker guard) fixed the ticker-fragmentation the early 13-gem run exposed (RNMBY/RHMTY are one company; nuclear split across SMR/OKLO/CCJ).

**Two backtest surfaces.**
- `firehose.py --fixture` — a look-ahead-clean **mechanics** test against a fixed article set (perfect-retrieval assumption): given the early articles, the engine enters BWET on its first under-the-radar write-up and rides it while the Iran/Hormuz thesis is live (~+220% vs SPY ~+9%, BWET-only). An upper bound on the mechanics, not lift.
- `firehose.py --gdelt --seed <file>` — a **realistic** backtest: real date-honored GDELT headlines per week (`src/gdelt.py`) + the early niche pieces GDELT misses, seeded at their true dates. The curator must *find* the gem in genuine noise — the fast dev loop for hunting weaknesses (it drove a sticky-hold, selectivity/vehicle-selection, and ticker-validation hardening). **The [live dashboards](#live-dashboard) render this surface with `--enrich` Wayback ledes added** (event-first agent + GDELT/Wayback/seeds, English-filtered), one per gem (BWET, MP, GEO+MSTR, RNMBY, GDX, SMR) — each showing the catalyst-gated agent finding its gem in genuine noise, holding while the thesis is live, and exiting when the catalyst resolves. Retrieval is clean now (non-English **0%**). Sizing knobs (`concentration_cap`, `min_trade_size`, `lookback_period_days`, `risk_aversion`) were settled on the [parameter-sweep dashboard](https://joehahn.github.io/geo-herd-rider/sweeps/); the gem dashboards render the chosen defaults (cap 1.0 · lookback 14 · min_trade 0.0 · risk_aversion 0.1 · max_agents 7 · spy_agent 5 · gold-agent 5, model Sonnet-5) — a concentrated, low-risk-aversion tilt (a forward-test candidate, not validated). Returns are hindsight upper bounds.

**Why every number here is an upper bound.** No search tool gives true point-in-time retrieval — Anthropic's `before:` and Tavily's `end_date` leak post-cutoff articles, and the early "under-the-radar" pieces don't rank into a date-bounded pull (`src/search.py` enforces a hard client-side date bound, and even then they're missed). [**GDELT**](agent_design.md#retrieval-gdelt-and-seeds-current) (`src/gdelt.py`) *does* honor dates, but under-indexes niche trade press, so it picks a gem up only once mainstream piles in (late) — which is why the early pieces are seeded back at their true dates (a backtest shortcut, so seeded numbers are upper bounds). On top of that, the curator model was trained past these events. So every backtest figure above is a **ceiling**, reported as such — never read it as realized lift.

**Backtest roadmap (this README's scope).** We harden the engine on a widening historical slice, one rung at a time:
1. **BWET alone** — lock the mechanics on the single motivating gem (enter early, ride, exit on resolution).
2. **BWET + its two nearest-in-time gems** — confirm the scout/matcher keep separate events separate and the optimizer shares capital sanely across a handful of concurrent events.
3. **The full locked gem set** (`data/fixtures/gems.json`) — recall / precision / tail / exit across all verticals and geopolitical types.

Later phases extend beyond backtesting and are intentionally out of scope for this README; they'll be folded back in once we get there.

## Requirements

- **Python 3.12** with the `requirements.txt` packages (anthropic, openai, yfinance, pandas, numpy, scipy, pyyaml, requests, matplotlib, pydantic).
- **An Anthropic API key** (`ANTHROPIC_API_KEY`) is the only key the default pipeline needs. Running the curator bills your Anthropic account.
- **Optional keys:** `OPENROUTER_API_KEY` (only for the cheap open-weight models: mimo, llama4, deepseek, grok4) and `TAVILY_API_KEY` (date-bounded news search in `src/search.py`).
- **No key needed** for GDELT (the news pool), the Wayback Machine (as-of-date ledes), or yfinance (prices). The fixture/mechanics dashboard (`build_dashboard.py`) makes no LLM calls, so it needs no key at all.

You do **not** need Claude Code to run this. Claude Code is the tool the repo was developed with, not a runtime dependency; the solution calls the Anthropic API directly through the `anthropic` Python SDK (`src/llm.py`).

## Setup

```bash
git clone <this repo>
cd geo-herd-rider
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# The LLM curator calls the Anthropic API — bring your own key.
cp .env.example .env        # then edit .env, or just export the var:
export ANTHROPIC_API_KEY=sk-ant-...
# optional: OPENROUTER_API_KEY (cheap models), TAVILY_API_KEY (date-bounded news search)
```

`.env` is gitignored, so your key is never committed.

## Run it

**Mechanics test (fixture — look-ahead-clean, assumes perfect retrieval):**

```bash
python src/firehose.py --fixture data/fixtures/firehose_bwet.json --start 2026-02-06 --end 2026-06-18
python scripts/build_dashboard.py          # rebuild the $50K dashboard (no LLM cost)
```

**Scored multi-event harness (the dev loop — recall / precision / tail vs the gem set):**

```bash
# Single-scan baseline (Opus) over the gems.json window:
python scripts/run_harness.py

# Scout->event-agent variant, on the cheap dev model (MiMo via OpenRouter):
python scripts/run_harness.py --agent --provider openrouter --model xiaomi/mimo-v2.5-pro

# Add --seed data/fixtures/gems_seeds.json for the retrieval-perfect overlay (decomposition).
# GDELT pools cache after the first (throttled) fetch. All figures are hindsight upper bounds.
```

## Notes

Developed with [Claude Code](https://claude.com/claude-code). See [`CLAUDE.md`](CLAUDE.md) for the rules Claude follows in this repo, [`agent_design.md`](agent_design.md) for the event-agent design, [`TODO.md`](TODO.md) for backlog, [`scripts/`](scripts/README.md) for how to run each script, and [`prior-work/`](prior-work/) for the earlier experiments this design builds on.

## Disclaimer

Technical demo. Not financial advice. Historical performance is not predictive. Do not trade real money on this output.

## License

MIT.
