---
name: source-of-truth-gate
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: (CLAUDE\.md|docs/DECISIONS\.md|facts/registry\.yaml)$
---

📋 **Editing a source-of-truth file — check the gate before you write a result.**

Before a result lands here it must have cleared:

1. **Cross-family review** of the code that produced it (`cross-review` skill), if the code computes a
   split, a metric, a feature, or an event-log transform.
2. **Adversarial refutation** in a fresh seat (`adversarial-verifier`) if the result changes direction
   or will be quoted outside the session. Default posture: refuted unless the evidence forces otherwise.
3. **Baselines beaten** on the identical split and population. A metric with no baseline is not a finding.

Also honor: `docs/DECISIONS.md` is append-only (a wrong row gets a superseding row); a retracted figure
in the registry gets `status: retracted` + `do_not_quote: true`, never deletion; CLAUDE.md stays under
~150 lines (procedures go in skills, enforcement in hooks).

Bookkeeping edits (renames, pointers, formatting) are fine. This is a reminder, not a block.
