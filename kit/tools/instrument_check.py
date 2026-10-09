#!/usr/bin/env python
"""instrument_check.py -- mechanical "can this field actually observe the quantity?" gate.

Run this BEFORE any outcome/return statistic is computed from a field, on every row the decision
statistic will actually consume -- not a spot sample. It never judges the hypothesis; it only checks
whether the column handed to the scorer can see the thing it is named for.

Motivation (incidents this generalizes from):
  - A "return" computed from a price field that, on part of the population, is not a market print at
    all but a fixed REFERENCE value (e.g. an offering/IPO reference price carried into every
    pre-listing row): a mode/reference-value check on the real column catches this mechanically, with
    no judgment call required.
  - A mean return that rests mostly on zero-volume carried-forward closes: a close price copied
    forward on a day with no trade is not an executable print, and a plain "is the column non-null"
    check does not catch that.
  - A silent API page cap (e.g. limit=1000, unpaged) that drops the early (losing) prints of whatever
    later became active, faking an edge ([[api-page-cap-drops-the-losses]]); a trades endpoint that
    pages NEWEST-FIRST, so an unsorted "first print" is actually the LAST print
    ([[newest-first-order-makes-first-print-a-lookahead]]).

Design constraints from an adversarial code-review pass on an earlier draft: (a) a fixed N-row sample
cannot establish it covered the exceptional regime that mattered in every case above -- so every flag
here is computed over every row supplied, not a sample; (b) "field is not constant" is not itself
evidence of an invalid instrument -- a fixed coupon or a single-instrument id is a legitimate constant,
so MODE_DOMINANT is suppressible with --allow-constant and only ever downgrades to INFO, never silently
vanishes; (c) a self-graded check is toothless -- so every flag is a mechanical count/share/example-rows
computation with fixed, CLI-printed thresholds, never a narrative judgment.

Verdict is never PASS/KILL. INSTRUMENT_NULL means NEEDS_DATA for the hypothesis, never PASS or KILL:
the field cannot be used as evidence either way until a real instrument for the quantity is found.

Fail-closed design notes carried from several review passes (kept because they are the reasons the
checks behave the way they do, not because the original review rounds matter):
  - Join/reference checks dedupe duplicate reference keys (REF_KEY_DUPLICATE) instead of silently
    multiplying the joined row count, and a null join key never matches another null join key.
  - An exact AT_REFERENCE hit on one row never suppresses floating-point-tolerance matching on other
    rows; REF_MISMATCH treats an unverifiable (non-numeric, non-finite) comparison as a mismatch, not
    as "covered with zero mismatches".
  - ORDER_VIOLATION uses non-strict (tie-tolerant) monotonicity in BOTH directions, so a tie inside a
    fully reversed sequence (e.g. [3,3,2,1] expected asc) reads as "opposite direction" (NULL), not
    "mixed order" (REVIEW).
  - A misspelled --volume-col/--order-col/--page-sizes-col/--ref-key, or a --where that filters out
    every row, surfaces a CHECK_NOT_RUN/EMPTY_INPUT flag instead of silently reporting OK.
  - A non-numeric value is coerced per-row (pd.to_numeric(errors="coerce")), never disabling a whole
    column's check; a partially-unparseable column emits a *_COVERAGE note instead of silent row loss.
  - +-inf is treated consistently regardless of a column's original string-vs-numeric dtype: a real,
    never-matching value for AT_REFERENCE, invalid for ZERO_VOLUME, unorderable for ORDER_VIOLATION.
  - A NaN/inf tolerance or threshold passed directly to the library API (bypassing CLI validation) is
    clamped to the strictest setting (fail-closed), never silently disabling the check.
  - REF_MISMATCH/same-file compare native pandas integer columns as exact int64 first, falling back to
    float64 only for non-integer input, so two genuinely distinct large integers (above 2**53, where
    float64 silently loses precision) are never equated.
  - Flooring the relative-mismatch denominator for a tiny |reference| would silently turn a RELATIVE
    tolerance into an ABSOLUTE one for small references (plausible for fractional-cent prices); the
    denominator uses the reference's own magnitude unless it is exactly 0.
  - SELF_COMPARE is a name-based heuristic with no provenance signal by design: it also fires if two
    genuinely independent sources happen to share a column name and agree on every row; rename one
    side's column if that is the actual situation.

Two opt-in checks (both off by default, same contract as every other check here -- a caller must ask
for them) came from a case where a tax-loss/basket gate passed every existing check (null/volume only)
on two corrupted series that drove the whole result: a closed-end fund's traded price that collapsed to
a few percent of its own NAV while NAV barely moved (not a real market event -- a feed fault), and a
price series with one vendor-splice jump (e.g. $3 to $193) landing on a zero-volume row. Neither pattern
is "null" or "zero", so neither existing check could see it:
  - PAIR_RATIO (`--pair-ratio COL_A COL_B --ratio-band LO HI`): flags rows where COL_A/COL_B falls
    outside [LO, HI] -- e.g. a fund's traded price vs its NAV should rarely trade outside roughly
    [0.7, 1.3] of par. COL_B == 0 with COL_A != 0 is an undefined/infinite ratio and is always flagged
    (fail-closed); 0 vs 0 is treated as agreement (mirrors `_relative_mismatch`'s zero-reference
    convention). Severity follows the same share-based NULL/REVIEW split as REF_MISMATCH
    (`--ratio-band-null`, default 0.2).
  - SPLICE_CHECK (`--splice-check COL --jump-x X [--volume-col V]`): flags a CONSECUTIVE-ROW move
    (row i vs row i-1, in the file's given order -- this check does not re-sort) whose ratio exceeds X
    or is below 1/X. A jump FROM an exact 0 to a nonzero value is always flagged (undefined ratio, same
    fail-closed convention as PAIR_RATIO); 0-to-0 is not a jump. If `--volume-col` is given, a flagged
    jump landing on a zero/null/+-inf volume row is always NULL (the vendor-splice pattern); a flagged
    jump with a real nonzero volume (a plausible stock split or a genuine repricing event) is REVIEW,
    not NULL; with no `--volume-col` the tool cannot corroborate either way and every flagged jump is
    REVIEW. Known limitation: no gap-filling across interior NaNs -- a jump is only evaluated between
    two LITERALLY adjacent rows that are both numeric/finite; a NaN row in between makes that pair
    uncheckable (reported via SPLICE_CHECK_COVERAGE) rather than comparing across the gap.

SCOPE CUT (documented limitation, not fixed): this tool assumes field magnitudes a typical financial-
research CSV/JSON actually contains -- float64-representable prices/volumes/notionals and string-typed
IDs (real-world security identifiers and filing accession numbers are alphanumeric strings, e.g.
"0001213900-23-050970", never raw int64). It is NOT exact for adversarial numeric corners: signed int64
arithmetic can overflow at +-2**62, a reference comparison against an UNMATCHED join key can force the
matched values' dtype to float64 and lose precision above 2**53 even though the matched pair started as
exact int64, and AT_REFERENCE's float subtraction can false-match two distinct integers above 2**53
against a reference of similar magnitude. Fixing all three exactly would require arbitrary-precision
(Decimal) arithmetic through every numeric path; given this tool's actual inputs, that cost was judged
disproportionate to the risk and the fix was deferred rather than attempted partially.

Library:
    check(df, field, *, role=None, ref_values=None, mode_share_max=0.5,
          allow_constant=False, volume_col=None, order_col=None, expected_order=None,
          page_sizes_col=None, api_cap=None, ref_df=None, ref_key=None, ref_field=None,
          ref_field_same_file=None, tol=0.01,
          mode_dominant_null=0.9, at_reference_null=0.5, ref_mismatch_null=0.2,
          ref_coverage_min=0.0) -> list[Flag]
    flag_pair_ratio(df, col_a, col_b, lo, hi, null_thresh=0.2) -> list[Flag]
    flag_splice_check(df, col, jump_x, volume_col=None) -> list[Flag]

CLI:
    python tools/instrument_check.py --csv F [--field COL [--field COL2 ...]]
        [--role price|count|time|id|amount] [--ref-value 25.0 --ref-value 100.0]
        [--mode-share-max 0.5] [--allow-constant] [--volume-col V]
        [--order-col T --expected-order asc|desc]
        [--page-sizes-col P --api-cap N]
        [--ref-csv R --ref-key K --ref-field F --tol 0.01]
        [--ref-field-same-file COL]
        [--pair-ratio COL_A COL_B --ratio-band LO HI [--ratio-band-null 0.2]]
        [--splice-check COL --jump-x X [--volume-col V]]
        [--where "has_G==True"] [--json] [--out OUT.txt] [--selftest]
    At least one of --field / --pair-ratio / --splice-check is required (or --selftest).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

MAX_EXAMPLES = 5

# ----------------------------------------------------------------------------- data model


@dataclass
class Flag:
    name: str
    field_name: str
    severity: str # "NULL" | "REVIEW" | "INFO"
    count: int
    denom: int
    examples: list = dc_field(default_factory=list)
    detail: str = ""

    @property
    def share(self) -> float:
        return (self.count / self.denom) if self.denom else 0.0

    def line(self) -> str:
        ex = ", ".join(str(e) for e in self.examples)
        return (
            f" [{self.severity:6s}] {self.name:16s} field={self.field_name!r} "
            f"count={self.count}/{self.denom} share={self.share:.4f}"
            + (f" examples=[{ex}]" if ex else "")
            + (f" -- {self.detail}" if self.detail else "")
        )


def _examples(df: pd.DataFrame, mask: pd.Series, n: int = MAX_EXAMPLES) -> list:
    idx = df.index[mask]
    return list(idx[:n])


def _null_mask(s: pd.Series) -> pd.Series:
    """NaN/None always null; +-inf counts as null only for numeric dtypes."""
    m = s.isna()
    if pd.api.types.is_numeric_dtype(s):
        with np.errstate(all="ignore"):
            arr = s.to_numpy(dtype="float64", na_value=0.0)
        m = m | np.isinf(arr)
    return m


# ----------------------------------------------------------------------------- individual flags


def flag_null_share(df: pd.DataFrame, field: str) -> Flag:
    n = len(df)
    m = _null_mask(df[field]) if n else pd.Series([], dtype=bool)
    cnt = int(m.sum())
    sev = "REVIEW" if cnt > 0 else "INFO"
    return Flag("NULL_SHARE", field, sev, cnt, n, _examples(df, m),
                "null/NaN/inf share of the field (denominator = all rows)")


def flag_mode_dominant(df: pd.DataFrame, field: str, mode_share_max: float,
                        allow_constant: bool, null_thresh: float) -> Flag | None:
    s = df[field]
    non_null_mask = ~_null_mask(s)
    s_nn = s[non_null_mask]
    n = len(s_nn)
    if n == 0:
        return None
    vc = s_nn.value_counts(dropna=True)
    top_val = vc.index[0]
    top_cnt = int(vc.iloc[0])
    share = top_cnt / n
    if share <= mode_share_max:
        return None
    mask = non_null_mask & (s == top_val)
    if allow_constant:
        sev = "INFO"
    elif share >= null_thresh:
        sev = "NULL"
    else:
        sev = "REVIEW"
    return Flag("MODE_DOMINANT", field, sev, top_cnt, n, _examples(df, mask),
                f"mode value={top_val!r} share={share:.4f} of non-null rows "
                f"(threshold {mode_share_max}, NULL at >={null_thresh}"
                + (", suppressed by --allow-constant" if allow_constant else "") + ")")


def flag_at_reference(df: pd.DataFrame, field: str, ref_values: list[float] | None,
                       null_thresh: float) -> list[Flag]:
    if not ref_values:
        return []
    s = df[field]
    # (round 2): a single non-numeric value used to fail
    # `s.astype(float)` for the WHOLE column, silently dropping AT_REFERENCE even when every
    # other row was clean -- coerce per-row with pd.to_numeric(errors="coerce") instead, so
    # one bad row only excludes itself.
    # (round 3): the OLD pre-filter used `_null_mask(s)` on the RAW
    # column before coercion, which treats numeric +-inf as null and excludes it -- but a
    # string column with the literal text "inf" survived coercion and stayed comparable. The
    # same underlying value (infinity) was in or out of the denominator purely based on the
    # column's original dtype. Determine validity from `sv` (the coerced value) ONLY: NaN is
    # excluded (unknown), +-inf is KEPT (a real, if unusual, number that correctly never
    # equals a finite reference and is never silently dropped).
    sv = pd.to_numeric(s, errors="coerce")
    non_null_mask = ~sv.isna()
    n = int(non_null_mask.sum())
    flags: list[Flag] = []
    # (round 4): a column that was PARTLY (not wholly) unparseable used
    # to drop those rows with no visible trace -- only a fully-unparseable column surfaced
    # CHECK_NOT_RUN. Report the excluded count whenever it's nonzero, mirroring
    # ORDER_COVERAGE's treatment of partially-unparseable order columns.
    n_raw_present = int(s.notna().sum())
    n_excluded = n_raw_present - n
    if n_excluded > 0:
        flags.append(Flag("AT_REFERENCE_COVERAGE", field, "REVIEW", n_excluded, n_raw_present,
                           _examples(df, s.notna() & sv.isna()),
                           f"{n_excluded}/{n_raw_present} non-null values could not be "
                           "coerced to numeric and are excluded from AT_REFERENCE"))
    if n == 0:
        # (round 3): a column with zero numerically-usable values (e.g.
        # every value is a non-numeric string) used to return None here -- silence, not a
        # visible "this check could not run".
        flags.append(Flag("CHECK_NOT_RUN", field, "REVIEW", 0, len(df), [],
                          "0 rows coerced to numeric -- AT_REFERENCE could not run"))
        return flags
    # robust float-equality against EVERY ref value independently .
    mask = pd.Series(False, index=s.index)
    for rv in ref_values:
        mask = mask | (non_null_mask & (sv - rv).abs().le(1e-9))
    cnt = int(mask.sum())
    if cnt == 0:
        return flags
    share = cnt / n
    sev = "NULL" if share >= null_thresh else "REVIEW"
    flags.append(Flag("AT_REFERENCE", field, sev, cnt, n, _examples(df, mask),
                       f"share exactly at ref value(s) {ref_values} (NULL at >={null_thresh})"))
    return flags


def flag_zero_volume(df: pd.DataFrame, field: str, volume_col: str | None) -> Flag | None:
    if not volume_col:
        return None
    if volume_col not in df.columns:
        return Flag("CHECK_NOT_RUN", field, "REVIEW", 0, 0, [],
                    f"--volume-col {volume_col!r} not found in frame -- ZERO_VOLUME was "
                    "requested but could not run (misspelled column name?)")
    s = df[field]
    non_null_mask = ~_null_mask(s)
    n = int(non_null_mask.sum())
    if n == 0:
        # (round 5): if the PRIMARY field has zero non-null rows,
        # ZERO_VOLUME used to return silent None -- NULL_SHARE still flags the all-null
        # field, so the verdict was never falsely OK, but the report's "every requested
        # check did run" framing was wrong. Make the non-run explicit.
        return Flag("CHECK_NOT_RUN", field, "REVIEW", 0, len(df), [],
                    f"{field!r} has 0 non-null rows -- ZERO_VOLUME has nothing to evaluate")
    # (round 2): comparing an object/string-typed volume column
    # directly against 0 silently never matches ("0" != 0), so ZERO_VOLUME could be
    # requested and simply never fire on a malformed/string CSV column. Coerce first; a
    # value that fails coercion is unverifiable and counted fail-closed (as zero/null-like),
    # consistent with the fail-closed rule used for REF_MISMATCH.
    # (round 3): an infinite volume coerced successfully (it is not
    # NaN and is not == 0), so it used to pass the executable-print check -- but +-inf is
    # not a real traded size either. Treat non-finite volume as invalid too.
    vol_num = pd.to_numeric(df[volume_col], errors="coerce")
    vol_arr = vol_num.to_numpy(dtype="float64", na_value=np.nan)
    with np.errstate(all="ignore"):
        vol_bad = np.isnan(vol_arr) | (vol_arr == 0) | np.isinf(vol_arr)
    vol_zero_or_null = pd.Series(vol_bad, index=vol_num.index)
    mask = non_null_mask & vol_zero_or_null
    cnt = int(mask.sum())
    if cnt == 0:
        return None
    share = cnt / n
    return Flag("ZERO_VOLUME", field, "REVIEW", cnt, n, _examples(df, mask),
                f"{volume_col} is 0 or null on these rows -- the price on them is not an "
                "executable print (always REVIEW-level per spec, never NULL alone)")


def flag_page_cap(df: pd.DataFrame, field: str, page_sizes_col: str | None,
                   api_cap: float | None) -> list[Flag]:
    if not page_sizes_col or api_cap is None:
        return []
    if page_sizes_col not in df.columns:
        return [Flag("CHECK_NOT_RUN", field, "REVIEW", 0, 0, [],
                     f"--page-sizes-col {page_sizes_col!r} not found in frame -- PAGE_CAP was "
                     "requested but could not run (misspelled column name?)")]
    # (round 2): same string-vs-numeric comparison bug as ZERO_VOLUME --
    # coerce before comparing to --api-cap.
    raw = df[page_sizes_col]
    s_num = pd.to_numeric(raw, errors="coerce")
    # (round 5): a NaN page-size value is not "numerically usable" but
    # also was not counted as "excluded" (n_raw_present used raw.notna(), which a NaN value
    # fails too, so both numerator and denominator silently dropped it together and the gap
    # was invisible). There is no separate NULL_SHARE-style check for an auxiliary column
    # like this one, so use the full row count as the denominator: every row the --page-
    # sizes-col check was asked to cover, null or not.
    s_num_arr = s_num.to_numpy(dtype="float64", na_value=np.nan)
    with np.errstate(all="ignore"):
        non_null_mask = pd.Series(np.isfinite(s_num_arr), index=s_num.index)
    n = int(non_null_mask.sum())
    flags: list[Flag] = []
    # (round 4): a partially (not wholly) unparseable page-size column
    # silently dropped the unparseable rows with no coverage note.
    n_excluded = len(df) - n
    if n_excluded > 0:
        flags.append(Flag("PAGE_CAP_COVERAGE", field, "REVIEW", n_excluded, len(df),
                           _examples(df, ~non_null_mask),
                           f"{n_excluded}/{len(df)} --page-sizes-col values (null, "
                           "non-finite, or non-numeric) are excluded from PAGE_CAP"))
    if n == 0:
        # (round 3): a page-size column with zero numerically-usable
        # values used to return None (silence) instead of a visible unevaluated check.
        flags.append(Flag("CHECK_NOT_RUN", field, "REVIEW", 0, len(df), [],
                          f"0 rows of --page-sizes-col {page_sizes_col!r} coerced to numeric "
                          "-- PAGE_CAP could not run"))
        return flags
    mask = non_null_mask & (s_num == api_cap)
    cnt = int(mask.sum())
    if cnt == 0:
        return flags
    share = cnt / n
    flags.append(Flag("PAGE_CAP", field, "NULL", cnt, n, _examples(df, mask),
                       f"{page_sizes_col} == --api-cap {api_cap} on {cnt} page(s) -- "
                       "pagination likely truncated silently (any occurrence is NULL)"))
    return flags


def flag_order_violation(df: pd.DataFrame, field: str, order_col: str | None,
                          expected_order: str | None) -> list[Flag]:
    if not order_col or not expected_order:
        return []
    if order_col not in df.columns:
        return [Flag("CHECK_NOT_RUN", field, "REVIEW", 0, 0, [],
                     f"--order-col {order_col!r} not found in frame -- ORDER_VIOLATION was "
                     "requested but could not run (misspelled column name?)")]
    raw = df[order_col]
    # (round 3): trying pd.to_datetime() FIRST on an already-numeric
    # column silently reinterprets the numbers as nanosecond epoch timestamps -- small
    # floats like [0.9, 0.1] both round to the same nanosecond-resolution timestamp, so a
    # real reversal disappeared as a tie. Numeric columns stay numeric; only object/string
    # columns are tried as dates.
    if pd.api.types.is_numeric_dtype(raw):
        # (round 4): Series.diff() subtracts in the ORIGINAL numeric
        # dtype. An unsigned column (e.g. uint8 [1, 0]) wraps to 255 instead of -1, which can
        # flip the direction verdict. Force a signed float dtype so diff() can never wrap.
        s = pd.to_numeric(raw, errors="coerce").astype("float64")
    else:
        s = pd.to_datetime(raw, errors="coerce")
        if s.isna().all():
            s = pd.to_numeric(raw, errors="coerce").astype("float64")
    valid = ~s.isna()
    if pd.api.types.is_float_dtype(s):
        # (round 4): +-inf survives `~s.isna()` (inf is not NaN), and a
        # diff against infinity produces an undefined/NaN result that corrupted the
        # opposite-direction classification. An order column value of infinity cannot be
        # meaningfully ordered against anything; treat it the same as unparseable.
        finite = np.isfinite(s.to_numpy(dtype="float64", na_value=0.0))
        valid = valid & finite
    s_valid = s[valid]
    n_invalid = int((~valid).sum())

    flags: list[Flag] = []
    if n_invalid > 0:
        # (review round 2): unparseable order values used to
        # vanish silently whenever >=2 valid values remained, so a partially-corrupt order
        # column could still report a clean order check. Always surface how many rows were
        # excluded, regardless of whether the remainder passed.
        flags.append(Flag("ORDER_COVERAGE", field, "REVIEW", n_invalid, len(df),
                           _examples(df, ~valid),
                           f"{n_invalid}/{len(df)} --order-col values were unparseable and "
                           "excluded from the ORDER_VIOLATION check below"))

    if len(s_valid) < 2:
        flags.append(Flag("CHECK_NOT_RUN", field, "REVIEW", n_invalid, len(df), [],
                          f"--order-col {order_col!r} had fewer than 2 parseable values -- "
                          "ORDER_VIOLATION could not run"))
        return flags

    # (review round 2): tz-aware timestamps converted to numpy via
    # .to_numpy() become an object array of Timestamps, whose np.diff() yields Timedelta
    # objects that raise TypeError when compared to a plain int. Stay in pandas (Series.diff)
    # the whole way -- it handles tz-aware, tz-naive and plain-numeric columns uniformly and
    # always returns a diff Series comparable to 0.
    diffs = s_valid.diff().iloc[1:]
    if pd.api.types.is_timedelta64_dtype(diffs):
        dvals = diffs.dt.total_seconds().to_numpy()
    else:
        dvals = pd.to_numeric(diffs, errors="coerce").to_numpy()
    idx = diffs.index
    total_pairs = len(dvals)

    # (round 1): ties must not downgrade a fully-reversed sequence to
    # "mixed order". Expected-direction compliance allows ties (non-strict monotone);
    # "opposite direction" is likewise tested as non-strict monotone in the other sense, so
    # e.g. [3,3,2,1] expected asc is correctly classified NULL/opposite, not REVIEW/mixed.
    if expected_order == "asc":
        expected_ok = bool(np.all(dvals >= 0))
        opposite_ok = bool(np.all(dvals <= 0))
        violating = dvals < 0
    else:
        expected_ok = bool(np.all(dvals <= 0))
        opposite_ok = bool(np.all(dvals >= 0))
        violating = dvals > 0

    cnt = int(violating.sum())
    if cnt == 0 or expected_ok:
        return flags
    viol_positions = idx[violating]
    mask = pd.Series(False, index=df.index)
    mask.loc[viol_positions] = True
    if opposite_ok:
        sev = "NULL"
        direction = f"monotone in the OPPOSITE direction (expected {expected_order})"
    else:
        sev = "REVIEW"
        direction = f"not monotone {expected_order} (mixed order, {cnt}/{total_pairs} pairs violate)"
    flags.append(Flag("ORDER_VIOLATION", field, sev, cnt, total_pairs, _examples(df, mask),
                       direction))
    return flags


def _relative_mismatch(a: pd.Series, b: pd.Series, tol: float) -> pd.Series:
    """True where a vs b counts as a mismatch. Non-finite (inf/-inf/NaN) on either side is
    ALWAYS a mismatch ."""
    # (round 3): `tol=float("nan")` makes every `rel > tol` comparison
    # False (NaN comparisons are always False), silently disabling REF_MISMATCH entirely --
    # reachable through the public check() API even with CLI-level --tol validation in
    # place. Fail closed here too: an invalid tol is treated as tol=0 (require exact
    # equality), never as "never mismatch".
    if not (isinstance(tol, (int, float)) and tol == tol and tol not in (float("inf"), float("-inf")) and tol >= 0):
        tol = 0.0

    # (round 4, BLOCKER): unconditionally casting to float64 loses
    # precision above 2**53 (e.g. large CUSIP-adjacent integer ids or cents-denominated
    # notionals), silently equating genuinely distinct values. When BOTH sides are already a
    # native pandas integer dtype (no NaN forced an upcast to float), compare the exact
    # integers first; fall back to float only for non-integer-dtype input.
    if pd.api.types.is_integer_dtype(a) and pd.api.types.is_integer_dtype(b):
        try:
            a_i = a.astype("int64")
            b_i = b.astype("int64")
            diff = (a_i - b_i).abs()
            if tol <= 0:
                return diff != 0
            denom_i = b_i.abs().where(b_i.abs() > 0, other=1)
            with np.errstate(all="ignore"):
                rel_i = diff.astype(float) / denom_i.astype(float)
            return rel_i > tol
        except (OverflowError, ValueError, TypeError):
            pass # fall through to the float path below

    af = a.astype(float)
    bf = b.astype(float)
    nonfinite = ~np.isfinite(af) | ~np.isfinite(bf)
    # (round 5, BLOCKER): flooring the denominator to 1.0 whenever
    # |b| <= 1e-12 silently turns a RELATIVE tolerance into an ABSOLUTE one for any small
    # reference -- a=2e-13 vs b=1e-13 (100% relative difference, a plausible pair of
    # fractional-cent crypto prices) computed (a-b)/1.0 = 1e-13 and reported "agreement" at
    # the default tol=0.01. Only b == 0 EXACTLY makes a relative ratio undefined; use b's own
    # magnitude as the denominator for every genuinely nonzero (however small) reference, and
    # treat a reference of exactly 0 as a special case: any nonzero `a` is then an infinite
    # relative difference (mismatch), and 0 vs 0 is agreement.
    zero_ref = bf == 0
    denom = bf.abs().where(~zero_ref, other=1.0)
    with np.errstate(all="ignore"):
        rel = (af - bf).abs() / denom
    mismatch = rel > tol
    mismatch = mismatch.where(~zero_ref, af != 0)
    return nonfinite | mismatch


def _dedupe_ref_map(key_s: pd.Series, val_s: pd.Series) -> tuple[pd.Series, int]:
    """Build a key->value lookup Series for `.map()`, dropping null keys and collapsing
    duplicate keys (keep first) so a join can never silently multiply rows . `key_s` and `val_s` may be the SAME column object (ref_key ==
    ref_field) -- this is read-only positional slicing, so that is safe."""
    non_null = key_s.notna()
    dup = key_s.duplicated(keep="first") & non_null
    n_dup = int(dup.sum())
    keep = (non_null & ~dup).to_numpy()
    mapping = pd.Series(val_s.to_numpy()[keep], index=key_s.to_numpy()[keep])
    return mapping, n_dup


def flag_ref_mismatch(df: pd.DataFrame, field: str, ref_df: pd.DataFrame | None,
                       ref_key: str | None, ref_field: str | None, tol: float,
                       ref_mismatch_null: float, key_col: str | None = None,
                       ref_coverage_min: float = 0.0) -> list[Flag]:
    if ref_df is None or not ref_key or not ref_field:
        return []
    kcol = key_col or ref_key
    if kcol not in df.columns or ref_key not in ref_df.columns or ref_field not in ref_df.columns:
        return [Flag("CHECK_NOT_RUN", field, "REVIEW", 0, 0, [],
                      "join/field columns not found -- REF_MISMATCH could not run")]

    flags: list[Flag] = []
    # review round 2 rewrite (2026-10-05): build the join with Series.map() instead of
    # DataFrame.merge(). This sidesteps three round-1/round-2 bugs at once: (finding 8) a
    # per-row sentinel for null keys could coincidentally collide with a real reference key;
    # (finding 9) reset_index() only special-cased a literal column named "index", so a
    # named DataFrame index broke the join; (finding 10) selecting the same column twice
    # (field==kcol, or ref_key==ref_field) via df[[a, a]] + a rename dict produced a
    # dict-key collision that silently dropped one side. .map() needs no column selection,
    # no reset_index, and NaN keys are never "found" in a Series index, so they can never
    # match -- all three classes of bug are structurally impossible here, not patched.
    key_map, n_dup = _dedupe_ref_map(ref_df[ref_key], ref_df[ref_field])
    if n_dup:
        flags.append(Flag("REF_KEY_DUPLICATE", field, "REVIEW", n_dup, len(ref_df), [],
                           f"--ref-csv had {n_dup} duplicate {ref_key!r} key(s) after the "
                           "first occurrence; collapsed before joining so duplicates cannot "
                           "multiply the mismatch denominator"))

    left_key = df[kcol]
    left_val = df[field]
    right_val = left_key.map(key_map) # NaN wherever the key is null or absent from key_map

    # (round 3): basing "matched" on right_val.notna() conflated "key
    # not found" with "key found, but the reference value itself is NaN" -- both produce
    # NaN from .map(), so a genuinely-present-but-null reference value was excluded from
    # coverage entirely instead of being fail-closed as an unverifiable mismatch. Determine
    # "joined" purely from key presence; let the existing to_numeric/coercion-failed logic
    # below fail-close on a null (or otherwise non-numeric) reference value exactly like it
    # already does for a non-numeric string.
    key_found = left_key.notna() & left_key.isin(key_map.index)
    matched = key_found & left_val.notna()
    coverage = int(matched.sum())
    total = len(df)
    cov_detail = f"join coverage {coverage}/{total} rows ({(coverage/total if total else 0):.1%})"

    if field == ref_field and coverage > 0:
        identical_share = (left_val[matched] == right_val[matched]).mean()
        if identical_share == 1.0:
            flags.append(Flag("SELF_COMPARE", field, "NULL", coverage, coverage, [],
                               f"{field!r} compared against a field of the same name "
                               f"({ref_field!r}) and identical on 100% of {coverage} joined "
                               "rows -- this is a self-comparison, not independent evidence. "
                               "Name-based heuristic (no provenance signal available): two "
                               "genuinely independent sources that happen to share a column "
                               "name and agree exactly will also trip this -- rename one "
                               "side's column if that is actually the case."))
            return flags

    flags.append(Flag("REF_COVERAGE", field, "INFO", coverage, total, [], cov_detail))
    # (round 4): coverage below 100% never escalated past INFO, so a
    # reference that matched e.g. 1/100 rows (and agreed on that one) could still read as a
    # clean OK with 99 rows unverified. Opt-in via --ref-coverage-min (default 0.0 = off, to
    # keep the original spec's default severity rules unchanged); when set, under-coverage
    # is a visible REVIEW, separate from REF_MISMATCH.
    cov_frac = (coverage / total) if total else 0.0
    if ref_coverage_min > 0 and coverage > 0 and cov_frac < ref_coverage_min:
        flags.append(Flag("LOW_REF_COVERAGE", field, "REVIEW", coverage, total, [],
                           f"join covered only {cov_frac:.1%} of rows, below "
                           f"--ref-coverage-min={ref_coverage_min:.1%} -- most rows are "
                           "unverified against the reference, not just sub-threshold"))
    if coverage == 0:
        # (round 2): zero rows joined used to report only an INFO
        # coverage note and nothing else, so a completely unmatched reference could still
        # read as OK. Zero evaluated rows is never a clean pass.
        flags.append(Flag("CHECK_NOT_RUN", field, "REVIEW", 0, total, [],
                           "the join matched 0 rows -- REF_MISMATCH could not evaluate "
                           "anything (check --ref-key / key dtypes / overlap)"))
        return flags

    l = pd.to_numeric(left_val[matched], errors="coerce")
    r = pd.to_numeric(right_val[matched], errors="coerce")
    coercion_failed = l.isna() | r.isna()
    mism = pd.Series(False, index=l.index)
    ok = ~coercion_failed
    if ok.any():
        mism.loc[ok] = _relative_mismatch(l[ok], r[ok], tol).to_numpy()
    # (round 1): a value that cannot be coerced to numeric must count
    # as an unverifiable mismatch (fail-closed), never as silent full coverage.
    mism.loc[coercion_failed] = True
    cnt = int(mism.sum())
    n = int(matched.sum())
    if cnt > 0:
        share = cnt / n
        sev = "NULL" if share > ref_mismatch_null else "REVIEW"
        orig_idx = list(mism.index[mism][:MAX_EXAMPLES])
        note = ""
        if coercion_failed.any():
            note = (f" ({int(coercion_failed.sum())} could not be coerced to numeric and "
                     "are counted fail-closed as mismatches)")
        flags.append(Flag("REF_MISMATCH", field, sev, cnt, n, orig_idx,
                           f"differs from ref field {ref_field!r} by > tol={tol} relative "
                           f"on {cnt}/{n} joined rows (NULL at share>{ref_mismatch_null})" + note))
    return flags


def flag_ref_same_file(df: pd.DataFrame, field: str, other_field: str, tol: float,
                        ref_mismatch_null: float, ref_coverage_min: float = 0.0) -> list[Flag]:
    if other_field not in df.columns:
        return [Flag("CHECK_NOT_RUN", field, "REVIEW", 0, 0, [],
                      f"--ref-field-same-file {other_field!r} not found in frame")]
    if field == other_field:
        n = len(df)
        return [Flag("SELF_COMPARE", field, "NULL", n, n, [],
                      "compared a field against itself (identical column name)")]
    a = df[field]
    b = df[other_field]
    # (round 2): _null_mask() also treats +-inf as null, which used to
    # drop infinite rows from `both_valid` BEFORE _relative_mismatch() could apply its
    # fail-closed non-finite rule -- the external-ref path already counted those as
    # mismatches, so the two ref paths disagreed on an equivalent input.
    # (round 3): requiring b.notna() here meant a genuinely-missing
    # reference value (NaN in `other_field`) was excluded from the comparable population
    # instead of being fail-closed as an unverifiable mismatch -- the same bug as the
    # cross-file path. Only the FIELD under test needs to be present; a missing comparison
    # value falls through to the existing coercion_failed fail-closed path below.
    both_valid = a.notna()
    n = int(both_valid.sum())
    flags: list[Flag] = []
    if n == 0:
        flags.append(Flag("CHECK_NOT_RUN", field, "REVIEW", 0, len(df), [],
                           f"0 rows had {field!r} non-null -- REF_MISMATCH could not "
                           "evaluate anything"))
        return flags
    equal_mask = (a[both_valid] == b[both_valid])
    identical_share = equal_mask.mean()
    # SELF_COMPARE fires only when identical on 100% of rows AND the two columns share the
    # same source column name -- names differ here, so even a 100% identical run is a
    # legitimate (if surprising) finding, not a self-comparison bug.
    flags.append(Flag("REF_COVERAGE", field, "INFO", n, len(df),
                       [], f"same-file comparison vs {other_field!r}: {n}/{len(df)} rows "
                           f"had {field!r} non-null; identical on {identical_share:.1%} of those"))
    # (round 4): see the cross-file REF_COVERAGE comment -- same opt-in
    # low-coverage visibility here.
    cov_frac = n / len(df) if len(df) else 0.0
    if ref_coverage_min > 0 and cov_frac < ref_coverage_min:
        flags.append(Flag("LOW_REF_COVERAGE", field, "REVIEW", n, len(df), [],
                           f"only {cov_frac:.1%} of rows had {field!r} non-null, below "
                           f"--ref-coverage-min={ref_coverage_min:.1%}"))
    l = pd.to_numeric(a[both_valid], errors="coerce")
    r = pd.to_numeric(b[both_valid], errors="coerce")
    coercion_failed = l.isna() | r.isna()
    mism = pd.Series(False, index=l.index)
    ok = ~coercion_failed
    if ok.any():
        mism.loc[ok] = _relative_mismatch(l[ok], r[ok], tol).to_numpy()
    mism.loc[coercion_failed] = True
    cnt = int(mism.sum())
    if cnt > 0:
        share = cnt / n
        sev = "NULL" if share > ref_mismatch_null else "REVIEW"
        idx = list(mism.index[mism][:MAX_EXAMPLES])
        note = ""
        if coercion_failed.any():
            note = (f" ({int(coercion_failed.sum())} could not be coerced to numeric and "
                     "are counted fail-closed as mismatches)")
        flags.append(Flag("REF_MISMATCH", field, sev, cnt, n, idx,
                           f"differs from same-file field {other_field!r} by > tol={tol} "
                           f"relative on {cnt}/{n} rows (NULL at share>{ref_mismatch_null})" + note))
    return flags


def _finite_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and \
        v not in (float("inf"), float("-inf"))


def flag_pair_ratio(df: pd.DataFrame, col_a: str, col_b: str, lo: float, hi: float,
                     null_thresh: float = 0.2) -> list[Flag]:
    """PAIR_RATIO: flags rows whose col_a/col_b falls outside [lo, hi] -- e.g. a closed-end fund's
    traded price vs its NAV, which should rarely trade outside roughly [0.7, 1.3] of par.

    The CLI validates lo/hi/null_thresh for finiteness, but this library function did not -- a caller going through check()/the library
    API directly with e.g. hi=nan would get a ratio>hi comparison that is always False, silently
    disabling the check rather than failing closed (verdict_for([]) == "OK"). Validated here too,
    same as every other threshold in this file (mode_share_max, tol, ...).
    """
    field_name = f"{col_a}/{col_b}"
    if not (_finite_num(lo) and _finite_num(hi)) or lo < 0 or hi <= lo:
        raise ValueError(f"flag_pair_ratio: lo={lo!r}, hi={hi!r} must be finite with 0 <= lo < hi")
    if not (_finite_num(null_thresh) and null_thresh >= 0):
        # fail closed to the strictest setting (any violation at all is NULL), mirroring
        # _relative_mismatch's fail-closed tol=0 clamp for an invalid tolerance.
        null_thresh = 0.0
    if col_a not in df.columns or col_b not in df.columns:
        return [Flag("CHECK_NOT_RUN", field_name, "REVIEW", 0, 0, [],
                      f"--pair-ratio column(s) not found: {col_a!r}, {col_b!r}")]
    if len(df) == 0:
        return [Flag("EMPTY_INPUT", field_name, "REVIEW", 0, 0, [],
                      "0 rows to check -- PAIR_RATIO could not run")]
    if col_a == col_b:
        # mirrors flag_ref_same_file's SELF_COMPARE: a ratio of a column against itself is
        # always exactly 1 and proves nothing about an independent reference.
        n = len(df)
        return [Flag("SELF_COMPARE", field_name, "NULL", n, n, [],
                      "col_a and col_b are the same column -- the ratio is trivially 1 "
                      "everywhere, not independent evidence")]

    n_total = len(df)
    a = pd.to_numeric(df[col_a], errors="coerce")
    b = pd.to_numeric(df[col_b], errors="coerce")
    a_arr = a.to_numpy(dtype="float64", na_value=np.nan)
    b_arr = b.to_numpy(dtype="float64", na_value=np.nan)
    with np.errstate(all="ignore"):
        present_arr = np.isfinite(a_arr) & np.isfinite(b_arr)
    present = pd.Series(present_arr, index=df.index)
    n_present = int(present.sum())

    flags: list[Flag] = []
    n_excluded = n_total - n_present
    if n_excluded > 0:
        flags.append(Flag("PAIR_RATIO_COVERAGE", field_name, "REVIEW", n_excluded, n_total,
                           _examples(df, ~present),
                           f"{n_excluded}/{n_total} rows excluded: {col_a!r} or {col_b!r} is "
                           "null, non-numeric, or +-inf and cannot form a ratio"))
    if n_present == 0:
        flags.append(Flag("CHECK_NOT_RUN", field_name, "REVIEW", 0, n_total, [],
                          "0 rows had both columns numeric and finite -- PAIR_RATIO could not run"))
        return flags

    # fail-closed convention matching _relative_mismatch: col_b == 0 exactly makes the ratio
    # undefined. A nonzero col_a against a zero col_b is an infinite/undefined ratio -- always
    # outside any finite band. A zero col_a against a zero col_b (0 vs 0) is agreement, not a
    # violation (mirrors the zero-reference special case used elsewhere in this file).
    b_zero = present_arr & (b_arr == 0)
    normal = present_arr & ~b_zero
    outside = np.zeros(n_total, dtype=bool)
    with np.errstate(all="ignore"):
        ratio = np.full(n_total, np.nan)
        ratio[normal] = a_arr[normal] / b_arr[normal]
        outside[normal] = (ratio[normal] < lo) | (ratio[normal] > hi)
    outside[b_zero & (a_arr != 0)] = True

    mask = pd.Series(outside, index=df.index) & present
    cnt = int(mask.sum())
    if cnt == 0:
        return flags
    share = cnt / n_present
    sev = "NULL" if share > null_thresh else "REVIEW"
    flags.append(Flag("PAIR_RATIO", field_name, sev, cnt, n_present, _examples(df, mask),
                       f"{col_a}/{col_b} outside band [{lo}, {hi}] on {cnt}/{n_present} rows "
                       f"(NULL at share>{null_thresh}); {col_b}==0 with {col_a}!=0 always counts"))
    return flags


def flag_splice_check(df: pd.DataFrame, col: str, jump_x: float,
                       volume_col: str | None = None) -> list[Flag]:
    """SPLICE_CHECK: flags a consecutive-row (row i vs row i-1, in the given row order -- no
    re-sort) move whose ratio exceeds jump_x or is below 1/jump_x. If volume_col is given, a
    flagged jump landing on a zero/null/+-inf volume row is NULL (a vendor-splice pattern: an
    EOD price jumping by ~60x at volume 0); without that corroboration (or with a real nonzero
    volume -- a plausible stock split) it is REVIEW, not NULL.

    jump_x was unvalidated at the library boundary in an earlier draft
    (a NaN/<=1 jump_x silently disables every comparison, same class of bug as PAIR_RATIO's
    thresholds). Separately, a misspelled volume_col used to be reported
    only when at least one jump was ALSO found (the CHECK_NOT_RUN append sat after the
    `cnt == 0: return` early exit) -- a broken --volume-col configuration on a clean price
    series silently read as a full pass. volume_col validity is now checked unconditionally,
    before any jump-count-dependent branch."""
    field_name = col
    if not (_finite_num(jump_x) and jump_x > 1.0):
        raise ValueError(f"flag_splice_check: jump_x={jump_x!r} must be a finite number > 1")
    if col not in df.columns:
        return [Flag("CHECK_NOT_RUN", field_name, "REVIEW", 0, 0, [],
                      f"--splice-check column {col!r} not found")]
    if len(df) == 0:
        return [Flag("EMPTY_INPUT", field_name, "REVIEW", 0, 0, [],
                      "0 rows to check -- SPLICE_CHECK could not run")]

    flags: list[Flag] = []
    vol_bad = None
    if volume_col:
        if volume_col not in df.columns:
            flags.append(Flag("CHECK_NOT_RUN", field_name, "REVIEW", 0, 0, [],
                              f"--volume-col {volume_col!r} not found -- SPLICE_CHECK volume "
                              "corroboration could not run; flagged jumps below are scored as "
                              "if no --volume-col were given"))
        else:
            vol_num = pd.to_numeric(df[volume_col], errors="coerce")
            vol_arr = vol_num.to_numpy(dtype="float64", na_value=np.nan)
            with np.errstate(all="ignore"):
                vol_bad_arr = np.isnan(vol_arr) | (vol_arr == 0) | np.isinf(vol_arr)
            vol_bad = pd.Series(vol_bad_arr, index=df.index)

    n_total = len(df)
    s = pd.to_numeric(df[col], errors="coerce")
    s_arr = s.to_numpy(dtype="float64", na_value=np.nan)
    with np.errstate(all="ignore"):
        finite = np.isfinite(s_arr)
    n_finite = int(finite.sum())

    n_excluded = n_total - n_finite
    if n_excluded > 0:
        flags.append(Flag("SPLICE_CHECK_COVERAGE", field_name, "REVIEW", n_excluded, n_total,
                           _examples(df, pd.Series(~finite, index=df.index)),
                           f"{n_excluded}/{n_total} rows excluded: {col!r} is null, "
                           "non-numeric, or +-inf"))
    if n_finite < 2:
        flags.append(Flag("CHECK_NOT_RUN", field_name, "REVIEW", 0, n_total, [],
                          f"fewer than 2 finite values in {col!r} -- SPLICE_CHECK could not run"))
        return flags

    prev_arr = s.shift(1).to_numpy(dtype="float64", na_value=np.nan)
    with np.errstate(all="ignore"):
        prev_finite = np.isfinite(prev_arr)
    pair_valid = finite & prev_finite
    n_pairs = int(pair_valid.sum())
    if n_pairs == 0:
        flags.append(Flag("CHECK_NOT_RUN", field_name, "REVIEW", 0, n_total, [],
                          "no two literally-adjacent finite rows -- SPLICE_CHECK could not run "
                          "(every pair has a gap; see SPLICE_CHECK_COVERAGE above)"))
        return flags

    prev_zero = pair_valid & (prev_arr == 0)
    normal_pairs = pair_valid & ~prev_zero
    jump_from_zero = prev_zero & (s_arr != 0) # undefined/infinite ratio -- always a jump
    outside = np.zeros(n_total, dtype=bool)
    with np.errstate(all="ignore"):
        ratio = np.full(n_total, np.nan)
        ratio[normal_pairs] = s_arr[normal_pairs] / prev_arr[normal_pairs]
        outside[normal_pairs] = (ratio[normal_pairs] > jump_x) | (ratio[normal_pairs] < 1.0 / jump_x)
    outside[jump_from_zero] = True

    mask = pd.Series(outside, index=df.index) & pd.Series(pair_valid, index=df.index)
    cnt = int(mask.sum())
    if cnt == 0:
        return flags

    if vol_bad is not None:
        zero_vol_mask = mask & vol_bad
        other_mask = mask & ~vol_bad
        cnt_zerovol = int(zero_vol_mask.sum())
        cnt_other = int(other_mask.sum())
        if cnt_zerovol > 0:
            flags.append(Flag("SPLICE_JUMP", field_name, "NULL", cnt_zerovol, n_pairs,
                               _examples(df, zero_vol_mask),
                               f"{cnt_zerovol} jump(s) > {jump_x}x (or < 1/{jump_x}x) land on a "
                               f"zero/null/inf {volume_col} row -- vendor-splice pattern "
                               "(always NULL)"))
        if cnt_other > 0:
            flags.append(Flag("SPLICE_JUMP_CANDIDATE", field_name, "REVIEW", cnt_other, n_pairs,
                               _examples(df, other_mask),
                               f"{cnt_other} jump(s) > {jump_x}x (or < 1/{jump_x}x) with a real "
                               f"nonzero {volume_col} -- could be a genuine split/event, needs "
                               "review (not auto-NULL)"))
    else:
        flags.append(Flag("SPLICE_JUMP_CANDIDATE", field_name, "REVIEW", cnt, n_pairs,
                           _examples(df, mask),
                           f"{cnt} jump(s) > {jump_x}x (or < 1/{jump_x}x); no usable --volume-col "
                           "-- cannot corroborate against the zero-volume vendor-splice pattern"))
    return flags


# ----------------------------------------------------------------------------- public API


def check(
    df: pd.DataFrame,
    field: str,
    *,
    role: str | None = None,
    ref_values: list[float] | None = None,
    mode_share_max: float = 0.5,
    allow_constant: bool = False,
    volume_col: str | None = None,
    order_col: str | None = None,
    expected_order: str | None = None,
    page_sizes_col: str | None = None,
    api_cap: float | None = None,
    ref_df: pd.DataFrame | None = None,
    ref_key: str | None = None,
    ref_field: str | None = None,
    key_col: str | None = None,
    ref_field_same_file: str | None = None,
    tol: float = 0.01,
    mode_dominant_null: float = 0.9,
    at_reference_null: float = 0.5,
    ref_mismatch_null: float = 0.2,
    ref_coverage_min: float = 0.0,
) -> list[Flag]:
    """Run every mechanical instrument check for one field against every row of df.

    `role` is informational only (printed in the report); it does not change which checks
    run or how they are scored -- a role-conditioned branch would be exactly the kind of
    self-judged special-casing the design-gate review rejected.
    """
    if field not in df.columns:
        return [Flag("MISSING_FIELD", field, "NULL", 0, 0, [], "field not present in frame")]

    if len(df) == 0:
        # (2026-10-05): a --where filter (or an already-empty input)
        # that removes every row must never read as a clean OK -- zero rows means nothing
        # was observed, not that nothing was wrong.
        return [Flag("EMPTY_INPUT", field, "REVIEW", 0, 0, [],
                     "0 rows to check (after any --where filter) -- no instrument checks "
                     "could run; this is NOT the same as a clean pass")]

    flags: list[Flag] = []
    flags.append(flag_null_share(df, field))
    f = flag_mode_dominant(df, field, mode_share_max, allow_constant, mode_dominant_null)
    if f:
        flags.append(f)
    flags.extend(flag_at_reference(df, field, ref_values, at_reference_null))
    f = flag_zero_volume(df, field, volume_col)
    if f:
        flags.append(f)
    flags.extend(flag_page_cap(df, field, page_sizes_col, api_cap))
    flags.extend(flag_order_violation(df, field, order_col, expected_order))
    flags.extend(flag_ref_mismatch(df, field, ref_df, ref_key, ref_field, tol,
                                    ref_mismatch_null, key_col, ref_coverage_min))
    if ref_field_same_file:
        flags.extend(flag_ref_same_file(df, field, ref_field_same_file, tol, ref_mismatch_null,
                                         ref_coverage_min))
    return flags


def verdict_for(flags: list[Flag]) -> tuple[str, list[Flag]]:
    """INSTRUMENT_NULL if any flag is severity NULL; REVIEW if any is REVIEW; else OK."""
    nulls = [f for f in flags if f.severity == "NULL"]
    if nulls:
        return "INSTRUMENT_NULL", nulls
    reviews = [f for f in flags if f.severity == "REVIEW"]
    if reviews:
        return "REVIEW", reviews
    return "OK", []


# ----------------------------------------------------------------------------- report rendering


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render_report(
    *, csv_path: str | None, df: pd.DataFrame, fields: list[str],
    per_field_flags: dict[str, list[Flag]], thresholds: dict[str, Any],
    where: str | None, extra_sections: dict[str, list[Flag]] | None = None,
) -> str:
    lines = []
    lines.append("instrument_check.py report")
    lines.append(f"generated_utc: {datetime.now(timezone.utc).isoformat()}")
    if csv_path:
        lines.append(f"input: {csv_path}")
        try:
            lines.append(f"input_sha256: {sha256_file(csv_path)}")
        except OSError as e:
            lines.append(f"input_sha256: UNCONFIRMED ({e})")
    lines.append(f"row_count: {len(df)}")
    if where:
        lines.append(f"where_filter: {where!r}")
    lines.append("thresholds: " + json.dumps(thresholds, sort_keys=True))
    lines.append("")

    overall_flags: list[Flag] = []
    for fld in fields:
        flags = per_field_flags[fld]
        overall_flags.extend(flags)
        lines.append(f"FIELD: {fld}")
        verdict, triggers = verdict_for(flags)
        if not flags:
            lines.append(" (no flags raised)")
        for fl in flags:
            lines.append(fl.line())
        lines.append(f" -> field verdict: {verdict}")
        lines.append("")

    # extra_sections holds dataframe-level checks that are not tied to a single --field
    # (PAIR_RATIO's two columns, SPLICE_CHECK's row-order check) -- rendered the same way, and
    # folded into the overall verdict/exit code just like a field would be.
    for section_name, flags in (extra_sections or {}).items():
        overall_flags.extend(flags)
        lines.append(f"CHECK: {section_name}")
        verdict, triggers = verdict_for(flags)
        if not flags:
            lines.append(" (no flags raised)")
        for fl in flags:
            lines.append(fl.line())
        lines.append(f" -> check verdict: {verdict}")
        lines.append("")

    overall_verdict, triggers = verdict_for(overall_flags)
    lines.append(f"OVERALL VERDICT: {overall_verdict}")
    lines.append(
        "INSTRUMENT_NULL means NEEDS_DATA for the hypothesis, never PASS or KILL."
    )
    # (round 3): calling every REVIEW "usable with a stated caveat" was
    # wrong for CHECK_NOT_RUN / EMPTY_INPUT / ORDER_COVERAGE, which mean a requested check
    # never ran at all (0 denominator or 0 rows) -- not "ran and found a sub-threshold
    # problem". Distinguish the two REVIEW populations explicitly.
    # (review round 1): PAIR_RATIO_COVERAGE/SPLICE_CHECK_COVERAGE are
    # the same kind of "these rows were excluded, not evaluated" note as AT_REFERENCE_COVERAGE/
    # PAGE_CAP_COVERAGE and belong in the same set.
    NOT_RUN_NAMES = {"CHECK_NOT_RUN", "EMPTY_INPUT", "ORDER_COVERAGE", "MISSING_FIELD",
                     "AT_REFERENCE_COVERAGE", "PAGE_CAP_COVERAGE",
                     "PAIR_RATIO_COVERAGE", "SPLICE_CHECK_COVERAGE"}
    if overall_verdict == "REVIEW":
        not_run = [f for f in triggers if f.name in NOT_RUN_NAMES]
        sub_threshold = [f for f in triggers if f.name not in NOT_RUN_NAMES]
        if not_run and not sub_threshold:
            lines.append("REVIEW here means a REQUESTED CHECK DID NOT RUN (see "
                         f"{sorted({f.name for f in not_run})}) -- this is NOT 'ran and "
                         "found a minor problem'; fix the config/column and re-run before "
                         "trusting this field.")
        elif not_run:
            lines.append("REVIEW here is a MIX: some flags ran and found a sub-threshold "
                         f"problem ({sorted({f.name for f in sub_threshold})}), others "
                         f"({sorted({f.name for f in not_run})}) mean a requested check did "
                         "not run at all. Resolve the not-run flags before treating this as "
                         "'usable with a caveat'.")
        else:
            lines.append("REVIEW means at least one sub-threshold flag fired (e.g. "
                         "ZERO_VOLUME>0, or a share below the NULL cutoff) and every "
                         "requested check did run -- usable with a stated caveat, not clean.")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- selftest


def _raises_ic(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


def _run_selftest() -> int:
    ok = True

    def expect(name, cond):
        nonlocal ok
        status = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"[{status}] {name}")

    # MODE_DOMINANT -> NULL (share >= 0.9), and INFO when --allow-constant
    df = pd.DataFrame({"p": [25.0] * 18 + [26.0, 27.0]})
    flags = check(df, "p", mode_share_max=0.5)
    v, _ = verdict_for(flags)
    expect("MODE_DOMINANT fires NULL at 90% share", v == "INSTRUMENT_NULL"
           and any(f.name == "MODE_DOMINANT" and f.severity == "NULL" for f in flags))

    flags2 = check(df, "p", mode_share_max=0.5, allow_constant=True)
    v2, _ = verdict_for(flags2)
    expect("MODE_DOMINANT suppressed to INFO by --allow-constant",
           v2 != "INSTRUMENT_NULL"
           and any(f.name == "MODE_DOMINANT" and f.severity == "INFO" for f in flags2))

    # fully constant field with --allow-constant -> never null (REVIEW/NULL absent for that flag)
    dfc = pd.DataFrame({"c": [25.0] * 20})
    flagsc = check(dfc, "c", allow_constant=True)
    vc, _ = verdict_for(flagsc)
    expect("fully constant field + allow_constant -> not INSTRUMENT_NULL", vc != "INSTRUMENT_NULL")

    # AT_REFERENCE -> NULL
    dfr = pd.DataFrame({"g": [25.0] * 15 + [10.0, 11.0, 12.0]})
    flagsr = check(dfr, "g", ref_values=[25.0])
    vr, _ = verdict_for(flagsr)
    expect("AT_REFERENCE fires NULL at share>=0.5", vr == "INSTRUMENT_NULL"
           and any(f.name == "AT_REFERENCE" and f.severity == "NULL" for f in flagsr))

    # ZERO_VOLUME -> REVIEW only (px varied so MODE_DOMINANT does not also fire)
    dfz = pd.DataFrame({"px": [float(i) for i in range(10)], "vol": [0] * 4 + [100] * 6})
    flagsz = check(dfz, "px", volume_col="vol")
    vz, _ = verdict_for(flagsz)
    zv_flags = [f for f in flagsz if f.name == "ZERO_VOLUME"]
    expect("ZERO_VOLUME fires REVIEW (not NULL) and verdict is at most REVIEW",
           len(zv_flags) == 1 and zv_flags[0].severity == "REVIEW" and vz in ("REVIEW",))

    # PAGE_CAP -> NULL
    dfp = pd.DataFrame({"px": range(10), "pagesize": [1000] * 3 + [500] * 7})
    flagsp = check(dfp, "px", page_sizes_col="pagesize", api_cap=1000)
    vp, _ = verdict_for(flagsp)
    expect("PAGE_CAP fires NULL on any occurrence", vp == "INSTRUMENT_NULL"
           and any(f.name == "PAGE_CAP" and f.severity == "NULL" for f in flagsp))

    # ORDER_VIOLATION opposite direction -> NULL
    dfo = pd.DataFrame({"px": range(10), "t": list(range(10, 0, -1))})
    flagso = check(dfo, "px", order_col="t", expected_order="asc")
    vo, _ = verdict_for(flagso)
    expect("ORDER_VIOLATION opposite-direction fires NULL", vo == "INSTRUMENT_NULL"
           and any(f.name == "ORDER_VIOLATION" and f.severity == "NULL" for f in flagso))

    # ORDER_VIOLATION partial (mixed) -> REVIEW only
    dfo2 = pd.DataFrame({"px": range(6), "t": [1, 2, 3, 2, 5, 6]})
    flagso2 = check(dfo2, "px", order_col="t", expected_order="asc")
    vo2, _ = verdict_for(flagso2)
    ov2 = [f for f in flagso2 if f.name == "ORDER_VIOLATION"]
    expect("ORDER_VIOLATION partial fires REVIEW only",
           len(ov2) == 1 and ov2[0].severity == "REVIEW" and vo2 in ("REVIEW",))

    # NULL_SHARE -> REVIEW only, never NULL by itself
    dfn = pd.DataFrame({"px": [1.0, 2.0, np.nan, np.inf, 5.0]})
    flagsn = check(dfn, "px")
    vn, _ = verdict_for(flagsn)
    expect("NULL_SHARE fires REVIEW, never promotes to INSTRUMENT_NULL alone",
           vn != "INSTRUMENT_NULL"
           and any(f.name == "NULL_SHARE" and f.severity == "REVIEW" for f in flagsn))

    # REF_MISMATCH + REF_COVERAGE via ref_df join
    left = pd.DataFrame({"key": ["a", "b", "c", "d"], "price": [10.0, 20.0, 30.0, 999.0]})
    right = pd.DataFrame({"key": ["a", "b", "c"], "ref_price": [10.0, 20.0, 30.5]})
    flags_rm = check(left, "price", ref_df=right, ref_key="key", ref_field="ref_price",
                      key_col="key", tol=0.01)
    vrm, _ = verdict_for(flags_rm)
    expect("REF_MISMATCH fires and REF_COVERAGE reports partial join",
           any(f.name == "REF_COVERAGE" for f in flags_rm)
           and any(f.name == "REF_MISMATCH" for f in flags_rm)
           and vrm == "INSTRUMENT_NULL") # 1 bad (d has no ref) -- only 3 joined, ~0% mismatch
    # recompute expectation properly: joined=3 (a,b,c), mismatches: a ok, b ok, c (30 vs 30.5 ~1.6%>1%) mismatch => 1/3=33%>20% -> NULL
    mism_flags = [f for f in flags_rm if f.name == "REF_MISMATCH"]
    expect("REF_MISMATCH share computed over joined rows only (1/3)",
           len(mism_flags) == 1 and mism_flags[0].count == 1 and mism_flags[0].denom == 3)

    # SELF_COMPARE via ref_df: same field name, identical values
    right_self = pd.DataFrame({"key": ["a", "b", "c", "d"], "price": [10.0, 20.0, 30.0, 999.0]})
    flags_self = check(left, "price", ref_df=right_self, ref_key="key", ref_field="price",
                        key_col="key")
    expect("SELF_COMPARE fires when compared field has the same name and is identical",
           any(f.name == "SELF_COMPARE" and f.severity == "NULL" for f in flags_self))

    # same-file comparison: different names, not identical on 100% -> no SELF_COMPARE, OK/REVIEW
    dfs = pd.DataFrame({"price": [10.0, 20.0, 30.0, 40.0], "other_close": [10.0, 20.0, 30.0, 41.0]})
    flags_sf = check(dfs, "price", ref_field_same_file="other_close", tol=0.01)
    expect("same-file differing-name comparison never raises SELF_COMPARE",
           not any(f.name == "SELF_COMPARE" for f in flags_sf))

    # same-file comparison: SAME name passed twice -> trivial SELF_COMPARE
    flags_sf2 = check(dfs, "price", ref_field_same_file="price", tol=0.01)
    expect("same-file SAME-name comparison raises SELF_COMPARE",
           any(f.name == "SELF_COMPARE" for f in flags_sf2))

    # clean frame -> OK
    df_ok = pd.DataFrame({"px": np.linspace(10, 20, 50)})
    flags_ok = check(df_ok, "px")
    v_ok, _ = verdict_for(flags_ok)
    expect("clean varied field with no ref checks -> OK", v_ok == "OK")

    # --- review regression cases (2026-10-05) ---

    # AT_REFERENCE: one exact hit must not suppress fp-tolerance matching on other rows
    df_at = pd.DataFrame({"g": [25.0, 25.0 + 4e-10, 30.0, 31.0]})
    flags_at = check(df_at, "g", ref_values=[25.0], at_reference_null=0.5)
    at_flag = [f for f in flags_at if f.name == "AT_REFERENCE"][0]
    expect("AT_REFERENCE counts both the exact and the fp-tolerance match (2/4)",
           at_flag.count == 2 and at_flag.denom == 4)

    # ORDER_VIOLATION: a tie inside a fully-reversed sequence is still opposite-direction NULL
    dfo3 = pd.DataFrame({"px": range(4), "t": [3, 3, 2, 1]})
    flagso3 = check(dfo3, "px", order_col="t", expected_order="asc")
    ov3 = [f for f in flagso3 if f.name == "ORDER_VIOLATION"]
    expect("tie inside a fully-reversed sequence is still NULL opposite-direction",
           len(ov3) == 1 and ov3[0].severity == "NULL")

    # Misspelled --volume-col surfaces CHECK_NOT_RUN instead of silently vanishing
    df_misspell = pd.DataFrame({"px": np.linspace(1, 2, 10)})
    flags_misspell = check(df_misspell, "px", volume_col="does_not_exist")
    expect("misspelled --volume-col raises CHECK_NOT_RUN, not silence",
           any(f.name == "CHECK_NOT_RUN" for f in flags_misspell))

    # EMPTY_INPUT: an all-rows-filtered frame is REVIEW, never a clean OK
    df_empty = pd.DataFrame({"px": []}, dtype=float)
    flags_empty = check(df_empty, "px")
    v_empty, _ = verdict_for(flags_empty)
    expect("empty frame -> REVIEW (EMPTY_INPUT), never OK", v_empty == "REVIEW"
           and any(f.name == "EMPTY_INPUT" for f in flags_empty))

    # REF_MISMATCH: duplicate ref keys must not multiply the joined row count
    left_dup = pd.DataFrame({"key": ["a", "b", "c"], "price": [10.0, 20.0, 30.0]})
    right_dup = pd.DataFrame({"key": ["a", "a", "a", "b"], "ref_price": [10.0, 999.0, 999.0, 20.0]})
    flags_dup = check(left_dup, "price", ref_df=right_dup, ref_key="key", ref_field="ref_price",
                       key_col="key")
    cov_dup = [f for f in flags_dup if f.name == "REF_COVERAGE"][0]
    expect("duplicate ref keys collapsed before join: coverage stays 2 (a,b), not inflated",
           cov_dup.count == 2 and cov_dup.denom == 3
           and any(f.name == "REF_KEY_DUPLICATE" for f in flags_dup))

    # REF_MISMATCH: null keys on both sides must never match each other
    left_nk = pd.DataFrame({"key": ["a", None, None], "price": [10.0, 50.0, 60.0]})
    right_nk = pd.DataFrame({"key": ["a", None], "ref_price": [10.0, 999.0]})
    flags_nk = check(left_nk, "price", ref_df=right_nk, ref_key="key", ref_field="ref_price",
                      key_col="key")
    cov_nk = [f for f in flags_nk if f.name == "REF_COVERAGE"][0]
    expect("null keys never match a null key on the other side: coverage is 1, not 3",
           cov_nk.count == 1 and cov_nk.denom == 3)

    # REF_MISMATCH: a non-numeric / infinite comparison must fail closed, not silently pass
    left_inf = pd.DataFrame({"key": ["a", "b"], "price": [10.0, float("inf")]})
    right_inf = pd.DataFrame({"key": ["a", "b"], "ref_price": [10.0, 5.0]})
    flags_inf = check(left_inf, "price", ref_df=right_inf, ref_key="key", ref_field="ref_price",
                       key_col="key")
    expect("an infinite value vs a finite reference counts as a mismatch, not silent OK",
           any(f.name == "REF_MISMATCH" for f in flags_inf))

    # --- review round 2 regression cases (2026-10-05) ---

    # same-file REF_MISMATCH must treat an infinite value as a mismatch too (parity with
    # the external-ref path), not drop it from the comparable population
    df_sf_inf = pd.DataFrame({"price": [10.0, 20.0, 30.0], "other": [10.0, float("inf"), 30.0]})
    flags_sf_inf = check(df_sf_inf, "price", ref_field_same_file="other")
    cov_sf_inf = [f for f in flags_sf_inf if f.name == "REF_COVERAGE"][0]
    mism_sf_inf = [f for f in flags_sf_inf if f.name == "REF_MISMATCH"]
    expect("same-file comparison counts the infinite row as covered+mismatched, not dropped",
           cov_sf_inf.count == 3 and len(mism_sf_inf) == 1 and mism_sf_inf[0].count == 1)

    # AT_REFERENCE: one non-numeric value must not cancel the check for every other row
    df_at2 = pd.DataFrame({"g": [25.0, 25.0 + 4e-10, "bad", 30.0]})
    flags_at2 = check(df_at2, "g", ref_values=[25.0], at_reference_null=0.5)
    at2 = [f for f in flags_at2 if f.name == "AT_REFERENCE"]
    expect("one non-numeric value does not suppress AT_REFERENCE on the rest of the column",
           len(at2) == 1 and at2[0].count == 2 and at2[0].denom == 3)

    # ZERO_VOLUME / PAGE_CAP: string-typed numeric columns must still compare correctly
    df_strcols = pd.DataFrame({"px": [1.0, 2.0, 3.0], "vol": ["0", "bad", "100"],
                                "pagesz": ["1000", "500", "1000"]})
    flags_zv2 = check(df_strcols, "px", volume_col="vol")
    zv2 = [f for f in flags_zv2 if f.name == "ZERO_VOLUME"]
    expect("ZERO_VOLUME fires on a string '0' volume column (fail-closed on 'bad' too)",
           len(zv2) == 1 and zv2[0].count == 2) # "0" and "bad" both count fail-closed
    flags_pc2 = check(df_strcols, "px", page_sizes_col="pagesz", api_cap=1000)
    pc2 = [f for f in flags_pc2 if f.name == "PAGE_CAP"]
    expect("PAGE_CAP fires on a string '1000' page-size column",
           len(pc2) == 1 and pc2[0].count == 2)

    # ORDER_VIOLATION: a timezone-aware order column must not crash the check
    df_tz = pd.DataFrame({"px": range(4),
                           "t": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03",
                                                 "2026-01-04"], utc=True)})
    flags_tz = check(df_tz, "px", order_col="t", expected_order="asc")
    expect("tz-aware order column does not raise and reports no violation",
           not any(f.name == "ORDER_VIOLATION" for f in flags_tz))

    # ORDER_VIOLATION: partial unparseable order values are surfaced even when >=2 valid
    # values remain (previously vanished silently)
    df_badorder = pd.DataFrame({"px": range(3), "t": ["2026-01-01", "bad", "2026-01-02"]})
    flags_badorder = check(df_badorder, "px", order_col="t", expected_order="asc")
    expect("partially-unparseable --order-col surfaces ORDER_COVERAGE",
           any(f.name == "ORDER_COVERAGE" for f in flags_badorder))

    # REF_MISMATCH: zero join coverage must not read as OK
    left_zero = pd.DataFrame({"key": ["x", "y"], "price": [1.0, 2.0]})
    right_zero = pd.DataFrame({"key": ["a", "b"], "ref_price": [1.0, 2.0]})
    flags_zero = check(left_zero, "price", ref_df=right_zero, ref_key="key", ref_field="ref_price",
                        key_col="key")
    v_zero, _ = verdict_for(flags_zero)
    expect("zero join coverage is REVIEW (CHECK_NOT_RUN), never OK", v_zero == "REVIEW")

    # REF_MISMATCH: field == join key column must not crash (duplicate-column edge case)
    left_samecol = pd.DataFrame({"id": ["a", "b"], "ignored": [1, 2]})
    right_samecol = pd.DataFrame({"id": ["a", "b"], "ref_id": ["a", "zzz"]})
    flags_samecol = check(left_samecol, "id", ref_df=right_samecol, ref_key="id",
                           ref_field="ref_id", key_col="id")
    expect("field == join key column does not raise",
           any(f.name in ("REF_COVERAGE", "REF_MISMATCH", "SELF_COMPARE") for f in flags_samecol))

    # --- review round 3 regression cases (2026-10-05) ---

    # ORDER_VIOLATION: a numeric order column must not be reinterpreted as a nanosecond
    # epoch timestamp (which can silently collapse a real reversal into a tie)
    df_numorder = pd.DataFrame({"px": [1, 2], "t": [0.9, 0.1]})
    flags_numorder = check(df_numorder, "px", order_col="t", expected_order="asc")
    ov_num = [f for f in flags_numorder if f.name == "ORDER_VIOLATION"]
    expect("numeric order column [0.9, 0.1] is detected as a real opposite-direction reversal",
           len(ov_num) == 1 and ov_num[0].severity == "NULL")

    # AT_REFERENCE / PAGE_CAP: zero numerically-usable values -> CHECK_NOT_RUN, not silence
    df_allbad = pd.DataFrame({"px": [10.0, 20.0], "junk": ["bad_a", "bad_b"]})
    flags_atbad = check(df_allbad, "junk", ref_values=[25.0])
    expect("AT_REFERENCE on an all-non-numeric field raises CHECK_NOT_RUN",
           any(f.name == "CHECK_NOT_RUN" for f in flags_atbad))
    flags_pcbad = check(df_allbad, "px", page_sizes_col="junk", api_cap=1000)
    expect("PAGE_CAP on an all-non-numeric page-size column raises CHECK_NOT_RUN",
           any(f.name == "CHECK_NOT_RUN" for f in flags_pcbad))

    # AT_REFERENCE: a numeric +-inf value is kept in the denominator (not silently excluded
    # the way the pre-coercion _null_mask used to exclude it)
    df_atinf = pd.DataFrame({"g": [25.0, 25.0, float("inf"), 30.0]})
    flags_atinf = check(df_atinf, "g", ref_values=[25.0], at_reference_null=0.5)
    at_inf = [f for f in flags_atinf if f.name == "AT_REFERENCE"][0]
    expect("AT_REFERENCE denominator includes the infinite row (2/4, not 2/3)",
           at_inf.count == 2 and at_inf.denom == 4)

    # ZERO_VOLUME: an infinite volume is not a real executable size either
    df_volinf = pd.DataFrame({"px": [1.0, 2.0, 3.0], "vol": [100.0, float("inf"), 50.0]})
    flags_volinf = check(df_volinf, "px", volume_col="vol")
    zv_inf = [f for f in flags_volinf if f.name == "ZERO_VOLUME"]
    expect("infinite volume counts as ZERO_VOLUME (not a real traded size)",
           len(zv_inf) == 1 and zv_inf[0].count == 1)

    # REF_MISMATCH: a NaN tol (reachable via the library API even with CLI validation)
    # must fail CLOSED (require exact equality), never silently accept every mismatch
    left_nantol = pd.DataFrame({"key": ["a"], "price": [10.0]})
    right_nantol = pd.DataFrame({"key": ["a"], "ref_price": [20.0]})
    flags_nantol = check(left_nantol, "price", ref_df=right_nantol, ref_key="key",
                          ref_field="ref_price", key_col="key", tol=float("nan"))
    expect("NaN tol fails closed (10 vs 20 still reports a mismatch)",
           any(f.name == "REF_MISMATCH" for f in flags_nantol))

    # REF_MISMATCH: a reference value that is itself NaN (key matched, value missing) must
    # be fail-closed, not silently excluded from coverage
    left_refnan = pd.DataFrame({"key": ["a", "b"], "price": [10.0, 20.0]})
    right_refnan = pd.DataFrame({"key": ["a", "b"], "ref_price": [10.0, np.nan]})
    flags_refnan = check(left_refnan, "price", ref_df=right_refnan, ref_key="key",
                          ref_field="ref_price", key_col="key")
    cov_refnan = [f for f in flags_refnan if f.name == "REF_COVERAGE"][0]
    mism_refnan = [f for f in flags_refnan if f.name == "REF_MISMATCH"]
    expect("a matched-but-NaN reference value counts as covered and fail-closed mismatched",
           cov_refnan.count == 2 and len(mism_refnan) == 1 and mism_refnan[0].count == 1)

    # same-file REF_MISMATCH: a NaN same-file reference value is fail-closed too
    df_sfnan = pd.DataFrame({"price": [10.0, 20.0], "other": [10.0, np.nan]})
    flags_sfnan = check(df_sfnan, "price", ref_field_same_file="other")
    cov_sfnan = [f for f in flags_sfnan if f.name == "REF_COVERAGE"][0]
    mism_sfnan = [f for f in flags_sfnan if f.name == "REF_MISMATCH"]
    expect("same-file NaN reference value counts as covered and fail-closed mismatched",
           cov_sfnan.count == 2 and len(mism_sfnan) == 1 and mism_sfnan[0].count == 1)

    # --- review round 4 regression cases (2026-10-05) ---

    # ORDER_VIOLATION: an unsigned-dtype order column must not wrap around during diff()
    df_uint = pd.DataFrame({"px": [1, 2], "t": np.array([1, 0], dtype="uint8")})
    flags_uint = check(df_uint, "px", order_col="t", expected_order="asc")
    ov_uint = [f for f in flags_uint if f.name == "ORDER_VIOLATION"]
    expect("unsigned order column [1,0] expected asc is a real opposite-direction NULL "
           "(not hidden by uint wraparound)",
           len(ov_uint) == 1 and ov_uint[0].severity == "NULL")

    # REF_MISMATCH: two distinct integers above 2**53 must not be equated by a float64 cast
    M = 9_007_199_254_740_992 # 2**53
    left_big = pd.DataFrame({"key": ["a"], "id": pd.array([M], dtype="int64")})
    right_big = pd.DataFrame({"key": ["a"], "ref_id": pd.array([M + 1], dtype="int64")})
    flags_big = check(left_big, "id", ref_df=right_big, ref_key="key", ref_field="ref_id",
                       key_col="key", tol=0.0)
    expect("distinct int64 values above 2**53 are not equated by a float64 round-trip",
           any(f.name == "REF_MISMATCH" for f in flags_big))

    # AT_REFERENCE / PAGE_CAP: a partially (not wholly) unparseable column surfaces a
    # coverage note, not silent row loss
    df_partial = pd.DataFrame({"g": [25.0, "bad", 30.0], "px": [1.0, 2.0, 3.0],
                                "pagesz": [500, "bad", 500]})
    flags_atpartial = check(df_partial, "g", ref_values=[25.0])
    expect("partially-unparseable AT_REFERENCE field surfaces AT_REFERENCE_COVERAGE",
           any(f.name == "AT_REFERENCE_COVERAGE" for f in flags_atpartial))
    flags_pcpartial = check(df_partial, "px", page_sizes_col="pagesz", api_cap=500)
    expect("partially-unparseable page-size column surfaces PAGE_CAP_COVERAGE",
           any(f.name == "PAGE_CAP_COVERAGE" for f in flags_pcpartial))

    # ORDER_VIOLATION: an infinite order value must not corrupt the direction classification
    df_ordinf = pd.DataFrame({"px": range(4), "t": [3, 2, -np.inf, -np.inf]})
    flags_ordinf = check(df_ordinf, "px", order_col="t", expected_order="asc")
    ov_inf = [f for f in flags_ordinf if f.name == "ORDER_VIOLATION"]
    expect("an infinite order value is excluded (via ORDER_COVERAGE), leaving a clean "
           "opposite-direction classification on the rest",
           any(f.name == "ORDER_COVERAGE" for f in flags_ordinf)
           and len(ov_inf) == 1 and ov_inf[0].severity == "NULL")

    # LOW_REF_COVERAGE: opt-in only; off by default, fires when --ref-coverage-min is set
    left_low = pd.DataFrame({"key": [f"k{i}" for i in range(10)], "price": list(range(10))})
    right_low = pd.DataFrame({"key": ["k0"], "ref_price": [0]})
    flags_lowdef = check(left_low, "price", ref_df=right_low, ref_key="key", ref_field="ref_price",
                          key_col="key")
    expect("LOW_REF_COVERAGE does not fire when ref_coverage_min is left at its 0.0 default",
           not any(f.name == "LOW_REF_COVERAGE" for f in flags_lowdef))
    flags_lowon = check(left_low, "price", ref_df=right_low, ref_key="key", ref_field="ref_price",
                         key_col="key", ref_coverage_min=0.5)
    expect("LOW_REF_COVERAGE fires when --ref-coverage-min is set and coverage is below it",
           any(f.name == "LOW_REF_COVERAGE" for f in flags_lowon))

    # --- review round 5 regression cases (2026-10-05) ---

    # REF_MISMATCH: a small-magnitude reference must stay a RELATIVE tolerance, not get
    # floored into an absolute one (2e-13 vs 1e-13 is a 100% relative difference)
    left_small = pd.DataFrame({"key": ["a"], "price": [2e-13]})
    right_small = pd.DataFrame({"key": ["a"], "ref_price": [1e-13]})
    flags_small = check(left_small, "price", ref_df=right_small, ref_key="key",
                         ref_field="ref_price", key_col="key", tol=0.01)
    expect("a 100% relative difference at small magnitude is not floored into a false pass",
           any(f.name == "REF_MISMATCH" for f in flags_small))

    # REF_MISMATCH: a reference of exactly 0 vs a nonzero field value must mismatch
    left_zeroref = pd.DataFrame({"key": ["a", "b"], "price": [0.0, 5.0]})
    right_zeroref = pd.DataFrame({"key": ["a", "b"], "ref_price": [0.0, 0.0]})
    flags_zeroref = check(left_zeroref, "price", ref_df=right_zeroref, ref_key="key",
                           ref_field="ref_price", key_col="key", tol=0.01)
    mism_zeroref = [f for f in flags_zeroref if f.name == "REF_MISMATCH"]
    expect("a reference of exactly 0 vs a nonzero value mismatches (1/2), 0 vs 0 agrees",
           len(mism_zeroref) == 1 and mism_zeroref[0].count == 1 and mism_zeroref[0].denom == 2)

    # ZERO_VOLUME: a primary field with 0 non-null rows surfaces CHECK_NOT_RUN explicitly
    df_allnan_px = pd.DataFrame({"px": [np.nan, np.nan], "vol": [100.0, 200.0]})
    flags_allnan_px = check(df_allnan_px, "px", volume_col="vol")
    expect("ZERO_VOLUME on an entirely-null primary field raises CHECK_NOT_RUN",
           any(f.name == "CHECK_NOT_RUN" for f in flags_allnan_px))

    # PAGE_CAP_COVERAGE: a NaN or infinite page size must be visibly excluded, not silently
    # dropped from both the numerator and denominator together
    df_pagegap = pd.DataFrame({"px": [1.0, 2.0], "pagesz": [500.0, np.nan]})
    flags_pagegap = check(df_pagegap, "px", page_sizes_col="pagesz", api_cap=1000)
    pc_cov = [f for f in flags_pagegap if f.name == "PAGE_CAP_COVERAGE"]
    expect("a NaN page size is visibly excluded (1/2), not silently absorbed into n_raw_present",
           len(pc_cov) == 1 and pc_cov[0].count == 1 and pc_cov[0].denom == 2)
    df_pageinf = pd.DataFrame({"px": [1.0, 2.0], "pagesz": [500.0, np.inf]})
    flags_pageinf = check(df_pageinf, "px", page_sizes_col="pagesz", api_cap=1000)
    pc_cov_inf = [f for f in flags_pageinf if f.name == "PAGE_CAP_COVERAGE"]
    expect("an infinite page size is excluded from PAGE_CAP's usable population too",
           len(pc_cov_inf) == 1 and pc_cov_inf[0].count == 1)

    # --- PAIR_RATIO / SPLICE_CHECK ---

    # PAIR_RATIO: a closed-end fund fixture -- price collapses to ~3% of NAV while NAV barely
    # moves (modeled on a real feed-fault pattern: price 16.11->0.54, NAV 16.96->16.43)
    df_fundnav = pd.DataFrame({
        "price": [16.11, 16.08, 16.16, 0.54, 0.538, 15.48, 0.542, 0.540],
        "nav": [16.96, 16.96, 17.00, 16.43, 16.43, 16.43, 16.43, 16.43],
    })
    flags_fundnav = flag_pair_ratio(df_fundnav, "price", "nav", 0.7, 1.3)
    pr_fundnav = [f for f in flags_fundnav if f.name == "PAIR_RATIO"]
    expect("PAIR_RATIO flags the fund price/NAV collapse (4 of 8 rows outside [0.7,1.3])",
           len(pr_fundnav) == 1 and pr_fundnav[0].severity == "NULL" and pr_fundnav[0].count == 4)

    # PAIR_RATIO: a plausible CEF discount band (all inside [0.7, 1.3]) -> no flag
    df_clean_ratio = pd.DataFrame({"price": [9.5, 10.1, 10.4], "nav": [10.0, 10.0, 10.0]})
    flags_cr = flag_pair_ratio(df_clean_ratio, "price", "nav", 0.7, 1.3)
    expect("PAIR_RATIO raises nothing when every ratio is inside the band",
           not any(f.name == "PAIR_RATIO" for f in flags_cr))

    # PAIR_RATIO: col_b == 0 with col_a != 0 is an undefined/infinite ratio, always flagged
    df_zeroden = pd.DataFrame({"a": [1.0, 0.0, 3.0], "b": [1.0, 0.0, 0.0]})
    flags_zd = flag_pair_ratio(df_zeroden, "a", "b", 0.7, 1.3)
    pr_zd = [f for f in flags_zd if f.name == "PAIR_RATIO"][0]
    expect("PAIR_RATIO: a!=0 over b==0 flags; 0-over-0 is agreement, not flagged (1/3)",
           pr_zd.count == 1 and pr_zd.denom == 3)

    # PAIR_RATIO: misspelled column -> CHECK_NOT_RUN, not silence
    flags_pr_bad = flag_pair_ratio(df_clean_ratio, "price", "does_not_exist", 0.7, 1.3)
    expect("PAIR_RATIO misspelled column raises CHECK_NOT_RUN",
           any(f.name == "CHECK_NOT_RUN" for f in flags_pr_bad))

    # SPLICE_CHECK: a vendor-splice fixture -- $3.188 -> $193.00 at volume 0 (modeled on a real
    # ticker-splice pattern), surrounded by ordinary small moves
    df_splice = pd.DataFrame({
        "close": [3.375, 3.375, 3.375, 3.188, 193.0, 191.0, 177.0, 170.0, 190.0, 190.0],
        "vol": [21400, 5000, 5700, 33000, 0, 0, 0, 0, 220, 530],
    })
    flags_splice = flag_splice_check(df_splice, "close", 5.0, volume_col="vol")
    sj_splice = [f for f in flags_splice if f.name == "SPLICE_JUMP"]
    expect("SPLICE_CHECK flags exactly the vendor-splice $3.19->$193 jump at volume 0, NULL, 1/9 pairs",
           len(sj_splice) == 1 and sj_splice[0].severity == "NULL"
           and sj_splice[0].count == 1 and sj_splice[0].denom == 9)
    expect("SPLICE_CHECK on the vendor-splice fixture raises no REVIEW-level SPLICE_JUMP_CANDIDATE too",
           not any(f.name == "SPLICE_JUMP_CANDIDATE" for f in flags_splice))

    # SPLICE_CHECK: same jump magnitude but with real nonzero volume -> REVIEW, not NULL
    df_split = pd.DataFrame({"close": [40.0, 41.0, 20.5, 20.8], "vol": [1000, 1000, 5000, 5000]})
    flags_split = flag_splice_check(df_split, "close", 1.8, volume_col="vol")
    sjc_split = [f for f in flags_split if f.name == "SPLICE_JUMP_CANDIDATE"]
    expect("SPLICE_CHECK with real volume on the jump is exactly REVIEW, 1/3 pairs, no NULL",
           len(sjc_split) == 1 and sjc_split[0].severity == "REVIEW"
           and sjc_split[0].count == 1 and sjc_split[0].denom == 3
           and not any(f.name == "SPLICE_JUMP" for f in flags_split))

    # SPLICE_CHECK: no --volume-col given -> REVIEW only, cannot corroborate (same jump as the
    # NULL case above, but severity must drop to REVIEW without the volume column)
    flags_splice_novol = flag_splice_check(df_splice, "close", 5.0)
    sjc_novol = [f for f in flags_splice_novol if f.name == "SPLICE_JUMP_CANDIDATE"]
    expect("SPLICE_CHECK without --volume-col cannot promote to NULL (exactly REVIEW, 1/9 pairs)",
           len(sjc_novol) == 1 and sjc_novol[0].severity == "REVIEW"
           and sjc_novol[0].count == 1 and sjc_novol[0].denom == 9
           and not any(f.name == "SPLICE_JUMP" for f in flags_splice_novol))

    # SPLICE_CHECK: ordinary smooth series -> no flags
    df_smooth = pd.DataFrame({"close": [10.0, 10.1, 10.05, 10.2, 10.15]})
    flags_smooth = flag_splice_check(df_smooth, "close", 5.0)
    expect("SPLICE_CHECK raises nothing on a smooth series",
           not any(f.name in ("SPLICE_JUMP", "SPLICE_JUMP_CANDIDATE") for f in flags_smooth))

    # SPLICE_CHECK: a jump from an exact 0 to nonzero is always flagged (undefined ratio);
    # the second pair (5.0 -> 5.1, ratio 1.02) is well inside jump_x=5 and must NOT also flag
    df_fromzero = pd.DataFrame({"close": [0.0, 5.0, 5.1]})
    flags_fz = flag_splice_check(df_fromzero, "close", 5.0)
    sjc_fz = [f for f in flags_fz if f.name == "SPLICE_JUMP_CANDIDATE"]
    expect("SPLICE_CHECK flags exactly the 0->5 jump (1/2 pairs), not the 5->5.1 pair too",
           len(sjc_fz) == 1 and sjc_fz[0].count == 1 and sjc_fz[0].denom == 2)

    # library-boundary validation: jump_x must be finite and > 1 even when called directly
    # (not just through the CLI, which validates separately)
    expect("flag_splice_check fails closed on jump_x <= 1",
           _raises_ic(lambda: flag_splice_check(df_smooth, "close", 1.0)))
    expect("flag_splice_check fails closed on jump_x = nan",
           _raises_ic(lambda: flag_splice_check(df_smooth, "close", float("nan"))))

    # SPLICE_CHECK: a misspelled --volume-col must surface CHECK_NOT_RUN even when the price
    # series itself has zero qualifying jumps 
    flags_smooth_badvol = flag_splice_check(df_smooth, "close", 5.0, volume_col="does_not_exist")
    expect("misspelled --volume-col on SPLICE_CHECK surfaces CHECK_NOT_RUN even with 0 jumps",
           any(f.name == "CHECK_NOT_RUN" for f in flags_smooth_badvol))

    # col_a == col_b for PAIR_RATIO is a trivial self-comparison (ratio always 1), same
    # SELF_COMPARE treatment as flag_ref_same_file gives a same-name comparison
    flags_selfratio = flag_pair_ratio(df_smooth, "close", "close", 0.7, 1.3)
    expect("PAIR_RATIO with col_a==col_b raises SELF_COMPARE, not a trivial pass",
           any(f.name == "SELF_COMPARE" and f.severity == "NULL" for f in flags_selfratio))

    # library-boundary validation: a NaN/infinite band or null_thresh must not silently
    # disable PAIR_RATIO even when called directly (not just through the CLI)
    expect("flag_pair_ratio fails closed on hi=nan",
           _raises_ic(lambda: flag_pair_ratio(df_fundnav, "price", "nav", 0.7, float("nan"))))
    expect("flag_pair_ratio fails closed on hi <= lo",
           _raises_ic(lambda: flag_pair_ratio(df_fundnav, "price", "nav", 1.3, 0.7)))
    # the fund fixture's violation share (4/8=0.5)
    # already exceeds the ORDINARY default null_thresh of 0.2, so it stays NULL whether the
    # invalid threshold is clamped to 0.0 or silently left at the 0.2 default -- the assertion
    # could not actually distinguish "clamped" from "not clamped". Use a LOW-share fixture
    # (1/8=0.125 < 0.2) instead: at the real default 0.2 this is REVIEW; only a fail-closed
    # clamp to 0.0 (where ANY violation at all is NULL) makes it NULL.
    df_lowshare = pd.DataFrame({"price": [10.0] * 7 + [0.5], "nav": [10.0] * 8})
    flags_lowshare_default = flag_pair_ratio(df_lowshare, "price", "nav", 0.7, 1.3)
    pr_lowshare_default = [f for f in flags_lowshare_default if f.name == "PAIR_RATIO"][0]
    expect("control: the low-share fixture is REVIEW at the real default null_thresh=0.2",
           pr_lowshare_default.severity == "REVIEW" and pr_lowshare_default.count == 1
           and pr_lowshare_default.denom == 8)
    flags_lowshare_nanull = flag_pair_ratio(df_lowshare, "price", "nav", 0.7, 1.3,
                                             null_thresh=float("nan"))
    pr_lowshare_nanull = [f for f in flags_lowshare_nanull if f.name == "PAIR_RATIO"][0]
    expect("flag_pair_ratio clamps an invalid null_thresh to 0.0 (same fixture flips to NULL)",
           pr_lowshare_nanull.severity == "NULL")

    # SPLICE_CHECK: misspelled column -> CHECK_NOT_RUN
    flags_sc_bad = flag_splice_check(df_smooth, "does_not_exist", 5.0)
    expect("SPLICE_CHECK misspelled column raises CHECK_NOT_RUN",
           any(f.name == "CHECK_NOT_RUN" for f in flags_sc_bad))

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ----------------------------------------------------------------------------- CLI


def _load_frame(path: str, is_json: bool) -> pd.DataFrame:
    if is_json:
        try:
            return pd.read_json(path)
        except ValueError:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return pd.DataFrame(data)
    return pd.read_csv(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv")
    ap.add_argument("--json", action="store_true", help="treat --csv path as JSON (list of records)")
    ap.add_argument("--field", action="append", default=[])
    ap.add_argument("--role", choices=["price", "count", "time", "id", "amount"], default=None)
    ap.add_argument("--ref-value", action="append", type=float, default=None, dest="ref_values")
    # (review round 2): these six thresholds used to default to their
    # real numeric value directly, so the --field-required guard below could only detect a
    # value DIFFERENT from the default, not "was this flag supplied at all" -- a splice-only
    # invocation that happened to spell out e.g. `--tol 0.01` (the real default) would silently
    # bypass the guard. Default to None (sentinel: "not supplied") and fill in the real default
    # explicitly, after the guard runs, so presence and value are no longer conflated.
    ap.add_argument("--mode-share-max", type=float, default=None)
    ap.add_argument("--allow-constant", action="store_true")
    ap.add_argument("--volume-col")
    ap.add_argument("--order-col")
    ap.add_argument("--expected-order", choices=["asc", "desc"])
    ap.add_argument("--page-sizes-col")
    ap.add_argument("--api-cap", type=float)
    ap.add_argument("--ref-csv")
    ap.add_argument("--ref-key")
    ap.add_argument("--ref-field")
    ap.add_argument("--ref-field-same-file")
    ap.add_argument("--tol", type=float, default=None)
    ap.add_argument("--mode-dominant-null", type=float, default=None)
    ap.add_argument("--at-reference-null", type=float, default=None)
    ap.add_argument("--ref-mismatch-null", type=float, default=None)
    ap.add_argument("--ref-coverage-min", type=float, default=None,
                     help="if >0, flag REF_MISMATCH/same-file join coverage below this "
                          "fraction as LOW_REF_COVERAGE (REVIEW); 0 (default) disables it")
    ap.add_argument("--pair-ratio", nargs=2, metavar=("COL_A", "COL_B"),
                     help="opt-in PAIR_RATIO check: flag rows where COL_A/COL_B falls outside "
                          "--ratio-band (e.g. a CEF's price vs its NAV)")
    ap.add_argument("--ratio-band", nargs=2, type=float, metavar=("LO", "HI"))
    ap.add_argument("--ratio-band-null", type=float, default=0.2,
                     help="PAIR_RATIO share threshold for NULL vs REVIEW (default 0.2, same "
                          "convention as --ref-mismatch-null)")
    ap.add_argument("--splice-check", metavar="COL",
                     help="opt-in SPLICE_CHECK: flag consecutive-row moves in COL bigger than "
                          "--jump-x (vendor-splice detector, e.g. a $3.19->$193 jump at volume 0)")
    ap.add_argument("--jump-x", type=float,
                     help="SPLICE_CHECK threshold: flag a consecutive-row ratio > X or < 1/X")
    ap.add_argument("--where", help="pandas df.query() expression applied before checking")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _run_selftest()

    if not args.csv or not (args.field or args.pair_ratio or args.splice_check):
        ap.error("--csv and at least one of --field / --pair-ratio / --splice-check are "
                  "required (or use --selftest)")

    # (review round 1): --pair-ratio/--splice-check made --field
    # optional, but --role, --ref-value, --mode-share-max, --allow-constant, --order-col/
    # --expected-order, --page-sizes-col/--api-cap, --ref-csv/--ref-key/--ref-field,
    # --ref-field-same-file and the four *_null/*_coverage-min thresholds are only ever applied
    # inside the `for fld in args.field` loop below. Without this guard, e.g. `--splice-check C
    # --jump-x 5 --order-col t --expected-order asc` (no --field) passed every existing
    # validation and exited 0/OK while silently never running the requested ORDER_VIOLATION
    # check at all.
    # (round 2): checking "value != the real default" let a caller who
    # happened to spell out the real default explicitly (e.g. `--tol 0.01`) silently bypass the
    # guard -- `is not None` on these now-sentinel-defaulted args checks PRESENCE, not value.
    field_scoped_used = (
        args.role is not None or bool(args.ref_values) or args.mode_share_max is not None or
        args.allow_constant or bool(args.order_col) or bool(args.expected_order) or
        bool(args.page_sizes_col) or args.api_cap is not None or bool(args.ref_csv) or
        bool(args.ref_key) or bool(args.ref_field) or bool(args.ref_field_same_file) or
        args.tol is not None or args.mode_dominant_null is not None or
        args.at_reference_null is not None or args.ref_mismatch_null is not None or
        args.ref_coverage_min is not None
    )
    if field_scoped_used and not args.field:
        ap.error("--role/--ref-value/--mode-share-max/--allow-constant/--order-col/"
                  "--expected-order/--page-sizes-col/--api-cap/--ref-csv/--ref-key/--ref-field/"
                  "--ref-field-same-file/--tol/--mode-dominant-null/--at-reference-null/"
                  "--ref-mismatch-null/--ref-coverage-min only apply inside a --field check -- "
                  "pass --field or drop these options")
    # (round 2): --volume-col has two valid consumers (the per-field
    # ZERO_VOLUME check, and SPLICE_CHECK's corroboration) -- it must require at least one of
    # them, not be unconditionally exempt (a bare `--pair-ratio ... --volume-col typo'd` used
    # to pass validation and silently never check that column at all).
    if args.volume_col and not (args.field or args.splice_check):
        ap.error("--volume-col only applies inside a --field check or alongside "
                  "--splice-check -- pass one of those or drop --volume-col")

    # fill in the real defaults now that presence has been checked above
    if args.mode_share_max is None:
        args.mode_share_max = 0.5
    if args.tol is None:
        args.tol = 0.01
    if args.mode_dominant_null is None:
        args.mode_dominant_null = 0.9
    if args.at_reference_null is None:
        args.at_reference_null = 0.5
    if args.ref_mismatch_null is None:
        args.ref_mismatch_null = 0.2
    if args.ref_coverage_min is None:
        args.ref_coverage_min = 0.0

    # (review round 2): a half-specified check combination used to
    # silently no-op (e.g. --order-col without --expected-order) and still exit 0/OK. Reject
    # these at the CLI boundary instead -- the library function still accepts partial kwargs
    # (e.g. a caller passing None for both), only the CLI enforces pairing.
    if bool(args.order_col) != bool(args.expected_order):
        ap.error("--order-col requires --expected-order and vice versa")
    if bool(args.page_sizes_col) != (args.api_cap is not None):
        ap.error("--page-sizes-col requires --api-cap and vice versa")
    ref_csv_parts = (bool(args.ref_csv), bool(args.ref_key), bool(args.ref_field))
    if any(ref_csv_parts) and not all(ref_csv_parts):
        ap.error("--ref-csv, --ref-key and --ref-field must be given together")
    if bool(args.pair_ratio) != bool(args.ratio_band):
        ap.error("--pair-ratio requires --ratio-band and vice versa")
    if args.ratio_band is not None:
        lo, hi = args.ratio_band
        if any(v != v or v in (float("inf"), float("-inf")) for v in (lo, hi)) or lo < 0 or hi <= lo:
            ap.error("--ratio-band LO HI must be finite with 0 <= LO < HI")
    if args.ratio_band_null != args.ratio_band_null or args.ratio_band_null < 0 or \
            args.ratio_band_null in (float("inf"), float("-inf")):
        ap.error("--ratio-band-null must be a finite number >= 0")
    if bool(args.splice_check) != (args.jump_x is not None):
        ap.error("--splice-check requires --jump-x and vice versa")
    if args.jump_x is not None and (args.jump_x != args.jump_x
                                     or args.jump_x in (float("inf"), float("-inf"))
                                     or args.jump_x <= 1.0):
        ap.error("--jump-x must be a finite number > 1")
    # (review round 2): argparse's type=float accepts "nan"/"inf"
    # strings; a NaN tolerance makes every `rel > tol` comparison False, silently disabling
    # REF_MISMATCH regardless of how wrong the values are.
    if not (args.tol == args.tol) or args.tol < 0 or args.tol in (float("inf"), float("-inf")):
        ap.error("--tol must be a finite number >= 0")
    if args.api_cap is not None and (args.api_cap != args.api_cap
                                      or args.api_cap in (float("inf"), float("-inf"))):
        ap.error("--api-cap must be a finite number")
    # (round 4): only tol/api_cap were validated for finiteness; a
    # share threshold like --mode-share-max inf or --ref-mismatch-null nan silently disables
    # the severity decision it governs (an infinite mode-share-max threshold can never be
    # exceeded; a NaN null-threshold makes every `share > threshold` comparison False).
    for name, val in (("--mode-share-max", args.mode_share_max),
                       ("--mode-dominant-null", args.mode_dominant_null),
                       ("--at-reference-null", args.at_reference_null),
                       ("--ref-mismatch-null", args.ref_mismatch_null),
                       ("--ref-coverage-min", args.ref_coverage_min)):
        if val != val or val in (float("inf"), float("-inf")) or val < 0:
            ap.error(f"{name} must be a finite number >= 0 (got {val!r})")
    # (round 5): --ref-value nan/inf passed argument parsing and every
    # existing validation; a NaN reference value makes every AT_REFERENCE comparison false,
    # silently disabling the exact check this tool exists to run (it's how probe_2's $25
    # offering-reference bug would have been MISSED if the reference itself were malformed).
    if args.ref_values:
        for rv in args.ref_values:
            if rv != rv or rv in (float("inf"), float("-inf")):
                ap.error(f"--ref-value must be finite (got {rv!r})")

    df = _load_frame(args.csv, args.json)
    if args.where:
        df = df.query(args.where)

    ref_df = None
    if args.ref_csv:
        ref_df = pd.read_csv(args.ref_csv)

    thresholds = {
        "mode_share_max": args.mode_share_max,
        "mode_dominant_null": args.mode_dominant_null,
        "at_reference_null": args.at_reference_null,
        "ref_mismatch_null": args.ref_mismatch_null,
        "ref_coverage_min": args.ref_coverage_min,
        "tol": args.tol,
        "api_cap": args.api_cap,
        "allow_constant": args.allow_constant,
    }

    per_field_flags = {}
    for fld in args.field:
        per_field_flags[fld] = check(
            df, fld,
            role=args.role,
            ref_values=args.ref_values,
            mode_share_max=args.mode_share_max,
            allow_constant=args.allow_constant,
            volume_col=args.volume_col,
            order_col=args.order_col,
            expected_order=args.expected_order,
            page_sizes_col=args.page_sizes_col,
            api_cap=args.api_cap,
            ref_df=ref_df,
            ref_key=args.ref_key,
            ref_field=args.ref_field,
            key_col=args.ref_key,
            ref_field_same_file=args.ref_field_same_file,
            tol=args.tol,
            mode_dominant_null=args.mode_dominant_null,
            at_reference_null=args.at_reference_null,
            ref_mismatch_null=args.ref_mismatch_null,
            ref_coverage_min=args.ref_coverage_min,
        )

    extra_sections: dict[str, list[Flag]] = {}
    if args.pair_ratio:
        col_a, col_b = args.pair_ratio
        lo, hi = args.ratio_band
        extra_sections[f"PAIR_RATIO {col_a}/{col_b}"] = flag_pair_ratio(
            df, col_a, col_b, lo, hi, null_thresh=args.ratio_band_null)
        thresholds["ratio_band"] = [lo, hi]
        thresholds["ratio_band_null"] = args.ratio_band_null
    if args.splice_check:
        extra_sections[f"SPLICE_CHECK {args.splice_check}"] = flag_splice_check(
            df, args.splice_check, args.jump_x, volume_col=args.volume_col)
        thresholds["jump_x"] = args.jump_x

    report = render_report(
        csv_path=args.csv, df=df, fields=args.field, per_field_flags=per_field_flags,
        thresholds=thresholds, where=args.where, extra_sections=extra_sections,
    )
    print(report)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"(written to {args.out})")

    # review pass: exit code reflects the worst verdict across all fields so
    # an automated caller can gate on it without re-parsing the report text.
    all_flags = [fl for flags in per_field_flags.values() for fl in flags]
    for flags in extra_sections.values():
        all_flags.extend(flags)
    overall, _ = verdict_for(all_flags)
    return {"OK": 0, "REVIEW": 1, "INSTRUMENT_NULL": 2}[overall]


if __name__ == "__main__":
    sys.exit(main())
