# METHODS — what this project already knows how to do

A methods canon exists so a failed idea gets compared against a NAMED prior failure instead of being
re-discovered from scratch, and so a new measurement reuses an existing tool/estimator instead of
re-deriving one under time pressure. Read before writing new statistics or measurement code; if you do
write something new, say in the design doc why nothing here fit.

Keep this file SHORT and durable: add a law/method once it has actually killed or validated something
twice, not on a single hunch. Churn belongs in `docs/STATE.md`, not here.

## 1. Failure-law table

Each law is a NAMED, reusable reason a class of proposals fails — not one incident, but a pattern that
recurred. Screen every new proposal against this table (`screen-proposal` §0a) before designing
anything; name the closest law and what is different this time, or reject as a re-run.

| id | name | the pattern | how to detect it early |
|---|---|---|---|
| L1 | *(worked example)* informational edge decays on publication-independent shrinkage | an in-sample effect is partly selection; the SAME shrinkage applies whether or not anyone else knows about it, so "nobody has found this" is not evidence it will hold up | compare the in-sample statistic against an honest out-of-sample or forward re-check before sizing it |
| L2 | *(worked example)* the payer is the same quantity as the toll | a premium is attributed to "illiquidity" or "inefficiency", but capturing it costs exactly that illiquidity/inefficiency, so net capture is near zero regardless of signal quality | name a payer that is NOT the cost you would pay to trade it (a venue rebate, a hard-coded fee, an accounting rule, a preference, a calendar mandate) |
| L3 | | | |
| L4 | | | |
| L5 | | | |
| ... | *(add rows as your project accumulates real, twice-recurring failure patterns)* | | |
| escape hatch | a law that would reject a KNOWN real positive | test any new screen/law against your project's own known positives before adopting it (`screen-proposal` §2b-equivalent) | if it rejects a positive, the law is mis-specified, not the positive |
| all-win trap | every cell in a sweep reports a positive result | that is a sign the baseline/null is broken, not that everything works — check the null construction before believing the sweep | |
| hindsight-entry trap | a backtest's entry condition is evaluated using information only available after the entry would have fired | re-derive the entry using only information available AT the entry timestamp; a `prestate.py`-frozen rule plus `instrument_check.py` catches most of this mechanically | |

## 2. Joint pass probability of a kill tree

A kill tree with N independent gates, each individually calibrated to have false-reject rate α_i against
a real effect at the minimum detectable effect (MDE), has joint pass probability roughly
`∏(1 - α_i)` under that MDE. A tree with 6 gates each at a nominal 15% false-reject rate passes a real
edge only ~0.85^6 ≈ 38% of the time — below the "coin flip" bar most people assume a well-designed test
has. Compute this BEFORE freezing the tree (`conjecture-brief` §6): if it is below 0.5, merge gates,
move some checks to a sizing step instead of a kill step, or accept the tree will sometimes drop a real
effect and say so in the design doc.

## 3. Validation methods and their tools

| method | what it answers | tool shipped here |
|---|---|---|
| instrument check | can this column actually observe the quantity it is named for (vs. a reference value, a page cap, a carried-forward close, an ordering assumption)? | `tools/instrument_check.py` |
| pre-statement / freeze | what rule was frozen when, with an append-only, content-addressed amendment trail | `tools/prestate.py` |
| deflated Sharpe ratio / probability of backtest overfitting | how much of a backtest statistic survives correcting for how many variants were tried | `tools/overfit_stats.py` |
| sequential decay monitor (CUSUM + Wald SPRT) | is a live/forward line's edge still there, checked continuously instead of once at the end | `tools/decay_monitor.py` |
| residual mining | after regressing out the cheap, obvious baseline, is anything LEFT that is a stable entity trait (not noise, not drift) — a generator of leads, never itself a finding | `tools/residual_mine.py` |
| cross-family code/design review | a reviewer from a different model family than the author checks correctness-critical code or a design before it is believed | `tools/verify.py` (see `02_VERIFICATION_LOOP.md`) |

## 4. Rigor rubric: a taste filter for literature and conjectures

Score every paper a literature sweep shortlists, every anchor paper in a mechanism memo, and every
conjecture brief on these five checks, each 0 / 1 / 2 (0-10 total):

| # | check | 2 = | 1 = | 0 = |
|---|---|---|---|---|
| R1 | Formal model | equations derive a signed, sized prediction before the data | a verbal model with a predicted sign | a pattern with no model |
| R2 | Identification | a natural experiment, rule discontinuity, randomization or exogenous shock isolates the cause | controls plus robustness | correlation only |
| R3 | Out-of-sample | an independent replication, a post-publication sample, or a pre-registered test | a holdout split by the same authors | in-sample only |
| R4 | Mechanism with an accounting identity | names the payer, and the payer's loss equals the measured gain (a conservation check) | names a plausible payer | no payer |
| R5 | Reproducible | public data and code | public data, no code | proprietary data |

**Use.** Rank papers by rigor (0-10) x relevance to a live line (0-3). A paper scoring below 5 may
suggest a conjecture but can never anchor a kill threshold or a published effect size in a frozen rule
file. A conjecture states which checks its own test can reach (normally R1, R3 and R4; R2 when a rule
boundary or dated shock exists), and writes R1 down as equations before any data is pulled — the model
is the falsifiable object; the data only tries to break it.

## 5. Estimator-trap checklist

Run through this before trusting any new statistic, not just a backtest return:

- [ ] **Page cap / silent truncation.** Does the data source cap a list endpoint below what you asked
      for? Page to exhaustion and check the real page size, not the requested `limit`.
- [ ] **Ordering assumption.** Is a "first" or "last" row actually sorted by the timestamp you think it
      is? Many trade/print APIs return newest-first.
- [ ] **Survivorship / current-identifier bias.** Building a historical population from TODAY's list of
      names/tickers/ids silently drops everything that no longer exists under that name.
- [ ] **Carried-forward / reference values mistaken for live prints.** A value copied forward on a
      no-activity period, or a fixed reference price, is not an executable observation.
- [ ] **Throughput quoted as profit.** Annualized notional volume through a strategy is not the same
      number as net earnings on committed capital; label which one you are reporting.
- [ ] **Mean of medians, or median of means.** State which you computed and why; they answer different
      questions and are not interchangeable.
- [ ] **Mixed populations.** Confirm every row in a pooled statistic is drawn from the same eligibility
      rule; a population change mid-sample silently changes what the statistic means.
- [ ] **A statistic normalized by an in-sample mean/std.** It inherits ALL of that mean/std's own
      shrinkage; pre-state which OUT-of-sample contrast you actually mean before computing it.
- [ ] **Hindsight entry.** The entry condition must be computable from information available strictly
      AT the entry timestamp, never from anything after it.
