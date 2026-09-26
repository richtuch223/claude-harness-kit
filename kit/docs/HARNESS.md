# HARNESS — how the agent harness is shaped, and why

Read before changing the harness, so the same dead ends are not re-walked. Append to §4 and §5 as you go.

## 1. Pick the right surface

| the thing is… | it belongs in |
|---|---|
| a rule that must be **enforced** | a **hook** (hookify rule, pre-commit, permissions) |
| a **procedure** | a **skill** (loaded on demand, ~zero standing cost) |
| a **delegation boundary** | a **subagent** (`.claude/agents/*.md`, model pin in frontmatter) |
| always-on project guidance | `CLAUDE.md`, kept under ~150 lines, paid every session |
| durable thesis / principles | `docs/PROJECT_CONTEXT.md` |
| **state** | exactly three files (§3) |

If a rule must hold, give it a hook: rules written only in prose were ignored in the source program.

## 2. What is installed

- **Agents:** `research-scout` (sonnet), `mechanical-runner` (sonnet), `investigator` (opus),
  `adversarial-verifier` (opus, fresh seat), `research-synthesizer` (sonnet).
- **Skills:** `stop-slop`, `grill`, `screen-proposal`, `experiment-spec`, `bank-result`, `cross-review`,
  `decision-trail`.
- **Guards** (need the hookify plugin): `no-secrets`, `protect-frozen-specs`, `no-heavy-data-in-repo`
  (block); `source-of-truth-gate` (warn).
- **Pre-commit** (`git config core.hooksPath ops/githooks`, per clone): staged `.py` must compile · a
  staged script's `--selftest` must pass · `facts/registry.yaml` must parse with unique ids · no secret in
  the diff, a staged key file, or a binary blob · no staged file over 5 MB (`PRECOMMIT_MAX_MB`) · no unstaged edits on a checked file · any git failure blocks.
- **Tools:** `tools/verify.py` (cross-family review; sealed paths in `tools/verify_sealed.txt`).
- **Workflow:** `.claude/workflows/discovery-sweep.js` (opt-in fan-out; costs nothing until run).

Guard status: <!-- record the date each guard was PROBED live, e.g. "no-secrets: blocked a test write 2026-10-01" -->

## 3. The state layers — exactly three, never a fourth

| layer | file | holds |
|---|---|---|
| live now | `docs/STATE.md` | threads in flight, next actions, blocked-on-maintainer items |
| decisions | `docs/DECISIONS.md` | append-only; a wrong decision gets a superseding row |
| numbers | `facts/registry.yaml` | one home per quotable figure, with population and status |

## 4. Deliberately NOT installed (and the trigger that would change that)

| left out | add when |
|---|---|
| SessionStart derived brief (`optional/session-brief/`) | STATE.md passes ~80 lines, or sessions keep re-asking "where are we" |
| number tripwire + JIT hooks (`optional/figure-hooks/`) | a number gets misquoted twice |
| multiplicity / alpha-wealth ledgers (see `03_SKILLS.md`, "Heavier skills") | a scarce holdout is sealed and tested repeatedly |
| domain hookify rules | a domain trap bites once; write the rule that day, with positive AND negative tests |

## 5. Tried and rejected in the source program

- **Mega-frameworks.** Each Claude Code release absorbs a layer they provide; the rest taxes context.
- **Routing a non-Anthropic model through `ANTHROPIC_BASE_URL`.** Collapses the tool-less code-review axis
  into the tool-bearing judgment axis.
- **A same-family second seat as the code gate.** Shares the author's blind spots.
- **Wholesale skill-pack installs.** One skill in six typically fills a gap; install that one.
- **A different-family model with write access.** It must never write to the repo.
