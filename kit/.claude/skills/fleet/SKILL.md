---
name: fleet
description: "The contract for any fan-out of agents (a Workflow tool, or 3+ parallel Agent calls). Trigger BEFORE authoring a workflow script or launching 3+ agents: it gives the seat/effort routing, the shared contract block to paste at the top of every prompt, the report fields, the declared-read/write concurrency rule, and the ledger row to log afterwards."
---

# fleet — how to fan out work cheaply and verifiably

Built after a day of running many agents in parallel with nobody tracking cost per ACCEPTED finding, and
after two file-collision incidents in one week (two agents editing the same files at once; a review seat
re-running a script while another agent was still fixing it). Standing direction: route scouting and
review to the cheapest seat that can actually do the job, keep the most expensive seat for judgment, and
log every fan-out so the next one can be compared against this one instead of against a feeling.

## 1. Route each job to the cheapest seat that can do it

| job | seat | how |
|---|---|---|
| web recon, lookups, ideation with no file access needed | **scout seat** (a `research-scout` agent, or a web-enabled model with no file access if your routing has one) | `Agent(subagent_type: 'research-scout', ...)` |
| read long text you already hold (transcripts, filings) | **cross-family reviewer**, `ask` mode (tool-less) | `tools/verify.py ask "<q>" -p <provider> -m <tier> --files ...` |
| data pulls, parsing, builds with tests | **cheap seat** (`mechanical-runner`, cheaper model tier) | `Agent(subagent_type: 'mechanical-runner', model: '<cheap>', effort: 'medium')` |
| screening, synthesis, designs, anything judged | **strong seat** (`investigator` / the main loop) | `effort: 'high'` |
| code gate before believing a number | **cross-family reviewer**, routine pass / panel for anything that gates real spend or a verdict | `tools/verify.py review -p <provider> -m <tier(s)> --files <script>` |
| refutation vote on a conclusion | cross-family reviewer (+ a second tier/provider on program-level calls) | `tools/verify.py refute ... --claim-file <c.md>` |
| design-gate before a spec freezes, final go/no-go | **`adversarial-verifier` agent**, one-shot, fresh context | `Agent(subagent_type: 'adversarial-verifier', ...)` |

The cross-family reviewer reviews; it never works autonomously. It can follow a rule even when the rule
stops all progress, which is exactly why it is the review seat and not a builder seat.

## 2. Effort tiers

- `low`: mechanical, no judgment (file moves, parsing a known format).
- `medium`: the cheap seat's builders and data pulls, behind a cross-family gate before anyone trusts the output.
- `high`: default for the strong seat and the main loop.
- `xhigh`: design-gate / go-no-go seats and verdict-bearing refutations only.
- `max`: never by default. Use it only when a lower setting has demonstrably failed the task.
- CLI seats (Codex, Gemini CLI): always pass the effort explicitly (`tools/verify.py --reasoning`,
  `tools/codex_scout.py --effort`). Never rely on `~/.codex/config.toml`: its default is `medium` and it is applied
  silently (the source program ran every scout at medium for two weeks). Both tools print the effort in the report
  header; read it before the findings. See `05_CODEX_ON_TOP.md`.

## 3. The contract block: paste VERBATIM as the FIRST text of every agent prompt

Parallel agent workloads are mostly cache reads once you fan out, so an identical prefix across a fleet
is the cheapest token there is. Put this block first, identical for every agent, and put the per-agent
task after it. Adjust the named files in the "never edit" line to your own project's state docs.

