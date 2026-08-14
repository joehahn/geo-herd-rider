"""dash_nav.py — the shared cross-page nav for every GHR dashboard. ONE source of truth.

Modelled on PWR's equivalent: a README link, then the dashboards grouped by ERA, with the page you
are currently on shown in bold rather than as a link. Adding a dashboard is one line here and it
appears in every page's nav.

The two axes:
  ERA    Backtest (historical GKG replay) -> Bootstrap (backtest tail spliced onto live pulls)
         -> Forwardtest (live, out-of-sample). Only Backtest exists today.
  STAGE  Firehose (news gathering, upstream of any LLM) -> Curator (the LLM's picks) -> Sweeps
         (parameter sensitivity).

A page that does not exist yet is rendered as PLAIN TEXT, not a dead link -- a nav that 404s is worse
than one that admits a gap, and it makes the shape of what is still missing visible at a glance.
"""
from datetime import datetime

README = ("https://github.com/joehahn/geo-herd-rider/blob/main/README.md", "README")

# (filename, label, built?) -- built=False renders as greyed plain text
BACKTEST = [
    ("fbt.html", "Firehose", True),
    ("cbt.html", "Curator", True),
    ("sbt.html", "Sweeps", True),
]
BOOTSTRAP = [
    ("fbs.html", "Firehose", True),      # built 2026-08-14
    ("cbs.html", "Curator", False),
]
FORWARDTEST = [
    ("fft.html", "Firehose", False),
    ("cft.html", "Curator", False),
]


def _link(href: str, name: str, built: bool, current: str) -> str:
    if not built:
        return f'<span style="color:#bbb;" title="not built yet">{name}</span>'
    if href == current:
        return f"<b>{name}</b>"
    return f'<a href="{href}">{name}</a>'


def _group(pages, current: str) -> str:
    return " &middot; ".join(_link(h, n, b, current) for h, n, b in pages)


def render(current: str = "", built: bool = True) -> str:
    """An HTML <nav>: README, then one row per era. `current` is the bare filename of the page being
    rendered, which is shown bold instead of linked so a reader can see where they are."""
    ts = (f'<span style="float:right;color:#aaa;font-weight:normal;">built '
          f'{datetime.now().strftime("%Y-%m-%d %H:%M")}</span>') if built else ""
    rows = "".join(
        f'<div><span style="color:#999;display:inline-block;min-width:92px;">{era}</span>'
        f'&nbsp;&nbsp;{_group(pages, current)}</div>'
        for era, pages in (("Backtest", BACKTEST), ("Bootstrap", BOOTSTRAP), ("Forwardtest", FORWARDTEST)))
    return ('<nav style="font-size:13px;color:var(--text2);border-bottom:1px solid var(--line);'
            'padding-bottom:12px;margin-bottom:22px;line-height:1.9;">'
            f'{ts}<div><a href="{README[0]}">{README[1]}</a></div>{rows}</nav>')
