"""curation_report.py -- one markdown report per scan anchor, assembled from the journal + the replay.

WHAT IT IS FOR. The dashboards say how much the book made and how broad it was; they cannot say what
the curator was thinking on a given scan. These reports do: for each anchor they name the events that
held capital, the events that nearly did, the curator's own words about each one, and what each was
worth over the period that followed. The point is to spot-check the event handling, so the shape is a
short data dump rather than an essay, and it stops at the funded set plus the near misses.

WHY IT IS BUILT AT REPLAY TIME rather than by the curation, which is where portfolio-wave-rider
writes its equivalent. Whether an event was FUNDED is decided by the max_watchlist cull and the
optimizer, and both are BOOK_KNOBS: hold the journal fixed, change max_watchlist, and a different set
of events holds capital. A report written by backtest_gdelt.py would therefore describe a book the
page no longer shows, and the two would disagree with nothing to catch it, which is the drift
provenance.py exists to stop. Built alongside the page, the report and the page cannot disagree,
reformatting costs a rebuild rather than a re-curation, and the reports for a curation that predates
this module appear the first time its page is rebuilt.

NO LLM RUNS HERE. Every sentence in a report is either the curator's own text, already paid for at
curation time and stored in the journal, or a number computed from the frozen price panel. That is
what makes a report reproducible and free.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

# How much of the curator's own prose a report quotes per event. Long enough to carry the reasoning,
# short enough that eight events stay inside a page and a half. The journal holds the full text.
ASSESS_CHARS = 320
SOURCES_PER_EVENT = 6   # the journal itself stores at most 6, so this never truncates now
MILESTONES_PER_EVENT = 4
EXITS_LISTED = 8         # unfunded exits listed one line each; the rest fold into a count
HISTORY_LINES = 6        # journal lines shown inline; the rest fold into a count
NEAR_MISSES = 3          # unfunded live events shown, ranked by the cull's own trend score
FUNDED_EPS = 0.001       # a weight below this is not a position, it is optimizer dust


def event_gain(gain: dict, gain_series: dict, dates: list, claim: dict, elife: dict,
               lo: str | None = None, hi: str | None = None) -> collections.Counter:
    """P&L per EVENT, optionally restricted to the days in [lo, hi].

    Extracted verbatim from build_cbt_dashboard's event-attribution panel so the reports and that
    panel cannot use two different rules. The rule, and why it is not a one-line dict comprehension:
    63 of 145 tickers are claimed by more than one event, so `{v: eid for e in ev for v in vehicles}`
    hands a ticker's whole lifetime P&L to whichever event iterated last. Instead each ticker's DAILY
    gain increment is split equally among the events that (a) list the ticker and (b) were live that
    day. Equal, because nothing in the book says which of two live events owns a shared position, and
    an equal split at least sums to the true total.

    `lo`/`hi` are inclusive ISO dates; None means unbounded, so the default call reproduces the
    full-span panel exactly. A ticker with no daily series cannot be windowed at all, so it lands in
    "(unassigned)" at full span and contributes nothing to a window rather than leaking a whole
    lifetime's P&L into one period."""
    out: collections.Counter = collections.Counter()
    full = lo is None and hi is None
    for tk, g in gain.items():
        ser, cands = gain_series.get(tk), claim.get(tk, [])
        if not ser or not cands or not dates:
            if full:
                out["(unassigned)"] += float(g or 0)
            continue
        prev = 0.0
        for i, day in enumerate(dates):
            cum = float(ser[i] or 0) if i < len(ser) else prev
            inc, prev = cum - prev, cum
            if not inc or (lo and day < lo) or (hi and day > hi):
                continue
            # A start of None means the event has no journal entries: culled at birth, never ran an
            # agent, so it never held anything. Treating None as "live forever" lets such an event
            # collect a share of every ticker it listed across the whole backtest.
            live = [k for k in cands
                    if elife[k][0] is not None and elife[k][0] <= day
                    and (elife[k][1] is None or day <= elife[k][1])]
            if not live:
                out["(unassigned)"] += inc
            else:
                for k in live:
                    out[k] += inc / len(live)
    return out


