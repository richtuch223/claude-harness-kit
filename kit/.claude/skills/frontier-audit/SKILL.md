---
name: frontier-audit
description: "Program-level meta-audit that re-reads closures and ranks where the next scout budget should go. Trigger when the session brief shows an audit is due (several research fleets since the last audit), before choosing a new fleet direction after a run of nulls, or when asked for a frontier audit. Re-reads only and changes no verdict. The five sections, the fan-out and the output contract live in the body."
---

# frontier-audit — re-type the closures, count the kills, rank the near-misses, find the blind spots

A periodic meta-audit of a research program's own closures. Without a cadence it tends to run once and then go
unread: the near-miss ranking (by the cost of clearing each blocker) and the negative-space census (where the program
has looked, and where it has not) are only useful if someone re-runs them as the program moves. A common finding to
check for: effort tends to follow cheap data rather than opportunity.

**Cadence:** every ~4 research fleets, or on a fixed calendar cadence if your project's scheduler
supports it. If you have a session-brief hook, have it print an "AUDIT DUE" line when enough
non-harness fleet rows in `ops/fleet_ledger.tsv` postdate the newest audit folder.

## Hard rules

- **Re-read only.** No new experiment, no holdout, no outcome read, no verdict edited. The audit writes
  ONE folder: `<staging>/frontier_audit_<YYYYMMDD>/AUDIT.md` (+ any census CSVs).
- **Any proposed reclassification of a closure** (for example a "done" that should be reopened, or a
  kill that rests on a missing measurement) goes to your design/closure gate as one batch. The audit
  lists it; it never banks it.
- Post-hoc kill-hunting is forbidden in both directions: no new kill reason and no rescue argument
  invented during the audit. Re-type a closure only from the evidence its own run recorded.
- Numbers come from `facts/registry.yaml`; a figure marked `do_not_quote` stays unquoted.

## The five sections (cite a file:line or a ledger phrase for every row)

1. **Closure re-typing since the last audit.** Every closure banked after the previous audit date gets
   one type: **G** mechanism closed globally · **F** one formulation closed · **P** one
   population/universe closed · **X** one execution route closed · **M** the failure was a missing
   measurement only. M-type closures are reopen candidates; list them separately. Sources:
   `docs/STATE.md`'s dead-ends section dated after the last audit, any staged "GATE2"/verdict files,
   `verdict_table.csv` if you keep one.
2. **Kill census by failure law.** If your project keeps a failure-law table (`docs/METHODS.md` §1),
   count closures per law and compare against the previous audit's census. A law whose share grew is
   where the scouts keep walking into the same wall: add it to the next brief's near-automatic rejects.
3. **Near-misses ranked by EV per unit cost of clearing the blocker.** A near-miss is a cell that failed
   on exactly one named blocker. Rank by (P the blocker clears) x (value if it clears) / (cost to clear:
   dollars + labor + days). Drop any cell where clearing the blocker leaves another known fatal problem,
   and say which. Re-check the previous audit's list: which blockers cleared, which were never
   attempted.
4. **Sampling-bias / negative-space census.** Count artifacts per lifecycle area (source files, staged
   fleets, ledger verdicts, memory files) using whatever area list your project tracks, plus any areas
   added since. Name the 5 most oversampled and the undersampled areas with material stakes and no
   fence. Nulls in oversampled areas mean "measured", not "picked clean".
5. **Ranked cells for the next scout budget** (max 7). Each: object · payer · anchor · access given your
   actual constraints (capital, jurisdiction, time) · what rations it · the one unknown · cheapest kill
   and its cost · P(a cheap scout yields a pre-registration-worthy candidate), tagged [E] evidence / [I]
   inference / [S] speculation. State the base rate (survivors per lead across the fleets audited) and
   keep every P at or below it unless evidence says otherwise. These cells feed `conjecture-brief` and
   a descriptor archive if you keep one; nothing launches from the audit itself.

## Fan-out (load the `fleet` skill first; contract block verbatim)

- 1 cheap-seat agent (effort medium): section 4 artifact counts + the list of closures dated after the
  last audit with their source paths. Pure counting; returns CSVs.
- 1 strong-seat agent (effort high): sections 1-3 from those lists.
- main loop: section 5 and a final unslop pass. 1 cross-family `ask` call on the finished AUDIT.md:
  "which ranked cell rhymes with a closed one, and which undersampled area did the audit miss?"
  Advisory.
- Design/closure gate batch only if section 1 proposes reclassifications.
- Log the run: `tools/fleet_ledger.py add ... --vein frontier-audit`.

## Output contract

`AUDIT.md` opens with: date, previous audit date, fleets covered, sources read, and what was NOT read.
Then sections 1-5. End with a one-line pointer added to your queue doc naming the top 3 cells. Commit
the folder.
