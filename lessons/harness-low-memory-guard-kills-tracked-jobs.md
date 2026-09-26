---
name: harness-low-memory-guard-kills-tracked-jobs
description: "The Claude Code harness kills ANY tracked background Bash job when system RAM is low, regardless of the job's own size (30 MB collectors died twice while a browser and other desktop apps held the RAM); run multi-hour collectors detached via PowerShell Start-Process and poll their logs."
metadata:
  type: feedback
---

On a 15 GB laptop (~4 GB free with the user's other apps open) the harness's low-memory guard killed two 30 MB
Python collectors twice in one evening. The guard is system-wide, not per-job.

**Why:** hours-long data pulls are a normal research workload; losing them silently costs a night.

**How to apply:** launch anything that must run > 10 minutes with
`Start-Process -FilePath python -ArgumentList "<script>" -WorkingDirectory <dir> -WindowStyle Hidden -RedirectStandardOutput <log>`
make every collector resumable (skip files that exist), and wake the session
with a `Monitor` that greps the script's own log for FINISHED. Monitors are harness tasks too and can also be killed:
after a long wait, read the logs directly. Killing by command-line pattern (`Stop-Process` on `*script.py*`) also hits
any other process whose arguments mention that file name (it killed an `ow_verify` review that listed the script as a
`--files` argument). Related: [[self-matching-hook-recursion]].
