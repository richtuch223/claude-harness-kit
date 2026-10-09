#!/usr/bin/env python
"""residual_mine.py -- free-data residual miner: abduction seeds from any held panel (GREEN, never reads an outcome).

Restores a residual-mining practice from an earlier project phase: a prior version had a hard-coded list
of forbidden outcome columns; this version makes that list the required --forbid-cols argument so the
firewall travels to a new panel/schema without a code edit.

Doctrine: model the best CHEAP baseline for every metric (size, price level, volume, trailing vol, calendar, category),
regress it out, and look at what is left. Stable residual structure is the map of unknown structure. The output is a
list of abduction SEEDS, each with a named surprise; a seed goes to `screen-proposal`, never straight to a test.

What it computes (all contemporaneous / descriptive; no forward or outcome column is ever read):
  1. baseline R^2 per target: how much of the metric the cheap shadow explains (high R^2 = shadowed, low = raw material)
  2. persistence: ICC (between-entity variance share) and split-half r of entity-mean residuals over even vs odd time
     indexes. A residual that is a stable ENTITY TRAIT is a seed; one that is noise is not.
  3. persistent outlier entities: entities whose mean residual is extreme in BOTH halves with the same sign
  4. time spikes: entity-demeaned time-mean residual (composition cannot manufacture a spike), min cross-section enforced
  5. cross-metric residual correlation: two metrics whose residuals co-move after the shadow is gone
  6. SEEDS.md: the ranked seeds with their named surprise and the counter-reading each must survive

Order of operations (the fleet contract): src/tools/instrument_check.py runs on every raw column the baselines and targets
consume, over every row, BEFORE any residual is computed. An INSTRUMENT_NULL aborts the run (NEEDS_DATA); REVIEW flags
are printed and carried into SEEDS.md.

Firewall (two layers, both fail-closed):
  - --forbid-cols is REQUIRED. Those columns are projected out on read (parquet) or dropped immediately after parse, and
    the run asserts they are absent. Pass `--forbid-cols NONE` only for a panel that has no outcome column at all; the
    choice is written into the manifest.
  - Any column whose NAME looks like an outcome (fwd, forward, future, return, ret_, pnl, profit, payout, settle, outcome,
    label, target, realized, resolution) is refused as a baseline, target or derive input unless named in --allow-name.
    --derive expressions are parsed for column references and checked the same way.

  python src/tools/residual_mine.py --input "<path or glob>" --entity product_id --time date \
      --forbid-cols NONE --derive "hl_spread=high_price/low_price" \
      --baselines log:market_price,cat:product_type,cat:category --targets log:hl_spread,lin:mid_over_mkt \
      --out <dir> [--era <col>] [--min-obs 5] [--min-entities 20] [--where "<pandas query>"]
  python src/tools/residual_mine.py --selftest

OLS is fit within each --era (default: one era) so an early residual never depends on a later regime's data. With no
--era the fit is whole-sample: acceptable for a descriptive mine (OLS on a non-outcome target can only remove structure),
and the manifest says so.
"""
from __future__ import annotations

import argparse, glob, hashlib, json, re, sys, tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instrument_check as ic  # noqa: E402

OUTCOME_NAME = re.compile(r"(?i)(fwd|forward|future|return|(^|_)ret(_|$)|pnl|profit|payout|settle|outcome|label|"
                          r"target|realized|resolution)")
CAT_MAX_LEVELS = 30
DRIFT_AC = 0.4   # median within-entity lag-1 AC of the residual above which persistence may be slow drift, not a trait
# Long panels (median span >= VR_MIN_SPAN obs): lag-1 AC cannot separate a stable trait with persistent mean-reverting noise
# from drift (both read high AC). There the drift label uses the pooled variance ratio
# VR(k) = mean (x_t - x_{t-k})^2 / (k * mean (x_t - x_{t-1})^2), k = median span // 4: ~1 for a random walk, well below 1
# for trait + stationary noise (calibration 2026-10-09: n=120 walk 0.93, trait+AR(0.9) 0.30, trait+AR(0.97) 0.67).
VR_MIN_SPAN = 36
DRIFT_VR = 0.8
# (calibrated in the selftest: a 12-step random walk reads ~0.56 after within-entity demeaning, a stable trait ~ -1/n)


# ----------------------------------------------------------------------------- reading + firewall

def _read_one(path: str, forbid: set[str]) -> pd.DataFrame:
    p = path.lower()
    if p.endswith(".parquet"):
        import pyarrow.parquet as pq
        names = pq.ParquetFile(path).schema_arrow.names
        return pd.read_parquet(path, columns=[c for c in names if c not in forbid])
    if p.endswith((".jsonl", ".jsonl.gz", ".ndjson")):
        df = pd.read_json(path, lines=True, compression="infer")
    elif p.endswith((".csv", ".csv.gz")):
        df = pd.read_csv(path, usecols=lambda c: c not in forbid, low_memory=False)
    elif p.endswith(".json"):
        df = pd.read_json(path)
    else:
        raise SystemExit(f"unsupported input type: {path}")
    return df.drop(columns=[c for c in df.columns if c in forbid])


