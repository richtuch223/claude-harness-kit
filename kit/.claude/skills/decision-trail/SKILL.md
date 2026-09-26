---
name: decision-trail
description: Keep a reviewable decision log during long autonomous or multi-phase runs — experiment sweeps, overnight jobs, data builds, any run whose outcome a maintainer or the verifier will review after stepping away. Trigger at launch of such a run, or when a maintainer asks to show your work.
---

# decision-trail — one append-only row per decision

Why: in the source program a reviewer overturned 3 of 4 closures in one batch because the runs' own
decision context was not reviewable, and a rewritten pre-commitment was caught only by file-mtime
forensics. A trail written *during* the run makes both checks cheap.

## Format
A TSV named `decision_trail.tsv` in the run's output directory (`experiments/<id>/`), one row per
decision or checkpoint:

```
ts	phase	decision	why	evidence	result
```

- **One row = one decision.** If it does not fit one line, the decision is not crisp yet; sharpen it.
- **Append-only.** A wrong call gets a new row that supersedes it, never an edit. The uncorrected
  history lets a reviewer see *when* something was known.
- **Evidence cites committed scripts and real output paths**, so the reviewer can re-run it.
- Log the moments that matter for review: a threshold chosen, an exclusion applied, a slice unblinded,
  a phase declared done, a kill condition hit or waived, an anomaly noticed and how it was resolved.

## Rules
- Commit the trail **with** the run's result, in the same commit as `RESULT.md`. A trail that dies
  with the scratchpad protects nothing.
- Subagent fleets: each worker logs to its own trail; the orchestrator's synthesis cites rows, not
  memories of rows.
- The trail is run output, not program state. It never substitutes for STATE.md or DECISIONS.md.
