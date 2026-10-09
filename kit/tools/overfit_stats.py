#!/usr/bin/env python
"""overfit_stats.py -- multiple-testing / overfitting correction for backtest Sharpe ratios.

Two formal numbers a strategy-review flagged as things a research gate otherwise handles by
judgment but can compute mechanically:

1. DEFLATED SHARPE RATIO (DSR). Source: Bailey, D.H. and Lopez de Prado, M. (2014), "The Deflated
   Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality", Journal of
   Portfolio Management 40(5). DSR answers "given that this Sharpe ratio was the BEST of N trials,
   how much of it is real?" by comparing the observed SR against the Sharpe ratio EXPECTED to arise
   from pure luck alone, given N independent trials of variance V, then expressing the gap as a
   probability (the probabilistic Sharpe ratio, PSR) that accounts for the return series' own
   skewness and kurtosis (Mertens 2002 / Lo 2002 non-normal Sharpe-ratio variance).
     E[max SR | N, V]  ~=  sqrt(V) * [ (1-gamma)*Z^-1(1 - 1/N) + gamma*Z^-1(1 - 1/(N*e)) ]
         (gamma = Euler-Mascheroni constant ~ 0.5772; Z^-1 = inverse standard normal CDF)
     sigma_hat(SR)      =  sqrt( (1 - skew*SR + (kurtosis-1)/4 * SR^2) / (n-1) )
         (kurtosis here is the RAW kurtosis, normal = 3, not excess kurtosis; sigma_hat(SR)
         already contains the 1/sqrt(n-1) sample-size term)
     DSR = PSR(SR0)      =  Phi( (SR_hat - SR0) / sigma_hat(SR_hat) )
   DSR close to 1 means the observed SR clears the luck benchmark; DSR ~ 0.5 means it is
   indistinguishable from the best of N noise draws.

2. PROBABILITY OF BACKTEST OVERFITTING (PBO) via Combinatorially Symmetric Cross-Validation
   (CSCV). Source: Bailey, D.H., Borwein, J., Lopez de Prado, M., and Zhu, Q.J. (2017), "The
   Probability of Backtest Overfitting", Journal of Computational Finance 20(4). Given a T x N
   matrix of N strategy-variant return series over T periods: split T into S even contiguous
   blocks; form every combination of S/2 blocks as an in-sample (IS) set with the complementary
   S/2 blocks as out-of-sample (OOS); for each combination pick the IS-best variant and find its
   OOS RELATIVE RANK among all N variants; transform the rank to a logit; PBO is the share of
   combinations where the logit is <= 0 (the IS-best variant performed at or below the OOS median
   -- i.e. selecting on IS told you nothing, or worse, about OOS).

This is a NEW, pure numpy/pandas tool: no scipy (the inverse normal CDF is Peter Acklam's rational
approximation, refined with one Halley step -- a standard public-domain algorithm, not scipy.stats).
Advisory only: neither number is a PASS/FAIL gate by itself -- a low DSR or high PBO is evidence a
result may be mostly selection, not a verdict (same non-interpretive stance as instrument_check.py).

Library:
    norm_cdf(x) -> float
    norm_ppf(p) -> float
    expected_max_sr(n_trials, var_trials_sr) -> float                      # SR0
    deflated_sharpe_ratio(sr_hat, n_obs, skew, kurtosis, n_trials,
                           var_trials_sr) -> dict(dsr, sr0, sigma_sr, psr_z)
    sharpe_ratio(returns: np.ndarray) -> float                              # mean/std, ddof=1
    cscv_pbo(returns_matrix: np.ndarray, s_blocks=16, stat_fn=sharpe_ratio,
             seed=None) -> dict(pbo, logits, n_combos, s_blocks, n_trials, t_used)

CLI:
    python src/tools/overfit_stats.py dsr --csv F --ret-col RET
        [--n-trials N --var-trials-sr V | --trials-csv F2 --trials-col COL]
        [--out OUT.txt]
    python src/tools/overfit_stats.py pbo --csv F [--s-blocks 16] [--out OUT.txt]
        (--csv for pbo is a T x N matrix: one column per strategy variant, one row per period)
    python src/tools/overfit_stats.py --selftest
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329


def _finite_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and \
        v not in (float("inf"), float("-inf"))


# ----------------------------------------------------------------------------- normal dist (pure python/math, no scipy)


def norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf (Python stdlib, not numpy/scipy)."""
    if x != x:
        return float("nan")
    if x == float("inf"):
        return 1.0
    if x == float("-inf"):
        return 0.0
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF. Peter Acklam's rational approximation (public domain,
    accurate to ~1.15e-9), refined with one step of Halley's method for extra precision.
    https://web.archive.org/web/20151030215612/http://home.online.no/~pjacklam/notes/invnorm/
    """
    if p != p:
        raise ValueError("norm_ppf: p is NaN")
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
            (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
             ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    # one Halley refinement step -- (2026-10-08, round 1): for an extreme
    # (but representable) tail probability like p=1e-320, the unrefined x is already ~-38, and
    # exp(x*x/2) = exp(~733) OVERFLOWS a float64 (max ~709) even though x itself is finite and
    # the Acklam approximation alone is already accurate to ~1.15e-9 out there. Skip the
    # refinement (not needed at that accuracy) rather than let the overflow produce inf/nan.
    if abs(x) < 37.0:
        e = 0.5 * math.erfc(-x / math.sqrt(2.0)) - p
        u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
        x = x - u / (1.0 + x * u / 2.0)
    return x


# ----------------------------------------------------------------------------- DSR


def expected_max_sr(n_trials: int, var_trials_sr: float) -> float:
    """E[max{SR_n}] under the null that all N trials have the same (zero) true SR and the
    observed trial SRs have variance var_trials_sr (Bailey & Lopez de Prado 2014, eq. 10)."""
    # (2026-10-08, round 2): `n_trials < 2` with n_trials=NaN is False (a
    # NaN comparison is always False), so a NaN n_trials used to slip past this guard entirely
    # and propagate into norm_ppf. Validate finiteness explicitly, not just the lower bound.
    if not _finite_num(n_trials) or n_trials < 2:
        raise ValueError("expected_max_sr: n_trials must be a finite number >= 2 (the formula "
                          "is undefined for a single trial -- there is no selection bias to "
                          "correct)")
    if not (_finite_num(var_trials_sr) and var_trials_sr >= 0):
        raise ValueError("expected_max_sr: var_trials_sr must be a finite number >= 0")
    n = float(n_trials)
    # (round 2, BLOCKER): for an astronomically large n_trials (e.g.
    # 1e16), `1.0 - 1.0/(n*e)` rounds to EXACTLY 1.0 in float64 (the subtracted term underflows
    # below machine epsilon), so norm_ppf's p>=1.0 branch returns +inf -- a finite, if absurd,
    # trial count must not silently produce an infinite/NaN benchmark. No research backtest
    # plausibly has more trials than this detects; raise rather than guess a cap.
    p1 = 1.0 - 1.0 / n
    p2 = 1.0 - 1.0 / (n * math.e)
    if p1 >= 1.0 or p2 >= 1.0:
        raise ValueError(f"expected_max_sr: n_trials={n_trials!r} is too large for float64 "
                          "precision (1/n or 1/(n*e) underflows to 0, so 1 - that rounds to "
                          "exactly 1.0) -- this formula is not meaningful at this scale")
    term = (1.0 - EULER_GAMMA) * norm_ppf(p1) + EULER_GAMMA * norm_ppf(p2)
    return math.sqrt(var_trials_sr) * term


def _sigma_sr(sr_hat: float, n_obs: int, skew: float, kurtosis: float) -> float:
    """The FULL standard error of the Sharpe ratio estimator (Mertens 2002 / Lo 2002):
    sigma_hat(SR) = sqrt( (1 - skew*SR + (kurtosis-1)/4*SR^2) / (n_obs-1) ). This already has
    the 1/sqrt(n_obs-1) sample-size term baked in -- PSR/DSR divide by this directly, with NO
    separate sqrt(n_obs-1) multiplication elsewhere  AGAIN on top of this
    function's own internal division by it, inflating z by an extra factor of sqrt(n_obs-1) --
    e.g. sr_hat=0.1, n_obs=101, skew=0, kurtosis=3, sr0=0 gave z~9.975/DSR~1.0 instead of the
    correct z~0.9975/DSR~0.8407)."""
    # (round 2): `n_obs < 2` with n_obs=NaN is False, so a NaN n_obs
    # used to slip past this guard and produce a NaN sigma_sr/DSR without raising.
    if not _finite_num(n_obs) or n_obs < 2:
        raise ValueError("n_obs must be a finite number >= 2")
    if not (_finite_num(skew) and _finite_num(kurtosis) and _finite_num(sr_hat)):
        # (round 1): pandas skew()/kurt() return NaN below their minimum
        # sample size (skew needs >=3 obs, kurt needs >=4); letting a NaN moment flow through
        # (NaN <= 0 is False, so the old inside<=0 guard below never caught it) silently
        # produced a NaN DSR that the CLI printed with a success exit code.
        raise ValueError(f"_sigma_sr: sr_hat={sr_hat!r}, skew={skew!r}, kurtosis={kurtosis!r} "
                          "must all be finite (skew needs >=3 return observations, kurtosis "
                          "needs >=4 -- recompute with more data)")
    inside = 1.0 - skew * sr_hat + ((kurtosis - 1.0) / 4.0) * sr_hat ** 2
    if inside <= 0:
        # (round 1): clamping this to a tiny positive number (so the sqrt
        # never goes complex/NaN) silently manufactured a near-zero standard error -- and
        # therefore a near-certain DSR -- out of a pathological (not just unlucky) moment
        # combination (e.g. sr_hat=1, skew=3, kurtosis=3 gives inside=-1.5). That is fabricated
        # confidence, not a fail-closed default. Raise instead: this combination of sr_hat/
        # skew/kurtosis has no valid asymptotic Sharpe-ratio variance under this formula.
        raise ValueError(f"_sigma_sr: skew={skew!r}, kurtosis={kurtosis!r}, sr_hat={sr_hat!r} "
                          f"give a non-positive variance term ({inside:.6g}) -- this moment "
                          "combination has no valid asymptotic Sharpe-ratio standard error "
                          "under the Mertens/Lo formula; DSR is undefined here, not ~1")
    return math.sqrt(inside / (n_obs - 1))


def deflated_sharpe_ratio(sr_hat: float, n_obs: int, skew: float, kurtosis: float,
                           n_trials: int, var_trials_sr: float) -> dict:
    """Returns dict(dsr, sr0, sigma_sr, psr_z). dsr is the final probability (0..1); sr0 is the
    luck benchmark (expected_max_sr); psr_z is the z-statistic fed to norm_cdf. z = (sr_hat -
    sr0) / sigma_sr -- sigma_sr (see _sigma_sr) already contains the 1/sqrt(n_obs-1) term, so
    no additional sqrt(n_obs-1) factor belongs here (see the  gate note on _sigma_sr)."""
    sr0 = expected_max_sr(n_trials, var_trials_sr)
    sigma_sr = _sigma_sr(sr_hat, n_obs, skew, kurtosis)
    z = (sr_hat - sr0) / sigma_sr
    return {"dsr": norm_cdf(z), "sr0": sr0, "sigma_sr": sigma_sr, "psr_z": z}


def sharpe_ratio(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return 0.0
    return float(r.mean() / sd)


# ----------------------------------------------------------------------------- PBO / CSCV


def cscv_pbo(returns_matrix: np.ndarray, s_blocks: int = 16, stat_fn=sharpe_ratio) -> dict:
    """Combinatorially Symmetric Cross-Validation PBO (Bailey, Borwein, Lopez de Prado & Zhu
    2017). returns_matrix is T (periods) x N (strategy variants). s_blocks must be even and
    <= T; T is truncated to the largest multiple of s_blocks (documented, not silent -- the
    truncated row count is returned as t_used)."""
    m = np.asarray(returns_matrix, dtype="float64")
    if m.ndim != 2:
        raise ValueError("cscv_pbo: returns_matrix must be 2-D (T x N)")
    # (2026-10-08, round 1): sharpe_ratio() silently drops non-finite
    # observations PER COLUMN and returns 0.0 for a too-short/all-missing series, which can
    # then win an IS or OOS comparison against a genuinely bad (negative-Sharpe) strategy --
    # a fabricated ranking, not a fail-closed result. CSCV has no defined missing-data
    # handling in the source paper; require a fully finite matrix and make the caller align/
    # clean data first.
    if not np.all(np.isfinite(m)):
        raise ValueError("cscv_pbo: returns_matrix must be fully finite (no NaN/inf) -- CSCV "
                          "has no missing-data handling; align and clean the return series "
                          "before calling (a column with gaps can otherwise silently win a "
                          "comparison via sharpe_ratio's 0.0 fallback)")
    t_total, n_trials = m.shape
    if n_trials < 2:
        raise ValueError("cscv_pbo: need at least 2 strategy variants (columns) to rank")
    if s_blocks < 2 or s_blocks % 2 != 0:
        raise ValueError("cscv_pbo: s_blocks must be a positive even integer")
    if s_blocks > t_total:
        raise ValueError(f"cscv_pbo: s_blocks ({s_blocks}) cannot exceed T ({t_total})")

    block_size = t_total // s_blocks
    half = s_blocks // 2
    # (2026-10-08, round 2): checking block_size alone over-rejects valid
    # configurations -- the Sharpe ratio is actually computed on the CONCATENATED half
    # (half*block_size rows), not a single raw block, so e.g. T=4, s_blocks=4 (block_size=1,
    # half=2, scored sample=2) is perfectly fine and was being wrongly rejected. Check the
    # sample size actually fed to stat_fn instead (this still rejects the original T=2,
    # s_blocks=2 case: half=1, block_size=1, scored sample=1).
    scored_n = half * block_size
    if scored_n < 2:
        raise ValueError(f"cscv_pbo: each IS/OOS sample would have only {scored_n} row(s) -- "
                          "need >= 2 for a meaningful Sharpe ratio (ddof=1); increase T or "
                          "decrease s_blocks")
    t_used = block_size * s_blocks
    m = m[:t_used, :]
    # (round 2, BLOCKER): a column that is EXACTLY constant over the used
    # span has an undefined Sharpe ratio (0/0), but sharpe_ratio()'s 0.0 fallback lets it still
    # "compete" and often WIN against a genuinely bad (negative-Sharpe) column, fabricating a
    # ranking from an undefined statistic. Reject upfront rather than let it flow through.
    # a tolerance (not exact ==0) is required: numpy's own mean-then-subtract std computation
    # on a literally-constant column (e.g. np.full(40, 0.001)) leaves a ~1e-19 floating-point
    # rounding residue, not a mathematical 0 -- an exact-equality check missed this realistic
    # case during this fix's own selftest.
    col_std = m.std(axis=0, ddof=1)
    if np.any(col_std < 1e-12):
        bad_cols = np.where(col_std < 1e-12)[0].tolist()
        raise ValueError(f"cscv_pbo: column(s) {bad_cols} are exactly constant over the used "
                          f"{t_used} rows -- their Sharpe ratio is undefined (0/0), not a real "
                          "competing score; drop or replace them before calling. (This checks "
                          "the full used span only, not every individual block -- a column "
                          "that happens to be constant within just one IS/OOS split while "
                          "varying elsewhere is a documented residual gap, not fixed here.)")
    blocks = [m[i * block_size:(i + 1) * block_size, :] for i in range(s_blocks)]

    logits = []
    for combo in itertools.combinations(range(s_blocks), half):
        is_idx = combo
        oos_idx = tuple(i for i in range(s_blocks) if i not in combo)
        is_mat = np.vstack([blocks[i] for i in is_idx])
        oos_mat = np.vstack([blocks[i] for i in oos_idx])
        is_stats = np.array([stat_fn(is_mat[:, j]) for j in range(n_trials)])
        oos_stats = np.array([stat_fn(oos_mat[:, j]) for j in range(n_trials)])
        # (round 2, BLOCKER): a CUSTOM stat_fn (the public API accepts
        # any callable) is not guaranteed to return finite values the way the built-in
        # sharpe_ratio() is -- an all-NaN is_stats/oos_stats array let every OOS comparison
        # silently evaluate to False, producing a fabricated PBO from no valid statistics.
        if not (np.all(np.isfinite(is_stats)) and np.all(np.isfinite(oos_stats))):
            raise ValueError("cscv_pbo: stat_fn returned a non-finite value for at least one "
                              "trial on this split -- a custom stat_fn must return a finite "
                              "number for every column")
        n_star = int(np.argmax(is_stats))
        # (round 1): counting every OOS value <= the winner's value
        # assigns the TOP rank to the whole tied group (e.g. two identical columns always
        # "rank" 2/3, never landing at the median where the tie actually belongs) -- use the
        # standard MIDRANK instead: r_c = (# strictly less) + (# equal, including itself + 1)/2,
        # which reduces to the ordinary (# <=) rank when there are no ties.
        less = int(np.sum(oos_stats < oos_stats[n_star]))
        ties = int(np.sum(oos_stats == oos_stats[n_star]))
        r_c = less + (ties + 1) / 2.0
        rank = r_c / (n_trials + 1.0)
        rank = min(max(rank, 1e-6), 1.0 - 1e-6)
        logits.append(math.log(rank / (1.0 - rank)))

    logits_arr = np.array(logits)
    pbo = float(np.mean(logits_arr <= 0.0))
    return {"pbo": pbo, "logits": logits_arr, "n_combos": len(logits_arr),
            "s_blocks": s_blocks, "n_trials": n_trials, "t_used": t_used}


# ----------------------------------------------------------------------------- selftest


def _run_selftest() -> int:
    ok = True

    def expect(name, cond):
        nonlocal ok
        status = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"[{status}] {name}")

    # norm_cdf / norm_ppf basic sanity + round trip
    expect("norm_cdf(0) == 0.5", abs(norm_cdf(0.0) - 0.5) < 1e-12)
    expect("norm_ppf(0.5) == 0", abs(norm_ppf(0.5)) < 1e-9)
    for p in (0.001, 0.025, 0.1, 0.5, 0.9, 0.975, 0.999):
        x = norm_ppf(p)
        expect(f"norm_cdf(norm_ppf({p})) round-trips", abs(norm_cdf(x) - p) < 1e-8)
    for x in (-2.5, -1.0, 0.0, 1.0, 2.5):
        p = norm_cdf(x)
        expect(f"norm_ppf(norm_cdf({x})) round-trips", abs(norm_ppf(p) - x) < 1e-6)

    # expected_max_sr: must INCREASE with more trials (more chances to get lucky) holding
    # variance fixed, and increase with variance holding trials fixed
    sr0_10 = expected_max_sr(10, 0.01)
    sr0_100 = expected_max_sr(100, 0.01)
    sr0_1000 = expected_max_sr(1000, 0.01)
    expect("expected_max_sr increases monotonically with n_trials",
           sr0_10 < sr0_100 < sr0_1000)
    sr0_lowvar = expected_max_sr(100, 0.01)
    sr0_hivar = expected_max_sr(100, 0.04)
    expect("expected_max_sr increases with trial-SR variance",
           sr0_lowvar < sr0_hivar)
    expect("expected_max_sr fails closed on n_trials=1 (undefined, not silently 0)",
           _raises(lambda: expected_max_sr(1, 0.01)))
    expect("expected_max_sr fails closed on negative variance",
           _raises(lambda: expected_max_sr(100, -0.01)))

    # DSR: more trials (same SR_hat, same other inputs) -> LOWER DSR (harder to clear a
    # higher luck benchmark) -- the central correction this tool exists to make.
    common = dict(sr_hat=0.3, n_obs=20, skew=0.0, kurtosis=3.0, var_trials_sr=0.01)
    dsr_few = deflated_sharpe_ratio(n_trials=5, **common)["dsr"]
    dsr_many = deflated_sharpe_ratio(n_trials=500, **common)["dsr"]
    expect(f"DSR decreases as n_trials increases (same SR_hat) ({dsr_few:.4f} -> {dsr_many:.4f})",
           dsr_many < dsr_few)

    # DSR: higher SR_hat (same trials) -> HIGHER DSR
    dsr_lowsr = deflated_sharpe_ratio(sr_hat=0.1, n_obs=20, skew=0.0, kurtosis=3.0,
                                       n_trials=50, var_trials_sr=0.01)["dsr"]
    dsr_hisr = deflated_sharpe_ratio(sr_hat=1.0, n_obs=20, skew=0.0, kurtosis=3.0,
                                      n_trials=50, var_trials_sr=0.01)["dsr"]
    expect(f"DSR increases as SR_hat increases (same trials) ({dsr_lowsr:.4f} -> {dsr_hisr:.4f})",
           dsr_hisr > dsr_lowsr)

    # DSR with skew=0, kurtosis=3 (normal) must reduce sigma_sr to the classic Lo(2002)/
    # Mertens asymptotic form sqrt((1+0.5*SR^2)/(n-1))
    res = deflated_sharpe_ratio(sr_hat=1.0, n_obs=101, skew=0.0, kurtosis=3.0,
                                 n_trials=10, var_trials_sr=0.0)
    classic = math.sqrt((1.0 + 0.5 * 1.0 ** 2) / 100)
    expect("sigma_sr reduces to the classic normal-returns form when skew=0, kurtosis=3",
           abs(res["sigma_sr"] - classic) < 1e-9)

    # (2026-10-08, round 1, BLOCKER): an earlier version multiplied z by
    # an EXTRA sqrt(n_obs-1) on top of _sigma_sr's own internal division by sqrt(n_obs-1),
    # inflating z (e.g. this exact case: z~9.975, DSR~1.0 instead of the correct z~0.9975,
    # DSR~0.8407). Pin the fix against the -computed independent reference value.
    res_ref = deflated_sharpe_ratio(sr_hat=0.1, n_obs=101, skew=0.0, kurtosis=3.0,
                                     n_trials=10, var_trials_sr=0.0)
    expect(f"DSR z-statistic matches the independently-computed reference (z={res_ref['psr_z']:.4f}, "
           "expected ~0.9975)", abs(res_ref["psr_z"] - 0.9975) < 1e-3)
    expect(f"DSR matches the independently-computed reference (dsr={res_ref['dsr']:.4f}, "
           "expected ~0.8407)", abs(res_ref["dsr"] - 0.8407) < 1e-3)

    # DSR with var_trials_sr=0 and n_trials just above the floor: sr0 should be finite and
    # small (near-zero variance collapses the luck benchmark toward 0)
    expect("sr0 -> 0 as var_trials_sr -> 0 regardless of n_trials",
           abs(expected_max_sr(50, 0.0)) < 1e-12)

    # DSR fails closed on bad n_obs
    expect("deflated_sharpe_ratio fails closed on n_obs < 2",
           _raises(lambda: deflated_sharpe_ratio(1.0, 1, 0.0, 3.0, 10, 0.01)))

    # (round 1, BLOCKER): a pathological skew/kurtosis/SR combination
    # that makes the variance term non-positive (e.g. sr_hat=1, skew=3, kurtosis=3 ->
    # inside=-1.5) must raise, not silently clamp to a near-zero variance (which fabricated a
    # near-certain DSR out of an undefined input).
    expect("deflated_sharpe_ratio fails closed on a non-positive variance term "
           "(sr_hat=1, skew=3, kurtosis=3)",
           _raises(lambda: deflated_sharpe_ratio(1.0, 101, 3.0, 3.0, 10, 0.01)))

    # (round 1): a NaN skew/kurtosis (as pandas returns for too-few
    # observations) must raise, not silently produce a NaN DSR with a clean exit.
    expect("deflated_sharpe_ratio fails closed on a NaN skew",
           _raises(lambda: deflated_sharpe_ratio(0.1, 10, float("nan"), 3.0, 10, 0.01)))
    expect("expected_max_sr fails closed on infinite var_trials_sr",
           _raises(lambda: expected_max_sr(10, float("inf"))))

    # (round 1): the Halley refinement step used to overflow
    # (exp(x*x/2) with x~-38) for an extreme but representable tail probability.
    expect("norm_ppf does not raise/overflow on an extreme tail probability (p=1e-320)",
           math.isfinite(norm_ppf(1e-320)))
    expect("norm_ppf(1e-320) is a large negative number of the right order of magnitude",
           norm_ppf(1e-320) < -38.0)

    # sharpe_ratio sanity: constant positive series under ddof=1 has zero std -> defined as 0,
    # not inf/NaN (fail-closed, matches instrument_check.py's non-finite conventions)
    expect("sharpe_ratio of a constant series is 0 (not inf/NaN)",
           sharpe_ratio(np.array([1.0, 1.0, 1.0, 1.0])) == 0.0)
    expect("sharpe_ratio of < 2 finite obs is 0",
           sharpe_ratio(np.array([1.0])) == 0.0)

    # --- PBO / CSCV ---
    rng = np.random.default_rng(20261008)

    # null case: N trials, all i.i.d. noise with the SAME mean/vol -- PBO should be roughly
    # symmetric around 0.5 (no systematic selection advantage). Coarse band, not exact: CSCV
    # enumerates ALL C(s_blocks, s_blocks/2) combinations exhaustively (not a Monte Carlo
    # sample of combinations), but those combinations share overlapping blocks and are
    # therefore statistically dependent on each other, and the single underlying random draw
    #  only gives one noisy realization of PBO.
    t_periods, n_trials_null = 400, 8
    null_matrix = rng.normal(loc=0.0005, scale=0.01, size=(t_periods, n_trials_null))
    res_null = cscv_pbo(null_matrix, s_blocks=8)
    expect(f"PBO null case is roughly 0.5 (got {res_null['pbo']:.3f})",
           0.25 <= res_null["pbo"] <= 0.75)
    expect("PBO null case ran the expected number of combinations (C(8,4)=70)",
           res_null["n_combos"] == 70)

    # injected-skill case: one trial has a materially higher true mean than the rest (a real,
    # large, consistent edge) -- PBO should be low (that trial both wins IS most often and
    # performs well OOS, so the IS-winner is rarely a below-median OOS performer)
    n_trials_skill = 8
    skill_matrix = rng.normal(loc=0.0002, scale=0.01, size=(t_periods, n_trials_skill))
    skill_matrix[:, 0] = rng.normal(loc=0.01, scale=0.01, size=t_periods)  # trial 0 has real skill
    res_skill = cscv_pbo(skill_matrix, s_blocks=8)
    expect(f"PBO injected-skill case is low (got {res_skill['pbo']:.3f})",
           res_skill["pbo"] <= 0.30)

    # (2026-10-08, round 1, BLOCKER): two IDENTICAL, non-constant columns
    # must midrank to exactly the OOS median (rank=0.5, logit=0) on every split, which is
    # "overfitting" under the stated definition (lines ~29-30: the IS-winner performed AT OR
    # BELOW the OOS median) -- PBO should be 1.0, not 0.0 (the old "count every value <= the
    # winner" rule put ties at the TOP of the group instead, giving rank=2/3 and PBO=0).
    tie_col = rng.normal(0.0005, 0.01, 40)
    tie_matrix = np.column_stack([tie_col, tie_col])
    res_tie = cscv_pbo(tie_matrix, s_blocks=4)
    expect(f"PBO on two identical non-constant columns is exactly 1.0 (midrank fix; got "
           f"{res_tie['pbo']:.3f})", abs(res_tie["pbo"] - 1.0) < 1e-9)

    # (round 1, BLOCKER): a matrix with any NaN/inf must raise, not let
    # sharpe_ratio's per-column 0.0 fallback silently fabricate a ranking for missing data.
    nan_matrix = null_matrix.copy()
    nan_matrix[0, 0] = np.nan
    expect("cscv_pbo fails closed on a non-finite value anywhere in the matrix",
           _raises(lambda: cscv_pbo(nan_matrix, s_blocks=8)))

    # (round 1), second half: T=2, s_blocks=2 gives block_size=1, too few
    # observations for a meaningful (ddof=1) Sharpe ratio in any block.
    expect("cscv_pbo fails closed on a block_size of 1 (T=2, s_blocks=2)",
           _raises(lambda: cscv_pbo(null_matrix[:2, :], s_blocks=2)))

    # (round 2, MAJOR): block_size=1 alone must NOT reject T=4, s_blocks=4
    # -- the scored sample is half*block_size=2*1=2, which IS enough for ddof=1.
    small_ok_matrix = rng.normal(0.001, 0.01, size=(4, 3))
    expect("cscv_pbo accepts block_size=1 when the SCORED sample (half*block_size) is >= 2 "
           "(T=4, s_blocks=4)", not _raises(lambda: cscv_pbo(small_ok_matrix, s_blocks=4)))

    # (round 2, BLOCKER): an exactly-constant column has an undefined
    # (0/0) Sharpe ratio and must not be allowed to silently "compete" via sharpe_ratio's 0.0
    # fallback (where it can win against a genuinely bad, negative-Sharpe column).
    const_col_matrix = np.column_stack([np.full(40, 0.001), rng.normal(-0.01, 0.01, 40)])
    expect("cscv_pbo fails closed on an exactly-constant column",
           _raises(lambda: cscv_pbo(const_col_matrix, s_blocks=4)))

    # (round 2, BLOCKER): a custom stat_fn that returns a non-finite
    # value for any trial on any split must raise, not silently fabricate a PBO (every OOS
    # comparison against NaN is False, which used to produce a deterministic PBO=1 from no
    # valid statistics at all).
    nanstat_matrix = rng.normal(0.001, 0.01, size=(40, 3))
    expect("cscv_pbo fails closed on a custom stat_fn returning NaN",
           _raises(lambda: cscv_pbo(nanstat_matrix, s_blocks=4,
                                     stat_fn=lambda r: float("nan"))))

    # (round 2, MAJOR): a NaN n_obs/n_trials must fail closed (a NaN
    # comparison like `nan < 2` is always False, so a naive lower-bound check alone misses it).
    expect("_sigma_sr fails closed on a NaN n_obs",
           _raises(lambda: _sigma_sr(0.1, float("nan"), 0.0, 3.0)))
    expect("expected_max_sr fails closed on a NaN n_trials",
           _raises(lambda: expected_max_sr(float("nan"), 0.01)))

    # (round 2, BLOCKER): an astronomically large n_trials makes
    # 1-1/(n*e) round to exactly 1.0 in float64, which used to silently return an infinite
    # (or 0*inf=NaN) benchmark instead of raising.
    expect("expected_max_sr fails closed on an astronomically large n_trials (1e16)",
           _raises(lambda: expected_max_sr(1e16, 0.01)))
    expect("expected_max_sr still works normally just below that regime (n_trials=1e6)",
           math.isfinite(expected_max_sr(1e6, 0.01)))

    # fails closed on malformed inputs
    expect("cscv_pbo fails closed on odd s_blocks",
           _raises(lambda: cscv_pbo(null_matrix, s_blocks=7)))
    expect("cscv_pbo fails closed on s_blocks > T",
           _raises(lambda: cscv_pbo(null_matrix[:4, :], s_blocks=8)))
    expect("cscv_pbo fails closed on a single-column matrix",
           _raises(lambda: cscv_pbo(null_matrix[:, :1], s_blocks=8)))

    # t_used truncation is reported, not silent
    uneven = rng.normal(size=(405, 4))  # 405 not divisible by 8
    res_uneven = cscv_pbo(uneven, s_blocks=8)
    expect("t_used truncates to the largest multiple of s_blocks and reports it",
           res_uneven["t_used"] == 400 and res_uneven["t_used"] < 405)

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except (ValueError, ZeroDivisionError):
        return True


# ----------------------------------------------------------------------------- CLI


def _cmd_dsr(args: argparse.Namespace) -> int:
    df = pd.read_csv(args.csv)
    if args.ret_col not in df.columns:
        print(f"ERROR: --ret-col {args.ret_col!r} not found in {args.csv}", file=sys.stderr)
        return 2
    n_raw = len(df)
    r_series = pd.to_numeric(df[args.ret_col], errors="coerce")
    r = r_series.to_numpy(dtype="float64")
    r = r[np.isfinite(r)]
    n_dropped = n_raw - len(r)
    # (2026-10-08, round 1): rows that fail to coerce to numeric (or are
    # NaN/inf) used to be dropped with no visible trace, silently computing SR_hat/skew/
    # kurtosis over a smaller population than --csv actually contains.
    if n_dropped > 0:
        print(f"NOTE: {n_dropped}/{n_raw} rows in {args.ret_col!r} were non-numeric/non-finite "
              "and were dropped before computing SR_hat/skew/kurtosis", file=sys.stderr)
    # (round 1): pandas .skew() needs >=3 observations and .kurt() needs
    # >=4 to return a real number (fewer silently returns NaN, which used to flow all the way
    # through to a NaN DSR printed with exit code 0 -- see _sigma_sr's own guard too).
    if len(r) < 4:
        print(f"ERROR: {len(r)} finite return observation(s) is fewer than the 4 needed for a "
              "reliable kurtosis estimate (skew needs >=3)", file=sys.stderr)
        return 2
    n_obs = len(r)
    sr_hat = sharpe_ratio(r)
    # pandas skew/kurtosis: .kurt() is EXCESS kurtosis (normal=0); the DSR formula wants RAW
    # kurtosis (normal=3), so +3 is required here -- a documented, deliberate conversion, not
    # an accidental mismatch.
    skew = float(pd.Series(r).skew())
    kurtosis = float(pd.Series(r).kurt()) + 3.0

    if args.trials_csv:
        tdf = pd.read_csv(args.trials_csv)
        if args.trials_col not in tdf.columns:
            print(f"ERROR: --trials-col {args.trials_col!r} not found in {args.trials_csv}",
                  file=sys.stderr)
            return 2
        n_trials_raw = len(tdf)
        trial_srs = pd.to_numeric(tdf[args.trials_col], errors="coerce").dropna().to_numpy()
        n_trials = len(trial_srs)
        if n_trials < n_trials_raw:
            print(f"NOTE: {n_trials_raw - n_trials}/{n_trials_raw} rows in --trials-col "
                  f"{args.trials_col!r} were non-numeric and were dropped from the trial "
                  "count/variance", file=sys.stderr)
        var_trials_sr = float(np.var(trial_srs, ddof=1)) if n_trials >= 2 else 0.0
    elif args.n_trials is not None and args.var_trials_sr is not None:
        n_trials = args.n_trials
        var_trials_sr = args.var_trials_sr
    else:
        print("ERROR: give either (--n-trials and --var-trials-sr) or "
              "(--trials-csv and --trials-col)", file=sys.stderr)
        return 2

    try:
        res = deflated_sharpe_ratio(sr_hat, n_obs, skew, kurtosis, n_trials, var_trials_sr)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    lines = [
        "overfit_stats.py dsr report",
        f"input: {args.csv} ({args.ret_col!r}, n_obs={n_obs})",
        f"observed SR_hat = {sr_hat:.4f} (raw, not annualized)",
        f"skew = {skew:.4f}  kurtosis (raw, normal=3) = {kurtosis:.4f}",
        f"n_trials = {n_trials}  var_trials_sr = {var_trials_sr:.6f}",
        f"SR0 (luck benchmark, expected max of {n_trials} trials) = {res['sr0']:.4f}",
        f"sigma_sr = {res['sigma_sr']:.4f}  psr_z = {res['psr_z']:.4f}",
        f"DEFLATED SHARPE RATIO (DSR) = {res['dsr']:.4f}",
        "DSR is advisory, not a PASS/FAIL gate: it is the probability SR_hat clears the "
        "luck benchmark given n_trials and their SR variance -- interpretation stays with "
        "the main loop / Gate-2, not this script.",
    ]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"(written to {args.out})")
    return 0


def _cmd_pbo(args: argparse.Namespace) -> int:
    df = pd.read_csv(args.csv)
    numeric_df = df.select_dtypes(include=[np.number])
    # (2026-10-08, round 1): a strategy column that parsed as text (e.g.
    # one bad cell forced the whole column to object dtype) used to silently disappear from
    # the trial universe -- select_dtypes drops it with no trace, and PBO would still report a
    # clean success over the SMALLER, different set of surviving columns.
    dropped_cols = [c for c in df.columns if c not in numeric_df.columns]
    if dropped_cols:
        print(f"NOTE: {len(dropped_cols)} column(s) were not numeric and were dropped from "
              f"the PBO trial universe: {dropped_cols}", file=sys.stderr)
    m = numeric_df.to_numpy(dtype="float64")
    if m.shape[1] < 2:
        print("ERROR: --csv for pbo needs >= 2 numeric columns (one per strategy variant)",
              file=sys.stderr)
        return 2
    try:
        res = cscv_pbo(m, s_blocks=args.s_blocks)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    lines = [
        "overfit_stats.py pbo (CSCV) report",
        f"input: {args.csv} (T={m.shape[0]} periods, N={res['n_trials']} variants, "
        f"t_used={res['t_used']}, s_blocks={res['s_blocks']}, n_combos={res['n_combos']})",
        f"PROBABILITY OF BACKTEST OVERFITTING (PBO) = {res['pbo']:.4f}",
        f"logit distribution: mean={res['logits'].mean():.4f} "
        f"median={np.median(res['logits']):.4f} "
        f"share<=0={float(np.mean(res['logits'] <= 0)):.4f}",
        "PBO is advisory, not a PASS/FAIL gate: a high PBO means the in-sample-best variant is "
        "not a reliable out-of-sample pick among the N variants tried -- interpretation stays "
        "with the main loop / Gate-2, not this script.",
    ]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"(written to {args.out})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    p_dsr = sub.add_parser("dsr", help="Deflated Sharpe Ratio")
    p_dsr.add_argument("--csv", required=True)
    p_dsr.add_argument("--ret-col", required=True)
    p_dsr.add_argument("--n-trials", type=int)
    p_dsr.add_argument("--var-trials-sr", type=float)
    p_dsr.add_argument("--trials-csv")
    p_dsr.add_argument("--trials-col")
    p_dsr.add_argument("--out")

    p_pbo = sub.add_parser("pbo", help="Probability of Backtest Overfitting (CSCV)")
    p_pbo.add_argument("--csv", required=True)
    p_pbo.add_argument("--s-blocks", type=int, default=16)
    p_pbo.add_argument("--out")

    args = ap.parse_args(argv)
    if args.selftest:
        return _run_selftest()
    if args.cmd == "dsr":
        return _cmd_dsr(args)
    if args.cmd == "pbo":
        return _cmd_pbo(args)
    ap.error("one of: dsr, pbo, --selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