def read_panel(pattern: str, forbid: set[str]) -> tuple[pd.DataFrame, list[str]]:
    paths = sorted(glob.glob(pattern)) or ([pattern] if Path(pattern).exists() else [])
    if not paths:
        raise SystemExit(f"no input matches {pattern}")
    df = pd.concat([_read_one(p, forbid) for p in paths], ignore_index=True)
    leaked = forbid & set(df.columns)
    assert not leaked, f"forbidden column present after read: {leaked}"
    return df, paths


def parse_specs(spec: str, kinds: set[str]) -> list[tuple[str, str]]:
    out = []
    for tok in [t.strip() for t in spec.split(",") if t.strip()]:
        kind, _, col = tok.partition(":")
        if kind not in kinds or not col:
            raise SystemExit(f"bad spec {tok!r}: use one of {sorted(kinds)} as kind:column")
        out.append((kind, col))
    return out


def refuse_outcome_names(cols: list[str], allow: set[str], what: str) -> None:
    bad = [c for c in cols if OUTCOME_NAME.search(c) and c not in allow]
    if bad:
        raise SystemExit(f"FIREWALL: {what} column(s) {bad} look like outcomes; pass --allow-name only if they are not")


EXPR_KEYWORDS = {"and", "or", "not", "in", "True", "False", "None"}


def expr_refs(expr: str, columns) -> list[str]:
    """The frame columns an eval/query expression reads, by WHITELIST .

    Every bare identifier must be a frame column or a boolean keyword, every backticked name must be a frame column,
    and `@` (pandas local-variable access, e.g. `@df.iloc[:, -1]`) is refused outright, as is attribute access. So no
    expression can read a column without naming it, and the returned list is exactly what it reads."""
    if "@" in expr:
        raise SystemExit(f"FIREWALL: '@' (local-variable access) is not allowed in an expression: {expr!r}")
    cols = set(map(str, columns))
    # round 4: the parser's placeholder names may not appear in the original text, so a real column with that name can
    # never be erased along with the placeholders (fail-closed)
    if re.search(r"BTCOL|STRLIT", expr):
        raise SystemExit(f"FIREWALL: 'BTCOL'/'STRLIT' are reserved by the expression parser: {expr!r}")
    # round 3: backticks FIRST (a quote inside a backticked name must not be read as a string literal), each replaced
    # by a placeholder identifier so a following '.' or '(' is still visible to the character whitelist below
    ticks = re.findall(r"`([^`]+)`", expr)
    body = re.sub(r"`[^`]+`", " BTCOL ", expr)
    body = re.sub(r"'[^'`]*'|\"[^\"`]*\"", " STRLIT ", body)   # then string literals ('Normal', "x")
    unknown_ticks = [t for t in ticks if t not in cols]
    if unknown_ticks:
        raise SystemExit(f"expression names unknown column(s) {unknown_ticks}: {expr!r}")
    # character whitelist: identifiers, numbers, whitespace, arithmetic/comparison/boolean operators, parentheses.
    # '.' is allowed only inside a number, so no attribute or method access (spread.shift(-1), spread.`shift`(-1));
    # no subscripts, slices, braces, quotes or statement separators survive.
    no_decimals = re.sub(r"(?<![A-Za-z0-9_])\d+\.\d*|\.\d+", " 0 ", body)
    bad = sorted(set(re.sub(r"[A-Za-z0-9_\s+\-*/%<>=!&|~(),]", "", no_decimals)))
    if bad:
        raise SystemExit(f"FIREWALL: character(s) {bad} not allowed in an expression (no attribute access, "
                         f"subscripts or quoting tricks): {expr!r}")
    if re.search(r"\bBTCOL\s*\(", body) or re.search(r"\bSTRLIT\b\s*\(", body):
        raise SystemExit(f"FIREWALL: calling a column is not allowed: {expr!r}")
    body = body.replace("BTCOL", " ").replace("STRLIT", " ")
    toks = re.findall(r"(?<![0-9.])[A-Za-z_][A-Za-z0-9_]*", body)
    unknown = sorted({t for t in toks if t not in cols and t not in EXPR_KEYWORDS})
    if unknown:
        raise SystemExit(f"expression uses name(s) that are not columns {unknown}: {expr!r}")
    return sorted(set(ticks) | {t for t in toks if t in cols})


def check_expr(expr: str, columns, allow: set[str], forbid: set[str], what: str) -> list[str]:
    refs = expr_refs(expr, columns)
    refuse_outcome_names(refs, allow, what)
    if forbid & set(refs):
        raise SystemExit(f"FIREWALL: {what} references forbidden column(s) {sorted(forbid & set(refs))}")
    return refs


def apply_derives(df: pd.DataFrame, derives: list[str], allow: set[str], forbid: set[str]) -> list[str]:
    """name=expr via DataFrame.eval. Returns the raw columns the expressions consumed. A derive may not overwrite an
    existing column ."""
    used = []
    original = set(map(str, df.columns))
    for d in derives:
        name, _, expr = d.partition("=")
        name, expr = name.strip(), expr.strip()
        if not name or not expr:
            raise SystemExit(f"bad --derive {d!r}: use name=expr")
        if name in df.columns:
            raise SystemExit(f"--derive {name!r} would overwrite an existing column; pick a new name")
        refuse_outcome_names([name], allow, f"derive {name!r}")
        refs = check_expr(expr, df.columns, allow, forbid, f"derive {name!r}")
        df[name] = df.eval(expr, local_dict={}, global_dict={})
        used += [r for r in refs if r in original]
    return used


