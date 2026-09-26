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

The five files here are the incidents that cost the most, each generalizable:

| file | the lesson |
|---|---|
| `self-matching-hook-recursion.md` | a checker that finds files by content and executes them must exclude itself (~1,400 processes) |
| `newest-first-order-makes-first-print-a-lookahead.md` | APIs return newest-first; "first" was "last"; sort before any first/last logic |
| `api-page-cap-drops-the-losses.md` | a 1,000-row page cap selected the sample and faked an edge |
| `bash-heredoc-backslash-n-gotcha.md` | write code with the editor tools, not heredocs |
| `harness-low-memory-guard-kills-tracked-jobs.md` | long jobs run detached, resumable, with a heartbeat |
