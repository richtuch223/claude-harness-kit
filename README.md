# claude-harness-kit — start here

A working agent harness for Claude Code, lifted out of a research repo that ran on it for three months
(Jun–Sep 2026: ~1,100 Python files, ~400 docs, 66+ sessions mined for what actually helped). Everything
here earned its place by catching a real bug or cutting a real cost. The evidence is in each doc.

**You do not need to run agent swarms to use this.** Most of the value is in four cheap things: a short
`CLAUDE.md`, skills whose bodies load only when used, hooks that enforce rules without anyone
remembering them, and a second-opinion code review from a different model family. The multi-agent parts
are included but never run unless you call them, so you can read them and decide.

## The headlines

| # | idea | why it matters | read |
|---|---|---|---|
| 1 | **Context efficiency** | Every token in standing context is paid on every session. The source repo measured its orientation cost drifting from ~18k to ~44k tokens without anyone noticing. Layer files by how often they change; grep, never read, the big ones. | `01_CONTEXT_EFFICIENCY.md` |
| 2 | **Cross-family verification loop** | The model that wrote the code shares its own blind spots. A tool-less reviewer from a different model family, run as a two-seat panel over converging rounds, caught leaks, page caps, and corrupt writes the author missed. | `02_VERIFICATION_LOOP.md` |
| 3 | **`stop-slop` (unslop)** | Output that reads like AI gets skimmed and distrusted. Five always-on lines in CLAUDE.md handle chat; the skill is the full pass for documents. | `03_SKILLS.md` §1 |
| 4 | **`grill`** | Ambiguity is cheapest to kill in conversation. Numbered questions, a recommended answer for each, two or three rounds, then build. | `03_SKILLS.md` §2 |
| 6 | **A CLAUDE.md that is a map, not a knowledge base** | The one file loaded every session. Ours went stale once and misdirected sessions for six weeks; the fix was pointers only. The anatomy, section by section. | `04_WRITING_CLAUDE_MD.md` |
| 5 | **Freeze → verify → bank** | Write down what "success" means before running; bank the result in the same turn you decide it. Prevents post-hoc tuning and lost verdicts. | `03_SKILLS.md` §3–5 |

Then `LESSONS.md` has the measured findings behind all of it, and `lessons/` has the incidents they came from.

## Pick a level — each one works on its own

| level | what you install | token cost | who it suits |
|---|---|---|---|
| **0. Discipline only** | `CLAUDE.md` template, `docs/` state files, skills, `.claudeignore` | ~2.9k tokens standing, measured (CLAUDE.md ~1.8k + skill/agent descriptions ~1.1k) | anyone; no subagents ever spawned |
| **1. + Guards** | hookify rules, pre-commit hook with `--selftest` runner | zero tokens (hooks run outside the model) | anyone who has had a key or a broken script committed |
| **2. + Second opinion** | `tools/verify.py` + `cross-review` skill, `adversarial-verifier` agent | per event (spec freeze, result, deploy), never per edit: 1 external call (single seat) or 2 (panel), repeated only after fixes; reading the report back costs Claude context | code whose numbers people act on |
| **3. + Delegation** | `mechanical-runner`, `investigator`, `research-scout` agents | cheaper models do the grunt work; the main context stays small | long jobs, big outputs you don't want in context |
| **4. + Swarm** (opt-in) | `.claude/workflows/discovery-sweep.js` | scouts × N + investigators × K + 2 skeptics each — expensive | only when you have ≥5 open questions worth parallel work |

Agents and skills cost their one-line description in every session (~1.1k tokens for all 12, measured);
their bodies, and any agent run, cost only when used. The workflow costs nothing until run.

**Staying at level 0 or 1:** delete `.claude/agents/` and `.claude/workflows/`, and delete the sections of
`CLAUDE.md` tagged *(level 2+)* / *(level 3+)*. Everything that remains works without subagents. Where a
skill asks for an independent review, do it by hand: open a **new** Claude Code session (no shared
history), paste only the claim and the repro command, and ask it to refute. That is what "fresh seat"
means everywhere in these docs.

## Install (10 minutes)

```bash
# from this folder, pointing at the root of your git repo
python install.py /path/to/your/repo            # copies kit/ in; never overwrites an existing file
python install.py /path/to/your/repo --dry-run  # show what would be copied first
```

