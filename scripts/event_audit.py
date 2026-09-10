#!/usr/bin/env python3
"""event_audit.py — one compact block per event, joining every stream the run wrote.

THE DEBUGGING LOOP READS THIS, NOT THE DASHBOARD. A 12-scan slice opens ~109 events and the CBT
page is 4 MB; this renders the same events as a few hundred lines of text, so a whole slice can be
read in one pass and the same event re-read next iteration to see whether a named defect closed.

It joins FOUR streams that otherwise live in four files:
  journal.json     catalyst, exit condition, vehicles, per-scan entries, milestones
  decisions.jsonl  scout proposal (with peers), matcher assignment, evrank sub-scores + why
  <run>.log        the admission gates -- what was dropped and why
  derived          death cause, which the report cannot currently state (48% of deaths)

FLAGS are the point. Each one encodes a defect we have actually hit, so the loop is checking
regressions rather than impressions:
  NOT-A-PARTY   the catalyst names a company that is not a vehicle          (ev582: Constellation)
  PEER-ARRIVAL  a vehicle arrived on someone else's proposal                (ev582: NEE off GEV)
  RESTATES      a vehicle clause restates the catalyst instead of a mechanism
  ONE-SIDED     the exit's branches do not point in different directions    (ev585)
  WHY-MISMATCH  the ranker's `why` names a field that is not its lowest score
  SUBJECT-DROPPED  the event no longer holds the ticker its catalyst is about
  NEVER-JUDGED  opened and retired before any event agent judged it -- 31% of v33
  NO-SCORE      an event that was live at a scan carries no evrank record

    python scripts/event_audit.py --run data/dbg_01 --limit 20
    python scripts/event_audit.py --run data/dbg_01 --only ev582
    python scripts/event_audit.py --run data/dbg_01 --fate counter --flags
"""
from __future__ import annotations
import argparse, json, re, sys, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agent import _restates_resolved as _agent_restates, _stem   # gates the scout already trusts

STOP = set("the a an of to in on for and or by at with its it is are was were be been as from that "
           "this new after over into up down us his her their has have will would could than then "
           "which who what when where why how not no but if all any more most other some such".split())


