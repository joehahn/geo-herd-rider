#!/usr/bin/env python3
"""escalator_ml.py — can a small classifier rank a month's shortlist by who ESCALATES next?

ESCALATOR = a name whose close runs >=1.5x its scan-day close within the next 21 bars (same test as
sweep_optimizer.py's escalator capture). The shortlist = every ticker the curation put on
`firehose_scans.csv` at that monthly scan. One row per (scan, ticker).

THREE FEATURE TIERS, nested, so each tier's contribution is visible on its own:
  A  price/volume      panel.csv + volume.csv, bars STRICTLY before the scan day. Clean.
  B  firehose coverage the journal's evscore counts (Python arithmetic) + event bookkeeping. Counts are
                       clean arithmetic, but over events an LLM defined -- so only mostly clean.
  C  LLM judgment      thesis_live, catalyst_resolved, exposure direction, milestone kind. Written by a
                       model whose training may postdate the scan -> SUSPECTED hindsight.
Models: M0 = trailing vol alone (the baseline the quiet floor already exploits), M1 = A, M2 = A+B,
M3 = A+B+C. L2 logistic; a depth-2 gradient-boosted tree as the nonlinearity cross-check.

VALIDATION. Expanding-window walk-forward by MONTH, first 12 scans train-only, one-scan embargo (a
21-bar label nearly fills the 30-day scan gap, so scan m-1's label is not known at scan m). Scored
WITHIN each month -- the book chooses inside a month's list, never across months.

PRE-REGISTERED PASS BAR (fixed 2026-09-25, before the first run; do not tune -- CLAUDE.md #5):
  a model PASSES iff its pooled within-month precision@K (K = CANON_BOOK max_watchlist = 20)
  (1) beats M0's with a within-month label-permutation p < 0.05, AND
  (2) still beats M0 with the largest theme cluster (crypto) removed.
Anything short of that is recorded as NO LIFT.

UPPER BOUND ONLY (CLAUDE.md #4, #7): one backtest curation, one view of it (#6). A pass earns a
forward test, not adoption.

    python scripts/escalator_ml.py [--run PATH] [--perms N]
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import provenance  # noqa: E402

warnings.filterwarnings("ignore")
HORIZON, THRESH, BURN_IN, SEED = 21, 1.5, 12, 7
K = int(provenance.CANON_BOOK["max_watchlist"])

# Theme clusters for the leave-one-theme-out check. Hand-listed from the escalator roster, not
# learned -- a cluster is a set of names that co-escalate on one macro driver.
THEMES = {
    "crypto": {"MARA", "RIOT", "CLSK", "COIN", "MSTR", "BTBT", "CIFR", "HUT", "WULF", "IREN", "BITF",
               "GBTC", "IBIT", "BITB", "ARKB", "BITC", "BMNR", "CORZ", "HIVE", "BTDR", "GLXY", "ARBK"},
    "quantum_space": {"QUBT", "RGTI", "IONQ", "QBTS", "ARQQ", "RKLB", "ASTS", "LUNR", "ACHR", "JOBY",
                      "RDW", "PL", "SPCE", "ARKX"},
}

A = ["vol60", "ret21", "ret63", "dd252", "maxret21", "log_dvol", "vol_surge", "penny"]
B = ["log_src", "velocity", "log_sup", "log_mentions", "ev_age", "tk_tenure", "is_opener", "log_ev_size"]
C = ["thesis_live", "catalyst_resolved", "dir_gains", "dir_loses", "ms_happened", "ms_expected"]
MODELS = {"M0_vol": ["vol60"], "M1_A": A, "M2_AB": A + B, "M3_ABC": A + B + C}


# --------------------------------------------------------------------------- dataset
def _price_feats(px: pd.Series, dv: pd.Series | None, day: pd.Timestamp) -> dict | None:
    """Tier A from bars strictly before `day`; label from the first bar >= day onward."""
    px = px.dropna()
    past, fut = px[px.index < day], px[px.index >= day]
    if len(past) < 40 or len(fut) < HORIZON + 1:
        return None
    r = past.pct_change().dropna()
    p0 = fut.iloc[0]
    f = {"vol60": r.iloc[-60:].std(),
         "ret21": past.iloc[-1] / past.iloc[-min(22, len(past))] - 1,
         "ret63": past.iloc[-1] / past.iloc[-min(64, len(past))] - 1,
         "dd252": past.iloc[-1] / past.iloc[-252:].max() - 1,
         "maxret21": r.iloc[-21:].max(),
         "penny": float(past.iloc[-1] < 5),
         "esc": bool(fut.iloc[1:HORIZON + 1].max() / p0 >= THRESH)}
    if dv is not None:
        d = dv[dv.index < day].dropna()
        f["log_dvol"] = np.log1p(d.iloc[-60:].median()) if len(d) >= 20 else np.nan
        f["vol_surge"] = (d.iloc[-5:].mean() / d.iloc[-60:].mean()) if len(d) >= 20 and d.iloc[-60:].mean() > 0 else np.nan
    return f


def build(run: Path) -> pd.DataFrame:
    scans = pd.read_csv(run / "firehose_scans.csv")
    ev = json.loads((run / "journal.json").read_text())["events"]
    by_cat = {e["catalyst"]: e for e in ev.values()}
    panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
    vol = pd.read_csv(run / "volume.csv", index_col=0, parse_dates=True)
    dvol = (panel * vol.reindex_like(panel))

    first_seen: dict[str, str] = {}
    rows = []
    for (week, tk), g in scans.groupby(["week", "ticker"], sort=True):
        first_seen.setdefault(tk, week)
        if tk not in panel:
            continue
        pf = _price_feats(panel[tk], dvol[tk] if tk in dvol else None, pd.Timestamp(week))
        if pf is None:
            continue
        # Tier B/C: a ticker under several events keeps the LOUDEST event's coverage (flagged).
        best = None
        for thesis in g.thesis:
            e = by_cat.get(thesis)
            if not e:
                continue
            ents = [x for x in e["entries"] if x["date"] <= week]
            if not ents:
                continue
            en = ents[-1]
            cov = en.get("coverage") or {}
            if best is None or cov.get("score", 0) > best[2].get("score", 0):
                best = (e, en, cov, len(ents))
        if best is None:
            continue
        e, en, cov, n_ent = best
        why = next((x["why"] for x in (en.get("exposure") or []) + (e.get("exposure") or [])
                    if x.get("ticker") == tk), "")
        ms = en.get("milestones") or []
        kind = ms[-1].get("kind", "") if ms else ""
        size = len(set(en.get("vehicles") or []) | {x.get("ticker") for x in en.get("exposure") or []} | {e.get("opened_on")})
        weeks_on = scans[(scans.ticker == tk) & (scans.week <= week)].week.nunique()
        rows.append({
            "week": week, "ticker": tk, "event": e["id"], "n_events": len(g),
            **pf,
            "log_src": np.log1p(cov.get("source_breadth", 0)),
            "velocity": float(np.clip(cov.get("velocity", 0.0), -1, 3)),
            "log_sup": np.log1p(cov.get("superlatives", 0)),
            "log_mentions": np.log1p(cov.get("mentions", 0)),
            "ev_age": n_ent, "tk_tenure": weeks_on,
            "is_opener": float(e.get("opened_on") == tk),
            "log_ev_size": np.log1p(size),
            "thesis_live": float(bool(en.get("thesis_live"))),
            "catalyst_resolved": float(bool(en.get("catalyst_resolved"))),
            "dir_gains": float(why.lower().startswith("gains")),
            "dir_loses": float(why.lower().startswith("loses")),
            "ms_happened": float(kind == "happened"),
            "ms_expected": float(kind not in ("", "happened")),
        })
    df = pd.DataFrame(rows)
    df["theme"] = df.ticker.map(lambda t: next((k for k, v in THEMES.items() if t in v), "other"))
    return df


# --------------------------------------------------------------------------- walk-forward
def _model(kind: str):
    if kind == "logit":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000, class_weight="balanced"))
    return HistGradientBoostingClassifier(max_depth=2, max_iter=150, learning_rate=0.05,
                                          l2_regularization=1.0, min_samples_leaf=40, random_state=SEED)


def walk_forward(df: pd.DataFrame, feats: list[str], kind: str = "logit") -> pd.Series:
    """Out-of-sample score for every row in a test month. Train = months <= m-2 (one-scan embargo)."""
    months = sorted(df.week.unique())
    out = pd.Series(np.nan, index=df.index)
    for i, m in enumerate(months):
        if i < BURN_IN:
            continue
        tr = df[df.week.isin(months[: i - 1])]
        te = df[df.week == m]
        if tr.esc.sum() < 5:
            continue
        X = tr[feats].fillna(tr[feats].median())
        mdl = _model(kind).fit(X, tr.esc)
        out.loc[te.index] = mdl.predict_proba(te[feats].fillna(tr[feats].median()))[:, 1]
    return out


def prec_at_k(df: pd.DataFrame, score: pd.Series, k: int = K) -> float:
    """Pooled within-month precision@k: escalators among each month's top-k, summed / slots used."""
    hit = n = 0
    for _, g in df.assign(s=score).dropna(subset=["s"]).groupby("week"):
        top = g.nlargest(min(k, len(g)), "s")
        hit += top.esc.sum(); n += len(top)
    return hit / n if n else np.nan