Then, in your repo:

1. **Fill the `<!-- FILL -->` markers** in `CLAUDE.md`, `docs/PROJECT_CONTEXT.md`, the `investigator`
   agent, and `screen-proposal`. `grep -rn "FILL" CLAUDE.md docs .claude` lists them.
2. **Append `gitignore.snippet`** to your `.gitignore`. If you have no `.env`, copy `.env.example` → `.env`;
   if you already have one, add only the missing keys (never overwrite it).
3. **Enable the pre-commit hook** (per clone, a fresh clone does not have it). It needs PyYAML to check
   `facts/registry.yaml`, and blocks the commit without it:
   `pip install pyyaml` then `git config core.hooksPath ops/githooks`
4. **Install the hookify plugin** (the guards are inert without it). In Claude Code, launched from the repo root:
   `/plugin install hookify@claude-plugins-official`, then restart.
5. **Pick a review route** for `tools/verify.py` (one is enough) and set `VERIFY_PROVIDER` in `.env`.
   Free option: a Gemini AI Studio key (`-p gemini`). See `02_VERIFICATION_LOOP.md`.
6. **Launch Claude Code from the git root.** From a parent folder none of `.claude/` loads. The source
   repo lost a week to this.

## Verify the install — probe, don't assume

A guard that silently doesn't fire is worse than no guard. The source repo ran for a week with all
eleven guard rules inert (the plugin was listed but never installed). Safest: run the probes in a
throwaway clone (`git clone . ../probe-clone && cd ../probe-clone && git config core.hooksPath ops/githooks`).
In your real repo, clean up each probe by its exact name before the next
(`git restore --staged harnessprobe_bad.py && rm harnessprobe_bad.py`):

