---
name: cross-review
description: The standing different-family verification gate via tools/verify.py — a non-Claude model (Gemini, DeepSeek, Kimi, gpt-oss, a local Ollama model, or the Gemini/Codex CLIs) reviews only the code we send; API routes are text-only; the Codex CLI route runs with its tools off; the Gemini CLI route can still read files. Trigger BEFORE trusting new correctness-critical code (splits, scorers, feature builders, data transforms, anything producing a quotable number), before any expensive or hard-to-reverse step, or when a maintainer asks for a second opinion / refutation vote. Advisory filter only; modes and command lines live in the body.
---

# cross-review — the different-family verification gate

**Why this exists:** correctness-critical work is verified by a model of a DIFFERENT FAMILY than its
author before it is believed. Same-family self-review shares the author's blind spots; a different
family catches lookahead, leakage, off-by-one, wrong join key, target contamination. In the source
program this gate caught a target leak the author's own review missed.

**What makes it safe:** Claude gathers the material (a git diff or named files) and `tools/verify.py`
sends exactly that. API routes are text-only: the reviewer has no tools at all. CLI routes run in an
empty scratch dir. A read-only sandbox blocks writes and network, NOT reads (a canary probe on codex-cli
0.155.1 read a file outside the scratch cwd); what stops reads on the Codex route is the tool switches the
script passes (shell, exec and web search off under `--strict-config`). The Gemini CLI route has no such
switches and is NOT hardened against file reads: on machines holding data that must not leave, use Codex
or an API route. The reviewer cannot execute;
re-derivation by running code stays with the `adversarial-verifier`.

**MANDATORY triggers:** new or changed code that computes a split, a metric, a feature, a data
transform, or any number that will be quoted; before any large data pull, training run, or spend;
before a result is banked. **Proportionality:** skip one-off log parses, plots, inventories, and scratch
work, **unless** their output will be quoted or acted on (then the mandatory triggers win).

**Output is a GATE, not the verdict.** The main loop adjudicates each finding against the real files
(confirmed or dismissed, with reasons). Severity labels from these models run hot; reproduce what matters.

## Providers — pick ONE route (any non-Claude family works)
Set the default once with `VERIFY_PROVIDER` in `.env` / the environment; `-p` overrides per call.

- **Codex CLI** (`-p codex-cli`, the script's built-in default): a ChatGPT subscription via OAuth
  (`npm i -g @openai/codex`, `codex login`; `codex.cmd` on Windows). The script always runs
  `codex exec --strict-config -c features.shell_tool=false -c features.unified_exec=false
  -c tools.web_search=false -s read-only --ephemeral` from an empty scratch cwd with the prompt on stdin.
  `-s read-only` stops writes and network; the tool switches are what stop file reads (verified with a
  canary probe on codex-cli 0.155.1; re-run it after a Codex upgrade). It refuses any write-access flag
  arriving via `-m`. Seat aliases live in `CODEX_TIERS`
  in the script; confirm the slugs against `/model` inside `codex` after login.
- **Gemini, free, no card:** `-p gemini` with `GEMINI_API_KEY` from aistudio.google.com (`--reasoning
  low` for code; flash models truncate at high), or `-p gemini-cli` (Google OAuth). Never `--yolo`.
  `-p gemini-cli` is NOT hardened against file reads; do not use it where the reviewer must not see local
  secrets.
- **OpenRouter** (`OPENROUTER_API_KEY`, pay-as-you-go): aliases `soul` (DeepSeek, hard review) ·
  `terra` (Kimi) · `luna` (gpt-oss-120b, cheap first pass). Ids drift; update `TIERS` in the script.
- `-p deepseek` / `-p groq` with their keys, or `-p local` against Ollama (needs a real GPU).

**Use a panel, not a seat.** `-m a,b` (Codex CLI) runs two seats concurrently and concatenates. In the
source program's bake-off no single model found both headline bugs; recall was complementary.
Routine gate = two cheap seats; event gate (spec freeze, banking, real spend) = two strong seats.
When quota is out, queue the review; never fall back to a same-family Claude seat for routine code.

Keys live in `.env` or the environment, never in the repo. `--dry-run` shows the exact payload.

## Mode A — code review
```bash
PYTHONUTF8=1 python tools/verify.py review --uncommitted --reasoning low --focus "leakage + split correctness in the new scorer" -o <scratchpad>/review.md
PYTHONUTF8=1 python tools/verify.py review --base main --focus "..." -o <scratchpad>/review.md
PYTHONUTF8=1 python tools/verify.py review --files src/pkg/split.py experiments/001_x/run.py --focus "..." -o <scratchpad>/review.md
```
Then **read the report and verify each finding against the actual code** before presenting. If it finds
nothing, say so explicitly and name the target inspected. For a BLOCKER-level disagreement, re-run with
a second family; two independent votes beat one re-run.

Later rounds: scope to "only report what stops the pipeline, changes a number, biases toward PASS, or
bypasses a guard", and review a small fix as a DIFF rather than re-reading the whole file. Findings
converging (e.g. 30 → 12 → 7 → 1) is what a closed gate looks like; a single "looks good" is not.

## Mode B — refutation vote on a result
Write the claim (one paragraph + exact script/data paths + the key numbers) to a scratch file, then
```bash
python tools/verify.py refute --claim-file <scratchpad>/claim.txt --files experiments/001_x/run.py -o <scratchpad>/refute.md
```
First line of the report is `REFUTED` / `NOT-REFUTED` / `CANNOT-DETERMINE`. Combine with the
`adversarial-verifier`'s vote (which DOES execute). Disagreement is a flag to dig; agreement is not a pass.

## Guardrails
- Prompt literally; `--focus` is one line. Send the SMALLEST material that contains the question.
- `tools/verify_sealed.txt` lists path patterns that must never leave the box (sealed holdouts, secrets);
  the script refuses them. Add a pattern the day a holdout is sealed.
- Never route here: experiment design, spec authorship, interpreting results into a verdict, any
  PASS/FAIL, any edit to frozen specs or DECISIONS.
