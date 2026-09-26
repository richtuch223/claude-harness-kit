---
name: self-matching-hook-recursion
description: "A checker that greps staged files for a token and EXECUTES the matches must exclude itself - the pre-commit hook ran itself with --selftest recursively (~1,400 chained python processes) on its first real commit, 2026-09-21."
metadata:
  type: feedback
---

`ops/githooks/pre_commit_check.py` runs `<script> --selftest` for every staged .py whose source contains the string
"--selftest". The hook's own source contains that string, so on the first commit that staged the hook it executed itself,
which executed itself again: a linear chain of ~1,400 python processes in about two minutes before it was noticed (the
commit "hung"). Collectors survived; 12 GB RAM was still free.

**Why:** my throwaway-repo test planted six faults but never staged the hook file itself, so the one input that recursed
was never exercised.

**How to apply:** any tool that discovers files by content and then runs them needs (1) an explicit self/dir exclusion,
(2) a re-entry guard (env var), (3) a no-op under the flag it passes to children - and its test must include ITS OWN file
as an input. Kill such a chain with a server-side command-line filter
(`Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%pre_commit_check%'"` + `Terminate`),
never by image name: the collectors are python.exe too (see [[harness-low-memory-guard-kills-tracked-jobs]]). Fastest first
move is to EDIT the script so new children exit at once; the chain then stops growing while the kill runs.