| check | how | expect |
|---|---|---|
| skills loaded | start Claude Code in the repo, ask "what skills do you have?" | `screen-proposal`, `grill`, `stop-slop`, … listed |
| hookify live | ask Claude to write `api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"` into `harnessprobe_secret.py` | the write is **blocked** (nothing to clean up) |
| pre-commit live | `printf 'def f(:\n' > harnessprobe_bad.py; git add harnessprobe_bad.py; git commit -m probe` | commit **refused** (DOES NOT COMPILE); then clean up that one file |
| pre-commit probe suite | `python ops/githooks/probe_pre_commit.py` (builds throwaway repos; needs git + PyYAML) | `22 / 22 probes as expected`, exit 0 |
| pre-commit no recursion | after the install commit, `touch` nothing; just confirm that commit (which staged the hook's own file) finished in seconds | no hang |
| verify.py selftest | `python tools/verify.py --selftest` | `0 failure(s)` |
| review payload | `python tools/verify.py review --files tools/verify_sealed.txt --focus "smoke" --dry-run` | payload printed, nothing sent |
| codex route hardened | `python tools/verify.py review --files tools/verify_sealed.txt -p codex-cli --dry-run` | the printed command carries `--strict-config`, the three `=false` tool switches and `--ephemeral` |
| **review route, live** | `python tools/verify.py review --files tools/verify_sealed.txt --focus "smoke test: reply NO FINDINGS if nothing is wrong" -o review_smoke.md` | a report file with a reply from the provider (proves credentials, model id, and response handling) |

## What's in the zip

```
README.md                  this file
01_CONTEXT_EFFICIENCY.md   headline 1
02_VERIFICATION_LOOP.md    headline 2
03_SKILLS.md               headlines 3–5: stop-slop, grill, the freeze → verify → bank lifecycle
04_WRITING_CLAUDE_MD.md    headline 6: the anatomy of a CLAUDE.md that points at everything else
LESSONS.md                 the measured findings and session habits, with evidence
CHANGES_2026-09-25.md      what changed since the 2026-09-23 build
install.py                 copies kit/ into a repo without overwriting
kit/                       THE DROP-IN — mirrors your repo root
  CLAUDE.md                template, 74 lines (~1.8k tokens), <!-- FILL --> markers
  .claudeignore            keeps secrets and heavy data off Claude's read path
  .env.example, gitignore.snippet
  .claude/settings.json    enables hookify, forces UTF-8 for Python hooks
  .claude/agents/          research-scout, mechanical-runner, investigator, adversarial-verifier, research-synthesizer
  .claude/skills/          stop-slop, grill, screen-proposal, experiment-spec, bank-result, cross-review, decision-trail
  .claude/hookify.*.md     no-secrets, protect-frozen-specs, no-heavy-data-in-repo, source-of-truth-gate
  .claude/workflows/       discovery-sweep.js (level 4, opt-in)
  ops/githooks/            pre-commit + pre_commit_check.py + probe_pre_commit.py (probe suite)
  tools/verify.py          the cross-family review gate (+ verify_sealed.txt)
  docs/                    STATE, DECISIONS, PROJECT_CONTEXT, HARNESS
  facts/registry.yaml      one home per quotable number
optional/                  context-efficiency hooks to add when their trigger fires
  session-brief/           SessionStart hook: inject a derived ~1k-token "where are we" brief
  figure-hooks/            number registry tripwire (Stop) + just-in-time figure injection (UserPromptSubmit)
lessons/                   incident memories (one lesson per file) + the memory-index format
```

## How this kit was itself verified (the loop, on this kit)

The kit went through the same gate it teaches, using `tools/verify.py` with a GPT-family reviewer (a
different family from the Claude author) on 2026-09-23:

| round | scope | findings | examples of what it caught |
|---|---|---|---|
| 1 | all code + all docs | 16 code, 19 docs | a figure hook emitting an unverified number as "authoritative"; the pre-commit secret scan skipping added lines that start with `++`; git's quoted filenames bypassing the compile check; level 0 not actually agent-free; the docs saying "no loops" while teaching a loop |
| 2 | changed files, material issues only | 11 (all new, none a round-1 fix left incomplete) | selftests passing on an **unstaged** helper that would not be committed; binary-classified files skipping the secret scan; `production.env` not blocked; the installer's next-step `cp .env.example .env` overwriting an existing `.env` |
| 3 | the three files changed in round 2 | 5 | renamed binaries escaping the scan; `40` "verified" because it occurs inside `400`; a scalar registry crashing the hook; heavy files copied to a renamed path at the repo root |

All 5 round-3 findings were fixed and each fix was probed. The heavy-data finding was closed structurally, not with a
longer regex: the pre-commit hook now blocks any staged file over 5 MB, whatever command created it. The round-3 fixes
were not sent back for a fourth review; that is the next step if you change those files.

Every finding was checked against the files before it was fixed, and every fix has a probe (the
selftests, plus planted faults in a throwaway repo, plus replayed hook events through the real hookify
plugin). Two defects were caught by those probes rather than by the reviewer: a hookify rule whose
regex no longer compiled after the rule loader collapsed a doubled backslash, so it **failed open**
and allowed everything; and a literal NUL byte written into a script by a shell heredoc.

## How the pieces fit (one loop)

```
idea ──► screen-proposal ──► grill (if design branches are open) ──► experiment-spec (freeze)
                                                                            │
         bank-result ◄── adversarial-verifier ◄── cross-review ◄── build / run (mechanical-runner)
            │              (fresh seat, runs repro)   (other model family, no tools)
            ▼
   DECISIONS.md · registry.yaml · STATE.md · commit        stop-slop on anything a human reads
```

Once installed **and probed** (level 1), hooks sit underneath all of it: they block secrets, frozen-file
edits, and heavy-data copies no matter which step you are in.

## Glossary

| term | meaning here |
|---|---|
| **seat** | one reviewer instance: a model plus a clean context. A *panel* is two seats from different models reviewing the same material independently. |
| **fresh seat** | a reviewer that has none of the author's conversation history: a subagent, or simply a new Claude Code session given only the claim. |
| **cross-family** | a model from a different vendor/lineage than the author (GPT, Gemini, DeepSeek… reviewing Claude's code). |
| **gate / Gate-0** | a check that must pass before a step. Gate-0 = design review of a spec before it freezes. |
| **freeze** | the point after which a spec is read-only (here: renaming `SPEC.draft.md` → `SPEC.md`). |
| **holdout / sealed** | data reserved for one final confirmatory test; looking at it early spends it. |
| **kill condition** | the pre-stated result that would make you stop or drop the idea. |
| **CI** | confidence interval. "At the right unit" = resampled by user/day/entity, not by row. |
| **bank** | record a decided result in the state files and commit it in the same turn. |
