---
name: protect-frozen-specs
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: experiments[/\\][^/\\]+[/\\]SPEC\.md$
---

🛑 **Frozen experiment spec — read-only.**

`experiments/<id>/SPEC.md` is frozen by definition (drafts are `SPEC.draft.md`; the freeze is the rename).
CLAUDE.md: a frozen spec is never edited after the run starts. Editing it silently invalidates every
number derived from it, and post-hoc changes to the primary metric or the split are exactly the
researcher degrees of freedom the freeze exists to remove.

**Do this instead:**
- Still drafting? Edit `SPEC.draft.md`. It becomes `SPEC.md` only after Gate-0 passes (`experiment-spec`).
- Need a variant? Write a NEW draft (`experiments/<next-id>_<slug>/SPEC.draft.md`) that cites this one.
- Found a defect in the frozen design? Say so to the maintainers; the disposition is theirs (a new id, or
  KILLED with the reason in `RESULT.md`).
- Banking the result? Status lives in `RESULT.md` (`bank-result`), never in `SPEC.md`.
- A maintainer explicitly authorising the edit can set `enabled: false` in this rule for that one turn.

Why path-only: hookify cannot read a file's current status on a Write, so a rule keyed on
`status: FROZEN` in the old text would only catch Edit and would let a Write replace the file.
