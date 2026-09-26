---
name: bash-heredoc-backslash-n-gotcha
description: On this Windows box the Bash tool turns a doubled backslash-n inside a quoted heredoc into a REAL newline, so python source embedded in heredocs must not rely on backslash escapes (use chr(10)/chr(92)).
metadata:
  type: feedback
---

Writing python via `python - <<'EOF'` or `cat > file <<'EOF'` in the Bash tool: a `\n` in the command text
arrives in the file as a literal line break, not as backslash-n. This produced an unterminated f-string that was
committed (70f3186) and took three fix attempts (2026-09-02) because each fix used the same escape.

**Why:** the tool/transport layer unescapes before Git Bash sees the heredoc, so the quoted delimiter does not
protect backslashes the way it would in a real terminal.

**How to apply:** in heredoc-embedded python, build escapes with `chr(10)`, `chr(92)+'n'`, or write the file with
the Write tool instead; always `py_compile` before committing a script written this way. Related:
on Windows, hooks and scripts should call `python`, not `python3`.
