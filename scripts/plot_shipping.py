"""plot_shipping.py — illustrate the wet-leads / dry-follows freight ladder.

Two panels for the README's canonical example (carriers -> Hormuz -> tanker rates ->
dry-bulk), drawn from real prices over the 2026 Trump-Iran war: tanker equities (wet bulk)
spike fast on the Hormuz threat; dry-bulk carriers follow a hop later and more slowly.
Indexed to 100 at the window start, SPY as the market baseline. Saves two PNGs under assets/.

    python scripts/plot_shipping.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
START, END = "2026-01-01", "2026-06-17"
WET = ["FRO", "STNG"]   # tanker equities (Frontline, Scorpio Tankers)
DRY = ["SBLK", "GNK"]   # dry-bulk carriers (Star Bulk, Genco)
EVENTS = {"2026-02-28": "Feb 28 strike", "2026-06-15": "Jun 15 Hormuz reopens"}


def panel(ax, tickers, prices, title):
    idx = prices / prices.iloc[0] * 100.0
    ax.plot(idx.index, idx["SPY"], color="0.7", lw=1.4, label="SPY")
    for t in tickers:
        ax.plot(idx.index, idx[t], lw=1.9, label=t)
    top = ax.get_ylim()[1]
    for d, lbl in EVENTS.items():
        x = pd.Timestamp(d)
        ax.axvline(x, color="0.5", ls="--", lw=0.8)
        ax.text(x, top, " " + lbl, rotation=90, va="top", ha="left", fontsize=7, color="0.45")
    ax.axhline(100, color="0.88", lw=0.8, zorder=0)
    ax.set_title(title, fontsize=11)
    ax.set_ylabel("indexed to 100 (2026-01-02)")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.margins(x=0.01)


def main():
    tickers = sorted(set(WET + DRY + ["SPY"]))
    prices = yf.download(tickers, start=START, end=END, auto_adjust=True,
                         progress=False)["Close"].dropna()
    ASSETS.mkdir(exist_ok=True)
    for names, fname, title in [
        (WET, "wet_tankers", "Wet bulk — tanker equities (rates spike fast)"),
        (DRY, "dry_bulk", "Dry bulk — bulk carriers (the slower hop)"),
    ]:
        fig, ax = plt.subplots(figsize=(5.6, 4.0))
        panel(ax, names, prices, title)
        fig.tight_layout()
        out = ASSETS / f"{fname}.png"
        fig.savefig(out, dpi=130)
        plt.close(fig)
        print("wrote", out)


if __name__ == "__main__":
    main()
