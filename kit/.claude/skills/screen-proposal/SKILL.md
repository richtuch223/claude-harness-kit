---
name: screen-proposal
description: Screen any new feature, model, data source, agent, or infrastructure idea BEFORE designing or building anything — whether proposed by a maintainer, a subagent, or you. Trigger at the first mention of a new candidate, or when asked to "screen" one. Returns BUILD-NOW / PROBE-FIRST / WAIT / ALREADY-DECIDED; the gate order lives in the body.
---

# screen-proposal — the gate every idea passes before it costs anything

Run the checks **in this order** and stop at the first hard fail. Cite evidence for each verdict; never
assert a step passed without showing what you checked. Teams that use agents are at high risk of
over-building; this skill exists to say no cheaply.

## 0. Is it already decided? (cheapest check — do it first)

```bash
grep -i -n "<keyword>" docs/DECISIONS.md docs/STATE.md
ls experiments/          # was it already tested?
```

- Decided with a stated reason → `ALREADY-DECIDED`. Report the row id and stop. Reopening requires a
  *new argument*, not a new attempt.
- Tested with a banked result → cite the registry id and the experiment folder. Do not re-derive.

## 0a. Which failure law does it rhyme with?

Name the closest law in `docs/METHODS.md` §1 (your project's failure-law table) and say what is
different this time. No difference named = REJECT as a re-run of that law. Then check the rest of
`docs/METHODS.md` for a method or instrument to reuse instead of re-deriving one.

## 0a2. Obscurity is not protection

Do not credit obscurity (an unpublished idea, a niche you found by not looking where everyone looks) as
protection against decay. A signal that is SEARCHED FOR shrinks out of sample whether or not it is
published: in-sample-to-out-of-sample shrinkage is a property of the search process, not of publicity.
Measure a candidate's real edge from forward or own-fill evidence, never from "nobody else has found
this yet." This does not mean obscurity is worthless — a barrier that is access, capacity, or
operations (not knowledge) is a different claim and is not ruled out — it means novelty is a filter
against re-running known effects, not a credit toward believing a new one.

## 0b. Literature gate — before any probe touches data

A probe that will cost a build or a review gate starts from the literature, not from a scout's
paragraph:
1. Find the **2-3 strongest papers** on the mechanism (seminal + most recent replication/decay study).
   Read them **in full** (a scout/search-tool summary does not count).
2. Write a **one-page mechanism memo**: who pays and why; why it persists; the published effect size,
   sample and costs; which factor or published portfolio it loads on; evidence of decay since
   publication; what the papers say kills it.
2b. Score each paper on the rigor rubric (`docs/METHODS.md` §4, R1-R5, 0-10). Only a paper scoring 5 or
   more may anchor the kill threshold; if none does, say so and treat the published effect as a prior,
   not a bar.
3. The probe's frozen kill threshold is set **relative to the published effect size** (state it in the
   frozen rule file).
4. Any PASS is first regressed on the nearest published portfolio/factor if one exists and is free to
   get (e.g. an asset-pricing factor library) before it goes to a code/design review gate. A pass that
   the published portfolio fully explains is the literature's result, not a new one.
Exempt: pure data recorders and instrument builds that judge no outcome.

## 1. The guiding question — or WAIT

> <!-- FILL: your project's guiding question. Example: "Does this materially improve our ability to
> (a) <core capability 1>, (b) <core capability 2>, (c) measure whether we improved, or (d) iterate faster?" -->

Name which part it serves, and the mechanism. "It would be cool" or "we will need it eventually" →
`WAIT`, with the trigger that would make it not-wait (a measured failure mode, a user count, a latency
number).

## 2. Stage check — does the order of operations allow it?

<!-- FILL: your project's stage order. Default below. -->
The default order is: working core loop → real users → real data → measured failure modes → targeted
sophistication → scale. Name the current stage (from `docs/STATE.md`). A proposal that belongs to a
later stage → `WAIT`, unless it is the cheapest way to unblock the current one. Infrastructure that
serves a failure mode nobody has measured yet is itself the failure mode.

## 3. What is the cheapest decisive probe, and its kill condition?

Convert the idea into the smallest experiment or prototype whose outcome would change what we do next.
State the kill condition BEFORE anything runs: the number, the population, the comparison. If no kill
condition can be stated, the proposal is not yet a proposal; return it to `grill`.

- Empirical question about users or data → an `experiment-spec` (frozen before run).
- Product surface question → a throwaway prototype in front of ≥3 real people, with the reaction
  we are looking for named in advance.
- Infrastructure question → the measurement that would show the current setup failing.

## 4. Baseline sanity

If the proposal is a model, heuristic, or algorithm: what trivial baseline must it beat, on which
split or benchmark, by how much to matter? <!-- FILL: your standing trivial baselines --> A proposal
that cannot name its baseline is not ready.

## 5. Cost and reversibility

Hours to the probe, dollars (API, data, compute), and whether it is reversible. Irreversible (a schema
committed to production, a data-collection promise to users, a public launch, real spend) → the
maintainers decide, not the session.

## Output contract

```
VERDICT: BUILD-NOW | PROBE-FIRST | WAIT | ALREADY-DECIDED
serves:        <which part of the guiding question> — <mechanism>
stage:         <current stage> — proposal belongs to: <stage>
cheapest probe: <what> — kill condition: <number, population, comparison>
baseline:      <what it must beat, on which split/benchmark>
cost:          <hours> / <$> — reversible: yes/no
prior art:     <DECISIONS rows, experiment folders, registry ids checked>
if WAIT:       the trigger that ends the wait
```

Never report BUILD-NOW or PROBE-FIRST without a pre-stated kill condition. If the probe needs an
experiment, the next step is `experiment-spec`, not a run.
