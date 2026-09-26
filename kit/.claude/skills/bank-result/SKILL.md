---
name: bank-result
description: Bank a decided experiment outcome or product decision the moment it is decided — PASS, FAIL, KILLED, PARKED, DECIDED — or when a maintainer says "bank it", "close it", "write it up". Same-turn banking is the rule; the STATE / DECISIONS / registry / commit steps live in the body.
---

# bank-result — bank a closure in the turn it is decided

A verdict that exists only in a chat turn has the same TTL as the chat. **Deferral is for the re-test,
never for the record.**

## 0. Guards before writing anything
- **Read the run's own output file**, not your memory of it. "Could not run" ≠ "refuted"; a data gap is
  not a product limit.
- **Report the whole pre-specified grid**, not the cells that support the headline. Post-hoc
  KILL-hunting is as forbidden as post-hoc rescue.
- **Route to the `adversarial-verifier` first when:** the result would change direction (a DECISIONS
  row), will be quoted to anyone outside the session, or is the 3rd+ closure today.
- Verdict wording is exact and carries reopen semantics: **PASS** (beat baselines by the pre-stated
  effect) · **FAIL** (did not; closes the vein as tested) · **KILLED** (a kill-tree arm fired) ·
  **PARKED** (set down with a dated un-park trigger) · **INCONCLUSIVE** (n or instrument insufficient;
  say what would suffice). A null is weak evidence; scope it to the population and instrument.

## 1. The banking pass — all in one turn, then commit
1. **`experiments/<id>/RESULT.md`** — frontmatter `status: BANKED` (or `KILLED`), then the verdict, the
   primary number with CI, every baseline, the anti-fooling checks and what they showed, the repro
   command, and the verifier's verdict if one ran.
2. **`facts/registry.yaml`** — every new quotable figure gets its ONE home with population, status,
   and producer script. A superseded figure gets `status: retracted` + `do_not_quote: true`, never deleted.
3. **`docs/DECISIONS.md`** — a new row if the result changes what we do next. Cite the experiment id.
4. **`docs/STATE.md`** — move the thread to `done`, then delete it once the DECISIONS row exists.
5. **Spec stays untouched.** `SPEC.md` is never edited, not even for status; the run/bank status is
   the `status:` line in `RESULT.md` (`RUN` → `BANKED`, or `KILLED`).
6. **Commit** — the result, the producing script, and the run's `decision_trail.tsv` in the same
   commit. Only after this is banked do you discuss any follow-up.

## Notes
- The source-of-truth hookify gate fires on these edits; that is expected. Banking is main-loop work,
  never delegated to a subagent.
- If a step cannot complete now (verifier pending), bank what IS decided and record the pending step as
  a dated `blocked` line in STATE.md. Never leave the whole verdict floating.