```
FLEET CONTRACT (identical for every agent in this run):
- DONE means: <the artifact(s) and the exact fields returned>. Stop when done; do not gold-plate.
- When a step does not need the principal's input, keep going. Stop and report BLOCKED only on a concrete blocker
  (missing access, contradictory spec, a rule that would have to change). Two failed attempts with no new diagnostic
  result also count as BLOCKED.
- Mark every claim you could not confirm as UNCONFIRMED. Cite a file:line, URL, or command output for every number.
- Pre-statement: any rule that will be judged against outcomes goes into the script docstring and
  <scratch>/prestate_log.txt (UTC + sha256) BEFORE any outcome is read. Pre-read amendments only, each with a reason.
- Traps: silent page caps, newest-first ordering, exact-duplicate rows that are real, mixed populations, medians where
  means are summed, throughput quoted as profit, an API error body parsed or cached as data (pass every fetch through
  tools/fetch_guard.py check_json first). Write scripts with the editor tools, never a shell heredoc.
- Never edit docs/STATE.md, docs/DECISIONS.md, facts/registry.yaml, CLAUDE.md, frozen configs or sealed data. Never
  commit, place orders, or open accounts. Report raw results; the main loop writes verdicts.
- Report the model and effort you ran on, and what you did NOT check.
- INSTRUMENT CHECK before any outcome: run tools/instrument_check.py on every row the decision statistic
  consumes (placeholder/reference values, page caps, ordering, zero-volume prints, independent source where one
  exists). INSTRUMENT_NULL = NEEDS_DATA, never PASS or KILL.
- Verdict wording: state the population and rule the verdict covers; an instrument null is not a KILL; one probe
  never closes a family. Never restate a pre-stated rule; amend it with tools/prestate.py and a reason.
```

## 3b. Rules for the agent that WRITES a probe's pre-stated rule (the rule-writer / spec author)

A rule-writer agent can itself restate a frozen criterion from memory, or let a probe's pass rest on an
unsourced constant — both invisible to a later code gate, which only ever sees the script, never the
original intent:
- Point the builder at the ROOT rule file (`tools/prestate.py show`); if a rule must change, write the
  change as an explicit amendment with the reason, never as a silently "restated" rule.
- Every rule carries an instrument-null branch (NEEDS_DATA, precedence over PASS/KILL).
- Every cost/threshold constant cites a measurement on the same population, or the rule pre-states rungs
  and reads the verdict at the realistic rung.
- Code gates on probe scripts run as `tools/prestate.py conform` (root rule + script), not on the script alone.

## 3c. Declare what each agent reads and writes

Before launching agents in parallel, write each agent's READ set and WRITE set (files, folders, data
dirs, the git index) into its prompt. Two agents may run at the same time only if neither writes
anything the other reads or writes: `W_A ∩ (R_B ∪ W_B) = ∅` and `W_B ∩ (R_A ∪ W_A) = ∅`. Otherwise run
them in sequence. The main loop counts too: do not edit a script while a review seat is re-running it,
and do not commit files another live session is editing. Why: declared read/write sets are the
conservative concurrency check that catches both "another session is editing the same files" and "a
review seat is re-running the script that still needs a fix" before either one corrupts a result. A
shared immutable input (a frozen rule, a data snapshot) is fine to read in parallel.

## 4. Report schema fields every fleet agent returns

`model`, `effort`, `artifacts` (paths), `unconfirmed` (list), `not_checked` (list), plus the task-specific
fields. In a Workflow, pass these in the `schema` so validation enforces them.

## 5. After the fleet: log it (cost per accepted finding)

```
python tools/fleet_ledger.py add --run <wf_id or label> --purpose "<one line>" --agents N \
    --claude-tokens <subagent_tokens from the task notification> --reviewer-calls N \
    --accepted N --vein <your-line-of-work|...|?> --research-accepted N \
    --banked "<commit or STATE.md phrase>" --notes "<model/effort mix>"
python tools/fleet_ledger.py summary
python tools/fleet_ledger.py mvt     # per-vein yield vs program average; flags an MVT REVIEW (never a stop)
```
`--research-accepted` counts verdicts, registry figures and line-feeding instruments only, never harness
fixes, so harness work cannot prop up a vein. Tag a mixed fleet `?` rather than guessing a split.
"Accepted" means a finding that survived review and was banked (a verdict, a registry figure, or a
harness fix). Benchmark integrity: if a result mixed models or efforts, say so in `--notes`; never
compare a hybrid run as pure.

## 6. Sizing a fleet

Default workflows stay small (roughly 10 agents or fewer) unless there is a specific reason to go wider.
Scale the cheap/scout seats before the strong seats. One agent per independent question; merge in the
main loop, not in an extra synthesis agent, unless the merge itself needs tools.
