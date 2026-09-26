# 04 — Writing the CLAUDE.md

`CLAUDE.md` is the only file Claude Code loads into every session automatically. Treat it as a **map
and a rulebook**, not a knowledge base: it tells a fresh session what the project is, what it must
never do, and **where to look for everything else**. The knowledge lives in other files, which the map
points to and a session opens only when it needs them.

This is the anatomy of the source repo's CLAUDE.md after three months (~400 docs, ~1,100 scripts behind
it), with the content removed and the pattern kept. The kit's `kit/CLAUDE.md` is the same skeleton at
day-zero size.

## The test for every line

> Does a fresh session need this **before its first action**, in **every** session?

- Yes → it stays.
- Only sometimes → it's a **pointer** to another file.
- It's a procedure → a **skill**.
- It must never be violated → a **hook**, with one line here saying the hook exists.
- It changes weekly → **STATE.md**, never here.

## The sections, in order

### 1. Identity (1–3 lines)
What the project is and what "good work" means here. No history, no roadmap.

### 2. Standing constraints: "these OVERRIDE default behavior, obey exactly"
Short, imperative, each one checkable. What must never be edited without asking (frozen configs, label
definitions), where secrets and heavy data must never go, and single-use rules (sealed holdouts).
Wording that worked: *"You do NOT: rewrite X, Y, Z without explicitly asking first."*

### 3. Read order, in tiers, with sizes
This section does the most for context efficiency. The day-zero version is just: this file, then
`docs/STATE.md`, then grep `docs/DECISIONS.md` (that is what `kit/CLAUDE.md` does). The example below is
the mature version, and it assumes pieces you add later (a session-brief hook, a memory index, a lookup
command); leave those lines out until they exist.

```markdown
## START HERE — hundreds of docs exist; you read TWO

**TIER 0 — already in your context, no action.** This file + the memory index + the auto-injected
live-state brief. If the brief is missing, run `<command>`.

**TIER 1 — read these two before proposing or running anything:**
1. `docs/<ONE_DOCTRINE_DOC>.md` — principles, rules, where to go next.
2. `docs/STATE.md` — the live queue.

Then **grep — do not read** `docs/<BIG_LEDGER>.md` (~77k tokens). Fastest: `<one-command lookup>`.
```

The source repo generates the file counts and token sizes in this section with a script, between
`<!-- BEGIN GENERATED -->` markers, so "~77k tokens, GREP IT" stays true without anyone re-measuring by
hand.

### 4. Current work: POINTER ONLY
The most important lesson from the source repo. Its CLAUDE.md once carried a 40-line "where next" list.
The list went stale, CLAUDE.md became the 2nd most-edited file in the repo (12 commits in 30 days), and
the stale list misdirected every session for six weeks, carrying the authority of the top doc. The fix
was to replace it with four lines of pointers:

```markdown
## CURRENT WORK — POINTER ONLY (state lives in docs/STATE.md)
Do NOT restate project state in this file.
- **Posture:** <the one stance a session must hold> → `docs/PROJECT_CONTEXT.md` §1
- **Where next:** → `docs/STATE.md`
- **Numbers:** never quote a figure from prose → `facts/registry.yaml`
- **Live threads:** `<command that prints the brief>`
```

### 5. Numbers come from one place
One paragraph: every quotable figure lives in `facts/registry.yaml`; grep it before quoting; retracted
means never quote. (Why: `LESSONS.md` §4.)

### 6. Model-tiered delegation *(only if you use agents, level 3+)*
A table of agent → model → what it's for, plus three rules: delegate mechanical work down; nothing
verifies itself; verification is event-triggered. Model pins live in agent frontmatter, and this table
names them.

### 7. Verification routing *(level 2+)*
Two or three lines: which command runs the cross-family code review and which agent runs the judgment
review. Detail lives in the `cross-review` skill.

### 8. Harness pointer
One line: "How the harness is shaped and what was rejected: `docs/HARNESS.md`. Read before changing it."

### 9. Output style (always on)
The five `stop-slop` core lines. This is the one procedure that earns a place in CLAUDE.md, because it
applies to every reply.

### 10. Project structure
Top-level folders, one line each. Name the one or two non-obvious locations (where the frozen model
lives, where heavy data lives).

### 11. Hard gotchas, numbered, each with its evidence
Only the traps that already cost real time, each with the number that proves it mattered:

```markdown
1. **The true training set is `<file>.bak.<date>`**, not the unsuffixed file (rewritten on <date>;
   sim PF 0.48 vs 1.36). All training uses the `.bak`.
```

A gotcha without its evidence gets "simplified" away by the next edit. A gotcha that must never be
violated also gets a hookify rule (the source repo had one for exactly this trap).

### 12. The measured path map
Mine your transcripts (`~/.claude/projects/<project>/*.jsonl`) for the paths sessions keep hunting with
`ls`/`find`, and list them with how often they were hunted:

```markdown
### Paths sessions actually hunt for (measured from 33 transcripts — don't `ls` for these)
- `data/fire_harvest/` — **hunted 31×.** `fires_master.parquet` is a HARVEST file: filter `band == ...`.
- ⚠️ **Empty despite being referenced:** `data/c2/sweeps/` (hunted 9×, 0 entries).
```

List the measured paths only, never guessed ones. In the source repo, 81 `ls`/`find` calls
re-discovered the same eight paths before this section existed.

### 13. Search discipline
Two lines: prefer the Grep/Glob tools over shell `grep`/`find`; use the language server for "does this
symbol still exist"; confirm you have the **current** artifact (date suffix, mtime) before building on
what a search found.

### 14. On first run
One or two concrete actions, e.g. "inspect one parquet to confirm column names before writing feature
code".

## Anti-patterns (all happened in the source repo)

| anti-pattern | what went wrong | do instead |
|---|---|---|
| project state in CLAUDE.md | stale "where next" misdirected sessions for 6 weeks | pointer to STATE.md |
| appending "BUT…" to a rule | one section reached 195 lines with an inline retraction | rewrite the rule |
| citing a big doc by line number | anchors drifted +27 lines in a day and broke a pointer | cite by phrase |
| dated override banners ("X is OFF until further notice") | they pile up at the top and outlive their reason | put the override in STATE.md with an expiry; keep CLAUDE.md timeless |
| a procedure written out in full | paid every session, used in one | a skill; CLAUDE.md names it |
| a rule that "must never" happen, in prose only | nothing enforced it | a hook, plus one line here |
| guessed file pointers | sessions still hunted | the measured path map |
| growth with nobody measuring | orientation cost went from ~18k to ~44k tokens | re-measure monthly; put sizes in the generated block |

## Size

The kit's template is 74 lines (≈1.8k tokens). The source repo's reached ≈7k tokens on a very large
project, and that was the upper end of acceptable. Aim for under ~150 lines. When something new wants
in, name what it displaces.