# ----------------------------------------------------------------------------- instrument check

def run_instrument_check(df: pd.DataFrame, cols: list[str], entity: str, time: str) -> tuple[list, list]:
    nulls, reviews = [], []
    for c in cols:
        if c not in df.columns:
            nulls.append((c, "MISSING_FIELD"))
            continue
        if not pd.api.types.is_numeric_dtype(df[c]):
            share = float(df[c].isna().mean()) if len(df) else 1.0
            if share > 0.5:
                nulls.append((c, f"NULL_SHARE {share:.1%} of rows missing on a categorical column"))
            elif share > 0:
                reviews.append((c, f"NULL_SHARE {share:.1%} of rows missing on a categorical column (rows dropped)"))
            continue
        verdict, flags = ic.verdict_for(ic.check(df, c, key_col=entity, order_col=None))
        for f in flags:
            (nulls if verdict == "INSTRUMENT_NULL" else reviews).append((c, f"{f.name} {f.count}/{f.denom}: {f.detail}"[:200]))
    return nulls, reviews


# ----------------------------------------------------------------------------- core mine

def _time_numeric(t: pd.Series) -> pd.Series:
    """Time as a number for spacing checks: datetimes -> days, numbers as-is; anything else -> NaN (spacing unchecked)."""
    if pd.api.types.is_numeric_dtype(t):
        return t.astype(float)
    try:
        dt = pd.to_datetime(t.astype(str), errors="coerce")
    except (TypeError, ValueError):
        return pd.Series(np.nan, index=t.index)
    return (dt - pd.Timestamp("1970-01-01")).dt.total_seconds() / 86400.0


def _safelog(x):
    return np.log(np.clip(np.asarray(x, dtype=float), 1e-12, None))


def design_matrix(df: pd.DataFrame, baselines: list[tuple[str, str]]) -> np.ndarray:
    parts = []
    for kind, col in baselines:
        if kind == "cat":
            s = df[col].astype(str)
            top = s.value_counts().index[:CAT_MAX_LEVELS]
            s = s.where(s.isin(top), "__other__")
            parts.append(pd.get_dummies(s, drop_first=True, dtype=float).values)
        else:
            v = _safelog(df[col]) if kind == "log" else df[col].astype(float).values
            v = (v - np.nanmean(v)) / (np.nanstd(v) + 1e-12)
            parts.append(v.reshape(-1, 1))
    parts.append(np.ones((len(df), 1)))
    return np.column_stack(parts)


