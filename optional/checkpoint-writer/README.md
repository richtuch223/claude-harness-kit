# checkpoint-writer — a PreCompact hook that saves what compaction would drop

**Trigger to install it:** a session lost its plan, a kill rule, or a rejected finding after context
compaction, and the next session rebuilt it from scratch.

Compaction summarizes the conversation; the summary is written by the model under pressure and drops
the state nobody wrote down. This hook fires on `PreCompact` (manual `/compact` and automatic) and
writes `ops/checkpoints/<UTC>.md` with the parts a script can derive (last commit, working-tree
counts, recently touched folders, frozen `SPEC*.md`, retracted registry ids) and headed blanks for
the parts it cannot (objective, phase, next action). The blanks are the point: the next session sees
exactly which three things to ask for.

## Install

1. Copy `checkpoint_writer.py` into your repo (the source program keeps it at `src/hooks/`).
2. Merge `settings.snippet.json` into `.claude/settings.json` (adjust the path).
3. `python <path>/checkpoint_writer.py --selftest` → `SELFTEST OK`.
4. Probe it: `/compact` in a session, then `ls ops/checkpoints/`.

If your Claude Code build rejects the `PreCompact` key, wire the same command under `SessionStart`;
the checkpoint header then says `FALLBACK` so nobody mistakes it for a true pre-compaction snapshot.

## Properties

- Fail-open: any exception prints to stderr and exits 0. A broken hook must never block compaction.
- Same-second collision guard (`O_EXCL`): two events never clobber one file.
- Read-only on everything it scans; it never edits `facts/registry.yaml`.
- Repo root: `--repo`, else `$CLAUDE_PROJECT_DIR` (set by Claude Code for hooks), else the git toplevel, else the cwd.
- One time budget (20 s) for every git call and the spec scan, below the 30 s hook timeout in the snippet: the
  file is always written, and a section that ran out of time says so.
- `WATCH_DIRS` at the top of the file lists the folders whose subfolders are ranked by mtime; edit it.

Commit the checkpoints or gitignore them: the source program commits them (small, dated, useful in
`git log` when a session's intent is in question).
