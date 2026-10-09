---
name: secret-fixture-must-build-at-runtime
description: A test fixture for a secret-scanner must build the fake secret string at RUNTIME (concatenation), never write it as a literal in the test file — otherwise the scanner (or a hosting platform's own secret scanner) flags the test file itself and blocks the commit that adds the test.
metadata:
  type: feedback
---

A probe suite for a pre-commit secret scanner needs a fake AWS-style key to prove the scanner catches it. Writing the
literal value directly in the probe file (the 4-letter AWS access-key prefix immediately followed by 16 more
uppercase-letter/digit characters) makes the probe file itself look like a leaked credential to the exact scanner it
is testing, and often to an external platform's own secret scanner too (GitHub's push-protection, for one — this very
lesson file avoids writing that literal value for exactly that reason). The fix used throughout this kit's own
`probe_pre_commit.py` is to build the string from pieces at runtime, e.g. `AK = PREFIX + SUFFIX` where neither half on
its own matches the pattern. The concatenated value is identical at scan time (the scanner sees the assembled string
when it reads the file the probe WRITES into a throwaway repo), but the probe script's own source no longer contains
the literal pattern.

**Why:** a secret-pattern regex (or a hosted scanner) matches on the literal bytes in a file; it cannot tell "this is
a test fixture proving the detector works" from "this is a real leaked key" by context alone, so a literal fixture is
indistinguishable from the real thing to the very check it is supposed to exercise.

**How to apply:**
- Never write a secret-shaped literal (API key, private key block, bearer token) directly in a script, docstring, or
  comment, even to test for it. Build it from concatenated pieces, a format string, or a small decode step at runtime.
- When a probe WRITES a fixture file for another tool to scan, build the fixture's content the same way, in memory,
  immediately before writing it to the throwaway path.
- This applies recursively: a script that scans for "does this file contain a secret-shaped literal" must itself be
  checked against this rule, since it is highly likely to contain example patterns.
