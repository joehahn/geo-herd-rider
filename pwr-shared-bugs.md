# Bugs found in GHR on 2026-08-22, and whether they reach PWR

Written for `portfolio-wave-rider`. GHR reuses PWR's optimizer spine
(`src/optimizer.py` ← PWR `src/portfolio.py`), so defects in the sizing path can be shared.

I checked PWR read-only at `../portfolio-wave-rider` rather than guessing. **Two of the five do not
apply, and on the biggest one PWR is already safer than GHR was** — the direction of copying should
go PWR → GHR, not the other way. Line numbers are PWR's as of this check.

---

## 1. A single foreign listing can wipe the whole universe from a lookback window

**Severity in GHR: catastrophic and silent.** 35% of the backtest held nothing at all.

**What happened.** GHR's sizing helper picked usable tickers with

```python
usable = [t for t in event_tickers if t in fit.columns and fit[t].notna().all()]
```

One London listing in the watchlist (`SATS.L`) trades on US market holidays. That inserted 21 rows
into the price panel where that ticker was priced and **all 437 others were NaN**. Any lookback
window containing such a row therefore failed `notna().all()` for *every US ticker at once* —
including the SPY/BIL anchors — so `usable` came back empty, the optimizer returned `None`, and the
book sat in cash for that entire rebalance period. 46% of possible windows contained one; 11
rebalances landed on one.

It never crashed and never warned. The equity curve simply went flat for a month at a time, which
**flattered drawdown and Sharpe, depressed return, and divided the capital-efficiency metrics by
capital-days that did not exist.** It was also invariant to every knob — `min_trade_size` 0.0 and
0.30 give the identical 267 dead days — which is why no sweep ever surfaced it.

**PWR status: PROTECTED, but incidentally.** `src/portfolio.py:1666` does

```python
full_prices = full_prices.dropna(how="all").ffill()
```

before any lookback slice. A holiday row survives `dropna(how="all")` (one ticker *is* priced), but
`.ffill()` then fills every other column from the prior close, so the later
`dropna(how="any", axis=1)` at lines 1788/1829 finds nothing to drop. PWR does not lose the universe.

**Two things to check anyway:**

- **The protection is a side effect.** The `ffill` is there for the row-wise-dropna reason documented
  at lines 329–330, not to defend against holiday rows. Anyone who removes or reorders it reintroduces
  the GHR failure at 1788/1829, which use the same "drop any column with a NaN" shape.
- **`ffill` has its own cost.** It manufactures **artificial zero-return days** on every holiday.
  Those inflate the observation count and depress estimated volatility, which biases Σ and therefore
  the mean-variance weights — mildly, but systematically, and in the direction of overstating
  risk-adjusted quality.

