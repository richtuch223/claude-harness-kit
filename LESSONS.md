# LESSONS — what three months of running this harness measured

Each lesson states its evidence, then how to apply it. The headline docs (01–03) cover context,
verification, and skills; this file holds the rest. The individual incident write-ups are in
`lessons/`, in the one-fact-per-file memory format.

## 1. A control that lives only in prose is not a control

**Evidence.**
- All eleven guard rules were inert on the always-on machine for its first week: the hookify plugin was
  listed but never installed. A command that should have been blocked ran unblocked.
- Every session for a week was launched from the parent folder, one level above the git root, so
  CLAUDE.md, all skills, and all hooks silently didn't load. Symptom: skills "weren't being used".
- The pre-commit hook recursed into ~1,400 Python processes on its first real commit: it ran
  `--selftest` on every staged file containing that string, and its own source contains it. The test
  had planted six faults and never staged the hook itself.
- A hookify rule whose frontmatter contained a literal run of three dashes (inside a regex) was split in
  the wrong place, matched everything, and denied every file edit in the session.

**Apply.** After installing any guard, **probe it** in a fresh session with something it must block
(README "Verify the install"). Unit-test every rule's regex on real positive AND negative cases by replaying events through the
plugin. **An invalid regex fails OPEN, silently**: while building this kit, one rule's `\\` was
collapsed to `\` by the rule loader, the pattern stopped compiling, and every command it should have
blocked was allowed. Write a literal backslash in a rule pattern as `\x5c`. Any
tool that finds files by content and then executes them needs a self-exclusion, a re-entry guard (env
var), and a test that includes its own file. Enable the pre-commit hook once per clone. Never
`--no-verify`. In hookify rules: never write three dashes in a row in the frontmatter; use field
`content` (covers Write and Edit), not `new_text` (Edit only).

## 2. Tier models by cost; escalate by judging the output

**Evidence.** Failure audit over 66 sessions / 439 real user messages: user corrections per 100 messages
fell from 7.5 to 4.1 to 2.5 across three successive main-loop models. Tool-error rates were ~1.2% for
the top two, i.e. the same. The cheapest tier was ruled out entirely for judgment work.

**Apply.** Main loop = the strongest model (design, interpretation, verdicts). Mechanical work goes down
to `mechanical-runner` (sonnet) with an exact recipe. Model pins in agent frontmatter are authoritative;
escalate a tier when the output misses the bar. Price is not the criterion; the output is.

## 3. Freeze before you run; bank in the same turn

**Evidence.** Post-hoc threshold tuning, "let's also try X" bundled into a clean primary test, and a
pre-commitment rewritten after the results were in (caught only by file-mtime forensics) are all
recorded incidents. A circular selection leak slipped one freeze, which is why design review now happens
**before** the freeze. A verdict that existed only in chat was lost with the chat.

**Apply.** `experiment-spec` → Gate-0 → freeze (rename) → run → `bank-result` the same turn. Scorers
print statistics; the main loop decides PASS/FAIL against the frozen file. Report a FAIL as a FAIL; never
push a rescue re-run.

## 4. Every number has exactly one home

**Evidence.** An audit found 587 numeric claims spread over up to six homes each, and every discrepancy
was optimistic. One unit error (annual *throughput* quoted as annual *earnings*, ~$22k/yr vs a true
~$400/yr) was caught and deleted, re-entered the ledger a week later, and was caught again at a gate.
Prose doesn't hold a correction. A registry row marked `status: retracted` + `do_not_quote: true`
does: the row stays, and a check can grep for the wrong value.

**Apply.** `facts/registry.yaml`; docs cite `{{id}}`. `population` is part of a figure's identity. When a
number gets misquoted twice, add `optional/figure-hooks/`: a Stop hook that scans each reply for
retracted values (report-only, loop-guarded). Ship enrichment hooks in measure-only mode first; the
source repo's just-in-time injector logged its hit rate before anything was allowed to depend on it.

## 5. The transport's defaults select your sample

**Evidence.** Two look-aheads, both invisible in code that "looked right": an endpoint capped at 1,000
rows with no cursor dropped exactly the rows that mattered, and an endpoint that returns newest-first
made "first" mean "last". A bias check that sampled "the 40 highest-volume items" sampled on the very
variable the truncation corrupted and understated the bias ten-fold.

**Apply.** Page every history endpoint to exhaustion. Sort by parsed timestamp before any
first/last/once-per-key logic, and assert monotonicity in the script. Check a suspected truncation bias
by stratifying on the **outcome**, never on the truncated quantity. A number from a capped pull is
unreliable in an unknown direction (in the incident above it was optimistic); re-pull completely before
quoting it. Tell every code reviewer to "check the row order of the input".

## 6. Unattended jobs: detached, resumable, heartbeat, restart

**Evidence.** Claude Code kills tracked background jobs when system RAM runs low, regardless of the
job's own size; two 30 MB collectors died twice in one evening. Killing by image name (`python.exe`)
took out unrelated jobs; killing by command-line pattern took out a review that merely listed the
script as an argument.

**Apply.** Anything over ~10 minutes launches detached (`Start-Process` / `nohup`) with its own log,
resumable (skip outputs that exist), and the session ends its turn instead of waiting. Wake on a log grep
for FINISHED. Kill by exact command-line filter. Give each long job a heartbeat (process alive AND output
fresh) before you need it.

## 7. Nulls are weak evidence

A negative from one instrument, one dataset, one metric does not close a direction. Record it with its
population and the instrument's known biases. The source repo retracted an "exhausted" framing once
after its own instruments turned out to be structurally negative-biased.

## 8. The fooling modes, generalized

| trap | what it looks like | the check |
|---|---|---|
| lookahead | random split of time-ordered data; a stat computed over the full span | temporal split; every feature strictly trailing |
| multiplicity | dozens of variants, one beats baseline | count variants in the spec; an ordinary CI on the best of N is optimistic, so re-test the winner on data it was not selected on (or use a multiple-comparison correction); many tests on one dataset → the heavier `prereg` in `reference/` |
| concentration | one user / one day / one entity drives it | minus-top-1, minus-top-2; CI at the right unit |
| no baseline | a clever model that a trivial rule matches | mandatory baselines in every spec |
| proxy ≠ outcome | offline metric moved, real outcome didn't | say which one you report |
| unreconciled outputs | per-segment numbers don't sum to the total | reconcile before quoting |
| transport selection | capped or newest-first pulls | §5 above |
| throughput ≠ profit | volume quoted as earnings | state the unit on every number |

## 9. Platform gotchas (Windows boxes, specifically)

- `python3` is a Microsoft Store stub on Windows; hooks call `python`.
- Python defaults to cp1252 there; emoji in a doc crash a bare run. `PYTHONUTF8=1` (the kit's
  `settings.json` sets it for hooks).
- The Bash tool can turn a backslash-n inside a quoted heredoc into a real newline. Write Python with the
  editor tools and `py_compile` anything written via heredoc.
- A User-scope env var isn't inherited by shells inside an already-running session.
- Claude can't edit `enabledPlugins` in settings or install plugins itself; a human runs
  `/plugin install`.

## 10. Measure the harness

Once a month: `/context` for standing cost, transcript mining (`~/.claude/projects/<project>/*.jsonl`)
for the tool mix and the most-hunted paths, and corrections per 100 user messages by model. Claim a
harness improvement only with a before/after number. If two docs answer the same question, one is
stale; merge them.
