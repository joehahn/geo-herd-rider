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
import re as _re
from pathlib import Path

# How much of the curator's own prose a report quotes per event. Long enough to carry the reasoning,
# short enough that eight events stay inside a page and a half. The journal holds the full text.
ASSESS_CHARS = 320
SOURCES_PER_EVENT = 6   # the journal itself stores at most 6, so this never truncates now
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
                 cum: float | None, date: str, note: str = "", rets: dict | None = None,
                 history: list | None = None, read: list | None = None,
                 matched: int = 0, cap: int = 0, exact: bool = True) -> list[str]:
    """One event, funded or not. Same shape either way, so the two sections read alike.

    The block shows EVERYTHING THE EVENT AGENT SAW, because a spot-check of a judgement that hides
    the judgement's inputs can only ever check it against itself. agent.event_agent_v2 hands the
    model exactly four things and all four are here: the FIXED catalyst it entered on, the KNOWN
    vehicles, its own journal digest since entry (`history`), and this scan's article slice
    (`read`). The fifth input, EVENT_AGENT_SYSTEM, is identical for every event and lives in
    src/agent.py, so the footer names it rather than reprinting it 300 times.

    ORDER IS THE STORY: what the event is, when it was entered and whether it is over, what would
    end it, what the book put behind it and what that earned, then the evidence trail oldest first.
    """
    # THE EVENT'S `vehicles` FIELD IS ITS CURRENT SET, NOT A UNION -- event_agent_v2 rewrites it
    # from each scan's output, so an event can NARROW: ev239 opened tracking ASST, CLSK and MSTR and
    # its record now holds ASST alone. 15 of 252 CBT events have an entry listing more vehicles than
    # the event does. Reading the field as a lifetime union got both jobs wrong: it printed
    # "1 over the event's life" beneath three tickers, and it would have dropped the percentage off
    # a funded ticker the event had since stopped tracking. The union is computed here instead.
    vs_now = sorted((entry or {}).get("vehicles") or e.get("vehicles") or [])
    vs = sorted({str(v).upper() for x in (e.get("entries") or [])
                 if str(x.get("date", ""))[:10] <= date
                 for v in (x.get("vehicles") or [])}
                | {str(v).upper() for v in (e.get("vehicles") or [])})
    held = {t: weights.get(t, 0.0) for t in vs if weights.get(t, 0.0) > FUNDED_EPS}
    rets = rets or {}
    _ents = [x for x in (e.get("entries") or []) if str(x.get("date", ""))[:10] <= date]
    _since = str(_ents[0].get("date", ""))[:10] if _ents else date
    # SCANS SO FAR, not the event's lifetime total: len(entries) counts entries this anchor has not
    # reached yet, so a fresh event read as "scan 6" in the report for its first week.
    n_scans = len(_ents)
    _fresh = bool(entry) and str(entry.get("date", ""))[:10] == date
    _ec = str((entry or {}).get("exit_case") or "none")

    # THE CATALYST IS THE EVENT'S IDENTITY. There is no title field in the journal, so the catalyst
    # is the closest thing to a name and heads the block; the line below repeats it with its entry
    # date, because that pairing is the one a reader checks first.
    # THE SCAN'S DATE IN THE HEADING. A block is a snapshot of one event at one anchor, and the
    # reports are linked individually, so one opened on its own gave no date until the Catalyst line.
    L = [f"### {eid} · {date} · {_trim(e.get('catalyst'), 110)}"
         + (f" · *{note}*" if note else "")]
    L.append(f"**Catalyst** {_since} · {_trim(e.get('catalyst'), 200)}")

    # WHAT THE BOOK PUT BEHIND IT, and what that earned. The weight says how much conviction the
    # optimizer expressed; the per-ticker return says whether it was repaid; the event's dollars are
    # its share of realised P&L (a ticker claimed by several live events splits equally).
    def _tk(t):
        r = rets.get(t)
        return f"{t} {held[t] * 100:.1f}%" + (f" \u2192 {r * 100:+.1f}%" if r is not None else "")

    _unfunded = [t for t in vs_now if t not in held]
    money = ([f"{_money(per)} this period"] if per is not None else []) + \
            ([f"{_money(cum)} since it opened"] if cum is not None else [])
    # ONE LINE FOR THE POSITION. What holds capital, what does not, and what it earned are three
    # clauses of a single fact, and three separate lines made a two-ticker event look like a
    # three-paragraph section.
    if held:
        _pos = ["**Funded.** " + ", ".join(_tk(t) for t in sorted(held, key=lambda t: -held[t]))]
        if _unfunded:
            _pos.append("**not funded** " + ", ".join(_unfunded))
    else:
        # "No capital" alone read oddly beside "+$10,832 since it opened" on an exit the book HAD
        # funded, just not at this anchor. The scan is what the clause is about, so it says so.
        _pos = ["**No capital at this scan.** vehicles " + (", ".join(vs_now) or "(none)")
                + (f", {len(vs)} tracked over its life" if len(vs) > len(vs_now) else "")]
    if money:
        _pos.append("**P&L** " + ", ".join(money))
    L.append(" · ".join(_pos))
    # THE EXIT TEST, THEN WHETHER IT FIRED. The condition the curator committed to comes first and
    # the verdict against it follows, so the two read as the question and its answer rather than as
    # two unrelated facts. A catalyst is written as a bare present-tense phrase ("FDA approves
    # Gedatolisib") naming the thing being WAITED FOR -- on ev157 it never happened at all -- so
    # whether it has landed is the most load-bearing line in the block.
    if (entry or {}).get("exit_advice"):
        L.append(f"**Exit condition.** {_trim(entry['exit_advice'], 200)}")
    if (entry or {}).get("catalyst_resolved"):
        L.append(f"**{str(entry.get('date', ''))[:10]} · catalyst RESOLVED**"
                 + (f": {_trim(entry.get('exit_case'), 200)}" if _ec != "none" else ""))
    elif entry and not entry.get("thesis_live", True):
        L.append(f"**{str(entry.get('date', ''))[:10]} · thesis dead**, catalyst never resolved"
                 + (f": {_trim(entry.get('exit_case'), 200)}" if _ec != "none" else ""))
    else:
        L.append(f"**Still pending** at scan {n_scans} of this event")

    if entry:
        # A carried-forward verdict must not be labelled "this scan": that would put words in the
        # curator's mouth for a scan where it said nothing.
        if entry.get("assessment"):
            L.append(f"**{'This scan' if _fresh else 'Last read'}.** {_trim(entry['assessment'])}")
        if str(entry.get("news_claims") or "").strip():
            L.append(f"**News claims.** {_trim(entry['news_claims'])}")

        # MILESTONES, dated by the scan that first recorded each, with that scan's citations beside
        # them. The journal has no date field for a milestone (the agent embeds one in the text when
        # it feels like it: 57% of 1,507 strings contain any digit at all) and no per-milestone
        # source either -- `sources` is a per-ENTRY list. Both are recovered from the journal's
        # shape: milestones accumulate, so a string's first appearance dates it, and the entry it
        # first appeared in is the scan whose citations were in front of the agent when it wrote it.
        # That is an association by scan, not a claim that a given URL is the citation FOR a given
        # milestone, and the footer says so.
        #
        # ITERATE THE LIST, NOT A SET: the agent writes milestones in a deliberate order (the arc,
        # oldest first), and a set shuffled everything recorded at the same scan.
        _first, _srcs, _last = {}, {}, set()
        for _x in (e.get("entries") or []):
            _d = str(_x.get("date", ""))[:10]
            if _d > date:
                break
            _ml = [str(_m).strip() for _m in (_x.get("milestones") or []) if str(_m).strip()]
            _last = set(_ml)
            for _m in _ml:
                if _m not in _first:
                    _first[_m] = _d
                    _srcs[_m] = [u for u in (_x.get("sources") or []) if u][:SOURCES_PER_EVENT]
        if _first:
            L.append("**Milestones**")
            # EVERY milestone the event ever recorded, not just the ones the latest entry still
            # lists: carrying them forward is the MODEL's habit, not the code's guarantee, and 8 of
            # 267 events with milestones (3%) dropped 31 strings that way.
            #
            # GROUPED BY SCAN, with that scan's citations printed ONCE underneath. `sources` is a
            # per-entry list, so hanging it off each milestone repeated the same four URLs three
            # times on ev204 and implied a per-milestone citation that the journal does not record.
            # THE SCAN IS THE UNIT, so the scan heads the group and its citations hang off IT.
            # Repeating four URLs on each of three milestones was noise; printing them once after
            # the last line of a group was worse, because it read as though only that milestone had
            # sources. `sources` is a per-ENTRY list -- the articles in front of the agent when it
            # wrote every milestone at that scan -- and nesting says exactly that with no repetition.
            _grp = collections.OrderedDict()
            for _m, _d in sorted(_first.items(), key=lambda kv: kv[1]):
                _grp.setdefault(_d, []).append(_m)
            for _d, _ms in _grp.items():
                _u = _srcs.get(_ms[0]) or []
                L.append(f"- **{_d}**"
                         + (" · sources cited: "
                            + ", ".join(f"[{i + 1}]({u})" for i, u in enumerate(_u)) if _u
                            else " · no sources cited"))
                for _m in _ms:
                    L.append(f"    - {_trim(_m, 240)}"
                             + ("" if _m in _last else
                                "  *(the agent stopped carrying this one)*"))
        # THIS SCAN'S CITATIONS, when no milestone was recorded here to carry them. Without this a
        # quiet scan's evidence -- the only evidence there is behind a "nothing changed" verdict --
        # would not appear anywhere.
        _now = [u for u in (entry.get("sources") or []) if u][:SOURCES_PER_EVENT]
        if _now and not any(d == date for d in _first.values()):
            L.append("**Sources cited:** "
                     + ", ".join(f"[{i + 1}]({u})" for i, u in enumerate(_now)))

    # INPUT 3: the journal digest, in the form agent._journal_digest builds it. This is the memory
    # the prompt tells the agent to re-read and test its exit condition against.
    if history:
        L.append("**What the event-agent re-reads on re-entry** \u2014 its own journal, "
                 + f"{len(history)} earlier "
                 + ("scan" if len(history) == 1 else "scans"))
        if len(history) > HISTORY_LINES:
            L.append(f"- …{len(history) - HISTORY_LINES} earlier scans, in the journal")
        for h in history[-HISTORY_LINES:]:
            L.append(f"- {str(h.get('date', ''))[:10]} · "
                     f"{'live' if h.get('thesis_live', True) else 'DEAD'} · "
                     f"{','.join(h.get('vehicles') or []) or '-'} · "
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
            _t = _trim(a.get("title") or "(untitled)", 110).replace("[", "(").replace("]", ")")
            L.append(f"- {'✓ ' if a.get('url') in _cited else ''}"
                     f"{str(a.get('published_date', ''))[:10]} · {a.get('source', '')} · "
                     f"[{_t}]({a.get('url', '')})")
        L += ["", "</details>"]
    if entry and not _fresh:
        L.append(f"*Not re-judged this scan; the verdict above is from "
                 f"{str(entry.get('date', ''))[:10]} and stands until something changes it.*")
    if not entry:
        L.append("*No judgement on or before this scan.*")
    L.append("")
    return L


# --------------------------------------------------------------------------- markdown -> HTML
# THE BLOCKS ARE BUILT AS MARKDOWN AND RENDERED HERE. Markdown stays the intermediate form because
# it is what the section builders above are readable in, and because every construct they emit is
# listed in this converter: headings, bold, italic, code, links, one level of nested list, <details>
# passthrough, and a rule. Nothing else is supported, and _assert_rendered() below fails the build
# if an unrendered construct reaches the output, so "the converter is incomplete" cannot pass
# silently.
_INLINE = [
    (_re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (_re.compile(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)"), r"<em>\1</em>"),
    (_re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
]


def _esc(t: str) -> str:
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline(t: str) -> str:
    """Escape, then apply inline markdown. Links first, so a URL's characters cannot be eaten."""
    out, last = [], 0
    for m in _re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", t):
        out.append(_esc(t[last:m.start()]))
        out.append(f'<a href="{_esc(m.group(2))}" title="{_esc(m.group(2))}" '
                   f'target="_blank" rel="noopener">{_esc(m.group(1))}</a>')
        last = m.end()
    out.append(_esc(t[last:]))
    h = "".join(out)
    for rx, rep in _INLINE:
        h = rx.sub(rep, h)
    # a bare URL on its own (the fallback source list) becomes its own link
    h = _re.compile(r"(?<!\")(?<!>)(https?://[^\s<]+)(?![^<]*</a>)").sub(
        r'<a href="\1" target="_blank" rel="noopener">\1</a>', h)
    return h


def _md_to_html(lines: list) -> str:
    html, depth = [], 0

    def _close(to):
        # A nested list lives INSIDE its parent <li>, so closing one closes that <li> too. Emitting
        # <ul> as a SIBLING of <li> renders the same in every browser and is still invalid, which
        # is the kind of thing that works until something parses it.
        nonlocal depth
        while depth > to:
            html.append("</ul>" if depth == 1 else "</ul></li>")
            depth -= 1

    for ln in lines:
        raw = ln.rstrip()
        if raw.startswith("<details") or raw.startswith("</details") or raw.startswith("<summary"):
            _close(0)
            html.append(raw if not raw.startswith("<summary") else raw)
            continue
        st = raw.lstrip()
        if not st:
            continue
        if st == "---":
            _close(0); html.append("<hr>"); continue
        if st.startswith("### "):
            _close(0); html.append(f"<h3>{_inline(st[4:])}</h3>"); continue
        if st.startswith("## "):
            _close(0); html.append(f"<h2>{_inline(st[3:])}</h2>"); continue
        if st.startswith("# "):
            _close(0); html.append(f"<h1>{_inline(st[2:])}</h1>"); continue
        if st.startswith("- "):
            want = 2 if (len(raw) - len(st)) >= 2 else 1
            while depth < want:
                if depth and html and html[-1].endswith("</li>"):
                    html[-1] = html[-1][:-5]          # re-open the parent item to nest inside it
                html.append("<ul>")
                depth += 1
            _close(want)
            html.append(f"<li>{_inline(st[2:])}</li>")
            continue
        _close(0)
        html.append(f"<p>{_inline(st)}</p>")
    _close(0)
    return "\n".join(html)


def _assert_rendered(html: str, where: str) -> None:
    """A construct the converter does not know would otherwise ship as literal `**text**`."""
    for bad in ("**", "](", "\n- "):
        if bad in html:
            raise SystemExit(f"curation_report: unrendered markdown {bad!r} in {where}")


# Card and line match build_cbt_dashboard's own values; the rest of the palette is passed in from
# there so one theme definition serves the dashboards and the reports they link.
def _page(title: str, body: str, back: str, light: dict, dark: dict) -> str:
    def _vars(pal, card, line):
        return (f"--surface:{pal['surface']}; --card:{card}; --text:{pal['text']}; "
                f"--text2:{pal['text2']}; --line:{line};")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>
:root {{ {_vars(light, '#ffffff', '#e6e5e1')} }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ {_vars(dark, '#222220', '#33322f')} }} }}
:root[data-theme="dark"] {{ {_vars(dark, '#222220', '#33322f')} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--surface); color:var(--text); font:15px/1.6
  -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; }}
