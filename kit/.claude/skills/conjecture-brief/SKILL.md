---
name: conjecture-brief
description: "Write the shared brief that chains one research conjecture into the next. Trigger BEFORE any multi-agent research fleet (3+ agents on one research question) and before the first probe of any new research vein, or when given a hypothesis or told to run it as a loop. Not for mechanical fleets (pulls, builds, parsing). The template, fill order, and launch recipe live in the body."
---

# conjecture-brief — the generator half of the research loop

Restores a practice that worked when it was used, and stopped only because nobody kept reusing the
template: a shared brief that each new fleet inherits from the last one's result, so a sequence of
probes compounds instead of restarting its context every time. The practice is worth restoring whenever
a project runs more than one research fleet on a related question.

The brief ADDS generation without loosening verification. It never replaces `screen-proposal`, a
pre-registration step, `tools/prestate.py`, `tools/instrument_check.py`, a literature check, or a
design/closure gate. It is staging text and carries no verdicts.

## 1. When to write one

- **Always:** before launching 3+ agents on one research question (any `fleet` fan-out that judges an
  outcome), and before the first probe that touches data in a vein that has no brief yet.
- **Chained:** when a fleet or probe on a vein finishes, the next fleet on that vein starts from a NEW
  brief whose §2 cites the previous brief's §9 hand-off. One brief per link in the chain, never an
  edited old one.
- **Skip:** mechanical fan-outs (data pulls, builds, parsing, file moves), a single-agent screen, or a
  pure recorder build with no outcome judged.

## 2. Fill order (each step feeds the next; copy `TEMPLATE.md`)

1. **§3 first, not §1.** Search your project's closed-question ledger (verdicts, a queue doc, a memory
   index) on at least 3 synonyms (mechanism, venue/instrument, plus the family name). Paste the hits
   verbatim, one line each, and say what each closes and what it does NOT close. A standing search
   command can still miss staging-only kills, so also grep any staging folders for candidate/screen/
   gate files directly. Name the failure law it rhymes with if your project keeps one (a failure-law
   table, see `docs/METHODS.md`). A hit that closes the conjecture with numbers ends the brief at §3:
   report ALREADY-CLOSED.
2. **§2 What the prior sequence established.** Number each established fact, link its file (or a
   registry id), and tag it LEDGER / VERIFIED / UNCONFIRMED. Agents cite these facts and do not
   re-derive them. Quote numbers only from the registry or the cited file, never from prose memory.
3. **§1 Conjecture.** Write one sentence, then label it **NOT YET A LAW: try to falsify it.** Name the
   central object (the quantity the conjecture is about) and its observables.
4. **§4 What is actually new here?** Pick exactly one taxonomy class your project uses. Name the
   **closest precedent** (a file or paper) and state the one difference. "Novel" with no named
   precedent gets the brief rejected.
5. **§5 Payer box.** Fill SOURCE -> TRANSMISSION -> EXIT. Then answer: who pays, why (a
   non-informational motive), and what rations it. "Inefficiency" or "ugliness" is not a rationing
   mechanism. Write the turnover/holding-period class and the lead time as timestamp arithmetic.
6. **§6 Kill conditions.** Write at most 8. Each one is a measurable threshold with its own
   instrument-null branch (NEEDS_DATA). State the minimum detectable effect and the **joint pass
   probability of the whole tree at that MDE** (see `docs/METHODS.md` §2 for the method). A tree that
   would fail a real edge more than half the time gets redesigned now, not at the design gate.
   Thresholds are set relative to the published effect size once the literature lane reports. These are
   CANDIDATE rules: freezing happens later via `tools/prestate.py freeze` or your pre-registration step.
7. **§7 Mandatory null controls.** Add at least 2 instruments or populations where the theory predicts
   NO edge, plus one positive control for the instrument itself. A brief with no control is not
   launchable.
8. **§8 Lanes.** Keep a fixed set, e.g.: LIT (2-3 papers read IN FULL — a scout summary does not count)
   · IDENTIFIABILITY (does the estimator recover the object in synthetic data?) · DOMAINS (2+
   instances) · DATA (source, history depth, **terms check**, instrument_check plan) · RED TEAM (the
   strongest reason the whole direction fails). Give each lane its DONE artifact and path. Lanes do not
   read each other's memos before synthesis.
9. **§9 Hand-off.** List what the next brief in the chain needs from this one, as questions the lanes
   must answer.

## 3. Launch recipe

- File: `<staging>/briefs/BRIEF_<slug>_<yyyymmdd>.md`. Copy it into the fleet folder as
  `_SHARED_BRIEF.md` when the fleet launches.
- **Agent prompt layout (cache-friendly, identical prefix):**
  1. the `fleet` §3 FLEET CONTRACT block, **verbatim, first**. Copy it from
     `.claude/skills/fleet/SKILL.md` at launch time; never retype it or keep a stale copy in the brief;
  2. brief §1-§8, identical for every agent;
  3. the per-agent lane assignment, last.
- Before launch: one cross-family design review,
  `tools/verify.py review -p <provider> -m <tier> --files <brief>`. If the brief's kill tree is about to
  become a frozen pre-registration, route that through your design/closure gate as usual. The brief
  review does not replace it.
- After the fleet: the main loop synthesizes the memos, answers §9, banks any verdict through your
  bank-result step, and logs the fleet with `tools/fleet_ledger.py add`. The next brief's §2 cites this
  synthesis.

## 4. Rejection checklist (fix before launch)

- §1 is more than one sentence, or is not labelled NOT YET A LAW.
- §1 has no equations (a verbal story is not a model), no predicted sign and size, no conservation
  check, or no rigor reach; or the anchor paper scores too low on your rigor rubric (`docs/METHODS.md`
  §4) to set a bar.
- §3 pastes no closed-ledger search output, or has no "staged candidates" line (write "none" if the
  search returned none).
- §4 says "novel" with no precedent.
- §5 has no payer, or names "the spread" or "generic illiquidity"/"generic inefficiency" as the payer.
- §6 has more than 8 conditions, a condition without a number, no instrument-null branch, or no joint
  pass probability.
- §7 has no null control where the theory predicts no edge.
- §8 LIT names no papers, or counts summaries as reading.
- Any sealed holdout, frozen config, or outcome statistic was read while writing the brief. A brief is
  written BEFORE outcomes. Reading a gap or return to "see if it is worth it" contaminates the later
  pre-statement.