**Remedy (GHR's, if PWR ever needs it):** drop non-trading rows *before* the column test, rather than
letting one poison every column.

```python
if len(fit.columns):
    _live_rows = fit.notna().mean(axis=1) > 0.5   # majority priced == a real trading day
    if _live_rows.any():
        fit = fit.loc[_live_rows]
```

**Detection, either project:**

```python
# rows where almost nothing is priced are holidays, not data
hol = panel.index[panel.isna().mean(axis=1) > 0.9]
print(len(hol), "poison rows;", {t: int(panel.loc[hol, t].notna().sum()) for t in panel.columns
                                 if panel.loc[hol, t].notna().any()})
```

If that prints a non-empty ticker list, a foreign listing is in the universe. Then confirm no
rebalance produces empty weights while the watchlist is non-empty.

---

## 2. An empty optimizer result discarding the anchors

**PWR status: NOT PRESENT — PWR already does this correctly, and GHR should have copied it.**

GHR had `w = (curator._optimized_weights(...) or {}) if uni else {}`. When the optimizer returned
nothing the `or {}` discarded **everything, including the `always_include` anchors**, despite a
comment claiming idle capital "always has a home". The book held nothing, not even BIL.

PWR handles the same situation three ways, all safe:

- `portfolio.py:1292` — `if not opt.get("success"): continue`, carrying the previous weights forward
  rather than going to cash.
- `portfolio.py:1790` — `if len(slice_cur) < min_obs or not cur_watchlist: continue`, same.
- `portfolio.py:1517` — `_optimize_or_equal_weight` falls back to equal weight on non-convergence.

**Nothing to fix in PWR.** Worth stating explicitly because it is the one place GHR diverged from the
PWR spine and was worse for it.

---

## 3. Numerical libraries are part of the provenance

**Severity: high, and invisible to every data check.**

Every GHR dashboard was built with the *system* python (3.9.6, numpy 2.0.2, pandas 2.3.3) while every
sweep ran under `.venv` (3.12.13, numpy 2.4.6, pandas 3.0.3). Byte-identical inputs — same scan-set
md5, same config dict, same frozen price panel — produced **$54,960 on one stack and $40,498 on the
other, a 36% gap**, because the mean-variance solve differs between numpy/scipy versions.

Two pages therefore disagreed about the same book all day, and **no amount of checking the data could
have found it.** It took hashing every input, proving them equal, and only then suspecting the
interpreter.

**PWR status: UNVERIFIED — PWR has a `.venv`, and this is a practice risk, not a code one.** If any
PWR report, dashboard or notebook is ever produced with a different interpreter than its backtests,
the same divergence applies.

**Remedy — refuse to publish from the wrong interpreter:**

```python
def check_interpreter() -> str:
    import sys
    venv = REPO_ROOT / ".venv"
    if not venv.exists():
        return ""
    if Path(sys.prefix).resolve() == venv.resolve():
        return ""
    return (f"running under {sys.executable} (Python {sys.version.split()[0]}), not the project venv. "
            f"Numerical results DIFFER between stacks -- rebuild with .venv/bin/python.")
```

Call it in every artefact-producing entry point and hard-stop on a published path. Recording
`numpy.__version__` / `pandas.__version__` alongside each stored result is the cheaper half-measure.

---

## 4. A frozen price panel that is not what the run actually used

**PWR status: NOT APPLICABLE today** — PWR has no write-then-reuse price-panel pattern. Relevant only
if one is added.

GHR froze a panel with `to_csv` and handed workers the **in-memory** frame. `to_csv`/`read_csv` is not
lossless: a round-trip moves float64 closes by ~6e-14 (last-bit decimal repr). That is nothing on its
own and everything in a mean-variance optimizer — GHR's own notes record small close differences
flipping a knife-edge cell from $2,914 to $106,328. The stored sweep was therefore **unreproducible
from its own frozen panel**: $44,405 recorded, $40,498 on recompute.

**Remedy:** re-read the file immediately after writing it, so the fetch path is byte-identical to the
reuse path.

```python
panel.to_csv(path)
panel = pd.read_csv(path, index_col=0, parse_dates=True)   # use what is ON DISK
```

---

## 5. A stacked "residual" band labelled as a real category

**PWR status: WORTH CHECKING — any stacked chart computed as `total − attributed` is a candidate.**

GHR's allocation-by-event chart drew `value − everything attributed to a live event` and labelled it
**"anchors"**. It was anchors *plus* every dollar held while no attributing event was live. The band
read **17.3%** of the portfolio where the real anchor holding was **8.1%** — better than 2x wrong
about the same quantity, and the more interesting half (money still funded after the thesis was
retired) was invisible.

**Remedy:** compute named categories from their own data, never as a leftover; give the true residual
its own band with an honest label. If two panels report the same quantity, assert they agree — the
discrepancy is the bug signal.

---

## What generalises

Three of these produced **plausible output with no error**, and two were **invariant to every knob**,
so parameter sweeps could never surface them. The checks that did work:

1. **Cross-check the same quantity computed two ways.** Plot-9-vs-plot-10 and CBT-vs-sweep each
   exposed a real bug. Any figure derivable twice should be asserted equal.
2. **Hash the inputs before blaming the data.** Proving scans, config and panel identical is what
   forced the interpreter hypothesis.
3. **Suspect invariance.** A number that does not move when the knobs move is not robust; it is
   probably not being computed the way you think.
