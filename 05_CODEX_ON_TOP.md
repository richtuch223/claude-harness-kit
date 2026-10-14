# 05 — Codex on top of Claude: a second vendor seat on a ChatGPT subscription, no API key

Claude Code is the author and orchestrator. This doc adds a GPT-family seat beside it, paid for by a
ChatGPT subscription through OAuth, and wires it into two jobs the kit already defines: the
cross-family review gate (`tools/verify.py`) and web scouting (`tools/codex_scout.py`). Everything
here was run on codex-cli 0.155.1 (Sep–Oct 2026); re-check the flags after an upgrade (§4).

## 1. Why a second vendor, and why OAuth

- **Different family.** The model that wrote the code shares its own blind spots. The source program's
  GPT seat caught a target leak in a pilot sweep that the Claude author's own review missed, and the
  kit's own review rounds (README) came from that seat. Same-family self-review is not a gate.
- **Separate quota.** A ChatGPT plan is a second pool of compute that Claude's usage does not touch.
  The source program's scouting fleets burned millions of strong-model tokens while its Codex seat sat
  at ~2% use; moving web recon there cost nothing extra.
- **No API credit on a subscription.** A ChatGPT plan carries no API key and no API credit. The only
  way to spend it programmatically is the Codex CLI signed in with ChatGPT OAuth (`codex login`). The
  token lives in `~/.codex/auth.json`, outside the repo; nothing goes in `.env`, and nothing can leak
  through a committed file. Never copy `~/.codex/` into a repo.

The same pattern works for Google: `npm i -g @google/gemini-cli`, run `gemini` once to log in, then
`-p gemini-cli`. That route is **not** hardened against file reads (§4), so it is the fallback, not
the default.

## 2. Install (five minutes)

```bash
npm i -g @openai/codex
codex login            # opens the browser: "Sign in with ChatGPT"
codex --version        # the kit was probed on 0.155.1
```

Windows: npm installs both a `codex.cmd` shim and a bare `codex` sh script; the kit's tools look for
`codex.cmd` first because the bare script cannot be executed from Python on Windows.

`VERIFY_PROVIDER=codex-cli` is already the default in `tools/verify.py`. For the scout, pass
`--model <id>` or export `CODEX_SCOUT_MODEL=<id>` into the process environment (§5 says how to find an
id). Both tools read the environment; neither loads a `.env` file, so a value that only sits in `.env`
is not seen unless your shell exports it.

## 3. The two seats and what each may see

| seat | tool | sees | may do | never |
|---|---|---|---|---|
| **review gate** | `tools/verify.py review/refute/ask -p codex-cli` | only the text Claude sends (diff, named files, a claim) | find bugs, vote on a refutation, read long text you already hold | execute anything, read the repo, decide PASS/FAIL |
| **web scout** | `tools/codex_scout.py run/one` | its prompt + the public web | look up fees, terms, rules, literature; return cited leads | read files, run code, design tests, write verdicts |

The firewall from `02_VERIFICATION_LOOP.md` applies to both: no experiment design, no spec authorship,
no interpretation into a verdict, no edits to frozen files or sealed data. Scout output is advisory recon;
every number in it is a lead to verify, never a registry fact.

Typical calls:

```bash
# routine code gate before believing a number (one seat)
python tools/verify.py review -p codex-cli -m <alias> --reasoning high --files src/build_panel.py -o reviews/gate_r1.md
# event gate: two seats from the same vendor, different models, run in parallel; union of findings
python tools/verify.py review -p codex-cli -m <alias1>,<alias2> --reasoning xhigh --files ...
# refutation vote on a written claim, with the repro output pasted in as a file
python tools/verify.py refute -p codex-cli -m <alias> --claim-file claim.md --files repro_output.txt
# six web scouts from six prompt files, four at a time
python tools/codex_scout.py run --prompts-dir prompts/ --out-dir scouts/ --jobs 4 --model <id> --effort high
```

`verify.py` cannot execute a repro. Claude runs it and passes the output in `--files`; the reviewer
judges the output, not the claim.

## 4. Hardening that is not optional

**A read-only sandbox blocks writes and network, not reads.** A canary probe on codex-cli 0.155.1 showed a
`codex exec -s read-only` reviewer, launched from an empty scratch directory, reading a file elsewhere on
the disk and printing it back. On a machine that holds API keys, sealed holdout data, or a credentials
file, that is an exfiltration path through the reviewer's own answer.

What closed it: switching the tools off. Both kit tools run Codex with

```
--strict-config -c features.shell_tool=false -c features.unified_exec=false -s read-only --ephemeral
```

plus `-c tools.web_search=false` for the review gate and `tools.web_search=true` for the scout. With
the shell and exec tools off, the same probe answered `CANNOT_READ`; the scout answered `CANNOT_READ`
while its web search still worked. That is what the probes show; the full inventory of tools a future
Codex build might expose is not something the kit can enumerate, hence the canary after every upgrade. `--strict-config` makes an unknown or renamed config key FAIL the
call, so a future Codex that renames `features.shell_tool` fails loudly instead of silently handing the
shell back. `--ephemeral` keeps the call out of Codex's session history.

