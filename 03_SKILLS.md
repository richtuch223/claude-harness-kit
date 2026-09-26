# 03 — The skills

A skill is a markdown file (`.claude/skills/<name>/SKILL.md`). Only its one-line `description` sits in
context; Claude loads the body when the description matches what's happening, or when you type
`/<name>`. So a skill costs almost nothing until used, and it's the right home for any procedure.

**Why descriptions matter:** a skill that never triggers is dead weight. Write the description as a
*trigger* ("Trigger when…"), not a summary. If you notice a skill not firing, call it by name once
(`/stop-slop`) and sharpen its description with the situation you were in.

| skill | one line | level |
|---|---|---|
| **`stop-slop`** | strip AI tells from anything a human will read | 0 |
| **`grill`** | interview to convergence before expensive work | 0 |
| `screen-proposal` | the cheap "no" gate every idea passes first | 0 |
| `experiment-spec` | freeze what success means before running | 0 |
| `bank-result` | record a decided result in the same turn | 0 |
| `decision-trail` | append-only TSV log for long autonomous runs | 0 |
| `cross-review` | different-family code review via `tools/verify.py` | 2 |

---

## 1. `stop-slop` — the unslop pass ★

**The problem.** Model-written prose has tells: throat-clearing ("It's worth noting that"), "not X, but
Y" contrasts, rule-of-three lists, em-dash chains, -ing puffery ("highlighting", "showcasing"), hedges
stacked two deep, a closing line that tries to be profound. Readers learn to skim it, and then they miss
the one sentence that mattered.

**Two layers:**
1. **Always on, for chat** — five lines in the `CLAUDE.md` template ("Terminal output style"): start at
   the finding, every sentence is a fact/decision/consequence, active voice, no chatbot closers, name the
   file/number. Standing cost: ~80 tokens.
2. **The full pass, for documents** — the skill. Ten rules plus a crutch-word list (*load-bearing,
   crucial, robust, deep dive, nuanced, seamless, leverage, delve* → replace with the plain claim), and a
   diagnostic: *"What makes this obviously AI-generated?"* then fix those tells. Two or more hits in a
   section → rewrite the section, don't patch phrases.

**When to reach for it:** a README, a PR description, a design doc, a status update, an email, release
notes, anything leaving the terminal. Type `/stop-slop` over a draft, or ask "unslop this".

**The house rules that matter most:** lead with the outcome (the reader reads two sentences); numbers
appear with their population attached; verdict words (PASS, FAIL, KILLED, PARKED) keep their exact
meaning and are never swapped for synonyms.

Adapted from Hardik Pandya's `stop-slop` and poteto's pstack `unslop`.

---

## 2. `grill` — resolve every design branch before compute is spent ★

**The problem.** Ambiguity in a request surfaces mid-build (expensive) or at review (worse). `grill`
moves it to minute zero.

**Protocol:**
1. **Answer what the repo can answer first.** Grep DECISIONS/STATE before asking anything. A question
   the codebase already answers is never asked.
2. **Ask in rounds.** Each round contains every question whose prerequisites are settled. No
   drip-feeding.
3. **Number every question and give a recommended answer** with a one-line reason, so the human can
   reply "1a, 2 agreed, 3: no, use X" and be done.
4. **Questions only data can answer are not asked.** They become the cheapest probe, with a kill
   condition stated up front.
5. **Stop when the branches resolve**, two or three rounds max. Then hand off to `screen-proposal`,
   `experiment-spec`, or the build.

**Challenge unsupported assumptions when you find them.** Agreeing with a well-grounded recommended
default is a fine outcome; accepting a premise nobody checked is not. Attack the definition, the unit,
and the horizon first.

Adapted from Matt Pocock's `grill-me`. Use it by saying "grill me on this".

---

## 3. `screen-proposal` — say no cheaply

Runs in order and stops at the first fail: already decided? → serves the guiding question? → right
stage? → cheapest decisive probe + kill condition → baseline → cost/reversibility. Returns BUILD-NOW /
PROBE-FIRST / WAIT / ALREADY-DECIDED. Never PROBE-FIRST without a pre-stated kill condition. Fill in your
guiding question and stage order (the `<!-- FILL -->` markers).

## 4. `experiment-spec` — freeze before you run

A spec is drafted as `SPEC.draft.md`; **freezing is a rename to `SPEC.md`**, after which a hookify rule
blocks every edit. Required sections: hypothesis, what's already decided, population, split rule,
exactly ONE primary metric with its CI, baselines, kill tree, anti-fooling checks, offline→real mapping,
reconciliation, repro. A design review by the `adversarial-verifier` (Gate-0) happens before the freeze.
Why: post-hoc threshold tuning, "let's also try X" bundled into the primary, and a pre-commitment
rewritten after the results were in all happened in the source repo.

For non-research code, the same idea at smaller scale: write the acceptance test before the change.

## 5. `bank-result` — record it in the turn you decide it

The closing half of the lifecycle: result file → registry (retracted numbers are kept and marked, never
deleted) → DECISIONS row → STATE update → one commit with the script and its decision trail. A verdict
that existed only in chat was lost with the chat once; the rule became "defer the re-test, never the
record".

## 6. `decision-trail` — make long runs reviewable

One TSV row per decision during an unattended run (`ts phase decision why evidence result`),
append-only, committed with the result. It turns "why did the overnight run conclude X?" from forensics
into reading. Adapted from pstack `show-me-your-work`.

## 7. `cross-review` — see `02_VERIFICATION_LOOP.md`

---

## Heavier skills from the source repo, not shipped

`prereg` (full pre-registration with a multiplicity ledger, i.e. a count of every test run against the
same data, and an "alpha-wealth" budget that makes each new test spend part of a fixed false-positive
allowance), `verdict` (the bank step plus a mandatory second review before any batch of closures), and
`ow-review` (the original verifier skill). They are heavier versions of
`experiment-spec` / `bank-result` / `cross-review`. They are domain-bound, so they are described here rather than
shipped; build them from these descriptions when your project has a scarce holdout or runs many tests against the
same data.