def words(s: str) -> set[str]:
    """Distinctive tokens of a phrase, STEMMED and INCLUDING alphanumerics.

    Both details are load-bearing, and both were wrong first time round. `[a-z]{4,}` dropped KB408
    and NXC-201 -- precisely the tokens that prove an exit is still on its catalyst's subject -- and
    without stemming "the investigation concludes" looked unrelated to "investigates NVIDIA". Each
    bug manufactured EXIT-DRIFT reports out of exits that were perfectly on topic."""
    toks = re.findall(r"[a-z0-9][a-z0-9-]{2,}", (s or "").lower())
    out = set()
    for w in toks:
        if w in STOP or w.isdigit():
            continue
        # agent._stem knows nothing of nominalisations, so "investigation" never met "investigates"
        for suf in ("ations", "ation", "ions", "ion"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.add(_stem(w))
    return out - {""}


def overlap(a: str, b: str) -> float:
    A, B = words(a), words(b)
    return len(A & B) / max(1, min(len(A), len(B)))


# a date, a docket, a named window -- the difference between an exit that can fire and one that waits
_DATED = re.compile(r"\b(20\d\d|q[1-4]\b|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
                    r"pdufa|docket|deadline|expir|scheduled|by the end of|on or before)\b", re.I)
_UNDATED = re.compile(r"no date announced|date (?:not|un)|pending\s*$", re.I)


def exit_flags(exit_txt: str) -> list[str]:
    """Flags on the exit condition. A MISSING DATE IS NOT ONE OF THEM.

    UNDATED-EXIT was here and has been removed: it fired on 103 of 113 judged events while the
    reading that motivated it turned out to be wrong. "the EC investigation concludes, no date
    announced" is a perfectly sound exit -- an opened investigation MUST conclude, and the date
    simply is not public yet. What separates a firable exit from a dead one is whether the
    catalyst's own procedure REQUIRES the act, not whether a calendar entry exists for it. A flag
    that fires on nearly everything teaches the reader to ignore it, which is worse than absent."""
    f = []
    t = (exit_txt or "").strip()
    if not t or t.lower() in {"none", "n/a"}:
        return ["NO-EXIT-TEXT"]
    # branches: split on or/unless/either, then ask whether they differ
    parts = [p.strip() for p in re.split(r"\bor\b|\bunless\b|/", t) if len(p.strip()) > 3]
    if len(parts) < 2:
        f.append("ONE-SIDED")
    elif all(overlap(parts[0], p) >= 0.8 for p in parts[1:]):
        f.append("ONE-SIDED")
    return f


# A COMPANY NAME, NOT AN ASSET OR A PRIVATE FIRM. The map is learned from the scout's own
# `company` field and it picks up junk: one proposal carried company "Bitcoin" with ticker "BTC",
# after which EVERY catalyst mentioning bitcoin reported a missing vehicle, and "SpaceX" arrived as
# its own ticker though it is private and cannot be held at all. Two cheap conditions kill both:
# the ticker must be TICKER-SHAPED (1-5 letters -- SPACEX is six), and the name must look like a
# company, i.e. carry a corporate suffix or be more than one word. "Bitcoin" is neither.
_CORP_WORDS = ('inc', 'corp', 'ltd', 'plc', 'llc', 'sa', 'ag', 'nv', 'co', 'company', 'holdings', 'group', 'partners', 'technologies', 'therapeutics', 'pharmaceuticals', 'pharma', 'energy', 'resources', 'systems', 'industries', 'international', 'laboratories', 'motors', 'bank', 'financial', 'capital')


def _is_company(name: str, ticker: str) -> bool:
    t = (ticker or "").strip()
    if not (t.isalpha() and 1 <= len(t) <= 5):
        return False
    w = [x for x in re.split(r"[^a-z0-9]+", (name or "").lower()) if x]
    return len(w) > 1 or bool(set(w) & set(_CORP_WORDS))


def load(run: Path):
    J = json.loads((run / "journal.json").read_text())
    dec = [json.loads(l) for l in (run / "decisions.jsonl").open()]
    log = ""
    for p in (run.with_suffix(".log"), run / "run.log"):
        if p.exists():
            log = p.read_text(errors="replace"); break
    return J, dec, log


def build(run: Path):
    J, dec, log = load(run)
    events = J["events"]

    scout = {r["context"]: r for r in dec if r["kind"] == "scout"}
    match = {r["context"]: r for r in dec if r["kind"] == "match"}
    rank = [r for r in dec if r["kind"] == "evrank"]
    culled = collections.defaultdict(list)
    for r in rank:
        for e in r.get("culled") or []:
            culled[e].append(r["context"])

    # name -> ticker, learned from what the scout itself proposed across the run
    name2tk = {}
    for r in scout.values():
        for c in r.get("proposed") or []:
            if isinstance(c, dict) and c.get("company") and c.get("ticker") \
                    and _is_company(str(c["company"]), str(c["ticker"])):
                name2tk[str(c["company"]).lower()] = c["ticker"]

    # which proposal did each ticker arrive on, per scan
    arrived = collections.defaultdict(dict)      # date -> ticker -> (owner_ticker, owner_thesis)
    for d, r in scout.items():
        for c in r.get("proposed") or []:
            if not isinstance(c, dict):
                continue
            arrived[d].setdefault(c.get("ticker"), (c.get("ticker"), c.get("thesis")))
            for p in c.get("peers") or []:
                arrived[d].setdefault(p, (c.get("ticker"), c.get("thesis")))
    return events, scout, match, rank, culled, name2tk, arrived, log


def fate_of(e, culled) -> str:
    ents = e.get("entries") or []
    if not ents:
        return "no entries"
    last = ents[-1]
    if e["status"] == "merged":
        return "MERGED into another event"
    if e["status"] == "live":
        return "still live at the last scan"
    if last.get("catalyst_resolved"):
        return "AGENT: catalyst resolved"
    if not last.get("thesis_live", True):
        return "AGENT: thesis dead"
    if e["id"] in culled:
        return f"COUNTER: ranker evicted ({culled[e['id']][-1]})"
    return "COUNTER: silence/age cap"


def render(eid, e, ctx, show_all_scans=False) -> tuple[str, list[str]]:
    events, scout, match, rank, culled, name2tk, arrived, log = ctx
    L, flags = [], []
    ents = e.get("entries") or []
    born = ents[0]["date"] if ents else "?"
    fate = fate_of(e, culled)
    L.append(f"{eid}  {e['status']}  born {born}  {len(ents)} scan(s)")
    L.append(f"  CATALYST  {e.get('catalyst','')}")
    L.append(f"  PENDING   {e.get('pending_next','')}")

    # PENDING-DRIFT: `pending_next` names an act the CATALYST'S OWN PROCEDURE does not require.
    # ADDED 2026-09-10, after auditing the 2026-07-26 scan showed EXIT-DRIFT was watching the wrong
    # pair. The exit clause is written FROM pending_next and tracks it faithfully -- ev539's
    # "exit if/when the SEC grants or refuses the Ethereum ETF filing" is a correct exit for its
    # pending_next. The break is one step upstream, between the catalyst and pending_next, and
    # that is where the prompt's own rule lives: "if the world simply carries on, does this next
    # act HAVE to occur?" Examples this catches, all from v37:
    #   ev48  Goldman starts Krystal Biotech at buy  ->  regulatory approvals for new indications
    #   ev58  BlueFire reports 3,363% revenue increase  ->  NASDAQ uplisting
    #   ev62  France encourages crypto; Circle registers  ->  the SEC decision on a Bitcoin ETF
    # An analyst initiation obliges no approval, revenue growth obliges no uplisting, and a French
    # registration obliges no US ETF ruling. 77 of 595 events (13%) share no stemmed token at all.
    # IT IS NOISY, MEASURED: hand-judging all 6 hits on the 2026-09-10 playtest, FIVE were false
    # positives and one was real. A good pending_next uses ANAPHORA -- "the deal closes", "the
    # partnership leads to contracts" -- and so shares no content word with its catalyst by
    # construction; two more were stemming splits (partner/partnerSHIP, expansion/expandS) and one
    # named the drug ("retatrutide") where the catalyst gave the description ("triple-hormone
    # obesity drug"). Read this flag as A PLACE TO LOOK, never as a defect count: 55 of 601 on v37
    # is an upper bound and the true rate is plausibly nearer a tenth of that.
    # THE ONE REAL HIT was worth the noise -- ev82, "NuScale Power wins engineering contract to
    # advance SMR commercialization" -> "the EC investigation concludes" -- the scout copying the
    # PROMPT'S WORKED EXAMPLE onto an unrelated catalyst. That case is now REJECTED AT THE DOOR by
    # agent._copied_and_unrelated, which pairs this same unrelatedness test with "is it verbatim
    # prompt prose", so it should stop appearing here in curations made after 2026-09-10.
    # IT DOES NOT CATCH ev539, and that limit is the point of writing it down. Its catalyst
    # ("Goldman's BITCOIN ETF filing") and its pending_next ("the SEC's decision on ETHEREUM ETF
    # filing") share `filing` and `etf`, so any overlap test passes them. The defect there is a
    # CONTRADICTED ENTITY -- two different assets of the same class -- not a missing overlap, and
    # nothing here detects it. That was the only funded event of its scan, 23.8% of the book.
    if e.get("pending_next") and e.get("catalyst"):
        _cw, _pw = words(e.get("catalyst")), words(e.get("pending_next"))
        if _pw and _cw and not (_pw & _cw):
            flags.append("PENDING-DRIFT")

    # EXIT-DRIFT: the exit names a DIFFERENT occurrence than the catalyst that opened the event
    # (ev9: opened on an FDA label clearance, left waiting on reimbursement policy). Such an exit
    # cannot fire on its own catalyst, so the event can only ever die on a counter.
    if ents:
        _body = re.sub(r"^exit\s*(if\s*/?\s*when)?\s*", "",
                       (ents[-1].get("exit_advice") or "").strip(), flags=re.I)
        # ZERO shared tokens, not a ratio: a short on-topic exit ("clinical outcomes for KB408")
        # scores a low ratio on merit, while a genuinely drifted one shares nothing at all.
        if words(_body) and not (words(_body) & words(e.get("catalyst") or "")):
            flags.append("EXIT-DRIFT")
    if not ents:
        # opened and retired before any event agent ever judged it -- there is no record to audit
        flags.append("NEVER-JUDGED")
        L.append("  EXIT      (none -- event was retired before any agent judged it)   [NEVER-JUDGED]")
        ef = []
    else:
        exit_txt = ents[-1].get("exit_advice") or ""
        ef = exit_flags(exit_txt)
        flags += ef
        _note = list(ef) + ([] if _DATED.search(exit_txt or "") else ["no date (not a defect)"])
        L.append(f"  EXIT      {exit_txt}" + (f"   [{', '.join(_note)}]" if _note else "   [ok]"))

    # BIRTH: what the scout proposed, and what the matcher did with it
    sr = scout.get(born); mr = match.get(born)
    own = None
    if sr:
        cands = [c for c in (sr.get("proposed") or []) if isinstance(c, dict)]
        scored_c = [(overlap(str(c.get("thesis")), e.get("catalyst") or "")
                     + (0.5 if c.get("ticker") in e["vehicles"] else 0.0), c) for c in cands]
        scored_c = [(sc, c) for sc, c in scored_c if sc >= 0.5]
        if scored_c:
            own = max(scored_c, key=lambda x: x[0])[1]
    # THE SUBJECT, when the run recorded one. Curations from 2026-09-09 stamp `opened_on` -- the
    # ticker whose thesis became the catalyst -- so the audit can say outright when an event has
    # shed the very name it is about and is being carried by a peer. Dropping it is sometimes RIGHT
    # (a probe into NVIDIA helps AMD), so this is a flag to read, never a verdict.
    _subj = e.get("opened_on")
    if _subj:
        _held_now = set((ents[-1].get("vehicles") if ents else None) or e.get("vehicles") or [])
        if _held_now and _subj not in _held_now:
            flags.append("SUBJECT-DROPPED")
            L.append(f"  SUBJECT   {_subj} — the catalyst is about it, and it is NO LONGER held "
                     f"(now {', '.join(sorted(_held_now))})   [SUBJECT-DROPPED]")
        else:
            L.append(f"  SUBJECT   {_subj} (the others are peers)")
    if own:
        asg = {a["ticker"]: a["event"] for a in (mr or {}).get("assigned", [])}
        L.append(f"  BIRTH     scout proposed {own.get('ticker')}"
                 f"{' (peers ' + ', '.join(own.get('peers') or []) + ')' if own.get('peers') else ''}"
                 f" -> matcher: {asg.get(own.get('ticker'), '?')}")

    # NOT-A-PARTY: a company named in the catalyst that never became a vehicle
    cat_l = (e.get("catalyst") or "").lower()
    named = {tk for nm, tk in name2tk.items() if len(nm) > 4 and nm in cat_l}
    missing = sorted(named - set(e["vehicles"]))
    if missing:
        flags.append("NOT-A-PARTY")

    # VEHICLES
    exp = {x.get("ticker"): x.get("why") for x in (e.get("exposure") or []) if isinstance(x, dict)}
    L.append("  VEHICLES")
    for tk in e["vehicles"]:
        why = exp.get(tk) or ""
        body = re.sub(r"^(gains|loses)\s*--\s*", "", why, flags=re.I).strip()
        marks = []
        owner = arrived.get(born, {}).get(tk)
        if owner and owner[0] != tk and own and owner[0] != own.get("ticker"):
            marks.append(f"PEER-ARRIVAL off {owner[0]}")
            flags.append("PEER-ARRIVAL")
        opener = own.get("ticker") if own else None
        if body and tk != opener and _agent_restates(body, e.get("catalyst") or ""):
            marks.append("RESTATES"); flags.append("RESTATES")
        if not body:
            marks.append("NO-CLAUSE"); flags.append("NO-CLAUSE")
        # co-claimants
        span = {en["date"] for en in ents}
        others = [o["id"] for o in events.values()
                  if o["id"] != eid and tk in o["vehicles"] and o["status"] != "merged"
                  and span & {x["date"] for x in (o.get("entries") or [])}]
        if others:
            marks.append(f"also in {','.join(others[:3])}")
        L.append(f"    {tk:<7} {why}" + (f"   [{'; '.join(marks)}]" if marks else ""))
    if missing:
        L.append(f"    (none)  catalyst names {', '.join(missing)} -- NOT a vehicle   [NOT-A-PARTY]")

    # SCORES
    scored = [(en["date"], (en.get("coverage") or {}).get("evrank")) for en in ents]
    scored = [(d, v) for d, v in scored if v]
    if not scored:
        flags.append("NO-SCORE")
        L.append("  SCORES    none   [NO-SCORE]")
    else:
        for d, v in (scored if show_all_scans else scored[-1:]):
            L.append(f"  SCORE {d}  key {v.get('key')} = cat{v.get('catalyst_strength')} "
                     f"+ mkt{v.get('market_impact')} + arc{v.get('arc_progress')} "
                     f"+ ½×align{v.get('thesis_alignment')}"
                     f"{' + span' if v.get('spans_a_month') else ''}"
                     f"   [exit {v.get('exit_quality')} NOT counted]")
            w = str(v.get("why") or "")
            if w:
                subs = {k: v.get(k) for k in ("catalyst_strength", "market_impact", "arc_progress",
                                              "thesis_alignment", "exit_quality")
                        if v.get(k) is not None}
                # 25% of entries TIE for lowest, so accept the why if it names ANY tied-lowest field
                lo = min(subs.values()) if subs else None
                low = {k for k, x in subs.items() if x == lo}
                if low and not any(k in w for k in low):
                    flags.append("WHY-MISMATCH")
                    L.append(f"    why: {w}   [WHY-MISMATCH: lowest is {'/'.join(sorted(low))}={lo}]")
                else:
                    L.append(f"    why: {w}")

    # ARC
    L.append("  ARC")
    for en in (ents if show_all_scans else ents[-2:]):
        ms = en.get("milestones") or []
        L.append(f"    {en['date']}  live={en.get('thesis_live')} resolved={en.get('catalyst_resolved')}"
                 f"  milestones {len(ms)}  sources {len(en.get('sources') or [])}")
        if en.get("assessment"):
            L.append(f"      \"{str(en['assessment'])[:150]}\"")
    L.append(f"  DEATH     {fate}")
    return "\n".join(L), sorted(set(flags))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--only", default=None, help="one event id, or a comma list")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fate", default=None, choices=["agent", "counter", "live", "merged"])
    ap.add_argument("--flags", action="store_true", help="only events carrying a flag")
    ap.add_argument("--all-scans", action="store_true", help="every scan, not just the last two")
    ap.add_argument("--summary", action="store_true", help="flag tally only")
    a = ap.parse_args()

    run = ROOT / a.run
    ctx = build(run)
    events, _, _, _, culled = ctx[0], ctx[1], ctx[2], ctx[3], ctx[4]

    ids = list(events)
    if a.only:
        want = {x.strip() for x in a.only.split(",")}
        ids = [i for i in ids if i in want]

    tally = collections.Counter(); shown = 0; blocks = []
    for eid in ids:
        e = events[eid]
        f = fate_of(e, culled)
        if a.fate == "agent" and not f.startswith("AGENT"): continue
        if a.fate == "counter" and not f.startswith("COUNTER"): continue
        if a.fate == "live" and e["status"] != "live": continue
        if a.fate == "merged" and e["status"] != "merged": continue
        txt, flags = render(eid, e, ctx, a.all_scans)
        for x in flags:
            tally[x] += 1
        if a.flags and not flags:
            continue
        blocks.append(txt)
        shown += 1
        if a.limit and shown >= a.limit:
            break

    if not a.summary:
        print("\n\n".join(blocks))
    print(f"\n{'='*70}\n{shown} event(s) shown of {len(ids)}   flags:")
    for k, v in tally.most_common():
        print(f"   {v:4d}  {k}")


if __name__ == "__main__":
    main()
