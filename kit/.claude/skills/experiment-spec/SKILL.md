---
name: experiment-spec
description: Author and freeze an experiment spec before anything runs. Trigger when a screened proposal needs an empirical answer, when a scratch probe is about to become a real comparison, or when a maintainer says "spec it" / "freeze it". Drafting is free; executing against a non-frozen spec is not allowed. The frozen sections and the Gate-0 routing live in the body.
---

# experiment-spec — freeze the question before the answer can be tuned

**Drafting is free. Executing is not.** Nothing in this skill runs anything; it produces
`experiments/<nnn>_<slug>/SPEC.draft.md` and hands off to Gate-0. **The freeze is a rename:**
`SPEC.draft.md` → `SPEC.md`. The hookify guard blocks every edit to a `SPEC.md`, so the file name is
the contract: `SPEC.md` exists ⇒ frozen.

## 0. Preconditions — refuse to draft without these
- The idea passed `screen-proposal` with a stated kill condition.
- You know whether it touches a **sealed holdout** (a slice of data reserved for a single confirmatory
  read). If it does: STOP; that needs explicit maintainer sign-off in the prompt.

## 1. Start from the nearest existing spec — do not invent structure
```bash
ls experiments/*/SPEC.md experiments/*/SPEC.draft.md
```
Copy the closest skeleton. Your first experiment (`000_baselines`) becomes the template.

## 2. The frozen sections — every one is required

```yaml
---
id: <nnn>_<slug>
status: DRAFT            # DRAFT in SPEC.draft.md; FROZEN in SPEC.md. Run/bank status lives in RESULT.md.
created: <date>
frozen: <date or null>
---
```

1. **One-paragraph hypothesis, mechanism first.** Why it should work, and what observable behavior
   would falsify it.
2. **What is already decided and must not be re-run.** Cite DECISIONS rows and prior experiment ids.
3. **Frozen population and dataset.** Which rows, which date range, which labels mean what. Dedup /
   entity resolution applied. Exclusions and why.
4. **Split rule.** Temporal by default: `train < T ≤ test`, with T stated. Any random split needs a
   reason here. State how entities first seen only in test are handled.
5. **The PRIMARY metric, pre-committed — exactly one.** Estimator, parameters, the unit of the CI
   (clustered at the right unit — user, day, entity — not per-row), n, and the minimum effect that
   would matter. Everything else is secondary and labelled as such.
6. **Baselines.** The trivial baselines, on the identical split and population. The candidate must beat
   the strongest of them by the stated minimum effect.
7. **Kill tree.** Each arm: the condition, the threshold, and what its failure *means* for the thesis.
   A FAIL should close exactly the vein it tested and name why.
8. **Anti-fooling pre-commitments.** Segment slices to check, minus-top-1 / minus-top-2 concentration
   check, multiplicity (how many variants are allowed before the primary is contaminated: state the
   number now).
9. **Offline → real-world mapping.** Which real outcome this offline number is a proxy for, and what
   would make the proxy wrong.
10. **Reconciliation check.** How the results will be checked against the script's own other outputs
    (per-segment numbers sum to the total; coverage matches the population definition).
11. **Repro.** The single command that reproduces the primary number from a clean shell, and the
    `--selftest` that proves the scorer fails closed on synthetic leakage.

## 3. Gate-0 before freeze — do not skip
Route the DRAFT to the `adversarial-verifier` for a **design** review. It hunts: a primary metric not
actually pre-committed, hidden researcher degrees of freedom, a split that leaks, weak baselines, a
kill condition that cannot fire. Gate-0 returns DO-NOT-FREEZE → fix or drop. Do not argue it through.

On FREEZE: set `status: FROZEN` and `frozen: <date>` in the draft, then `git mv SPEC.draft.md SPEC.md`
and commit. Log the spec in `docs/STATE.md` as a `waiting` thread. From this point the hookify guard
blocks every edit to `SPEC.md`; the `git mv` is the last write.

## 4. On execution (later, and only when FROZEN)
- Scorer code goes through `cross-review` before its numbers are trusted.
- Report the FAIL if it fails. No post-hoc threshold tuning, no bundling "let's also try X" into the
  primary; extensions are separate follow-up specs.
- Bank the outcome with `bank-result` in the same turn it is decided.

## Naming
`experiments/<nnn>_<slug>/SPEC.draft.md` while drafting → `SPEC.md` on freeze. Zero-padded sequential
ids. A folder with only a `SPEC.draft.md` is not executable.