def _pct(panel, tickers, lo: str, hi: str) -> float | None:
    """Equal-weight return of `tickers` over (lo, hi], from the frozen panel. None if unpriceable.

    This is how an UNFUNDED event is scored. It has no position, so it has no P&L; the only honest
    question is what its vehicles did over the period the book declined to hold them, which is
    exactly the comparison that says whether the cull is throwing away money."""
    rets = []
    for tk in tickers:
        try:
            col = panel[tk].loc[:hi].dropna()
            a = col.loc[:lo]
            if len(a) < 1 or len(col) < 2 or col.index[-1] <= a.index[-1]:
                continue
            p0, p1 = float(a.iloc[-1]), float(col.iloc[-1])
            if p0 > 0:
                rets.append(p1 / p0 - 1.0)
        except Exception:  # noqa: BLE001 -- an unpriced vehicle is skipped, never counted as flat
            continue
    return (sum(rets) / len(rets)) if rets else None


def agent_inputs(pool: list, e: dict, entry: dict, entries_before: list, cap: int):
    """The article slice ONE event-agent read at this scan, reconstructed from the archived pool.

    THIS IS THE AGENT'S INPUT, and it was the one thing the report could not show. `sources` is what
    the agent CITED, which is a fraction of what it was handed: at 2026-05-27 ev199 matched 259
    articles in the week's pool, read the top 20, and cited one. Without the slice a reader cannot
    tell "the agent weighed the evidence and passed" from "the evidence never reached it".

    Reconstruction is exact rather than approximate: agent._filter_event is deterministic (a
    structural score over title/snippet, recency breaking ties, no LLM and no fitted weights) and the
    week's whole pool is archived per anchor. The one input that has to be recovered is the event's
    VEHICLE SET AS IT STOOD at this scan -- the journal stores the lifetime union, and events grow
    vehicles as the matcher folds new proposals in. Using the union pulls a different slice: on ev230
    it recovered 1 of the 4 cited URLs, against 4 of 4 using the scan's own vehicles.

    The caller gets `ok`, which is that check made explicit: every cited URL should reappear in the
    reconstructed slice, and when it does not the report says the slice is approximate instead of
    presenting a guess as the record."""
    veh = sorted({str(v).upper() for x in (entries_before + [entry])
                  for v in (x.get("vehicles") or [])} or {str(v).upper()
                                                          for v in (e.get("vehicles") or [])})
    key = {"id": "x", "catalyst": e.get("catalyst") or "", "vehicles": veh}
    try:
        import agent as _agent
        matched = _agent._filter_event(pool, key, cap=0)
        got = _agent._filter_event(pool, key, cap=cap or 0)
    except Exception:  # noqa: BLE001 -- no archive, or a pool this code cannot read
        return [], 0, True
    cited = {u for u in (entry.get("sources") or []) if u}
    urls = {a.get("url") for a in got}
    return got, len(matched), (not cited or cited <= urls)


def _weights(r: dict) -> dict:
    """firehose.backtest stores an anchor's weights as "TSEM:0.40;USO:0.39", not a dict, so the
    string is the interface and parsing it here beats changing a shape three dashboards read.
    `latest` hands over a real dict, so both forms are accepted."""
    w = (r or {}).get("weights") or {}
    if isinstance(w, dict):
        return {str(t).upper(): float(v) for t, v in w.items()}
    return {p.split(":")[0].strip().upper(): float(p.split(":")[1])
            for p in str(w).split(";") if ":" in p}


def _how_it_ended(e: dict, entry: dict, cap: int) -> str:
    """Why this event is over, in the journal's own terms.

    The order matters: catalyst_resolved is the design's clean exit and takes precedence over an
    exit_case that merely names the trigger, and the age cap is only credited when nothing else
    fired, since an event can hit its final scan and resolve in the same breath."""
    if entry.get("catalyst_resolved"):
        return "catalyst RESOLVED" + (f": {entry['exit_case']}"
                                      if str(entry.get("exit_case") or "none") != "none" else "")
    if str(entry.get("exit_case") or "none") != "none":
        return f"exit condition met: {entry['exit_case']}"
    if cap and len(e.get("entries") or []) >= cap:
        return f"aged out at the {cap}-scan cap, catalyst never resolved"
    return "no stated reason"


def _entry_at(e: dict, date: str) -> dict | None:
    """The event's journal entry for this scan, or None if the agent did not re-judge it here."""
    for x in e.get("entries") or []:
        if str(x.get("date", ""))[:10] == date:
            return x
    return None


