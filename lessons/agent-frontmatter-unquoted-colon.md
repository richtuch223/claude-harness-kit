---
name: agent-frontmatter-unquoted-colon
description: An agent/skill .md whose frontmatter description contains an unquoted ": " is invalid YAML, and the harness silently drops the whole file — the agent or skill simply does not exist, with no error anywhere. Validate every frontmatter block with a real YAML parser after any edit, and quote any description containing ": ".
metadata:
  type: feedback
---

**What happened:** an agent's frontmatter description read (among other things) `"...Default posture: refuted unless
the evidence forces otherwise."` — a colon followed by a space inside an UNQUOTED YAML scalar. YAML reads a bare
`key: value` pair as starting a new mapping, so `description: Adversarial skeptic... Default posture: refuted...`
parses as "mapping values are not allowed here" and the whole file fails to load. The harness's loader skipped the
broken file with no warning: spawning the agent failed with "agent type not found", which reads like the file was
deleted, not like it has a syntax error. Every routing rule that named that agent had been silently falling back to
something else (or to nothing) for as long as the bug was present.

**Second-order case:** a later fix that WRAPPED the description in quotes left the closing `---` glued onto the last
field's value on one physical line (e.g. `model: opus---` with no line break), and a pre-commit check that split the
file on any `---` substring accepted that as valid frontmatter anyway — the delimiter check must require the closing
`---` to be its OWN line, not merely present somewhere in the text.

**Why:** frontmatter is almost always edited as prose, not as data, so an author writes a natural sentence containing
a colon without ever thinking "this is YAML." The harness fails CLOSED in the sense that it never loads a broken
agent, but closed in a way that produces no visible error — which is worse than failing loudly, because nothing
reminds anyone to check.

**How to apply:**
- After editing any `.claude/agents/*.md` or `.claude/skills/*/SKILL.md`, parse its frontmatter block with a real
  YAML parser (`yaml.safe_load`) before trusting it — do not eyeball it.
- Quote a description (or any frontmatter value) that contains `: `, or any value containing a character YAML treats
  specially (`: `, `#`, leading `*`/`&`/`!`, etc.).
- Make the closing-delimiter check LINE-ANCHORED (`^---$` on its own line), not a substring search, so a value that
  happens to contain `---` cannot be mistaken for the real closing fence.
- Add this check to a pre-commit hook so a broken agent/skill file blocks the commit instead of silently shipping.
