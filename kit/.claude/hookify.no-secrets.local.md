---
name: no-secrets
enabled: true
event: file
action: block
conditions:
  - field: content
    operator: regex_match
    pattern: (AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|-{5}BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-{5}|(?i:api[_-]?key|secret|password|token)\s*[:=]\s*['"][A-Za-z0-9_\-/+]{24,}['"])
---

🛑 **Key-shaped literal in a file edit.**

CLAUDE.md: no secrets in the repo. Keys live in `.env` (git-ignored) or the OS keychain, and code reads
them from the environment.

**Do this instead:**
- `os.environ["SERVICE_API_KEY"]` (and add the name to `.env.example` with a dummy value).
- If this is a placeholder, use one that cannot pattern-match a real key (e.g. `sk-EXAMPLE`).

A committed key is unrevocable history: it has to be rotated, not just deleted. The pre-commit hook
scans the staged diff as a second line; this rule is the first.

Rule-authoring notes (hookify): the frontmatter is split on the literal three-dash string, so that
string must never appear in the frontmatter, not even in a comment (it took the whole harness down
once, 2026-09-21). Write a run of dashes as a counted repeat. Field `content` covers both Write and
Edit; `new_text` covers Edit only. No inline case flag in the middle of a pattern; use a scoped group.