Rules that follow:

- Never add shell or exec to the scout "so it can read the prompt file": that reopens file reads. The
  prompt goes in on stdin.
- Never pass `danger-full-access`, `workspace-write`, or `--dangerously-bypass-approvals-and-sandbox`;
  `verify.py` refuses them.
- **After every Codex upgrade, re-run the canaries** before trusting either tool:
  `python tools/verify.py --selftest` (checks the flags are still emitted) and
  `python tools/codex_scout.py --canary --model <id>` (live: writes a temp file holding a random token, asks
  the scout to print its first line, and passes only when the call succeeded, the final answer is exactly
  `CANNOT_READ`, and the token appears nowhere in the output). A PASS is that probe's outcome on that build,
  not a proof that no other tool could read a file.
- `tools/verify_sealed.txt` lists path patterns that must never leave the machine; `verify.py` refuses
  to send them. Keep it current.

## 5. Pin the model and the effort; never inherit them

`~/.codex/config.toml` sets a default model and a default `model_reasoning_effort`, and `codex exec`
applies them silently. The source program ran every scout at `medium` for two weeks because nobody had
looked at that file. Both kit tools therefore pass the model and effort on every call and print them in
the report header (`<!-- codex_scout model=... effort=... -->`, and the `verify.py` header). Read the
header before reading the findings; a gate run at the wrong effort is not the gate you think you ran.

- Review gate: `--reasoning high` routine, `xhigh` for anything that gates real spend or a verdict.
- Scout: `--effort high` by default; `medium` is fine for lookups with an obvious primary source.
- `max`/the top tier: never by default.

**Model ids.** They change, and a wrong id fails only at call time. `CODEX_TIERS` in `verify.py` ships
with placeholders. Fill them by probing each candidate id with a one-line call:

```bash
codex exec --strict-config -c features.shell_tool=false -c features.unified_exec=false -c tools.web_search=false \
  -s read-only --skip-git-repo-check --ephemeral -m <candidate-id> "reply OK"
```

An id that answers is available to your account today. A failure is inconclusive until you read the
error: an explicit unknown-model error means the id does not exist, while an auth, quota, transport or
config error says nothing about the id. Record the probe date next to the alias; the source program
re-probed after each vendor release. If `codex` offers `/model` interactively, that lists the ids your
plan can use.

**Panels.** In a small bake-off (two tasks, weak evidence) no single model caught both headline bugs;
each caught one. Recall across models was complementary, so a two-model union beats any one seat for an
event gate. Routine gates use one seat; the panel is for freezes, verdicts, and real spend.

## 6. Running the two side by side

- Claude Code is the only thing that writes to the repo. It calls Codex through the two tools; Codex
  never has the repo.
- One review round = one `verify.py` call, Claude adjudicates each finding against the real files,
  fixes, then reviews the diff of the fixes. Stop when every material finding is fixed or dismissed with
  a reason written down. Converging rounds, not loops: three rounds is typical, five is a smell.
- Keep parallelism small on a laptop. The source program's always-on box hard-rebooted while a numerical
  job on all cores ran beside four Claude agents and two Codex calls; the system log points at a starved
  display driver, and the mechanism is not proven (`lessons/`). Two heavy external calls at a time,
  thread caps on numeric work.
- Log every fan-out in `ops/fleet_ledger.tsv` with the model and effort mix (`tools/fleet_ledger.py`);
  a scout fleet that keeps returning nothing accepted is a vein to stop drilling.
- Quotas: Codex calls draw on the ChatGPT plan's limits, not Claude's. A rate-limit or auth error comes
  back as `codex CLI failed (N)` with the stderr tail; `codex login` again if the token expired. A stalled
  reviewer writes `result.json` with the reason and exits 4 instead of hanging (a reasoning model once
  burned 45 minutes returning nothing).

## 7. Checklist before the first real gate

- [ ] `codex --version` prints; `codex login` done; `~/.codex/auth.json` exists and is outside the repo.
- [ ] `python tools/verify.py --selftest` → 0 failures (flags present).
- [ ] `python tools/verify.py review --files tools/verify_sealed.txt -p codex-cli --dry-run` shows
      `--strict-config`, the three `=false` switches, `--ephemeral`.
- [ ] Live smoke: `python tools/verify.py review --files tools/verify_sealed.txt --focus "smoke: reply NO FINDINGS if nothing is wrong" -o review_smoke.md`.
- [ ] `CODEX_TIERS` filled from probed ids, probe date noted.
- [ ] `python tools/codex_scout.py --canary --model <id>` → a line starting `CANARY PASS:`.
- [ ] `tools/verify_sealed.txt` lists your secrets and sealed paths.
- [ ] The review report header shows the model and effort you intended.
