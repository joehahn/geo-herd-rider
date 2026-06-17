"""plot_shipping.py — the motivating chart: BWET vs SPY across the 2026 Iran war.

BWET (a dry-bulk freight ETF) is the far end of the carriers -> Hormuz -> dry-bulk chain,
and the ticker that motivated this project: it ran ~5x from the Feb-2026 carrier deployment
to its May peak while SPY sat flat — a four-month herd pivot telegraphed by Trump's tweets,
with a May rollover as the smart money rotated out ahead of the peace deal. One line tells
the whole thesis. Indexed to 100 at the carrier deployment. Saves assets/bwet_vs_spy.png.

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
START, END = "2025-06-17", "2026-06-17"   # ~1 year: ~8mo of low pre-war baseline + the pivot
ANCHOR = "2026-02-20"   # carriers transit the western Med — index to 100 here
EVENTS = {"2025-12-28": "Iran protests", "2026-02-20": "carriers → W. Med",
          "2026-02-28": "strike", "2026-06-15": "peace deal"}


def main():
    p = yf.download(["BWET", "SPY"], start=START, end=END, auto_adjust=True,
                    progress=False)["Close"].dropna()
    base = p.loc[p.index >= pd.Timestamp(ANCHOR)].iloc[0]
    idx = p / base * 100.0

    fig, ax = plt.subplots(figsize=(7.2, 4.3))
    ax.plot(idx.index, idx["SPY"], color="0.7", lw=1.6, label="SPY")
    ax.plot(idx.index, idx["BWET"], color="#c0392b", lw=2.1, label="BWET (dry-bulk freight)")
    top = ax.get_ylim()[1]
    for d, lbl in EVENTS.items():
        x = pd.Timestamp(d)
        ax.axvline(x, color="0.5", ls="--", lw=0.8)
        ax.text(x, top, " " + lbl, rotation=90, va="top", ha="left", fontsize=7.5, color="0.4")
    ax.axhline(100, color="0.88", lw=0.8, zorder=0)
    ax.set_title("BWET vs SPY — the 2026 Iran-war herd pivot (~8x from the Dec protests)", fontsize=11)
    ax.set_ylabel("indexed to 100 (carriers → W. Med, Feb 2026)")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.margins(x=0.01)
    fig.tight_layout()

    ASSETS.mkdir(exist_ok=True)
    out = ASSETS / "bwet_vs_spy.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print("wrote", out)


if __name__ == "__main__":
    main()
