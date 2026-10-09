#!/usr/bin/env python
"""decay_monitor.py -- sequential monitor for a live/forward line's per-unit excess returns.

A strategy-review flagged
that this program has only PRE-STATED kill triggers (checked once, after the fact) and no running
sequential monitor on a forward ledger / recorder. This tool is that monitor: given a CSV of
(date, value) where value is a per-unit excess return (realized minus a reference/benchmark, in
whatever unit the line's edge is measured in), it runs TWO classical sequential tests side by side
and emits one of four states with the running statistic:

  CONTINUE     -- neither test has decided yet; keep collecting
  PAUSE        -- the CUSUM monitor has detected a downward (decaying) shift; stop and look
  ACCEPT_H1    -- the SPRT has accepted "the edge is real, at about the claimed size"
  ACCEPT_H0    -- the SPRT has accepted "there is no edge" (mean = 0)

1. ONE-SIDED CUSUM (Page, E.S. 1954, "Continuous Inspection Schemes", Biometrika 41(1/2)).
   Detects a downward shift in the mean away from a reference mu0, using an allowance k (set to
   half the shift you want to be sensitive to -- the task's own convention) and a decision
   interval h:
       S_0 = 0
       S_i = max(0, S_{i-1} + (mu0 - k) - x_i)      [one-sided LOWER/decay-detecting CUSUM]
   PAUSE triggers the first time S_i >= h. This is a QUALITY-CONTROL alarm, not a hypothesis
   test: it has no built-in "accept H0" state, it only ever says "something changed, look now"
   (sticky once alarmed) or stays silent.

2. WALD SEQUENTIAL PROBABILITY RATIO TEST (SPRT) (Wald, A. 1945, "Sequential Tests of Statistical
   Hypotheses", Annals of Mathematical Statistics 16(2)) between H0: mean=0 and H1: mean=theta
   (the backtest's claimed edge), both versus a KNOWN sigma (the per-unit standard deviation),
   i.e. the classical Gaussian-mean SPRT:
       llr_i = theta*x_i/sigma^2 - theta^2/(2*sigma^2)          (per-observation log-LR)
       Lambda_n = sum_{i<=n} llr_i
       A = ln((1-beta)/alpha)      B = ln(beta/(1-alpha))
       ACCEPT_H1 the first time Lambda_n >= A; ACCEPT_H0 the first time Lambda_n <= B.
   alpha = Type-I error (accept H1 when H0 true), beta = Type-II error (accept H0 when H1 true).

Per-row overall `state` is sticky and prioritizes a decided SPRT (ACCEPT_H1/ACCEPT_H0) over a
CUSUM alarm (PAUSE) over CONTINUE, since SPRT is the terminal kill/confirm decision this program
actually needs; CUSUM is an earlier-warning "stop and look" signal that can fire before SPRT
decides. Both running statistics are reported on every row regardless of which one "wins" the
overall state, so nothing is hidden.

Pure numpy/pandas (no scipy). Advisory: this script computes CONTINUE/PAUSE/ACCEPT_H1/ACCEPT_H0
mechanically from the numbers given; it does not itself authorize stopping a line or taking
capital action -- that stays with the main loop / Gate-2, same non-interpretive stance as
instrument_check.py and overfit_stats.py.

Library:
    cusum_one_sided(values, mu0, k, h, direction="down") -> dict(s_hist, alarm_idx)
    sprt_wald(values, theta, sigma, alpha=0.05, beta=0.05) -> dict(llr_hist, decision, decision_idx)
    run_monitor(df, value_col, mu0, k, h, theta, sigma, alpha=0.05, beta=0.05,
                direction="down") -> pandas.DataFrame (adds s_cusum, cusum_state, lambda_sprt,
                sprt_state, state columns)

CLI:
    python src/tools/decay_monitor.py --csv F --value-col V [--date-col D]
        --mu0 0.0 --target-shift DELTA --h H
        --theta THETA --sigma SIGMA [--alpha 0.05] [--beta 0.05]
        [--direction down|up] [--out OUT.txt] [--out-csv OUT.csv]
    python src/tools/decay_monitor.py --selftest
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _finite_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and \
        v not in (float("inf"), float("-inf"))


# ----------------------------------------------------------------------------- CUSUM


def cusum_one_sided(values: np.ndarray, mu0: float, k: float, h: float,
                     direction: str = "down") -> dict:
    """One-sided Page CUSUM. direction='down' detects a downward shift (decay); 'up' detects an
    upward shift. k is the allowance (reference value, conventionally half the shift size to
    detect); h is the decision interval. Sticky: alarm_idx is the FIRST crossing, s_hist keeps
    accumulating after it (so a caller can see how far past the threshold it went)."""
    # (2026-10-08, round 1): `h <= 0`/`k < 0` with a NaN h/k is False (a
    # NaN comparison is always False), so a NaN parameter used to slip past these guards --
    # NaN then poisons every `s + ... ` update, and `max(0.0, nan)` silently returns 0.0 (never
    # alarming, no matter how large the true shift is).
    if not _finite_num(mu0):
        raise ValueError("cusum_one_sided: mu0 must be a finite number")
    if not _finite_num(k) or k < 0:
        raise ValueError("cusum_one_sided: k (allowance) must be a finite number >= 0")
    if not _finite_num(h) or h <= 0:
        raise ValueError("cusum_one_sided: h (decision interval) must be a finite number > 0")
    if direction not in ("down", "up"):
        raise ValueError("cusum_one_sided: direction must be 'down' or 'up'")
    v = np.asarray(values, dtype="float64")
    s = 0.0
    s_hist = np.empty(len(v), dtype="float64")
    alarm_idx = None
    for i, x in enumerate(v):
        if not np.isfinite(x):
            # fail closed: a non-finite observation cannot silently vanish from the running
            # statistic -- treat it as the worst-case direction-consistent value (it cannot
            # make the alarm LESS likely to fire than if the monitor had simply paused).
            x = -np.inf if direction == "down" else np.inf
        if direction == "down":
            s = max(0.0, s + (mu0 - k) - x)
        else:
            s = max(0.0, s + (x - mu0 - k))
        s_hist[i] = s
        if alarm_idx is None and s >= h:
            alarm_idx = i
    return {"s_hist": s_hist, "alarm_idx": alarm_idx}


# ----------------------------------------------------------------------------- Wald SPRT


def sprt_wald(values: np.ndarray, theta: float, sigma: float,
               alpha: float = 0.05, beta: float = 0.05) -> dict:
    """Wald SPRT for H0: mean=0 vs H1: mean=theta, known sigma. Sticky: decision_idx is the
    FIRST crossing; llr_hist keeps accumulating after it. Returns an extra "n_invalid" count
    (observations that contributed exactly 0, not a real likelihood ratio -- see below)."""
    # (2026-10-08, round 1): `sigma <= 0`/`theta == 0` with a NaN sigma/
    # theta is False (NaN comparisons are always False), so a NaN silently passed; an infinite
    # sigma also passed `sigma <= 0` yet makes coef_lin/coef_const both 0, silently disabling
    # the test (every llr contribution becomes 0 with no error).
    if not _finite_num(sigma) or sigma <= 0:
        raise ValueError("sprt_wald: sigma must be a finite number > 0")
    if not _finite_num(theta) or theta == 0:
        raise ValueError("sprt_wald: theta (the H1 mean) must be a finite nonzero number -- "
                          "H0 and H1 would otherwise be the same hypothesis")
    if not (0 < alpha < 1) or not (0 < beta < 1):
        raise ValueError("sprt_wald: alpha and beta must both be in (0, 1)")
    # (round 1): alpha+beta>=1 can flip the usual B<0<A ordering (e.g.
    # alpha=beta=0.8 gives A~-1.386, B~1.386, i.e. B > A) -- a single observation can then
    # satisfy BOTH "accept" conditions simultaneously, and which one fires becomes an
    # arbitrary artifact of branch order rather than a real sequential-test decision.
    if alpha + beta >= 1:
        raise ValueError("sprt_wald: alpha + beta must be < 1 (otherwise the Wald decision "
                          "boundaries A and B are not properly ordered: B < 0 < A)")
    A = math.log((1.0 - beta) / alpha)
    B = math.log(beta / (1.0 - alpha))
    v = np.asarray(values, dtype="float64")
    coef_lin = theta / (sigma ** 2)
    coef_const = (theta ** 2) / (2.0 * sigma ** 2)
    lam = 0.0
    llr_hist = np.empty(len(v), dtype="float64")
    decision = None
    decision_idx = None
    n_invalid = 0
    for i, x in enumerate(v):
        if np.isfinite(x):
            lam += coef_lin * x - coef_const
        else:
            # (round 1, BLOCKER): substituting x=0.0 into the SAME
            # formula (coef_lin*x - coef_const) does NOT contribute zero -- it still adds
            # -coef_const (a real, nonzero, permanently negative drift toward ACCEPT_H0) for
            # every invalid observation. That fabricated evidence for H0 out of missing data
            # (e.g. 6 NaNs with theta=sigma=1 drove lambda to -3 and an outright ACCEPT_H0).
            # A truly zero contribution means skipping the update entirely.
            n_invalid += 1
        llr_hist[i] = lam
        if decision is None:
            if lam >= A:
                decision, decision_idx = "ACCEPT_H1", i
            elif lam <= B:
                decision, decision_idx = "ACCEPT_H0", i
    return {"llr_hist": llr_hist, "decision": decision, "decision_idx": decision_idx,
            "A": A, "B": B, "n_invalid": n_invalid}


# ----------------------------------------------------------------------------- combined monitor


def run_monitor(df: pd.DataFrame, value_col: str, mu0: float, k: float, h: float,
                 theta: float, sigma: float, alpha: float = 0.05, beta: float = 0.05,
                 direction: str = "down") -> pd.DataFrame:
    if value_col not in df.columns:
        raise ValueError(f"run_monitor: value_col {value_col!r} not found in frame")
    values = pd.to_numeric(df[value_col], errors="coerce").to_numpy(dtype="float64")
    invalid_mask = ~np.isfinite(values)
    n_nan = int(np.sum(invalid_mask))
    cusum = cusum_one_sided(values, mu0, k, h, direction=direction)
    sprt = sprt_wald(values, theta, sigma, alpha=alpha, beta=beta)

    out = df.copy()
    out["s_cusum"] = cusum["s_hist"]
    out["cusum_alarm"] = False
    if cusum["alarm_idx"] is not None:
        out.iloc[cusum["alarm_idx"]:, out.columns.get_loc("cusum_alarm")] = True
    out["lambda_sprt"] = sprt["llr_hist"]
    out["sprt_state"] = "CONTINUE"
    if sprt["decision"] is not None:
        out.iloc[sprt["decision_idx"]:, out.columns.get_loc("sprt_state")] = sprt["decision"]

    # (2026-10-08, round 1, MAJOR): a decided SPRT is sticky and takes
    # state priority over everything after it -- so a non-finite observation arriving AFTER a
    # terminal ACCEPT_H1/ACCEPT_H0 was previously invisible in `state` (e.g. values=[4, NaN]
    # with theta=sigma=1 decides ACCEPT_H1 at row 0 and stays ACCEPT_H1 at row 1 even though
    # row 1's data was invalid). `invalid_data_seen` is tracked SEPARATELY from `state` and is
    # sticky from the first bad row onward, so a caller checking state alone no longer has a
    # silent blind spot -- it does not change which value `state` itself takes.
    out["invalid_data_seen"] = np.cumsum(invalid_mask) > 0

    # overall state priority: a decided SPRT (terminal) > a CUSUM alarm (early warning) > CONTINUE
    state = np.where(out["sprt_state"] != "CONTINUE", out["sprt_state"],
                      np.where(out["cusum_alarm"], "PAUSE", "CONTINUE"))
    out["state"] = state
    out.attrs["n_nan_excluded"] = n_nan
    out.attrs["sprt_n_invalid"] = sprt["n_invalid"]
    out.attrs["cusum_alarm_idx"] = cusum["alarm_idx"]
    out.attrs["sprt_decision"] = sprt["decision"]
    out.attrs["sprt_decision_idx"] = sprt["decision_idx"]
    out.attrs["sprt_A"] = sprt["A"]
    out.attrs["sprt_B"] = sprt["B"]
    return out


# ----------------------------------------------------------------------------- selftest


def _run_selftest() -> int:
    ok = True

    def expect(name, cond):
        nonlocal ok
        status = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"[{status}] {name}")

    rng = np.random.default_rng(20261008)

    # --- guards fail CLOSED ---
    expect("cusum_one_sided rejects h<=0",
           _raises(lambda: cusum_one_sided(np.array([0.0, 0.0]), 0.0, 0.1, 0.0)))
    expect("cusum_one_sided rejects k<0",
           _raises(lambda: cusum_one_sided(np.array([0.0, 0.0]), 0.0, -0.1, 1.0)))
    expect("cusum_one_sided rejects a bad direction string",
           _raises(lambda: cusum_one_sided(np.array([0.0]), 0.0, 0.1, 1.0, direction="sideways")))
    expect("sprt_wald rejects sigma<=0",
           _raises(lambda: sprt_wald(np.array([0.0]), 0.01, 0.0)))
    expect("sprt_wald rejects theta==0 (H0==H1)",
           _raises(lambda: sprt_wald(np.array([0.0]), 0.0, 0.01)))
    expect("sprt_wald rejects alpha outside (0,1)",
           _raises(lambda: sprt_wald(np.array([0.0]), 0.01, 0.01, alpha=0.0)))
    expect("sprt_wald rejects beta outside (0,1)",
           _raises(lambda: sprt_wald(np.array([0.0]), 0.01, 0.01, beta=1.5)))

    # (2026-10-08, round 1, MAJOR): a NaN h/k/mu0/theta/sigma used to slip
    # past a bare `<=`/`<` comparison (NaN comparisons are always False).
    expect("cusum_one_sided fails closed on a NaN h",
           _raises(lambda: cusum_one_sided(np.array([0.0, 0.0]), 0.0, 0.1, float("nan"))))
    expect("cusum_one_sided fails closed on a NaN k",
           _raises(lambda: cusum_one_sided(np.array([0.0, 0.0]), 0.0, float("nan"), 1.0)))
    expect("cusum_one_sided fails closed on a NaN mu0",
           _raises(lambda: cusum_one_sided(np.array([0.0, 0.0]), float("nan"), 0.1, 1.0)))
    expect("sprt_wald fails closed on a NaN theta",
           _raises(lambda: sprt_wald(np.array([0.0]), float("nan"), 0.01)))
    expect("sprt_wald fails closed on a NaN sigma",
           _raises(lambda: sprt_wald(np.array([0.0]), 0.01, float("nan"))))
    expect("sprt_wald fails closed on an infinite sigma (would silently zero out the test)",
           _raises(lambda: sprt_wald(np.array([0.0]), 0.01, float("inf"))))
    # (round 1, MAJOR): alpha+beta>=1 inverts the A/B ordering (B > A),
    # so a single observation can satisfy both "accept" branches at once.
    expect("sprt_wald fails closed on alpha+beta >= 1 (inverted A/B ordering)",
           _raises(lambda: sprt_wald(np.array([0.5]), 1.0, 1.0, alpha=0.8, beta=0.8)))

    # (round 1, BLOCKER): a non-finite SPRT observation must contribute
    # EXACTLY zero to lambda, not -theta^2/(2*sigma^2) (the old x=0.0 substitution). Six NaNs
    # with theta=sigma=1 used to drive lambda to -3.0 and fabricate an ACCEPT_H0 from nothing.
    res_allnan = sprt_wald(np.full(6, np.nan), 1.0, 1.0, alpha=0.3, beta=0.3)
    expect("sprt_wald: an all-NaN stream leaves lambda at exactly 0.0 (no fabricated evidence)",
           res_allnan["llr_hist"][-1] == 0.0 and res_allnan["decision"] is None
           and res_allnan["n_invalid"] == 6)
    res_mixed = sprt_wald(np.array([0.5, np.nan, 0.5]), 1.0, 1.0, alpha=0.3, beta=0.3)
    res_nogap = sprt_wald(np.array([0.5, 0.5]), 1.0, 1.0, alpha=0.3, beta=0.3)
    expect("sprt_wald: a NaN row leaves lambda IDENTICAL to skipping that row entirely",
           abs(res_mixed["llr_hist"][-1] - res_nogap["llr_hist"][-1]) < 1e-12)

    # --- CUSUM: a clean no-shift stream (mean=mu0) should very rarely alarm over a modest
    # horizon; an actually-decaying stream (mean well below mu0 - the shift CUSUM is tuned to
    # detect) should alarm quickly. Empirical, order-of-magnitude comparison (not an exact ARL
    # formula match -- documented as a coarse check).
    # h is in the SAME RAW UNITS as the data (per Page's classical CUSUM -- not standardized).
    # A shift of only a fraction of sigma0 (e.g. delta < sigma0) makes ANY one-sided CUSUM slow
    # and noisy by construction (low signal-to-noise); that is a property of the underlying
    # statistics, not a bug in this implementation, so the test below uses a clearly detectable
    # shift (delta = 5*sigma0) the way a real forward-ledger decay check would be tuned, and
    # h is set to twice the per-step drift k produces under the alternative.
    # (2026-10-08, round 1, MAJOR): alarm_idx/decision_idx is a 0-indexed
    # row position (the FIRST crossing is at index 0 after seeing 1 observation), so the
    # number of observations actually consumed is idx+1, not idx -- every crossing used to be
    # undercounted by exactly one step. A run that never crosses within n_obs is RIGHT-CENSORED
    # at the window length: substituting n_obs for it is a real, named quantity (a
    # window-censored restricted mean), not the true unrestricted ARL -- reported as such
    # (censored_frac), not conflated with it.
    mu0, sigma0, delta, h = 0.0010, 0.004, 0.0200, 0.02
    k = 0.01
    n_reps, n_obs = 200, 300

    def _cusum_run_lengths(true_mean):
        lens, n_censored = [], 0
        for _ in range(n_reps):
            stream = rng.normal(true_mean, sigma0, n_obs)
            res = cusum_one_sided(stream, mu0, k, h, direction="down")
            if res["alarm_idx"] is None:
                lens.append(n_obs)
                n_censored += 1
            else:
                lens.append(res["alarm_idx"] + 1)
        return lens, n_censored / n_reps

    noshift_lens, noshift_censored = _cusum_run_lengths(mu0)
    decay_lens, decay_censored = _cusum_run_lengths(mu0 - delta)
    rmrl0 = float(np.mean(noshift_lens))  # restricted mean run length, censored at n_obs
    rmrl1 = float(np.mean(decay_lens))
    expect(f"CUSUM restricted-mean run length under no shift is much longer than under the "
           f"decay shift (RMRL0={rmrl0:.1f} [{noshift_censored:.0%} censored], "
           f"RMRL1={rmrl1:.1f} [{decay_censored:.0%} censored])", rmrl0 > 3.0 * rmrl1)
    expect("CUSUM under a real decay shift alarms within the observation window most of the "
           "time (low censoring)", decay_censored < 0.2)
    expect("CUSUM under no shift rarely alarms within the observation window (high censoring)",
           noshift_censored > 0.7)

    # --- SPRT: a no-edge stream should predominantly ACCEPT_H0; an edge-present stream
    # (mean close to theta) should predominantly ACCEPT_H1. Compare empirical average decision
    # index ("run length") against Wald's approximate ARL identity as a loose, order-of-
    # magnitude sanity check, not an exact match.
    theta, sigma1, alpha, beta = 0.0020, 0.01, 0.05, 0.05
    A = math.log((1 - beta) / alpha)
    B = math.log(beta / (1 - alpha))

    def _run_sprt_reps(true_mean, n_reps=300, n_obs=2000):
        decisions = []
        run_lens = []
        n_censored = 0
        for _ in range(n_reps):
            stream = rng.normal(true_mean, sigma1, n_obs)
            res = sprt_wald(stream, theta, sigma1, alpha=alpha, beta=beta)
            decisions.append(res["decision"])
            # (round 1): decision_idx+1 is the number of observations
            # actually consumed (idx is 0-based); an undecided run is censored at n_obs, not a
            # true stopping time -- tracked separately via n_censored.
            if res["decision_idx"] is None:
                run_lens.append(n_obs)
                n_censored += 1
            else:
                run_lens.append(res["decision_idx"] + 1)
        return decisions, run_lens, n_censored / n_reps

    dec_h0, len_h0, censored_h0 = _run_sprt_reps(0.0)
    dec_h1, len_h1, censored_h1 = _run_sprt_reps(theta)
    frac_accept_h0_under_h0 = np.mean([d == "ACCEPT_H0" for d in dec_h0])
    frac_accept_h1_under_h1 = np.mean([d == "ACCEPT_H1" for d in dec_h1])
    expect(f"SPRT under H0 (no edge) mostly ACCEPT_H0 ({frac_accept_h0_under_h0:.2f})",
           frac_accept_h0_under_h0 >= 1.0 - 2 * alpha)
    expect(f"SPRT under H1 (true edge=theta) mostly ACCEPT_H1 ({frac_accept_h1_under_h1:.2f})",
           frac_accept_h1_under_h1 >= 1.0 - 2 * beta)
    expect(f"SPRT censoring is low at this horizon (n_obs=2000) under both hypotheses "
           f"(censored_h0={censored_h0:.2%}, censored_h1={censored_h1:.2%})",
           censored_h0 < 0.05 and censored_h1 < 0.05)

    # Wald's identity (approximate ARL under H0): E_mu[llr] at mu=0 is -theta^2/(2 sigma^2);
    # ARL0 ~= [(1-alpha)*B + alpha*A] / E_0[llr]. Loose multiplicative tolerance (0.2x-5x) since
    # this is an asymptotic approximation, not exact for finite samples, and censoring (checked
    # to be low above) is folded into the empirical mean rather than excluded.
    e0_llr = -(theta ** 2) / (2 * sigma1 ** 2)
    arl0_theory = ((1 - alpha) * B + alpha * A) / e0_llr
    arl0_empirical = float(np.mean(len_h0))
    ratio0 = arl0_empirical / arl0_theory if arl0_theory else float("nan")
    expect(f"SPRT empirical ARL under H0 is roughly Wald's theoretical ARL0 "
           f"(empirical={arl0_empirical:.1f}, theory={arl0_theory:.1f}, ratio={ratio0:.2f})",
           0.2 <= ratio0 <= 5.0)

    e1_llr = (theta ** 2) / (2 * sigma1 ** 2)
    arl1_theory = ((1 - beta) * A + beta * B) / e1_llr
    arl1_empirical = float(np.mean(len_h1))
    ratio1 = arl1_empirical / arl1_theory if arl1_theory else float("nan")
    expect(f"SPRT empirical ARL under H1 is roughly Wald's theoretical ARL1 "
           f"(empirical={arl1_empirical:.1f}, theory={arl1_theory:.1f}, ratio={ratio1:.2f})",
           0.2 <= ratio1 <= 5.0)

    # --- combined run_monitor: state priority and sticky behaviour ---
    df_edge = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=n_obs, freq="D"),
                             "value": rng.normal(theta, sigma1, n_obs)})
    out_edge = run_monitor(df_edge, "value", mu0=0.0, k=theta / 2, h=50.0,
                            theta=theta, sigma=sigma1, alpha=alpha, beta=beta, direction="down")
    expect("run_monitor on an edge-present stream reaches ACCEPT_H1 in the final state",
           out_edge["state"].iloc[-1] == "ACCEPT_H1")
    # (2026-10-08, round 1, MINOR): comparing the two COUNTS of
    # "ACCEPT_H1" rows passes even if both columns reverted together to CONTINUE after the
    # decision (a non-sticky bug) -- check every row from the first decision onward actually
    # KEEPS that decision (no reversion), not just that the counts happen to match.
    first_decided = out_edge.index[out_edge["sprt_state"] == "ACCEPT_H1"][0]
    expect("run_monitor sprt_state is sticky: every row from the first decision onward stays "
           "ACCEPT_H1 (no reversion to CONTINUE)",
           (out_edge.loc[first_decided:, "sprt_state"] == "ACCEPT_H1").all())
    expect("run_monitor overall state matches sprt_state row-for-row once SPRT has decided "
           "(state priority is applied correctly, not just counted)",
           (out_edge.loc[first_decided:, "state"] == "ACCEPT_H1").all())

    df_decay = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=n_obs, freq="D"),
                              "value": rng.normal(mu0 - delta, sigma0, n_obs)})
    out_decay = run_monitor(df_decay, "value", mu0=mu0, k=k, h=h,
                             theta=0.01, sigma=sigma0, alpha=0.2, beta=0.2, direction="down")
    expect("run_monitor flags PAUSE or a terminal SPRT state on a clearly decaying stream",
           out_decay["state"].iloc[-1] in ("PAUSE", "ACCEPT_H0", "ACCEPT_H1"))

    # non-finite observations must not silently corrupt the running statistic to NaN
    df_nan = pd.DataFrame({"value": [0.001, np.nan, 0.001, np.inf, 0.001, -np.inf, 0.001]})
    out_nan = run_monitor(df_nan, "value", mu0=0.0, k=0.0005, h=10.0,
                           theta=0.002, sigma=0.01, alpha=0.1, beta=0.1)
    expect("run_monitor never produces a NaN running statistic from non-finite observations",
           out_nan["s_cusum"].notna().all() and out_nan["lambda_sprt"].notna().all())
    expect("run_monitor: invalid_data_seen is False before the first bad row, True from "
           "then on (sticky), matching the 3 non-finite rows at positions 1, 3, 5",
           list(out_nan["invalid_data_seen"]) ==
           [False, True, True, True, True, True, True])

    # (2026-10-08, round 1, MAJOR): a terminal SPRT decision made BEFORE
    # a later non-finite observation must not hide that later bad data -- values=[4, NaN] with
    # theta=sigma=1 decides ACCEPT_H1 at row 0 (sticky, correctly stays ACCEPT_H1 in `state`),
    # but invalid_data_seen must still go True at row 1, independently of `state`.
    df_decided_then_bad = pd.DataFrame({"value": [4.0, np.nan]})
    out_decided_then_bad = run_monitor(df_decided_then_bad, "value", mu0=0.0, k=0.0, h=1.0,
                                        theta=1.0, sigma=1.0, alpha=0.05, beta=0.05)
    expect("run_monitor: a terminal decision stays in `state`, but invalid_data_seen still "
           "surfaces a LATER bad observation independently",
           out_decided_then_bad["state"].iloc[-1] == "ACCEPT_H1"
           and bool(out_decided_then_bad["invalid_data_seen"].iloc[-1]) is True
           and not bool(out_decided_then_bad["invalid_data_seen"].iloc[0]))

    # PAUSE-to-terminal transition: a CUSUM alarm (PAUSE) that later resolves into a decided
    # SPRT must show PAUSE first, then the terminal state, in that row order. CUSUM is tuned
    # tight (small h) to alarm almost immediately on a real shift; SPRT tests an UNRELATED,
    # much smaller claimed edge (theta=0.001) at strict error rates, so it needs many more
    # observations of the same (much larger) realized shift before it can decide -- giving a
    # genuine multi-row PAUSE window ahead of the eventual terminal state.
    rng_pause = np.random.default_rng(5)
    df_pause_then_h0 = pd.DataFrame({"value": rng_pause.normal(-0.01, 0.01, 120)})
    out_pause_then_h0 = run_monitor(df_pause_then_h0, "value", mu0=0.0, k=0.005, h=0.01,
                                     theta=0.001, sigma=0.01, alpha=0.01, beta=0.01)
    states_seen = list(out_pause_then_h0["state"])
    expect(f"run_monitor shows a PAUSE-then-terminal transition in row order (states: "
           f"{states_seen})",
           "PAUSE" in states_seen
           and states_seen.index("PAUSE") < len(states_seen) - 1
           and states_seen[-1] in ("ACCEPT_H0", "ACCEPT_H1"))

    # misspelled value_col fails closed (raises), not silently empty
    expect("run_monitor raises on a misspelled value_col",
           _raises(lambda: run_monitor(df_nan, "does_not_exist", 0.0, 0.1, 1.0, 0.01, 0.01)))

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except (ValueError, ZeroDivisionError):
        return True


# ----------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv")
    ap.add_argument("--value-col")
    ap.add_argument("--date-col")
    ap.add_argument("--mu0", type=float, default=0.0)
    ap.add_argument("--target-shift", type=float,
                     help="the CUSUM allowance k is set to half of this (the shift size the "
                          "monitor should be sensitive to, in the same units as --value-col)")
    ap.add_argument("--h", type=float, help="CUSUM decision interval")
    ap.add_argument("--direction", choices=["down", "up"], default="down")
    ap.add_argument("--theta", type=float, help="SPRT H1 mean (the backtest's claimed edge)")
    ap.add_argument("--sigma", type=float, help="known per-unit standard deviation for SPRT")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--beta", type=float, default=0.05)
    ap.add_argument("--out")
    ap.add_argument("--out-csv")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _run_selftest()

    required = [args.csv, args.value_col, args.target_shift, args.h, args.theta, args.sigma]
    if any(v is None for v in required):
        ap.error("--csv, --value-col, --target-shift, --h, --theta and --sigma are all "
                  "required (or use --selftest)")
    # (2026-10-08, round 1): argparse's type=float accepts "nan"/"inf"
    # strings, and a bare `<= 0`/`== 0` comparison against NaN is always False -- mirror the
    # library's own _finite_num guards here so the CLI boundary cannot be bypassed with a
    # malformed numeric string the library would otherwise have to catch itself.
    if not _finite_num(args.mu0):
        ap.error("--mu0 must be a finite number")
    if not _finite_num(args.h) or args.h <= 0:
        ap.error("--h must be a finite number > 0")
    if not _finite_num(args.target_shift) or args.target_shift < 0:
        ap.error("--target-shift must be a finite number >= 0 (it is halved to form the "
                  "CUSUM allowance k)")
    if not _finite_num(args.sigma) or args.sigma <= 0:
        ap.error("--sigma must be a finite number > 0")
    if not _finite_num(args.theta) or args.theta == 0:
        ap.error("--theta must be a finite nonzero number")
    if not (0 < args.alpha < 1) or not (0 < args.beta < 1):
        ap.error("--alpha and --beta must both be in (0, 1)")
    # (round 1): alpha+beta>=1 inverts the Wald A/B ordering.
    if args.alpha + args.beta >= 1:
        ap.error("--alpha + --beta must be < 1 (otherwise the Wald decision boundaries are "
                  "not properly ordered)")

    df = pd.read_csv(args.csv)
    if args.value_col not in df.columns:
        print(f"ERROR: --value-col {args.value_col!r} not found in {args.csv}", file=sys.stderr)
        return 2

    k = args.target_shift / 2.0
    out = run_monitor(df, args.value_col, mu0=args.mu0, k=k, h=args.h,
                       theta=args.theta, sigma=args.sigma, alpha=args.alpha, beta=args.beta,
                       direction=args.direction)

    n = len(out)
    lines = [
        "decay_monitor.py report",
        f"input: {args.csv} ({args.value_col!r}, n={n}, {out.attrs['n_nan_excluded']} "
        "non-finite observation(s) handled fail-closed: CUSUM biases toward alarm, SPRT "
        "contributes exactly 0 -- see invalid_data_seen column, independent of the final "
        f"state, {bool(out['invalid_data_seen'].iloc[-1])} as of the last row)",
        f"CUSUM: mu0={args.mu0} k={k} (target_shift={args.target_shift}/2) h={args.h} "
        f"direction={args.direction}",
        f"  alarm at row {out.attrs['cusum_alarm_idx']}" if out.attrs['cusum_alarm_idx'] is not None
        else "  no CUSUM alarm over the series",
        f"SPRT: theta={args.theta} sigma={args.sigma} alpha={args.alpha} beta={args.beta} "
        f"A={out.attrs['sprt_A']:.4f} B={out.attrs['sprt_B']:.4f}",
        f"  decision: {out.attrs['sprt_decision']} at row {out.attrs['sprt_decision_idx']}"
        if out.attrs['sprt_decision'] is not None else "  no SPRT decision over the series",
        f"FINAL STATE: {out['state'].iloc[-1]}",
        f"final s_cusum={out['s_cusum'].iloc[-1]:.4f}  final lambda_sprt={out['lambda_sprt'].iloc[-1]:.4f}",
        "Advisory: this state is a mechanical statistic, not a kill/accept decision -- that "
        "stays with the main loop / Gate-2.",
    ]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"(written to {args.out})")
    if args.out_csv:
        Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.out_csv, index=False)
        print(f"(per-row series written to {args.out_csv})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
