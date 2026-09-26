# session-brief — a derived "where are we" at session start

**Add when:** `docs/STATE.md` passes ~80 lines, or sessions keep opening with "where were we?".

A SessionStart hook's stdout is added to Claude's context. `session_brief.py` builds a short brief from
things that cannot go stale: the git branch and recent commits, uncommitted-file count, the `live` /
`waiting` / `blocked` thread headings in `docs/STATE.md`, and the open `- [ ]` items under "Blocked on a
maintainer". It prints how many threads it loaded, so an empty brief is visible instead of silent. The
output is hard-capped (default 4,000 chars ≈ 1k tokens; `BRIEF_MAX_CHARS` overrides).

Install: copy `session_brief.py` to `<repo>/tools/hooks/`, merge `settings.snippet.json` into
`.claude/settings.json`, run `python tools/hooks/session_brief.py --selftest`, then start a session and
ask "what did the session brief say?".

The full-scale version that this is cut down from (the source repo's `command_center.py --brief --hook`, not shipped): threads from a YAML file, pinned gotchas, a budget meter, a `--closed "<term>"`
search across every verdict source, and a dangling-pointer audit. It injected ~950 tokens in 0.09 s.
Read it for ideas; it is tied to that repo's file layout.
