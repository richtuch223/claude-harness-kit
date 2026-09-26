---
name: grill
description: Interview the maintainers to convergence before expensive work. Trigger when a maintainer says grill / grill me, when new scope docs land, or when a loose idea, spec draft, or multi-hour build still has unsettled design branches that would otherwise surface mid-build or at Gate-0.
---

# grill — resolve every design branch before compute is spent

Spec ambiguity is cheapest to kill in conversation, before a spec is authored or a multi-hour build
launches. Today it surfaces at Gate-0 (expensive, late) or mid-build (worse). This skill moves it to
minute zero.

## Protocol
1. **Answer what the repo can answer first.** Grep `docs/DECISIONS.md`, `docs/STATE.md`,
   `docs/PROJECT_CONTEXT.md`, `experiments/` before asking anything. A question a decision row already
   answers is never asked; asking it re-litigates a settled matter. Cite what you found instead.
2. **Ask in rounds.** Each round contains **every question whose prerequisites are already settled**.
   No drip-feeding one question at a time; no asking Q7 when its premise depends on the unanswered Q3.
3. **Number every question and give the answer you recommend** with a one-line reason. The maintainer
   should be able to reply "1a, 2 agreed, 3: no, use X" and be done. Where options are cleanly
   enumerable, the AskUserQuestion tool is the right surface; free-form otherwise.
4. **Classify ungrillable questions.** A question only data can answer is not asked; it is converted
   into the cheapest probe and listed as such, with its pre-stated kill condition. Debating an
   empirical unknown is the failure mode.
5. **Stop when the branches resolve, not when the questions run out.** Every design decision is either
   settled by a maintainer, settled by the repo, or converted to a named probe. Then hand off: to
   `screen-proposal` (new idea), `experiment-spec` (test design), or the build.

## Rules
- **No artifacts.** Grilling produces shared understanding and, if useful, a short numbered decision
  list in the final message, which then gets banked as DECISIONS rows. Never a new doc.
- **Challenge unsupported assumptions.** Agreement with a well-grounded recommended default is fine. If a maintainer's framing contradicts a decision row or the operating
  principle (e.g. proposes infrastructure for an unmeasured failure), say so in the question. A grill
  where every answer is "agreed" was a wasted round. Attack the definition, the unit, and the horizon
  first.
- **Bounded.** Two, at most three rounds. If branches still do not resolve, the idea is not ready to
  grill; it needs a scout pass or a probe, and you should say which.