.wrap {{ max-width:900px; margin:0 auto; padding:26px 20px 60px; }}
h1 {{ font-size:21px; margin:0 0 6px; font-weight:600; }}
h2 {{ font-size:16px; margin:26px 0 6px; font-weight:600; border-bottom:1px solid var(--line);
  padding-bottom:5px; }}
h3 {{ font-size:15px; margin:20px 0 4px; font-weight:600; }}
p {{ margin:3px 0; }}
ul {{ margin:3px 0 3px 0; padding-left:22px; }}
ul ul {{ margin:2px 0; }}
li {{ margin:1px 0; }}
code {{ background:var(--card); border:1px solid var(--line); border-radius:4px; padding:0 4px;
  font-size:12.5px; }}
a {{ color:inherit; text-decoration:underline; text-underline-offset:2px; }}
em {{ color:var(--text2); font-style:italic; }}
hr {{ border:0; border-top:1px solid var(--line); margin:22px 0 10px; }}
details {{ margin:6px 0; background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:7px 11px; }}
summary {{ cursor:pointer; color:var(--text2); font-size:13.5px; }}
details ul {{ font-size:13px; }}
.nav {{ font-size:13px; color:var(--text2); border-bottom:1px solid var(--line);
  padding-bottom:11px; margin-bottom:20px; }}
