# DECISIONS — append-only

One row per settled decision. A wrong decision gets a NEW row that supersedes it; never edit or delete a
row. Cite by the decision id, never by line number. Grep this file before re-litigating anything.

Format: `D<nnn> · <date> · <who decided> · <decision> · <why> · <supersedes>`

---

**D001 · <date> · <who> · Adopt the claude-harness-kit: tiered agents, frozen specs, cross-family review gate, pre-commit selftests, three state files.**
Why: <!-- FILL -->. Deliberately-not-installed pieces and their re-add triggers are in `docs/HARNESS.md` §4. Supersedes: none.
