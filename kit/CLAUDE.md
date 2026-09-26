# <!-- FILL: project name --> — operating rules for agent sessions

<!-- FILL: one to three sentences. What the project is and what good work means here. Current goals go in docs/STATE.md, not here. -->

## Read order (Tier 0 — cheap, every session)
1. This file.
2. `docs/STATE.md` — what is in flight, what is next, what is blocked on a maintainer. The ONLY hand-maintained live-state file.
3. `docs/DECISIONS.md` — append-only log of settled decisions. Grep before re-litigating anything.
4. `facts/registry.yaml` — every quotable number has ONE home here. Cite ids, never restate values.

Tier 1, on demand: `docs/PROJECT_CONTEXT.md` (thesis + principles), `docs/HARNESS.md` (why the harness is shaped this way and what was deliberately left out).

## Standing constraints (obey exactly)
- **Frozen experiment specs are read-only.** `experiments/<id>/SPEC.md` is frozen by definition (drafts are `SPEC.draft.md`; the freeze is the rename). Never edited, not even for status. Variants get a NEW spec. Hookify blocks every edit to a `SPEC.md`.
- **No secrets in the repo.** Keys live in `.env` (git-ignored) or the OS keychain. Once installed and probed, hookify blocks key-shaped literals and the pre-commit hook scans the diff.
- **Heavy data never enters git.** Raw dumps, embeddings, model weights live in `data_local/` (git-ignored) or `DATA_ROOT`. Commit derived summaries and plots, not sources.
- **Every new correctness-critical script ships a `--selftest`**: synthetic fixtures in a temp dir, no network, no real data, non-zero exit on any failed check, and it must show guards fail CLOSED. The pre-commit hook runs it. Correctness-critical = anything computing a metric, a split, a feature, a data transform, or a number that will be quoted.
- **Write Python with the Write/Edit tools, never through a bash heredoc** (backslash escapes can get corrupted in transit).
- **Launch Claude Code from the git root.** From a parent folder none of `.claude/` loads and the guards silently do not exist.
<!-- FILL: project-specific constraints (frozen configs, sealed data, deploy rules). If a rule must be ENFORCED, write a hookify rule for it too. -->

## Model-tiered delegation *(level 3+ — delete this section if you don't install the agents)*
Main loop = the strongest model available (design, interpretation, verdicts). Mechanical work goes DOWN to the cheapest agent that can do it. Model pins in `.claude/agents/*.md` are authoritative; you have standing permission to escalate a tier when the cheaper output does not meet the bar. Judge the output, not the price.

| Tier | Agent | Use for |
|---|---|---|
| sonnet | `research-scout` | breadth: "what exists / what's interesting here" sweeps, ranked leads, many in parallel |
| sonnet | `mechanical-runner` | running existing scripts with given params, data pulls, parsing/aggregating outputs, batch scoring, plots, file inventory. Give it an exact RECIPE. |
| opus | `investigator` | one promoted question, resolved rigorously: baseline vs candidate, proper split, CI, leakage audit |
| main loop | orchestration | experiment design, spec authorship, interpreting results, decisions |
| opus (separate seat) | `adversarial-verifier` | refute a result before it is believed; design-review a spec before it freezes |
| sonnet | `research-synthesizer` | end of a sweep: one cited brief + proposed diffs to the state files |
| different family (tool-less) | `cross-review` skill → `tools/verify.py` | code review of correctness-critical code by a model that is not Claude |

Rules:
- **Delegate-before-doing.** Any multi-step mechanical job gets a written recipe (exact commands, paths, expected output shape) and goes to `mechanical-runner`. The main loop reads the summary, not the raw output. Skip the delegation tax for a single quick command.
- **Nothing verifies itself.** Before the main loop decides PASS/FAIL, the result is attacked by a reviewer with none of the author's context (`adversarial-verifier`, or a new session at level 0/1), and correctness-critical code goes to a different model family (`cross-review`). The verifier recommends; the main loop decides against the frozen spec's pre-stated criterion and records any disagreement.
- **Verification is event-triggered and scope-bounded.** Trigger on: a spec freeze, a result that will be quoted or acted on, a change of direction, a production deploy. One doc or one question per call. Re-review only after a substantive fix; never re-ask about unchanged material. A verifier that runs continuously becomes a co-author.
- Subagents never edit frozen specs, `docs/DECISIONS.md`, `facts/registry.yaml`, or this file. They propose diffs.

## Experiment discipline (the lite version — grows only when a failure earns it)
- **Spec before run.** `experiment-spec` skill: frozen population, split rule, ONE primary metric with its estimator and CI, the baselines it must beat, the leakage checklist, and what a FAIL would mean. Drafting is free; running against a non-frozen spec is not allowed.
- **Temporal splits by default.** Random splits of time-ordered data leak the future. Any random split needs a stated reason in the spec.
- **Baselines are mandatory.** <!-- FILL: your trivial baselines --> A candidate that does not beat a trivial baseline is not a result.
- **Scorers report statistics, not verdicts.** PASS/FAIL is decided in the main loop against the frozen spec, then banked with the `bank-result` skill in the same turn it is decided.
- **Offline metric ≠ real outcome.** Say which one you are reporting.
- **Anti-fooling checklist** (run before believing anything): leakage (future rows, duplicates across the split, input row order) · multiplicity (how many variants were tried?) · concentration (does it survive minus the top-1 contributor?) · baseline beat · era/segment dependence · does the number reconcile with the script's own other outputs?

## The guiding question
Before adding any feature, model, agent, or infrastructure: <!-- FILL: your guiding question -->. If not, it waits. The `screen-proposal` skill exists to say no cheaply.

## Skills (procedures load on demand, not here)
`screen-proposal` (new idea) → `grill` (unsettled design) → `experiment-spec` (freeze) → run → `cross-review` (code gate) → `adversarial-verifier` (result gate) → `bank-result` (record). Plus `decision-trail` for long autonomous runs and `stop-slop` for human-facing prose.

## Terminal output style — unslop, always on
- Start at the finding. No preamble, no restating the question.
- Every sentence states a fact, a decision, or a consequence. Cut the rest.
- Active voice, named actors. No "not X, but Y" crutch, no em-dash chains, no -ing puffery.
- No chatbot closers. End when the content ends.
- Specific beats vague: name the file, the population, the registry id.

## Project structure
```
<!-- FILL: your package layout -->
experiments/      one folder per experiment: SPEC.md (frozen), run script, outputs/, RESULT.md, decision_trail.tsv
tools/            harness tools: verify.py (cross-family review gate)
ops/githooks/     pre-commit guard (enable per clone: git config core.hooksPath ops/githooks)
docs/             PROJECT_CONTEXT · STATE · DECISIONS · HARNESS
facts/            registry.yaml — one home per number
data_local/       git-ignored heavy data (or DATA_ROOT)
.claude/          agents/ · skills/ · workflows/ · hookify.*.md guards · settings.json
```

Keep this file under ~150 lines. When something feels like it belongs here, it is usually a skill (procedure) or a hook (enforcement).
