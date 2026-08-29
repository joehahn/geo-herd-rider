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
    ("cbs.html", "Curator", True),       # built 2026-08-25 -- the bootstrap curation
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


SITE = "https://jmh-datasciences.com"
REPO = "https://github.com/joehahn/geo-herd-rider"


def footer() -> str:
    """The shared page footer: who built this, where to find them, how it may be reused, and the
    not-investment-advice line. ONE source of truth, same as the nav above it.

    It exists because `docs/` is CC BY 4.0. These pages are meant to be screenshotted into other
    people's slides, so the attribution condition has to ride ON the artifact -- someone lifting a
    chart never opens the README where the licence actually lives.

    The disclaimer scopes its claim to BACKTESTED figures deliberately. A blanket "every number here
    is a hindsight upper bound" would be false on the forwardtest pages, which are the one clean
    out-of-sample read in this project (README non-negotiable #4), and this footer renders on those
    pages too."""
    a = 'style="color:inherit;text-decoration:underline;"'
    return (
        '<footer data-ghr-footer style="font-size:12px;line-height:1.75;color:var(--text2,#777);'
        'border-top:1px solid var(--line,#e2e2e2);margin-top:40px;padding:14px 0 24px;">'
        f'Built by <a href="{SITE}" {a}>Joseph M. Hahn, Ph.D. &mdash; JMH DataSciences</a>'
        f' &middot; <a href="{REPO}" {a}>geo-herd-rider on GitHub</a><br>'
        f'This page is <a href="{REPO}/blob/main/LICENSE-docs.md" {a}>CC BY 4.0</a> &mdash; reuse it, '
        f'including commercially, with attribution to Joseph M. Hahn, {SITE}<br>'
        '<b>Not investment advice.</b> Research output. Backtested figures are hindsight upper '
        'bounds, not realized return.'
        '</footer>')


def stamp(html: str) -> str:
    """Insert `footer()` just before </body>. IDEMPOTENT -- a page already carrying the footer comes
    back untouched, so re-stamping a built page, or a builder that routes through here twice, cannot
    duplicate it. Every dashboard writer passes its output through this on the way to disk."""
    if "data-ghr-footer" in html:
        return html
    if "</body>" not in html:
        return html + footer()
    return html.replace("</body>", footer() + "</body>", 1)