def mine(df: pd.DataFrame, entity: str, time: str, baselines, targets, era: str | None,
         min_obs: int, min_entities: int) -> dict:
    base_cols = [c for _, c in baselines]
    n_in = len(df)
    df = df.dropna(subset=base_cols + [entity, time] + ([era] if era else [])).reset_index(drop=True)
    n_dropped = n_in - len(df)
    # gate #5: the panel grain is one row per (entity, time); duplicates fake split-half persistence, so refuse them
    df[entity] = df[entity].astype(str)        # round-2 gate #2: validate on the key representation used downstream
    dup = int(df.duplicated(subset=[entity, time]).sum())
    if dup:
        raise SystemExit(f"GRAIN: {dup} duplicate ({entity}, {time}) rows; dedupe upstream or narrow with --where")
    eras = df[era].astype(str).values if era else np.array(["all"] * len(df))
    # gate #3: category levels and scaling are chosen WITHIN each era, so later data never changes an earlier design
    X_by_era = {e: design_matrix(df[eras == e].reset_index(drop=True), baselines) for e in np.unique(eras)}
    res = pd.DataFrame({"entity": df[entity].astype(str).values, "time": df[time].values, "era": eras})
    r2 = {}
    for kind, col in targets:
        y = _safelog(df[col]) if kind == "log" else df[col].astype(float).values
        fin = np.isfinite(y)
        if kind == "log":
            fin &= df[col].astype(float).values > 0
        r = np.full(len(df), np.nan)
        for e in np.unique(eras):
            idx = np.flatnonzero(eras == e)
            Xe = X_by_era[e]
            m = fin[idx] & np.isfinite(Xe).all(axis=1)
            if m.sum() < max(50, Xe.shape[1] * 5):
                continue
            beta, *_ = np.linalg.lstsq(Xe[m], y[idx][m], rcond=None)
            r[idx[m]] = y[idx][m] - Xe[m] @ beta
        sd = pd.Series(r).groupby(pd.Series(eras)).transform("std").values
        res["res__" + col] = r / (sd + 1e-12)
        ok = np.isfinite(r)
        r2[col] = float(1 - np.nanvar(r[ok]) / (np.nanvar(y[ok]) + 1e-18)) if ok.sum() > 2 else float("nan")
    rcols = [c for c in res.columns if c.startswith("res__")]
    res = res.sort_values(["entity", "time"]).reset_index(drop=True)

    persist, outliers = [], []
    for c in rcols:
        sub = res[["entity", "time", c]].dropna()
        sub = sub[sub.groupby("entity")[c].transform("size") >= min_obs].copy()
        n_ent = sub["entity"].nunique()
        if n_ent < min_entities:
            persist.append({"metric": c[5:], "n_entities": n_ent, "icc": np.nan, "splithalf_r": np.nan,
                            "block_r": np.nan, "lag1_ac": np.nan, "var_ratio": np.nan, "median_span": np.nan,
                            "frozen_share": np.nan})
            continue
        g = sub.groupby("entity")[c]
        mean, n, grand = g.mean(), g.size(), sub[c].mean()
        icc = float((n * (mean - grand) ** 2).sum() / (((sub[c] - grand) ** 2).sum() + 1e-18))
        sub["half"] = sub.groupby("entity").cumcount() % 2
        halves = sub.groupby(["entity", "half"])[c].mean().unstack()
        halves = halves.dropna()
        sh = float(halves[0].corr(halves[1])) if len(halves) >= min_entities else np.nan
        # block split (early vs late half of each entity's own time series): parity halves interleave adjacent
        # points, so on a slow-moving panel they measure stickiness, not a trait. A trait must survive the block split.
        k = sub.groupby("entity").cumcount()
        size = sub.groupby("entity")[c].transform("size")
        sub["block"] = (k >= size / 2).astype(int)
        blocks = sub.groupby(["entity", "block"])[c].mean().unstack().dropna()
        br = float(blocks[0].corr(blocks[1])) if len(blocks) >= min_entities else np.nan
        # median within-entity lag-1 autocorrelation of the residual: near 1 means the series is near-static over the
        # span, so ANY split looks persistent and the span is too short to tell a trait from a stale quote
        lagged = sub.groupby("entity")[c].shift(1)
        dm = sub[c] - sub.groupby("entity")[c].transform("mean")
        dml = lagged - sub.groupby("entity")[c].transform("mean")
        num = (dm * dml).groupby(sub["entity"]).sum()
        den = (dm ** 2).groupby(sub["entity"]).sum()
        ac = (num / den.replace(0, np.nan)).dropna()
        lag1 = float(ac.median()) if len(ac) else np.nan
        # entities whose residual never moved over the span: a frozen quote looks exactly like a stable trait
        frozen = float((sub.groupby("entity")[c].std() < 1e-9).mean())
        # variance ratio at k = median span // 4, computed PER ENTITY and summarized by the median . Eligible
        # entities: at least 2k+1 observations, regular spacing (no step > 1.5x the entity's median step, so a calendar
        # gap is never compressed into one lag), and non-zero one-step variance (a constant entity has no defined VR).
        # Fewer than min_entities eligible -> VR undefined (NaN) and the short-panel lag-1 rule decides.
        med_span = int(size.groupby(sub["entity"]).first().median())
        vr = np.nan
        if med_span >= VR_MIN_SPAN:
            kk = max(2, med_span // 4)
            tnum = _time_numeric(sub["time"])
            vrs = []
            for _, ge in sub.assign(_tn=tnum).groupby("entity"):
                y = ge[c].to_numpy(dtype=float)
                if len(y) < 2 * kk + 1:
                    continue
                if ge["_tn"].notna().all():
                    steps = np.diff(ge["_tn"].to_numpy(dtype=float))
                    med_step = np.median(steps)
                    if med_step <= 0 or np.any(steps > 1.5 * med_step):
                        continue
                d1 = np.diff(y)
                v1 = np.mean(d1 ** 2)
                if not np.isfinite(v1) or v1 <= 0:
                    continue
                dk = y[kk:] - y[:-kk]
                vrs.append(np.mean(dk ** 2) / (kk * v1))
            if len(vrs) >= min_entities:
                vr = float(np.median(vrs))
        persist.append({"metric": c[5:], "n_entities": n_ent, "icc": icc, "splithalf_r": sh,
                        "block_r": br, "lag1_ac": lag1, "var_ratio": vr, "median_span": med_span,
                        "frozen_share": frozen})
        # persistent outliers: |z| of entity mean beyond 2.5 SD of entity means in BOTH halves, same sign
        z0 = (halves[0] - halves[0].mean()) / (halves[0].std() + 1e-12)
        z1 = (halves[1] - halves[1].mean()) / (halves[1].std() + 1e-12)
        both = halves[(z0.abs() > 2.5) & (z1.abs() > 2.5) & (np.sign(z0) == np.sign(z1))]
        for ent in both.index:
            outliers.append({"metric": c[5:], "entity": ent, "z_even": round(float(z0[ent]), 2),
                             "z_odd": round(float(z1[ent]), 2), "n_obs": int(n[ent])})
    persist = pd.DataFrame(persist).sort_values("block_r", ascending=False, na_position="last")
    def _status(a, b, v, span):
        long_panel = np.isfinite(span) and span >= VR_MIN_SPAN and np.isfinite(v)
        drifting = (v >= DRIFT_VR) if long_panel else (np.isfinite(a) and a >= DRIFT_AC)
        if drifting:
            return ("DRIFTING (variance ratio near 1: random-walk-like)" if long_panel else
                    "DRIFTING (slow drift makes any split look persistent; span too short to call a trait)")
        return "SEED" if np.isfinite(b) and b >= 0.5 else ""
    persist["status"] = [_status(a, b, v, sp) for a, b, v, sp in
                         zip(persist["lag1_ac"], persist["block_r"], persist["var_ratio"], persist["median_span"])]
    outliers = pd.DataFrame(outliers, columns=["metric", "entity", "z_even", "z_odd", "n_obs"])

    rd = res.copy()
    for c in rcols:
        rd[c] = rd[c] - rd.groupby("entity")[c].transform("mean")
    spikes = {}
    for c in rcols:
        # gate #4: the minimum cross-section counts entities with a VALID residual for THIS metric
        vc = rd[["time", "entity", c]].dropna()
        t_n = vc.groupby("time")["entity"].nunique()
        s = vc[vc["time"].isin(t_n[t_n >= min_entities].index)].groupby("time")[c].mean()
        if len(s) < 3:
            continue
        z = (s - s.mean()) / (s.std() + 1e-12)
        top = z.abs().sort_values(ascending=False).head(5)
        spikes[c[5:]] = {(str(k.date()) if isinstance(k, pd.Timestamp) and k == k.normalize() else str(k)):
                         round(float(z[k]), 2) for k in top.index}

    corr = res[rcols].corr(min_periods=50) if len(rcols) > 1 else pd.DataFrame()
    pairs = []
    for i, a in enumerate(rcols):
        for b in rcols[i + 1:]:
            v = corr.loc[a, b] if not corr.empty else np.nan
            if np.isfinite(v):
                pairs.append((a[5:], b[5:], float(v)))
    pairs.sort(key=lambda t: -abs(t[2]))
    return {"residuals": res, "r2": r2, "persistence": persist, "outliers": outliers, "spikes": spikes,
            "pairs": pairs, "n_rows": len(df), "n_dropped_missing": n_dropped, "n_entities": res["entity"].nunique(), "n_times": res["time"].nunique()}


def write_seeds(out: Path, m: dict, reviews: list, manifest: dict) -> str:
    L = ["# Residual-mine abduction seeds", "",
         f"Input: `{manifest['inputs'][0]}`" + (f" (+{len(manifest['inputs']) - 1} files)" if len(manifest['inputs']) > 1 else ""),
         f"Rows {m['n_rows']:,} ({m['n_dropped_missing']:,} dropped for a missing baseline/key) · entities {m['n_entities']:,} · time points {m['n_times']:,} · eras: {manifest['era'] or 'one (whole-sample fit)'}",
         f"Forbidden columns: {manifest['forbid_cols']}", "",
         "Every seed below is a named surprise for `screen-proposal`, NOT a finding. Each must survive its counter-reading",
         "(a missing baseline, a data artifact, or composition) before anyone designs a test.", ""]
    if reviews:
        L += ["## Instrument REVIEW flags (resolve before trusting any seed on these columns)", ""]
        L += [f"- `{c}`: {msg}" for c, msg in reviews] + [""]
    L += ["## Baseline R^2 (high = the metric is the shadow; low = raw material)", ""]
    L += [f"- `{k}`: {v:.3f}" for k, v in m["r2"].items()] + [""]
    L += ["## Persistent residuals (entity trait after the shadow is removed)", "",
          "| metric | entities | ICC | parity split r | block split r | lag-1 AC | var ratio | frozen share | status |",
          "|---|---|---|---|---|---|---|---|---|"]
    for _, r in m["persistence"].iterrows():
        L.append(f"| {r['metric']} | {r['n_entities']} | {r['icc']:.3f} | {r['splithalf_r']:.3f} | {r['block_r']:.3f} | "
                 f"{r['lag1_ac']:.3f} | {r['var_ratio']:.3f} | {r['frozen_share']:.1%} | {r['status']} |")
    L += ["", "Counter-reading for any SEED: a baseline we did not include (product type, venue, listing age) carries it.",
          "A high frozen share means many entities never changed over the span: a stale quote reads as a stable trait.",
          "Name that baseline and re-run with it before calling the residual a surprise.", ""]
    L += ["## Persistent outlier entities (|z| > 2.5 in both halves, same sign)", ""]
    if len(m["outliers"]):
        top = m["outliers"].reindex(m["outliers"][["z_even", "z_odd"]].abs().min(axis=1).sort_values(ascending=False).index)
        L += [f"- `{r.metric}` · entity `{r.entity}` · z {r.z_even:+.2f} / {r.z_odd:+.2f} · n={r.n_obs}" for r in top.head(25).itertuples()]
        L += ["", "Counter-reading: a mislabeled or stale row (check the raw rows for the entity before anything else)."]
    else:
        L.append("- none")
    L += ["", "## Time spikes (entity-demeaned time-mean residual, z)", ""]
    L += [f"- `{k}`: " + ", ".join(f"{t} ({z:+.2f})" for t, z in v.items()) for k, v in m["spikes"].items()]
    L += ["", "Counter-reading: a recorder outage, a vendor refresh, or a calendar effect the baselines lack.", "",
          "## Cross-metric residual correlation (strongest pairs)", ""]
    L += [f"- `{a}` x `{b}`: {v:+.3f}" for a, b, v in m["pairs"][:10]] or ["- n/a"]
    L += ["", "Counter-reading: two metrics built from the same raw field co-move by construction.", ""]
    text = "\n".join(L) + "\n"
    (out / "SEEDS.md").write_text(text, encoding="utf-8")
    return text


def run(args) -> int:
    forbid_raw = args.forbid_cols.strip()
    forbid = set() if forbid_raw.upper() == "NONE" else {c.strip() for c in forbid_raw.split(",") if c.strip()}
    allow = {c.strip() for c in (args.allow_name or "").split(",") if c.strip()}
    baselines = parse_specs(args.baselines, {"log", "lin", "cat"})
    targets = parse_specs(args.targets, {"log", "lin"})
    refuse_outcome_names([args.entity, args.time] + ([args.era] if args.era else []), allow, "entity/time/era")
    tcols = [c for _, c in targets]
    if len(set(tcols)) != len(tcols):
        raise SystemExit(f"each target column may appear once (two transforms would overwrite one residual): {tcols}")
    df, paths = read_panel(args.input, forbid)
    if forbid & {args.entity, args.time, args.era}:
        raise SystemExit("FIREWALL: entity/time/era is a forbidden column")
    if args.where:
        check_expr(args.where, df.columns, allow, forbid, "--where")
        df = df.query(args.where, local_dict={}, global_dict={}).copy()
    derived_inputs = apply_derives(df, args.derive or [], allow, forbid)
    model_cols = [c for _, c in baselines] + [c for _, c in targets]
    refuse_outcome_names(model_cols, allow, "baseline/target")
    if forbid & set(model_cols):
        raise SystemExit(f"FIREWALL: forbidden column(s) used in the model: {forbid & set(model_cols)}")
    derived_names = {d.partition("=")[0].strip() for d in (args.derive or [])}
    raw_cols = sorted(set([c for c in model_cols if c not in derived_names] + derived_inputs))
    nulls, reviews = run_instrument_check(df, raw_cols, args.entity, args.time)
    if nulls:
        print("INSTRUMENT_NULL -> NEEDS_DATA; no residual computed:")
        for c, msg in nulls:
            print(f"  {c}: {msg}")
        return 2
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    m = mine(df, args.entity, args.time, baselines, targets, args.era, args.min_obs, args.min_entities)
    manifest = {"inputs": paths, "input_sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths[:50]},
                "forbid_cols": sorted(forbid) or "NONE (declared: panel has no outcome column)", "allow_name": sorted(allow),
                "entity": args.entity, "time": args.time, "era": args.era, "where": args.where,
                "derive": args.derive, "baselines": args.baselines, "targets": args.targets,
                "min_obs": args.min_obs, "min_entities": args.min_entities, "instrument_reviews": reviews}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    m["residuals"].to_parquet(out / "residuals.parquet")
    m["persistence"].to_csv(out / "persistence.csv", index=False)
    m["outliers"].to_csv(out / "outliers.csv", index=False)
    (out / "time_spikes.json").write_text(json.dumps(m["spikes"], indent=2), encoding="utf-8")
    (out / "baseline_r2.json").write_text(json.dumps(m["r2"], indent=2), encoding="utf-8")
    print(write_seeds(out, m, reviews, manifest))
    return 0


# ----------------------------------------------------------------------------- selftest

def selftest() -> int:
    ok = True
    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    rng = np.random.default_rng(7)
    n_ent, n_t = 200, 12
    ent = np.repeat(np.arange(n_ent), n_t)
    t = np.tile(np.arange(n_t), n_ent)
    size = np.exp(rng.normal(3, 1, n_ent))[ent]
    trait = rng.normal(0, 1, n_ent)[ent]               # a hidden stable entity trait
    df = pd.DataFrame({"ent": ent, "t": t, "size": size, "cat": np.where(ent % 3 == 0, "a", "b"),
                       "spread": np.exp(-0.5 * np.log(size) + 0.4 * trait + rng.normal(0, 0.3, len(ent))),
                       "noise": np.exp(-0.5 * np.log(size) + rng.normal(0, 0.3, len(ent))),
                       "fwd_ret_7d": rng.normal(0, 1, len(ent))})
    df.loc[df["ent"] == 5, "spread"] *= 40             # one persistent outlier entity
    df.loc[df["t"] == 7, "noise"] *= 3                  # one market-wide time spike
    tmp = Path(tempfile.mkdtemp())
    df.to_parquet(tmp / "panel.parquet")
    m = mine(df.drop(columns=["fwd_ret_7d"]), "ent", "t", [("log", "size"), ("cat", "cat")],
             [("log", "spread"), ("log", "noise")], None, 5, 20)
    p = m["persistence"].set_index("metric")
    check("planted entity trait is persistent (split-half r > 0.5)", p.loc["spread", "splithalf_r"] > 0.5)
    check("pure-noise residual is not persistent (split-half r < 0.3)", p.loc["noise", "splithalf_r"] < 0.3)
    check("planted trait survives the block split and is labeled SEED", p.loc["spread", "status"] == "SEED")
    walk = df.copy()
    walk["spread"] = np.exp(np.concatenate([np.cumsum(rng.normal(0, 1, n_t)) for _ in range(n_ent)]))
    mw = mine(walk.drop(columns=["fwd_ret_7d"]), "ent", "t", [("log", "size"), ("cat", "cat")],
              [("log", "spread")], None, 5, 20)
    pw = mw["persistence"].set_index("metric")
    check("a random-walk residual is labeled DRIFTING, not SEED", pw.loc["spread", "status"].startswith("DRIFTING"))
    # long panels: trait + AR(0.9) noise must read SEED (variance ratio), a long random walk DRIFTING
    n_l, t_l = 150, 120
    ent_l = np.repeat(np.arange(n_l), t_l); tt_l = np.tile(np.arange(t_l), n_l)
    noise = np.zeros((n_l, t_l))
    for t in range(1, t_l):
        noise[:, t] = 0.9 * noise[:, t - 1] + rng.normal(0, 0.44, n_l)
    trait_l = rng.normal(0, 1, (n_l, 1))
    size_l = np.exp(rng.normal(3, 1, n_l))[ent_l]
    long_trait = pd.DataFrame({"ent": ent_l, "t": tt_l, "size": size_l, "y": (trait_l + noise).ravel()})
    long_walk = long_trait.assign(y=np.cumsum(rng.normal(0, 1, (n_l, t_l)), axis=1).ravel())
    pl = mine(long_trait, "ent", "t", [("log", "size")], [("lin", "y")], None, 5, 20)["persistence"].set_index("metric")
    pwl = mine(long_walk, "ent", "t", [("log", "size")], [("lin", "y")], None, 5, 20)["persistence"].set_index("metric")
    check("long panel: trait + AR(0.9) noise reads SEED via the variance ratio (lag-1 AC alone would call it drift)",
          pl.loc["y", "status"] == "SEED" and pl.loc["y", "lag1_ac"] >= DRIFT_AC)
    check("long panel: a random walk reads DRIFTING via the variance ratio", pwl.loc["y", "status"].startswith("DRIFTING"))
    # gate r5 #1/#3: constant entities never make VR zero or defined
    const = long_trait.assign(y=np.repeat(rng.normal(0, 1, n_l), t_l))
    pc = mine(const, "ent", "t", [("log", "size")], [("lin", "y")], None, 5, 20)["persistence"].set_index("metric")
    check("gate r5: an all-constant long panel has an undefined variance ratio, not 0", not np.isfinite(pc.loc["y", "var_ratio"]))
    # gate r5 #2: a random walk with one long calendar gap per entity is excluded from VR, never read as a stable trait
    gap_walk = long_walk.copy()
    gap_walk["t"] = gap_walk["t"] + np.where(gap_walk["t"] >= 1, 500, 0)
    pg = mine(gap_walk, "ent", "t", [("log", "size")], [("lin", "y")], None, 5, 20)["persistence"].set_index("metric")
    check("gate r5: gapped random walks are not read as SEED", pg.loc["y", "status"] != "SEED")
    check("planted outlier entity is found in both halves", "5" in set(m["outliers"].query("metric == 'spread'")["entity"]))
    check("planted time spike ranks first for its metric", list(m["spikes"]["noise"])[0] == "7")
    check("size shadow explains part of the noise metric (R^2 > 0.5)", m["r2"]["noise"] > 0.5)
    rd, _ = read_panel(str(tmp / "panel.parquet"), {"fwd_ret_7d"})
    check("forbidden column projected out on parquet read", "fwd_ret_7d" not in rd.columns)
    def raises(fn):
        try:
            fn(); return False
        except SystemExit:
            return True
    check("outcome-looking target name is refused", raises(lambda: refuse_outcome_names(["fwd_ret_7d"], set(), "t")))
    check("derive referencing a forbidden column is refused",
          raises(lambda: apply_derives(df.copy(), ["x=fwd_ret_7d*2"], set(), {"fwd_ret_7d"})))
    check("derive referencing an outcome-looking name is refused even when not forbidden",
          raises(lambda: apply_derives(df.copy(), ["x=fwd_ret_7d*2"], set(), set())))
    check("--allow-name lets a non-outcome lookalike through",
          not raises(lambda: refuse_outcome_names(["target_price"], {"target_price"}, "t")))
    check("gate #2: a backticked outcome column in a derive is refused",
          raises(lambda: apply_derives(df.assign(**{"fwd-return": 1.0}), ["x=`fwd-return`*2"], set(), set())))
    check("gate r2 #1: '@df.iloc' in a derive is refused",
          raises(lambda: apply_derives(df.copy(), ["x=@df.iloc[:, -1]"], set(), set())))
    check("gate r2 #1: attribute access in a derive is refused",
          raises(lambda: apply_derives(df.copy(), ["x=spread.shift(-1)"], set(), set())))
    mixed = df.drop(columns=["fwd_ret_7d"]).copy()
    mixed = pd.concat([mixed.assign(ent=mixed["ent"].astype(object)), mixed.assign(ent=mixed["ent"].astype(str))])
    check("gate r2 #2: int 1 and string '1' entity copies are refused as duplicates",
          raises(lambda: mine(mixed, "ent", "t", [("log", "size")], [("log", "spread")], None, 5, 20)))
    two = df.assign(target=1.0, target_price=df["size"])
    check("gate r2 #3: an allowed column is not blocked by an unused column that is its prefix",
          not raises(lambda: apply_derives(two, ["x=target_price*2"], {"target_price"}, set())))
    q = df.assign(**{"price'future'": 1.0, "shift": 1.0})
    check("gate r3 #1: a quote inside a backticked outcome name cannot hide it",
          raises(lambda: apply_derives(q.copy(), ["x=`price'future'` * 1"], set(), set())))
    check("gate r3 #2: backticked method access is refused",
          raises(lambda: apply_derives(q.copy(), ["x=spread.`shift`(-1)"], set(), set())))
    check("method access after a digit-ending column name is refused",
          raises(lambda: apply_derives(df.assign(a12=1.0, shift=1.0), ["x=a12.shift(-1)"], set(), set())))
    check("gate r4: a real column named like a parser placeholder cannot be read unreported",
          raises(lambda: apply_derives(df.assign(BTCOL=1.0), ["x=BTCOL"], set(), set())))
    check("subscripts are refused", raises(lambda: apply_derives(df.copy(), ["x=spread[1]"], set(), set())))
    check("decimals and comparisons still parse", expr_refs("size > 1.5 and spread <= .25", df.columns) == ["size", "spread"])
    check("string literals in --where are not read as column names",
          expr_refs("cat == 'a' and size > 1", df.columns) == ["cat", "size"])
    check("gate #8: a derive cannot overwrite an existing column",
          raises(lambda: apply_derives(df.copy(), ["size=spread*2"], set(), set())))
    dupd = pd.concat([df, df]).drop(columns=["fwd_ret_7d"])
    check("gate #5: duplicate (entity, time) rows are refused",
          raises(lambda: mine(dupd, "ent", "t", [("log", "size")], [("log", "spread")], None, 5, 20)))
    def run_args(**kw):
        base = dict(input=str(tmp / "panel.parquet"), forbid_cols="NONE", allow_name=None, baselines="log:size",
                    targets="log:spread", derive=None, where=None, entity="ent", time="t", era=None, min_obs=5,
                    min_entities=20, out=str(tmp / "out_gate"))
        base.update(kw)
        return lambda: run(argparse.Namespace(**base))
    check("gate #1: --where on an outcome column is refused", raises(run_args(where="fwd_ret_7d > 0")))
    check("gate r2 #1: '@df.iloc' in --where is refused", raises(run_args(where="@df.iloc[:, -1] > 0")))
    check("gate #1: --era on an outcome-looking column is refused", raises(run_args(era="fwd_ret_7d")))
    check("gate #7: two transforms of one target are refused", raises(run_args(targets="log:spread,lin:spread")))
    era_df = df.drop(columns=["fwd_ret_7d"]).assign(era=np.where(df["t"] < 6, "e1", "e2"),
                                                     cat2=np.where(df["t"] < 6, "a", np.where(df["ent"] % 2, "x", "y")))
    m_full = mine(era_df, "ent", "t", [("log", "size"), ("cat", "cat2")], [("log", "spread")], "era", 3, 20)
    m_early = mine(era_df[era_df["era"] == "e1"], "ent", "t", [("log", "size"), ("cat", "cat2")], [("log", "spread")],
                   "era", 3, 20)
    a = m_full["residuals"].query("era == 'e1'").sort_values(["entity", "time"])["res__spread"].values
    b = m_early["residuals"].sort_values(["entity", "time"])["res__spread"].values
    check("gate #3: adding a later era leaves earlier-era residuals unchanged", np.allclose(a, b, equal_nan=True))
    bad = df.copy(); bad["size"] = 25.0
    nulls, _ = run_instrument_check(bad, ["size"], "ent", "t")
    check("instrument_check NULL on a constant baseline aborts the run", len(nulls) >= 1)
    rc = run(argparse.Namespace(input=str(tmp / "panel.parquet"), forbid_cols="fwd_ret_7d", allow_name=None,
                                baselines="log:size,cat:cat", targets="log:spread,log:noise", derive=["hl=spread/noise"],
                                where=None, entity="ent", time="t", era=None, min_obs=5, min_entities=20,
                                out=str(tmp / "out")))
    check("end-to-end run writes SEEDS.md and manifest", rc == 0 and (tmp / "out" / "SEEDS.md").exists()
          and "fwd_ret_7d" in (tmp / "out" / "manifest.json").read_text())
    print("residual_mine selftest: " + ("all checks passed" if ok else "FAILED"))
    return 0 if ok else 1


def main(argv=None) -> int:
    if argv is None and "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", required=True)
    ap.add_argument("--entity", required=True)
    ap.add_argument("--time", required=True)
    ap.add_argument("--forbid-cols", required=True, help="comma list projected out on read, or NONE")
    ap.add_argument("--allow-name", help="comma list of outcome-lookalike names that are NOT outcomes")
    ap.add_argument("--baselines", required=True, help="kind:col list, kind in log/lin/cat")
    ap.add_argument("--targets", required=True, help="kind:col list, kind in log/lin")
    ap.add_argument("--derive", action="append", help="name=expr (pandas eval), repeatable")
    ap.add_argument("--where", help="pandas query applied after read")
    ap.add_argument("--era")
    ap.add_argument("--min-obs", type=int, default=5)
    ap.add_argument("--min-entities", type=int, default=20)
    ap.add_argument("--out", required=True)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
