# BRIEF: <vein / conjecture name> (<yyyy-mm-dd>)

Status: GREEN staging. No verdicts, no real spend, no real orders. Author: <seat, model, effort>.
Chain position: <first brief on this vein | follows `<previous brief path>` §9>.
Copy to `<fleet folder>/_SHARED_BRIEF.md` at launch. Every agent reads the whole brief; its own lane
assignment follows separately.

---

## 0. Fleet contract block (placeholder)

> At launch, paste the FLEET CONTRACT block from `.claude/skills/fleet/SKILL.md` §3 here, **verbatim, as
> the first text of every agent prompt**. Fill the `DONE means:` line from §8 below. Do not keep a copy
> in this file: the fleet skill is the one source, and a stale copy breaks the shared cache prefix.

---

## 1. Conjecture: NOT YET A LAW

> <One sentence. The mechanism, the population, and the observable consequence.>

**This is a conjecture, not a law. Every lane tries to falsify it.**

- **Central object:** <the quantity the conjecture is about, with notation if useful>
- **Observables:** <what we can actually record or download that is an emission of that object>
- **What would make it false in one line:** <the cleanest refuting observation>

### 1b. The model, in equations (rigor rubric R1, `docs/METHODS.md` §4)

<Write the payer's constraint or objective, the resulting price or flow, and the predicted SIGN and SIZE
of the observable, before any data is pulled. State the conservation check: whose loss equals the gain
we expect.>

- **Rigor reach:** which of R1-R5 this test can reach, and why the others are out of reach
  (R2 needs a rule boundary, dated shock or randomization; say whether one exists).
- **Anchor papers and their rigor scores:** <citation: R1..R5 = n/10>. Below 5 suggests, never anchors.

## 2. What the prior sequence established (do not re-derive; cite)

Tag each fact LEDGER (prior work, cite doc), VERIFIED (measured this session, name the command), or
UNCONFIRMED. Numbers come from `facts/registry.yaml` ids or the cited file, never from prose.

1. <fact> — `<path>` / `{{registry.id}}` — LEDGER
2. ...

**Consequence for this brief:** <what the prior results force: the coordinate to use, the trap to
avoid, the population that is already spent>

## 3. Anti-rediscovery list

### 3a. Closed-ledger hits (pasted from real runs; 3+ synonyms)

Command: `<your project's "search everything closed" command>`, run <date> on: <term1>, <term2>,
<term3>, ...

| term | section | hit (verbatim, trimmed) | closes | does NOT close |
|---|---|---|---|---|
| <term> | VERDICT / QUEUE / STAGED | <file:line + text> | <what population/rule it closes> | <what stays open> |

Extra staging grep (candidate/screen/gate files that a standing search command might miss): <hits or
"none">.

### 3b. Not novel: do not present these as findings

<established ideas, standard metrics, published methods that a lane might "discover">

### 3c. Failure law it rhymes with (`docs/METHODS.md` §1)

| law | why it rhymes | what is different this time (or "nothing": then REJECT) |
|---|---|---|
| L<n> <name> | ... | ... |

## 4. What is actually new here?

Choose exactly one: *nothing (established and commoditised)* / *known math, new dataset* / *known math,
unusual population* / *known method, novel partial-observation structure* / *novel cross-domain
mapping* / *potentially novel problem*.

- **Class:** <one>
- **Closest precedent:** <paper or file, with the precise claim it makes>
- **The difference:** <one or two sentences; must be testable>

## 5. Payer box

```
[ SOURCE STATE ] -> [ TRANSMISSION ] -> [ TRADEABLE/ACTIONABLE EXIT ]
<who/what creates it> -> <how it reaches a price/decision we can act on> -> <instrument, venue, holding period, how it settles>
```

- **Who pays:** <a named counterparty class>
- **Why they pay (non-informational motive):** <preference / constraint / obligation / error; say which, and the evidence>
- **What rations it (why it is not competed away):** <attention / access / capital lock-up / eligibility / latency / ...>
- **Turnover/holding-period class:** <state the class and which cost screen applies, if your project has one>
- **Lead time as arithmetic:** `t_observe -> t_decide -> t_act -> t_payer_acts -> t_exit` = <numbers>
- **Size at your actual stake:** <capacity per episode and per year, labelled GUESS unless cited>

## 6. Kill conditions (at most 8; each a measurable threshold)

Every condition has an instrument-null branch: if the instrument cannot measure it, the result is
NEEDS_DATA, never PASS or KILL. Thresholds are CANDIDATES until frozen with `tools/prestate.py` / your
pre-registration step, and are re-set against the published effect size once the LIT lane reports.

| # | condition | statistic and threshold | instrument | instrument-null branch |
|---|---|---|---|---|
| K1 | ... | ... | ... | NEEDS_DATA if ... |

**MDE:** <the smallest effect worth finding, in the same units as the kill statistics>
**Joint pass probability of the tree at the MDE:** <per-gate estimates and the product; label GUESS or
show the computation; see `docs/METHODS.md` §2>.
If below 0.5: <what was merged, moved to sizing, or dropped to lift it>.

## 7. Mandatory null controls

| control | why the theory predicts NO edge here | what a positive result here would mean |
|---|---|---|
| N1 | ... | the instrument or the conjecture is wrong |
| N2 | ... | ... |
| P1 (positive control for the instrument) | <a case where the answer is known> | instrument broken if missed |

## 8. Lanes (fixed set; independent; each writes its own memo)

Memo path: `<fleet folder>/MEMO_<lane>.md`. Lead with the bottom line; tag MEASURED / LEDGER /
UNCONFIRMED; end with the lane's single strongest reason the direction fails.

| lane | question | DONE artifact |
|---|---|---|
| LIT | Read 2-3 papers IN FULL (seminal + latest replication or decay): who pays, published effect size, sample, costs, decay since publication, nearest factor/portfolio, what kills it. | `LIT_MEMO.md` (one page; see `screen-proposal` §0b) |
| IDENTIFIABILITY | Is the central object identified from the observables? Synthetic test: simulate with and without the effect; does the estimator separate them? | memo + synthetic script |
| DOMAINS | 2+ concrete instances (venue, contract, population), each with the payer box | memo + census table |
| DATA | Source, history depth, refresh, **terms check** (automation and ML clauses), instrument_check plan, what must be recorded forward | memo + data map |
| RED TEAM | Strongest case that the direction fails; which law; which prior closure it repeats | memo |

Papers for LIT (read in full, not summarized):
1. <citation + URL> — <why it is the strongest>
2. ...

## 9. Hand-off: what the next brief in the chain needs from this one

The synthesis must answer these, and the next brief's §2 cites the answers:
1. <question> — answered by lane <X>
2. ...

**Chain decision the main loop owes after synthesis:** <the fork: e.g. continue to pre-registration /
narrow to domain D2 / park on instrument / close the formulation (state the population)>.