</style></head><body><div class="wrap">
<div class="nav"><a href="{_esc(back)}">&larr; back to the dashboard</a></div>
{body}
</div></body></html>
"""


def write_reports(out_dir, *, arm: str, ev: dict, log: list, fm: dict, panel,
                  gain: dict, gain_series: dict, dates: list, capital: float,
                  run: str, fingerprint: str, corpus: str, profile: str,
                  page_title: str, archive_dir=None, palette=None, back: str = "cbt.html"
                  ) -> list[dict]:
    """Write one report per anchor into `out_dir`; return a row per report for the page's link table.

    THE DIRECTORY IS CLEARED FIRST, for this arm only. A cadence change moves every anchor date, so
    yesterday's files would otherwise survive beside today's under different names and the page would
    link a set that no longer matches the book. Clearing means what is on disk is always exactly what
    the page links."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in list(out_dir.glob(f"*-{arm}-curation.html")) + list(
            out_dir.glob(f"*-{arm}-curation.md")):
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
    # WAS THIS EVENT EVER FUNDED, at any anchor up to now? "Held going in" was the wrong test for
    # which exits deserve a full block: ev157 made +$10,832 over ten scans and then exited from a
    # period it happened not to be held in, so the most profitable exit in the run got a one-line
    # summary while a never-funded one-scan event beside it got the same treatment. What makes an
    # exit worth reading is whether the book ever had money in it.
    _cum_veh = {}
    for k, e in ev.items():
        acc, seq = set(), []
        for x in e.get("entries") or []:
            acc |= {str(v).upper() for v in (x.get("vehicles") or [])}
            seq.append((str(x.get("date", ""))[:10], set(acc)))
        _cum_veh[k] = seq

    def _veh_at(k, d):
        """The event's vehicles as at `d`, and EMPTY before it existed.

        Falling back to the event's current set for a date before its first entry made ev204 --
        entered 2026-03-28 -- claim credit for every earlier anchor at which USO happened to be
        funded for some other event, and print "funded at 3 of 2 scans"."""
        out = set()
        for dt, st in _cum_veh.get(k, []):
            if dt <= d:
                out = st
            else:
                break
        return out

    fund_dates = collections.defaultdict(list)
    for _r in log:
        _d = str(_r.get("week"))[:10]
        _w = {t for t, v in _weights(_r).items() if v > FUNDED_EPS}
        if not _w:
            continue
        for _k in ev:
            if _veh_at(_k, _d) & _w:
                fund_dates[_k].append(_d)
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
        # PER-TICKER RETURN over the period this anchor opens. The weight says what the optimizer
        # was willing to risk; this says whether the market agreed, and the two side by side are
        # what makes a funded line worth reading.
        rets = {t: _pct(panel, [t], d0, d1) for t in funded_tk} if d1 else {}
        # AN EXITED POSITION IS SCORED OVER THE PERIOD THE BOOK ACTUALLY HELD IT, which ended here.
        # Using the forward period instead reported what the ticker did AFTER the book sold, and
        # printed it beside the weight as though it were the trade's result.
        rets_prev = ({t: _pct(panel, [t], anchors[i - 1], d0) for t in held_before}
                     if i else {})
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
                L += _event_block(k, ev[k], _all_f[k][1], weights=weights, date=d0, rets=rets,
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
        # THREE KINDS OF EXIT, and lumping them cost the most informative one its detail.
        #   paid    the book had money in this at some point, so the exit is a decision that
        #           settled a position. Full block.
        #   quick   resolved within two scans and never funded: the scout proposed a thesis that
        #           was already over, or that ended before capital could reach it. 129 of 224
        #           finished CBT events (58%) are this shape, so they are their own group rather
        #           than the bulk of an undifferentiated list.
        #   ran     everything else unfunded: aged out at the cap, exit condition met, or ended
        #           with nothing stated.
        _paid, _quick, _ran = [], [], []
        for k, x in exited_now:
            _n = len([y for y in (ev[k].get("entries") or []) if str(y.get("date", ""))[:10] <= d0])
            if [d for d in (fund_dates.get(k) or []) if d <= d0]:
                _paid.append((k, x, _n))
            elif _n <= 2 and x.get("catalyst_resolved"):
                _quick.append((k, x, _n))
            else:
                _ran.append((k, x, _n))

        def _one(k, x, n):
            """A compact exit. The final assessment is the point: "no stated reason" on its own
            says nothing, and the agent's last words usually say why it let go."""
            return (f"- **{k}** · {', '.join(sorted(_veh_at(k, d0))[:4])} · "
                    f"{n} scan{'s' if n != 1 else ''} · {_how_it_ended(ev[k], x, _ec)}"
                    + (f"  \n  {_trim(x.get('assessment'), 150)}"
                       if x.get("assessment") else ""))

        if exited_now:
            L += ["## Exited at this scan", ""]
            for k, x, n in _paid:
                _h, _rd, _m, _ok = _inputs(k)
                # Only anchors up to this one: a report describes the book as it stood here, and
                # a later funding would be information the scan did not have.
                _fd = [d for d in (fund_dates.get(k) or []) if d <= d0]
                # COUNTED SINCE THE EVENT STARTED, which is the span the fraction is over; a
                # last-held date is added only when it says something the start date does not.
                _st = str((ev[k].get("entries") or [{}])[0].get("date", ""))[:10]
                _note = (f"funded at {len(_fd)} of {n} scans since {_st}"
                         + (f", last held {_fd[-1]}" if _fd[-1] not in (_st, d0) else ""))
                L += _event_block(k, ev[k], x, weights=held_before, date=d0, per=None,
                                  cum=cum.get(k), history=_h, read=_rd, matched=_m, cap=ev_cap,
                                  exact=_ok, rets=rets_prev,
                                  note=("held going in" if _fd[-1] == d0 else _note))
            for _lbl, _grp in (("Resolved before the book acted, never funded", _quick),
                               ("Ran their course unfunded", _ran)):
                if not _grp:
                    continue
                L += [f"**{_lbl}.**", ""]
                for k, x, n in _grp[:EXITS_LISTED]:
                    L.append(_one(k, x, n))
                if len(_grp) > EXITS_LISTED:
                    L.append(f"- …and {len(_grp) - EXITS_LISTED} more")
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
                              date=d0, note=note, rets=rets,
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
              "what lets an agent notice that a due date has passed; the URLs beside one are the "
              "sources the agent cited AT THAT SCAN, which is an association by scan and not a "
              "claim that a given article is the citation for a given milestone. **Scan N** counts how "
              "many times an agent has re-read that thesis, so a high N against an unchanged "
              "assessment is a thesis nothing is confirming. The money is this event's share of "
              "realised P&L: a ticker claimed by several live events splits its gain equally "
              "between them. **Exits when** is the condition the curator committed to at the "
              "outset, so an event whose exit cannot be dated is a theme wearing an event's "
              "clothes, and only the `max_event_scans` age cap will ever end it.", ""]
        # HTML, NOT MARKDOWN, and served from the same GitHub Pages site as the dashboards. A
        # markdown blob link threw the reader out of the site into the code host's file browser,
        # with its own chrome and theme above the report; a relative .html link also works when the
        # dashboard is opened straight off disk, which the blob URL never did.
        _light, _dark = palette or ({}, {})
        _body = _md_to_html(L)
        _assert_rendered(_body, f"{d0}-{arm}")
        f = out_dir / f"{d0}-{arm}-curation.html"
        f.write_text(_page(f"{page_title} curation — {d0}", _body, back, _light, _dark))
        rows.append({"date": d0, "file": f.name, "live": len(live), "funded": len(held),
                     "ret": ret if isinstance(ret, (int, float)) else None,
                     "opened": len(opened), "exited": len(exited)})
    return rows
