# 02 — The cross-family verification loop

**The rule: nothing verifies itself.** The model that wrote a piece of code shares its own blind spots
when it reviews that code, and a session that watched the code being written also inherits the author's
framing. So the kit splits verification into two independent axes: a different model family for the
code, and a Claude reviewer with a clean context for the conclusions.

| axis | catches | who | shape |
|---|---|---|---|
| **CODE** | lookahead, leakage, off-by-one, wrong join key, row-order bugs, guards that fail open | a **different model family** (GPT, Gemini, DeepSeek, Kimi…) | reviews only what we send (`tools/verify.py`). API routes: text in, text out, no tools at all. CLI routes: Codex runs with its shell, exec and web tools switched off; Gemini CLI is not hardened against file reads (see Safety below) |
| **JUDGMENT** | overclaiming, circular selection, hidden degrees of freedom, "the metric moved but the outcome didn't" | the strongest Claude model in a **fresh seat** (`adversarial-verifier` agent) | runs the repro itself; default verdict is REFUTED |

Keep them separate. Routing a non-Claude model through Claude's tool loop, or letting a different-family
model write to the repo, collapses the two and was rejected on evidence in the source repo.

## What it caught (source repo, 2026)

- A trade-history pull capped at 1,000 rows per market silently dropped the early (losing) prints of
  markets that later got busy, faking an ~80% edge whose true value was ~0%. The cross-family reviewer
  flagged it as MAJOR before the corrected data existed; the author's self-review missed it.
- A "first print" read from an API that returns **newest-first** was actually the *last* print, a
  look-ahead worth 26 percentage points (+57.8% → honest +31.7%, CI through zero). The fresh-seat
  judgment verifier caught this one; the code reviewer had not. That is why both axes exist.
- A two-task bake-off across four GPT-family seats: every seat found real bugs the author missed, and
  **no single seat found both headline bugs**. One pair caught a gzip append-after-kill that made a file
  0/100 readable; the other pair caught a 4,000-event page cap. Recall is complementary, so run a panel.
- A fresh-context skeptic overturned 3 of 4 "closed" results in one batch because the runs' reasoning
  wasn't reviewable. That produced the `decision-trail` skill.
- The kit's own pre-commit hook, reviewed by a two-seat panel in 49 seconds: three defects the author's
  own fault test missed (it checked the working tree instead of the staged content; a failing git
  command read as "nothing staged, ok"; a timeout didn't kill the child process tree). All fixed; the
  version in `kit/` is the fixed one.

## The loop: rounds until every material finding is resolved

One "looks good" is not a closed gate. The gate closes when **every material finding is either fixed
or dismissed with evidence**. The count is only a progress signal. In practice it shrinks round over round:

```
round 1  full review, two seats                         → 30 findings
          adjudicate each against the real code: fix / dismiss-with-reason
round 2  re-review, scoped: "only report what stops the  → 29
          pipeline, changes a number, biases toward PASS,
          or bypasses a guard"
round 3  review only the DIFF of the fixes               → 12
round 4                                                  → 7
round 5                                                  → 1   ← adjudicated (dismissed with a
                                                                reason) → closed
```

(Those are the real counts from one gate in the source repo.) Rules for the loop:
- **Adjudicate every finding against the files.** Severity labels run hot: a harmless approximation got
  called BLOCKER. Read the finding text, not the header; cheap models sometimes emit "no bug" filler
  under a MAJOR heading.
- **Scope later rounds down.** Round 1 is broad; later rounds only hunt for things that change outcomes,
  and review a small fix as a diff, not the whole file again.
- **Close on resolution, not on count.** Scoping a round down will shrink the count by itself, so the
  count proves nothing. If *new material* findings keep appearing after several fix rounds, the design
  is the problem; go back to `grill`.
- **Re-review only after a substantive fix.** Re-asking about unchanged material, hoping for a different
  answer, is not a round.

## When to run it (triggered by events, repeated only after fixes)

A verifier that runs on every edit becomes a co-author and inherits the author's frame. It also burns
tokens. Trigger on events only:

| event | code gate (`cross-review`) | judgment gate (`adversarial-verifier`) |
|---|---|---|
| new/changed code that computes a metric, split, feature, or quoted number | routine panel (two cheap seats) | — |
| a spec about to freeze | — | design review (Gate-0) |
| a result about to be quoted, banked, or acted on | refutation vote (`verify.py refute`) | re-runs the repro and attacks it |
| real spend, a deploy, anything irreversible | strong panel | yes |
| one-off log parse, a plot, an inventory — **unless** its number will be quoted or acted on (then the rows above apply) | skip | skip |

One question per call. Never spawn the verifier twice on the same question hoping for a different answer.

## Low-token version

If you want none of the agent machinery, this still works:
1. `python tools/verify.py review --uncommitted --focus "<one line>" -o review.md` before you commit
   anything that produces a number. It's one external API call from a script and needs no subagents.
   It costs Claude context only if you ask Claude to read the report.
2. Read the report yourself, or ask Claude to adjudicate each finding against the files.
3. After fixing, review the diff of your fixes. Stop when every material finding is fixed or dismissed
   with a reason.

The free route: a Gemini AI Studio key, `-p gemini --reasoning low`. For stronger panels: a ChatGPT
subscription through the Codex CLI (`-p codex-cli -m terra,luna`: two seats, OAuth, no API key; seat
names are aliases in `CODEX_TIERS`, confirm them with `/model` in `codex`), or OpenRouter
(pay-as-you-go; the source repo's routine gates cost fractions of a cent to a few cents each).

## Safety properties of `tools/verify.py`

- API providers get text only; they have no way to touch the machine. CLI providers (Codex, Gemini CLI)
  run from an empty scratch directory with the prompt on stdin, and write-access flags are refused.
- **A read-only sandbox blocks writes and network, not reads.** A canary probe on codex-cli 0.155.1 showed
  a `-s read-only` reviewer reading a file outside its scratch cwd back verbatim. What stops reads is
  switching the tools off: `verify.py` runs Codex with `--strict-config -c features.shell_tool=false
  -c features.unified_exec=false -c tools.web_search=false --ephemeral`, and with those off the same probe
  answered CANNOT_READ. `--strict-config` makes a key renamed in a future Codex fail the call instead of
  silently handing the shell back. Re-run the canary after a Codex upgrade.
- **The Gemini CLI route is NOT hardened against file reads**: no tool-off switch is passed, and a scratch
  cwd does not confine its read tools. If the machine holds data that must not leave it, use Codex or an
  API route.
- `tools/verify_sealed.txt` lists path patterns that must never leave the machine (secrets, sealed
  holdout data). The script refuses them.
- `--max-input-chars` trips on an accidental dump; `--dry-run` prints exactly what would be sent.
- A stalled reviewer writes a `result.json` with the reason and exits 4 (a reasoning model once burned
  ~45 minutes and returned nothing).
- `python tools/verify.py --selftest` checks all of the above offline.

## The judgment verifier's checklist (from `adversarial-verifier`)

Reproduce (mandatory, run the command) · leakage and row order · trivial baseline · multiplicity (how
many variants were tried?) · concentration (minus the top-1 contributor) · offline metric vs real
outcome · does the headline reconcile with the script's own other outputs? One result in the source repo
passed a code gate while being internally impossible (−476% of capital at risk). A code gate checks
code, not whether the outputs agree with each other.