def month_auc(df: pd.DataFrame, score: pd.Series) -> float:
    a = [roc_auc_score(g.esc, g.s) for _, g in df.assign(s=score).dropna(subset=["s"]).groupby("week")
         if 0 < g.esc.sum() < len(g)]
    return float(np.mean(a)) if a else np.nan


def perm_p(df: pd.DataFrame, score: pd.Series, base: pd.Series, n: int, rng) -> float:
    """P(shuffled-within-month labels give model-minus-M0 precision gap >= observed)."""
    d = df.assign(s=score, b=base).dropna(subset=["s"])
    obs = prec_at_k(d, d.s) - prec_at_k(d, d.b)
    ge = 0
    for _ in range(n):
        dd = d.copy()
        dd["esc"] = dd.groupby("week").esc.transform(lambda x: rng.permutation(x.values))
        ge += (prec_at_k(dd, dd.s) - prec_at_k(dd, dd.b)) >= obs
    return (ge + 1) / (n + 1)


def boot_ci(df: pd.DataFrame, score: pd.Series, base: pd.Series, n: int, rng) -> tuple[float, float]:
    """Month-block bootstrap CI of the precision@K gap over M0."""
    d = df.assign(s=score, b=base).dropna(subset=["s"])
    months = d.week.unique()
    gaps = []
    for _ in range(n):
        pick = rng.choice(months, len(months), replace=True)
        bd = pd.concat([d[d.week == m].assign(week=f"{m}#{j}") for j, m in enumerate(pick)])
        gaps.append(prec_at_k(bd, bd.s) - prec_at_k(bd, bd.b))
    return tuple(np.percentile(gaps, [5, 95]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=provenance.CANON_RUN)
    ap.add_argument("--perms", type=int, default=1000)
    ap.add_argument("--out", default="data/escalator_ml")
    a = ap.parse_args()
    out = ROOT / a.out; out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)

    df = build(ROOT / a.run)
    df.to_csv(out / "dataset.csv", index=False)
    months = sorted(df.week.unique())
    test = df[df.week.isin(months[BURN_IN:])]
    print(f"run {a.run}: {len(df)} rows, {df.esc.sum()} escalators ({df.esc.mean():.1%}), "
          f"{len(months)} scans; test = {len(test)} rows / {test.esc.sum()} esc over "
          f"{len(months) - BURN_IN} months; K={K}")
    print(f"test base rate {test.esc.mean():.1%} (= precision@K of a random pick)\n")

    scores = {}
    for name, feats in MODELS.items():
        scores[name] = walk_forward(df, feats, "logit")
    for name in ("M2_AB", "M3_ABC"):
        scores[name + "_gbt"] = walk_forward(df, MODELS[name], "gbt")
    pd.DataFrame(scores).assign(week=df.week, ticker=df.ticker, esc=df.esc).to_csv(out / "oos_scores.csv", index=False)

    base = scores["M0_vol"]
    nocrypto = df.theme != "crypto"
    cutoff = months[BURN_IN + (len(months) - BURN_IN) // 2]
    early, late = df.week < cutoff, df.week >= cutoff
    lines = ["| model | P@K | gap vs M0 | 90% CI | perm p | AUC/mo | P@K no-crypto | gap no-crypto | P@K early | P@K late | PASS |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in scores.items():
        pk, pk0 = prec_at_k(df, s), prec_at_k(df, base)
        nc, nc0 = prec_at_k(df[nocrypto], s[nocrypto]), prec_at_k(df[nocrypto], base[nocrypto])
        if name == "M0_vol":
            p, ci, ok = np.nan, (np.nan, np.nan), "-"
        else:
            p = perm_p(df, s, base, a.perms, rng)
            ci = boot_ci(df, s, base, 500, rng)
            ok = "PASS" if (p < 0.05 and nc > nc0) else "no"
        lines.append(f"| {name} | {pk:.1%} | {pk - pk0:+.1%} | [{ci[0]:+.1%}, {ci[1]:+.1%}] | {p:.3f} | "
                     f"{month_auc(df, s):.3f} | {nc:.1%} | {nc - nc0:+.1%} | "
                     f"{prec_at_k(df[early], s[early]):.1%} | {prec_at_k(df[late], s[late]):.1%} | {ok} |")
    print("\n".join(lines))
    print(f"\n(early/late split at {cutoff})")

    # What the logistic actually leans on, fit on ALL rows -- descriptive only, not a test.
    for name in ("M2_AB", "M3_ABC"):
        X = df[MODELS[name]].fillna(df[MODELS[name]].median())
        m = _model("logit").fit(X, df.esc)
        coef = pd.Series(m[-1].coef_[0], index=MODELS[name]).sort_values(key=abs, ascending=False)
        print(f"\n{name} standardized coefficients (full-sample, descriptive):")
        print("  " + ", ".join(f"{k} {v:+.2f}" for k, v in coef.items()))
    (out / "report.md").write_text("\n".join(lines) + f"\n\nearly/late split at {cutoff}\n")


if __name__ == "__main__":
    main()
