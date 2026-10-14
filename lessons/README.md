# lessons — incident memories, and the memory format

Claude Code keeps a per-project memory directory (`~/.claude/projects/<project>/memory/`). The source
repo used it as a set of small files, **one fact per file**, plus an index of one-line pointers
(`MEMORY.md`) that loads every session. The index is the "pointer" layer: cheap to load, and each line
says when the full file is worth opening.

File format:

    ---
    name: short-kebab-slug
    description: one line, used to decide relevance
    metadata:
      type: user | feedback | project | reference
    ---
    The fact. For feedback/project: a **Why:** line and a **How to apply:** line. Link related memories
    with [[their-slug]].

Rules that kept it useful: don't save what the repo already records (code, git history, CLAUDE.md);
update an existing file instead of adding a near-duplicate; delete memories that turn out wrong. Memory
lives in the home directory and does not travel with a clone, so the source repo keeps a repo-tracked
export (`ops/memory_export/`).

The files here are the incidents that cost the most, each generalizable:

| file | the lesson |
|---|---|
| `self-matching-hook-recursion.md` | a checker that finds files by content and executes them must exclude itself (~1,400 processes) |
| `newest-first-order-makes-first-print-a-lookahead.md` | APIs return newest-first; "first" was "last"; sort before any first/last logic |
| `api-page-cap-drops-the-losses.md` | a 1,000-row page cap selected the sample and faked an edge |
| `bash-heredoc-backslash-n-gotcha.md` | write code with the editor tools, not heredocs |
| `harness-low-memory-guard-kills-tracked-jobs.md` | long jobs run detached, resumable, with a heartbeat |
| `secret-fixture-must-build-at-runtime.md` | a secret-scanner test fixture must build the fake key at runtime, or it looks like a real leak |
| `agent-frontmatter-unquoted-colon.md` | an unquoted `: ` in agent/skill frontmatter is invalid YAML and the file is silently dropped |
| `current-identifier-survivorship-trap.md` | never resolve a historical population through a CURRENT identifier list — it drops the dead |
| `free-data-apis-wall-history.md` | free APIs usually serve live data, not deep history; check lookback before designing a historical test |
| `in-sample-mean-normalization-hides-the-contrast.md` | a statistic measured against its own in-sample mean carries all of that mean's shrinkage; pre-state the real contrast |
| `api-error-body-cached-as-data.md` | a parseable JSON reply is not valid data; validate shape and keys before parse/cache (`tools/fetch_guard.py`) |
| `amend-after-a-failed-edit-records-a-no-op.md` | chain the edit and the pre-statement amend; `prestate.py amend` now refuses a no-op |
| `parallel-numeric-work-can-hard-reboot-the-box.md` | the box hard-rebooted under all-core numeric work beside six agent/CLI processes (display-driver starvation is the unproven hypothesis); cap threads, two heavy jobs at once |
