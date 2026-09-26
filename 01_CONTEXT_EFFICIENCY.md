# 01 — Context efficiency

The model reads its context window on every turn. Anything that sits there permanently (CLAUDE.md,
skill descriptions, agent descriptions, whatever a session reads to orient itself) is paid for on every
session, whether it helps or not. A long, stale context also makes the model worse, not only more
expensive: in the source repo's failure audit, 3 of 16 real user corrections in one month came from
long-thread context degradation.

This is the cheapest part of the kit to adopt and needs no agents at all.

## 1. Know what you're paying for

| surface | when it's paid | cost in this kit |
|---|---|---|
| `CLAUDE.md` | every session, in full | template: 74 lines ≈ 1.8k tokens (the source repo's grew to ≈ 7.1k); keep yours under ~150 lines |
| skill + agent **descriptions** | every session | ≈ 1.1k tokens for all 12 in the kit |
| skill **bodies** | only when the skill triggers | 0 until used |
| agent descriptions | every session | one line each |
| agent bodies / subagent runs | only when spawned; their work happens in a **separate** context | 0 until used |
| hooks | never (they run outside the model) | 0, unless a hook *injects* text |
| files Claude reads to orient | every session that reads them | this is where the money goes |

**Measure it.** `/context` in Claude Code shows the breakdown. For files, bytes ÷ 4 ≈ tokens. Re-measure
monthly: the source repo cut orientation from ~40.6k to ~18.2k tokens in one consolidation pass, then
drifted back to ~44.3k over seven weeks because one queue file grew and nobody re-measured.

## 2. Layer files by how often they change, not by topic

| layer | example | change rate | rule |
|---|---|---|---|
| L0 doctrine | `CLAUDE.md`, `docs/PROJECT_CONTEXT.md` | only when something is proven wrong | hard line cap; adding a rule means naming what it displaces |
| L1 procedures | `.claude/skills/*` | when you learn a better method | loaded on demand, so detail belongs here |
| L2 decisions | `docs/DECISIONS.md` | append-only, constantly | **grep it, never read it whole** |
| L3 live state | `docs/STATE.md` | free churn | the only hand-maintained "where are we" file |
| numbers | `facts/registry.yaml` | cuts across layers | one home per figure; docs cite the id |

The failure this prevents: the source repo's two most stable files (CLAUDE.md and its research canon)
were its 2nd and 4th most edited, because a "where next" section lived in them. It went stale and
misdirected every session for six weeks while reading with the authority of the top doc.

**Rules that fall out:**
- Something that churns goes to STATE.md, never CLAUDE.md.
- A procedure goes in a skill; a rule that must hold goes in a hook. CLAUDE.md holds what every
  session needs before its first action.
- When a doc contradicts CLAUDE.md, rewrite the claim. Never append a "BUT…" clause; one section in the
  source repo reached 195 lines that way.
- Exactly three state files. A fourth is the failure mode; extend one instead.

## 3. Grep, don't read

Any doc over a screen gets grepped. The source repo's verdict ledger reached ~79k tokens; every agent
prompt there says "GREP, never read, RESEARCH_STATE.md". It also grew a one-command lookup
(`command_center.py --closed "<term>"`) that searches verdicts, queue, doctrine, memory, and written
probes at once, so "has this been tried?" costs one tool call instead of a document read.

Transcript mining found 18.7% of tool calls were searches, and 81 `ls`/`find` calls re-discovered the
same eight data paths. The fix was a measured path map in CLAUDE.md. Put the paths people actually
hunt for in CLAUDE.md; leave out the ones you guess they'll need.

## 4. Keep things off the read path entirely

`.claudeignore` (in the kit) keeps secrets, heavy data, model weights, logs, and caches out of what Claude
reads. One lesson from the source repo: ignoring a whole `archive/` folder hid the decision record from
reads while leaving it reachable by shell, which made the record look missing. Ignore the **heavy
artifacts**, not the reasoning.

## 5. Derived beats written

A paragraph describing current state goes stale the day it's written. A script that *computes* the
state from git plus one small YAML can't drift from its sources (it can still break; see below). The source repo's SessionStart hook
(`command_center.py --brief`) injects ~950 tokens of live state in 0.09 s; before it, three docs each
answered "what's next" and disagreed. No long-thread corrections were recorded after it shipped.

The kit ships this as **optional** (`optional/session-brief/`), because a hand-kept STATE.md is cheaper
until it passes about a screen. When you build one:
- print how many things it loaded, and exit non-zero on a dangling pointer. A derived surface fails
  silently: a missing `pyyaml` made the source repo's brief load zero threads for a week while looking fine;
- smoke-test it on a fresh clone.

## 6. What delegation is for

Subagents run in their own context window. In the source repo their value was keeping raw output
**out** of the main context:
- a long mechanical run (data pull, batch scoring) whose logs you never need to see;
- a verifier that must not inherit the author's framing (context isolation is the point);
- parallel breadth (several scouts), only when you actually have several questions.

Not for trivia. On a single machine with a large-context main model, writing the recipe often costs more
than doing the job. Write a recipe only when it's shorter than the work. The main loop reads the
subagent's **summary**, never its raw output.

## 7. Session habits (no file can enforce these)

- Start at STATE.md, then grep DECISIONS.md. Never re-derive what a decision row settled.
- Confirm you have the **current** artifact (date suffix, mtime) before building on what a grep found.
  2 of 16 real corrections in one audit were "you're looking at old stuff".
- Batch independent tool calls. End the turn instead of polling; long jobs run detached with a log.
- Decisions waiting on a human go to ONE list (STATE.md "Blocked on a maintainer") in the same turn.
  In chat scrollback they got lost repeatedly.
- Bank a result in the turn you decide it. A verdict that lives only in chat has the chat's lifetime.
- Memory is for the non-obvious: one fact per file, with *why* and *how to apply*, and an index of
  one-line pointers (`lessons/` has the format). Don't save what the repo already records.

## 8. Before adding anything to the harness

1. Name the surface (hook / skill / agent / CLAUDE.md line).
2. Price it in standing tokens.
3. Prefer derived over written.
4. Check it doesn't contradict something already loaded.
5. Measure after, and claim an improvement only with a before/after number.

Rejected in the source repo on evidence: mega-frameworks (each Claude Code release absorbs what they
provide; the rest taxes every session), wholesale skill-pack installs (one skill in six fills a real
gap, so install that one), and N-parallel "arena" attempts. Rewriting four skill descriptions as short
*triggers* cut ~900 words of standing context to ~180.
