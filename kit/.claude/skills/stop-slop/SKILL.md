---
name: stop-slop
description: Unslop pass for human-facing prose. Trigger BEFORE handing over any text a person will read outside this chat — README, PR description, commit body longer than a line, design doc, spec prose, result writeup, status update, email, release notes, onboarding copy — and whenever someone says "unslop", "stop-slop", or that the writing reads like AI.
---

# stop-slop — remove AI tells from deliverable prose

Applies to **documents humans will read**. Chat replies follow the always-on core in CLAUDE.md
("Terminal output style"); this skill is the full pass for documents. Frozen specs and DECISIONS rows
are exempt; their structure wins.

## The rules
1. **Cut throat-clearing.** No "It's worth noting that", "Importantly,", "Let's dive in". Start at the
   finding.
2. **Break the formulas.** No "not X, but Y" contrasts as a crutch; no rhetorical-question setups; no
   rule-of-three lists where two items or four would be honest.
3. **Active voice, named actors.** "The scorer rejected 4 of 9 variants", not "it was found that".
   Inanimate objects do not "reveal", "highlight", or "underscore".
4. **Specific beats vague.** Name the dataset, the number (registry id), the population. "Significant
   improvement" with no figure is a tell; so is a figure with no population scope.
5. **Vary rhythm.** Matching sentence lengths, a punchy one-line closer, and em-dash chains all read as
   generated. Mix long and short.
6. **Trust the reader.** No "in other words" restatements, no recap of the paragraph above, no hedges
   stacked two deep.
7. **Kill the pull-quotes.** A sentence that wants to be on a slide gets rewritten as a plain fact.
8. **Say what it does, not how it feels.** If a sentence cannot be restated as an instruction, a fact,
   or a number, cut it. Second test: if it could appear unchanged in another project's docs, it says
   nothing about this one.
9. **Density check.** Every sentence states a fact, a decision, or a consequence. Delete the rest.
10. **Some soul is allowed.** An opinion or a reaction to a surprising number beats a neutral listing;
    first person is fine; an uneven structure reads as written.

## House additions
- **Crutch-word list:** load-bearing, crucial/critical/key, robust, deep dive, nuanced, seamless,
  leverage (as a verb), delve. Replace with the plain claim.
- Numbers appear **only** as registry citations or with population and status attached.
- Verdict words (PASS, FAIL, KILLED, PARKED, INCONCLUSIVE) keep their exact meaning; do not synonymize.
- Lead with the outcome; mechanism second; method last. The reader reads the first two sentences.

## Diagnostic
Before shipping, ask once: **"what makes this obviously AI-generated?"** Then fix those tells. Scan for:
adverb stacks · passive voice · "here's what/why" openers · "not X, it's Y" · em-dash overuse · -ing
puffery · meta-commentary about the document itself · a final line that tries to be profound. Two or
more hits → revise the section, do not patch the phrase.
