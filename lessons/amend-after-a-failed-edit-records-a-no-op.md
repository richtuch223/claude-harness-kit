---
name: amend-after-a-failed-edit-records-a-no-op
description: Running `prestate.py amend` after a text edit that silently failed appends an AMEND record whose content equals the current head — a no-op in the audit trail that hides the failed edit. Chain the edit and the amend in one command, and refuse no-op amends by default.
metadata:
  type: feedback
---

Two studies in the source program each carry an AMEND record (seq=3) that changed nothing: the rule file was supposed
to be edited by a text replace, the replace matched nothing and returned without error, and `amend` ran anyway on the
unchanged file. The log now shows an amendment with a reason, a timestamp, and a diff that reads "(no textual
difference)". The real amendment landed one record later. Both no-ops had to be documented in the rule text itself so a
reader does not assume a hidden change.

**Why:** an append-only pre-statement log is only as honest as what it records. A record that changes nothing is
noise that a reviewer must explain away, and it masks the actual failure (the edit never applied), which is the thing
you wanted to know.

**How to apply:**
- Chain the edit and the amend so the second cannot run if the first did nothing: in the shell, `python edit.py && python
  tools/prestate.py amend ...`, with the edit script exiting non-zero when its anchor is not found (assert the anchor
  occurs exactly once before replacing).
- `tools/prestate.py amend` refuses an amendment whose normalized text equals the current head since this build;
  `--allow-noop` records it anyway when that is really what you mean (rare).
- After any amend, run `prestate.py show` and read the ROOT→CURRENT diff before launching the builder that will
  implement it.