def _entry_asof(e: dict, date: str) -> dict | None:
    """The event's LAST judgement on or before `date`, which is what the book was acting on.

    "Live at this anchor" cannot mean "has an entry dated this anchor". A held name may go
    unmentioned for max_stale_scans scans and keep its position the whole time, so the strict test
    reported ZERO funded events at CBT's last anchor while the standing recommendation held LMT at
    40%, DJT at 31% and ETHA at 18% -- three events whose most recent judgement predated the scan.
    An event carries its last verdict forward until something changes it, so that verdict is the one
    to read."""
    out = None
    for x in e.get("entries") or []:
        if str(x.get("date", ""))[:10] <= date:
            out = x
        else:
            break
    return out


def _trim(s, n=ASSESS_CHARS) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def _money(x: float) -> str:
    if abs(x) < 0.5:                    # "-$0" is not a number, it is a rounding artefact
        return "$0"
    return f"{'-' if x < 0 else '+'}${abs(x):,.0f}"


def _event_block(eid: str, e: dict, entry: dict, *, weights: dict, per: float | None,
                 cum: float | None, date: str, note: str = "",
                 history: list | None = None, read: list | None = None,
                 matched: int = 0, cap: int = 0, exact: bool = True) -> list[str]:
    """One event, funded or not. Same shape either way, so the two sections read alike.

    The block is built to show EVERYTHING THE EVENT AGENT SAW, because a spot-check of a judgement
    that hides the judgement's inputs can only ever check it against itself. agent.event_agent_v2
    hands the model exactly four things, and all four are here: the FIXED catalyst it entered on,
    the KNOWN vehicles, its own journal digest since entry (`history`), and this scan's article
    slice (`read`). The fifth input, EVENT_AGENT_SYSTEM, is the same for every event and lives in
    src/agent.py, so it is named in the footer rather than reprinted 300 times."""
    # THE EVENT'S vehicle list is the UNION over its life; the ENTRY's is what the agent was
    # tracking at this scan, and the two differ on 229 of 993 CBT entries. Weights are matched
    # against the union (a funded ticker must never lose its percentage), but the breadth shown is
    # the scan's, because "11 vehicles" is a statement about what the curator was holding in mind
    # here, not about everything the event ever touched.
    vs = sorted(e.get("vehicles") or [])
    vs_now = sorted((entry or {}).get("vehicles") or vs)
    held = {t: weights.get(t, 0.0) for t in vs if weights.get(t, 0.0) > FUNDED_EPS}
    head = ", ".join(f"{t} {w * 100:.1f}%" for t, w in sorted(held.items(), key=lambda kv: -kv[1]))
    _rest = len(vs_now) - len(held)
    L = [f"### {eid} · {head or ', '.join(vs_now[:6]) or '(no vehicle)'}"
         + (f" · {len(vs_now)} vehicles, {len(held)} funded" if held and _rest > 0 else
            (f" · {len(vs_now)} vehicles" if len(vs_now) > 6 else ""))
         + (f" · {len(vs)} over its life" if len(vs) != len(vs_now) else "")]
    if note:
        L.append(f"*{note}*")
    money = []
    if per is not None:
        money.append(f"{_money(per)} this period")
    if cum is not None:
        money.append(f"{_money(cum)} since it opened")
    # SCANS SO FAR, not the event's lifetime total: len(entries) counts entries this anchor has not
    # reached yet, so a fresh event read as "scan 6" in the report for its first week.
    n_scans = sum(1 for x in (e.get("entries") or []) if str(x.get("date", ""))[:10] <= date)
    _ents = [x for x in (e.get("entries") or []) if str(x.get("date", ""))[:10] <= date]
    _since = str(_ents[0].get("date", ""))[:10] if _ents else date
    if (entry or {}).get("catalyst_resolved"):
        _state = f"RESOLVED {str(entry.get('date', ''))[:10]}"
    elif entry and not entry.get("thesis_live", True):
        _state = f"thesis dead {str(entry.get('date', ''))[:10]}"
    else:
        _state = "still pending"
    # THE CATALYST LINE READ AS A COMPLETED FACT. It is written as a bare present-tense phrase
    # ("FDA approves Gedatolisib") and names the thing the curator is WAITING FOR, which on ev157
    # never happened at all. Nothing in the block said so, and a reader had to infer the tense from
    # exit_advice. catalyst_resolved and thesis_live already carry the answer, so state it.
    L.append(f"**Catalyst** ({_state}, entered {_since})**.** {_trim(e.get('catalyst'), 200)}  ")
    L.append(f"*Scan {n_scans} of this event"
             + (f" · {' · '.join(money)}" if money else "") + "*")
    # A carried-forward verdict must not be labelled "this scan" -- that would put words in the
    # curator's mouth for a scan where it said nothing.
    _fresh = bool(entry) and str(entry.get("date", ""))[:10] == date
    if entry:
        if entry.get("assessment"):
            L.append(f"**{'This scan' if _fresh else 'Last read'}.** {_trim(entry['assessment'])}")
        ms = [m for m in (entry.get("milestones") or []) if str(m).strip()]
        if ms:
            L.append("**Milestones observed so far** (dated waypoints already seen, carried "
                     "forward each scan; not the catalyst)**.**")
            L += [f"- {_trim(m, 160)}" for m in ms[:MILESTONES_PER_EVENT]]
            if len(ms) > MILESTONES_PER_EVENT:
                L.append(f"- …and {len(ms) - MILESTONES_PER_EVENT} more in the journal")
        if str(entry.get("news_claims") or "").strip():
            L.append(f"**News claims.** {_trim(entry['news_claims'])}")
        if entry.get("exit_advice"):
            _ec = str(entry.get("exit_case") or "none")
            L.append(f"**Exits when.** {_trim(entry['exit_advice'], 200)} "
                     + (f"`exit_case: {_ec}`" if _ec != "none" else ""))
        if entry.get("catalyst_resolved"):
            L.append(f"**Catalyst resolved{' this scan' if _fresh else ''}.**")
        _all_src = [u for u in (entry.get("sources") or []) if u]
        srcs = _all_src[:SOURCES_PER_EVENT]
        if srcs:
            L.append("Sources:" + (f" ({len(_all_src)} cited this scan, first {len(srcs)} shown)"
                                   if len(_all_src) > len(srcs) else ""))
            L += [f"- {u}" for u in srcs]
    # INPUT 3: the journal digest, in the form agent._journal_digest builds it -- one line per scan,
    # oldest first, live flag and vehicles included. This is the "memory" the prompt tells the agent
    # to re-read and test its exit condition against, so a reader checking whether it did needs the
    # same lines in front of them. A long-running event's early scans fold away rather than pushing
    # the current one off the screen.
    if history:
        L.append(f"**Its journal going in** ({len(history)} earlier "
                 + ("scan" if len(history) == 1 else "scans") + ", as the agent re-read it).")
        _shown = history[-HISTORY_LINES:]
        if len(history) > HISTORY_LINES:
            L.append(f"- …{len(history) - HISTORY_LINES} earlier scans, in the journal")
        for h in _shown:
            _fl = "live" if h.get("thesis_live", True) else "DEAD"
            L.append(f"- {str(h.get('date', ''))[:10]} · {_fl} · "
                     f"veh {','.join(h.get('vehicles') or []) or '-'} · "
                     f"{_trim(h.get('assessment'), 150)}")
    # INPUT 4: the articles it was handed. Collapsed, because 20 lines per event over eight events
    # is the "many pages" this report exists not to be, and expanded on demand because the whole
    # point is that nothing it saw is hidden.
    if read:
        _cited = {u for u in ((entry or {}).get("sources") or []) if u}
        L += ["", f"<details><summary>What it read this scan: {len(read)} of {matched} matching "
                  f"articles" + (f", capped at {cap}" if cap and matched > cap else "")
                  + (" (slice reconstructed; some cited URLs are missing from it, so treat it as "
                     "approximate)" if not exact else "") + "</summary>", ""]
        for a in read:
            _tick = "✓ " if a.get("url") in _cited else ""
            _t = _trim(a.get("title") or "(untitled)", 110).replace("[", "(").replace("]", ")")
            L.append(f"- {_tick}{str(a.get('published_date', ''))[:10]} · "
                     f"{a.get('source', '')} · [{_t}]({a.get('url', '')})")
        L += ["", "</details>"]
    if entry and not _fresh:
        L.append(f"*Not re-judged this scan; the verdict above is from "
                 f"{str(entry.get('date', ''))[:10]} and stands until something changes it.*")
    if not entry:
        L.append("*No judgement on or before this scan.*")
    L.append("")
    return L


def write_reports(out_dir, *, arm: str, ev: dict, log: list, fm: dict, panel,
                  gain: dict, gain_series: dict, dates: list, capital: float,
                  run: str, fingerprint: str, corpus: str, profile: str,
                  page_title: str, archive_dir=None) -> list[dict]:
    """Write one report per anchor into `out_dir`; return a row per report for the page's link table.

    THE DIRECTORY IS CLEARED FIRST, for this arm only. A cadence change moves every anchor date, so
    yesterday's files would otherwise survive beside today's under different names and the page would
    link a set that no longer matches the book. Clearing means what is on disk is always exactly what
    the page links."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"*-{arm}-curation.md"):
        old.unlink()

    # Event lifespans and ticker claims, in the form event_gain wants.
    elife, claim = {}, collections.defaultdict(list)
    for k, e in ev.items():
        en = e.get("entries") or []
        elife[k] = (str(en[0].get("date"))[:10] if en and en[0].get("date") else None,
                    None if e.get("status") == "live" or not en
                    else str(en[-1].get("date"))[:10])
        for v in e.get("vehicles") or []:
            claim[v].append(k)
    anchors = [str(r.get("week"))[:10] for r in log]
    lookback = int(fm.get("lookback_period_days") or 30)
    ev_cap = int(fm.get("event_news_cap") or 0)
    archive_dir = Path(archive_dir) if archive_dir else None

    def _pool(date: str) -> list:
        """The whole week's article pool, as archived when the curation ran. ~3 MB per anchor, read
        once per report and dropped, rather than holding 37 of them at once."""
        f = archive_dir / f"{date}.json" if archive_dir else None
        if not f or not f.exists():
            return []
        try:
            return json.loads(f.read_text()).get("pool") or []
        except Exception:  # noqa: BLE001 -- an unreadable archive costs the slice, not the report
            return []
    rows = []

    for i, r in enumerate(log):
        d0 = str(r.get("week"))[:10]
        pool = _pool(d0)

        def _inputs(k):
            """(history, read, matched, exact) for one event at this anchor."""
            _e = ev[k]
            _ents = [x for x in (_e.get("entries") or []) if str(x.get("date", ""))[:10] <= d0]
            _cur = _ents[-1] if _ents else {}
            _hist = _ents[:-1]
            if not pool or not _cur:
                return _hist, [], 0, True
            _rd, _m, _ok = agent_inputs(pool, _e, _cur, _hist, ev_cap)
            return _hist, _rd, _m, _ok
        d1 = anchors[i + 1] if i + 1 < len(anchors) else None
        weights = _weights(r)
        # WHAT THE BOOK HELD GOING IN. An event that exits at this anchor is only expensive if the
        # book was funding it, so the previous anchor's weights decide which exits get a full block.
        held_before = _weights(log[i - 1]) if i else {}
        funded_tk = {t for t, w in weights.items() if w > FUNDED_EPS}
        # LIVE means the agent re-judged it at THIS anchor and said the thesis holds. Read off the
        # journal rather than the watchlist, because the watchlist is tickers and an event is the
        # unit the curator actually reasons about.
        # TWO DIFFERENT QUESTIONS, and conflating them broke this report twice in one afternoon.
        #   live_now  = what the curator JUDGED at this scan and called live. This is the page's
        #               events_live, and it is the pool the near misses are drawn from.
        #   funded    = what holds capital. The watchlist is STICKY at the ticker level, so a
        #               position can rest on an event the curator last re-read scans ago (LMT at 40%
        #               on a thesis last judged 2024-04-07). Requiring an entry dated this scan
        #               reported ZERO funded events at CBT's last anchor; accepting any live-looking
        #               last verdict revived long-dead events and reported 76 live where the page
        #               says 28. So: strict for the count, last-verdict for what is actually held.
        live = {k: (e, x) for k, e in ev.items()
                if (x := _entry_at(e, d0)) is not None and x.get("thesis_live", True)}
        # RETIREMENT BEATS THE LAST ENTRY. An event can be retired by the age or silence cap while
        # its final entry still reads thesis_live, so testing the entry alone let a retired event go
        # on "carrying" a position. elife's end date is the retirement, so an event retired on or
        # before this anchor is not carrying anything and its ticker falls to the orphan list, which
        # is the truthful place for capital sitting on a thesis that is over.
        carried = {k: (e, x) for k, e in ev.items()
                   if k not in live and (x := _entry_asof(e, d0)) is not None
                   and x.get("thesis_live", True)
                   and (elife[k][1] is None or elife[k][1] > d0)
                   and (set(e.get("vehicles") or []) & funded_tk)}
        per = (event_gain(gain, gain_series, dates, claim, elife, lo=d0, hi=d1)
               if d1 else collections.Counter())
        cum = event_gain(gain, gain_series, dates, claim, elife, hi=(d1 or d0))

        held, missed = [], []
        for k, (e, entry) in live.items():
            (held if (set(e.get("vehicles") or []) & funded_tk) else missed).append(k)
        # ONE BLOCK PER FUNDED TICKER, not one per claiming event: LMT is claimed by three events and
        # printing it three times at 40% reads as three positions. The claimant with the most recent
        # verdict is the one the curator is actually tracking, so it leads and the others are named.
        # Its money line is its OWN attributed share (event_gain splits a shared ticker equally), not
        # the ticker's total.
        _all_f = {**live, **carried}
        _by_tk, _co = {}, collections.defaultdict(list)
        for tk in sorted(funded_tk):
            cl = sorted((k for k in _all_f if tk in (ev[k].get("vehicles") or [])),
                        key=lambda k: str(_all_f[k][1].get("date", "")), reverse=True)
            if cl:
                _by_tk[tk] = cl[0]
                _co[cl[0]] = [c for c in cl[1:]]
        held = list(dict.fromkeys(_by_tk.values()))
        missed = [k for k in missed if k not in held]
        # THE NEAR MISSES, ranked by the cull's OWN score so the report shows the events that came
        # closest rather than an arbitrary three. firehose._trend_rank is what _ranked_cull calls.
        try:
            from firehose import _trend_rank
            score = _trend_rank(sorted({v for k in missed
                                        for v in (ev[k].get("vehicles") or [])}),
                                panel, d0, lookback)
        except Exception:  # noqa: BLE001 -- an unpriceable week still gets a report, just unranked
            score = {}

        def _ev_score(k):
            vs = [score.get(v, float("-inf")) for v in (ev[k].get("vehicles") or [])]
            return max(vs) if vs else float("-inf")

        _wl = {t.strip().upper() for t in str(r.get("watchlist") or "").split(";") if t.strip()}

        def _on_wl(k):
            return bool({str(v).upper() for v in (ev[k].get("vehicles") or [])} & _wl)

        # AN EVENT THE OPTIMIZER ZEROED IS A NEARER MISS THAN ONE THE CULL NEVER PASSED: it survived
        # max_watchlist and then lost on the maths alone, which is the decision most worth checking.
        # Within each group the cull's own trend score orders them.
        missed.sort(key=lambda k: (_on_wl(k), _ev_score(k)), reverse=True)
        held.sort(key=lambda k: -max((weights.get(v, 0.0)
                                      for v in (ev[k].get("vehicles") or [])), default=0.0))
        _stale = [k for k in held if k in carried]
        # POSITIONS WITH NO LIVE THESIS BEHIND THEM. The watchlist is sticky and exit_patience_scans
        # gives a dying thesis two scans of grace, so capital can sit in a ticker whose event the
        # curator has already called dead: at CBT's last anchor ETHA holds 17.8% on ev144, retired.
        # That is a curator-quality fact, not a rendering edge case, so it gets a line rather than
        # being dropped for failing the live test.
        _anchor_tk = {str(t).upper() for t in (fm.get("always_include") or [])}
        orphans = []
        for tk in sorted(funded_tk - _anchor_tk):
            if any(tk in (ev[k].get("vehicles") or []) for k in held):
                continue
            cl = sorted((k for k in ev if tk in (ev[k].get("vehicles") or [])
                         and _entry_asof(ev[k], d0) is not None),
                        key=lambda k: str(_entry_asof(ev[k], d0).get("date", "")), reverse=True)
            last = _entry_asof(ev[cl[0]], d0) if cl else None
            orphans.append((tk, weights.get(tk, 0.0), cl[0] if cl else "(no event)",
                            str((last or {}).get("date", ""))[:10],
                            str((last or {}).get("exit_case") or "")))

        # Opened and exited AT THIS ANCHOR, the two lines that say what actually changed.
        opened = [k for k, e in ev.items()
                  if (en := e.get("entries") or []) and str(en[0].get("date"))[:10] == d0]
        exited = [k for k, e in ev.items()
                  if e.get("status") != "live" and (en := e.get("entries") or [])
                  and str(en[-1].get("date"))[:10] == d0]

        ret = r.get("week_return")
        # ONE FACT PER LINE. Both header lines had grown into "·"-joined runs of six or seven
        # facts that wrapped wherever the window happened to end, so nothing could be found by eye.
        # A markdown list is the only construction that breaks reliably on GitHub, since a bare
        # newline inside a paragraph does not.
        _spy = _pct(panel, ["SPY"], d0, d1) if d1 else None
        _same = sorted(set(opened) & set(exited))     # opened and closed inside one scan

        def _ids(x):
            return ", ".join(sorted(x)) if len(x) <= 6 else f"{len(x)}"

        L = [f"# {page_title} curation — {d0}", "",
             f"- **Run.** `{run}` · fingerprint `{fingerprint}`",
             f"- **Corpus.** `{corpus}`",
             f"- **Replayed under.** `{profile}`",
             "- **Where every line below comes from.** The curation journal and this build's frozen "
             "price panel. No LLM ran to write this report.",
             "",
             f"- **Period.** {d0} → {d1 or 'open (no forward period yet)'}"
             + (f" · book {ret * 100:+.1f}%" if isinstance(ret, (int, float)) else "")
             + (f" · SPY {_spy * 100:+.1f}%" if _spy is not None else ""),
             f"- **Events.** {len(live)} live this scan · {len(held)} funded"
             + (f", {len(_stale)} of them not re-read this scan" if _stale else "")
             + f" · {len(missed)} unfunded"]
        if orphans:
            L.append(f"- **Held with no live thesis.** {len(orphans)}")
        if opened:
            L.append(f"- **Opened.** {_ids(opened)}")
        if exited:
            L.append(f"- **Exited.** {_ids(exited)}"
                     + (f", of which {len(_same)} opened and exited in this same scan"
                        if _same else ""))
        _anchors_held = sorted(t for t in funded_tk
                               if t in {str(x).upper() for x in (fm.get("always_include") or [])})
        if _anchors_held:
            L.append("- **Anchors.** "
                     + ", ".join(f"{t} {weights[t] * 100:.1f}%" for t in _anchors_held)
                     + " (always_include, outside the watchlist and not an event)")
        L += ["", "## Funded", ""]
        if held:
            for k in held:
                _h, _rd, _m, _ok = _inputs(k)
                L += _event_block(k, ev[k], _all_f[k][1], weights=weights, date=d0,
                                  per=per.get(k) if d1 else None, cum=cum.get(k),
                                  history=_h, read=_rd, matched=_m, cap=ev_cap, exact=_ok,
                                  note=(f"vehicle also claimed by {', '.join(_co[k])}"
                                        if _co.get(k) else ""))
        else:
            L += ["*No funded position rests on a thesis that was live at this anchor. What the "
                  "book holds is listed below.*", ""]

        # THE EXIT IS THE MOST INFORMATIVE MOMENT AN EVENT HAS, and until now it was invisible: an
        # event that resolves sets thesis_live false in the same entry, so it drops out of `live`,
        # out of `carried`, and appeared nowhere but as an id on the Exited line. ev199's clean
        # resolution -- the best single piece of evidence that the machinery works -- could not be
        # read in any report.
        #
        # An exit the book was FUNDING gets the full block, inputs included, because that is a
        # judgement that moved money and is the one most worth checking. The rest are one line each:
        # a scan can retire thirteen events, eight of them opened and closed the same day, and
        # thirteen full blocks would bury the two that mattered.
        exited_now = []
        for k, e in ev.items():
            _en = [x for x in (e.get("entries") or []) if str(x.get("date", ""))[:10] <= d0]
            if not _en or str(_en[-1].get("date", ""))[:10] != d0:
                continue
            if _en[-1].get("thesis_live", True) and e.get("status") == "live":
                continue
            if _en[-1].get("thesis_live", True) and len(_en) < len(e.get("entries") or []):
                continue          # still live here; it ends at some later anchor
            exited_now.append((k, _en[-1]))
        _ec = int(fm.get("max_event_scans") or 0)
        _paid = [(k, x) for k, x in exited_now
                 if set(ev[k].get("vehicles") or []) & set(held_before)]
        _rest = [(k, x) for k, x in exited_now if (k, x) not in _paid]
        if exited_now:
            L += ["## Exited at this scan", ""]
            for k, x in _paid:
                _h, _rd, _m, _ok = _inputs(k)
                L += _event_block(k, ev[k], x, weights=held_before, date=d0, per=None,
                                  cum=cum.get(k), history=_h, read=_rd, matched=_m, cap=ev_cap,
                                  exact=_ok, note=f"held going in · {_how_it_ended(ev[k], x, _ec)}")
            for k, x in _rest[:EXITS_LISTED]:
                _n = len([y for y in (ev[k].get("entries") or [])
                          if str(y.get("date", ""))[:10] <= d0])
                _g = cum.get(k)
                L.append(f"- **{k}** · {', '.join(sorted(ev[k].get('vehicles') or [])[:4])} · "
                         f"{_n} scan{'s' if _n != 1 else ''} · {_how_it_ended(ev[k], x, _ec)}"
                         + (f" · {_money(_g)} lifetime" if _g else " · never funded"))
            if len(_rest) > EXITS_LISTED:
                L.append(f"- …and {len(_rest) - EXITS_LISTED} more, none of them funded")
            L.append("")

        if orphans:
            L += ["## Held with no live thesis", ""]
            for tk, w, k, dt, ec in orphans:
                L.append(f"- **{tk} {w * 100:.1f}%** · {k} last said live on {dt or '(never)'}"
                         + (f", exit_case `{ec}`" if ec and ec != "none" else "")
                         + ". The watchlist is sticky, so the position outlived the thesis.")
            L.append("")

        L += ["## Not funded, the three that came closest", ""]
        for k in missed[:NEAR_MISSES]:
            vs = sorted(ev[k].get("vehicles") or [])
            why = ("survived the cull, then the optimizer gave it no weight" if _on_wl(k)
                   else f"culled by max_watchlist {int(fm.get('max_watchlist') or 0)}")
            rp = _pct(panel, vs, d0, d1) if d1 else None
            note = why + (f" · its vehicles ran {rp * 100:+.1f}% over the period" if rp is not None
                          else "")
            _h, _rd, _m, _ok = _inputs(k)
            L += _event_block(k, ev[k], live[k][1], weights={}, per=None, cum=None,
                              date=d0, note=note,
                              history=_h, read=_rd, matched=_m, cap=ev_cap, exact=_ok)
        if not missed:
            L += ["*Every live event was funded.*", ""]

        # THE VERDICT LINE, and the reason the report exists. If the events the book declined to hold
        # keep beating the ones it held, the cull is the thing to fix, and no amount of reading
        # assessments would surface that as fast as one comparison per scan.
        if d1:
            fr = _pct(panel, sorted(funded_tk - _anchor_tk), d0, d1)
            mr = _pct(panel, sorted({v for k in missed for v in (ev[k].get("vehicles") or [])}),
                      d0, d1)
            L += ["## Funded against passed over", "",
                  f"- funded vehicles, equal weight: **{fr * 100:+.1f}%**" if fr is not None
                  else "- funded vehicles: unpriced",
                  f"- unfunded live events, equal weight: **{mr * 100:+.1f}%** "
                  f"({len(missed)} events)" if mr is not None
                  else "- unfunded live events: unpriced", ""]

        # A REPORT IS READ ON GITHUB by someone who did not build it, and four of its fields have
        # meanings that are not guessable from their names.
        L += ["---", "",
              "*How to read one event.* **Catalyst** is the specific, datable thing the curator "
              "is WAITING FOR, written in the present tense and usually not yet true; the header "
              "says whether it is still pending or has resolved, and the event lives or dies by "
              "it. **Milestones** are dated waypoints already observed on the way to it, which is "
              "what lets an agent notice that a due date has passed. **Scan N** counts how "
              "many times an agent has re-read that thesis, so a high N against an unchanged "
              "assessment is a thesis nothing is confirming. The money is this event's share of "
              "realised P&L: a ticker claimed by several live events splits its gain equally "
              "between them. **Exits when** is the condition the curator committed to at the "
              "outset, so an event whose exit cannot be dated is a theme wearing an event's "
              "clothes, and only the `max_event_scans` age cap will ever end it.", ""]
        f = out_dir / f"{d0}-{arm}-curation.md"
        f.write_text("\n".join(L) + "\n")
        rows.append({"date": d0, "file": f.name, "live": len(live), "funded": len(held),
                     "ret": ret if isinstance(ret, (int, float)) else None,
                     "opened": len(opened), "exited": len(exited)})
    return rows
