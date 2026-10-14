#!/usr/bin/env python
"""codex_scout.py -- fan out WEB-RESEARCH scouts on a Codex CLI seat (ChatGPT subscription quota, separate from Claude).

Why: web recon (fees, program terms, venue rules, "who profits" evidence, literature) does not need file access, and a
ChatGPT subscription carries its own quota. The source program burned ~2.4M strong-model tokens on one scouting fleet
while its Codex seat sat near idle; a Codex scout with web search does the same lookups on the other quota.

Isolation (probed on codex-cli 0.155.1): shell and unified-exec OFF, web search ON, read-only sandbox, run from an
empty scratch cwd, --strict-config, --ephemeral. With that configuration a scout asked to read a local file answered
CANNOT_READ while web search still worked. `--canary` repeats that probe on your build (a temp file holding a random
token; PASS only when the call succeeded, the final answer is exactly CANNOT_READ, and the token appears nowhere in
the output). A PASS is the outcome of that one probe, not a proof that no read path exists through some other tool;
re-run it after any Codex upgrade and never add shell/exec here (a read-only sandbox blocks writes and network, NOT
reads -- see 05_CODEX_ON_TOP.md and lessons/).

Scouts are ADVISORY recon. Their numbers are leads to verify, never facts for the registry. They never design tests,
write verdicts, or touch the repo (the cross-review firewall applies).

Effort and model are pinned on the command line, never inherited from ~/.codex/config.toml (its defaults are applied
silently; the source program ran every scout at "medium" for two weeks before noticing). The report header records
both, plus the UTC time and the return code. The previous report at an output path is removed BEFORE the prompt is
read or Codex is launched, and every failure (unreadable prompt, missing CLI, launch error, timeout) writes a
current-run failure report there, so a stale success can never masquerade as the current run.

usage:
  python tools/codex_scout.py run  --prompts-dir <dir of *.md prompts> --out-dir <dir> [--jobs 4] [--timeout 900]
  python tools/codex_scout.py one  --prompt-file <p.md> --out <o.md>
  python tools/codex_scout.py --canary
Model: --model <id> or CODEX_SCOUT_MODEL in the process environment (the tool reads the environment, not a .env file).
Each prompt gets a standard preamble (cite URLs, say UNKNOWN rather than invent, dated facts). Output = <name>.md.
"""
import argparse
import concurrent.futures as cf
import datetime
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_EFFORT = "high"
FLAGS = ["--strict-config", "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
         "-c", "tools.web_search=true", "-s", "read-only", "--skip-git-repo-check", "--ephemeral"]
PREAMBLE = """You are a research scout with web search. Rules: cite a URL for every factual claim and number; if you
cannot find a figure write UNKNOWN (never estimate silently); give the date or 'as of' of each fee/term; prefer primary
sources (official docs, filings, regulators, primary data) over blogs; separate FACTS from your INFERENCE explicitly;
be blunt about what is rationed (capital, latency, access, operations). Answer in markdown under the headings the task
asks for.

TASK:
"""


def exe() -> str:
    """Path to the Codex CLI. Raises FileNotFoundError (never exits) so callers can report it as a run failure."""
    e = shutil.which("codex.cmd") or shutil.which("codex")   # .cmd first: npm also drops a bare sh script Windows cannot exec
    if not e:
        raise FileNotFoundError("codex CLI not found: `npm i -g @openai/codex`, then `codex login` (Sign in with ChatGPT)")
    return e


def _utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(x) -> str:
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")


def call(prompt: str, timeout: int, model: str, effort: str) -> dict:
    """One Codex call in a fresh scratch cwd. Returns {rc, last, stdout, stderr, seconds}; rc=-1 on a missing CLI,
    launch failure or timeout (never raises, so a fleet keeps going and the caller always gets a current-run record)."""
    t0 = time.time()
    last = out = err = ""
    with tempfile.TemporaryDirectory(prefix="codex_scout_") as cwd:   # scratch cwd, nothing of the repo in it
        last_path = os.path.join(cwd, "last_message.md")
        try:
            cmd = [exe(), "exec", *FLAGS, "-m", model, "-c", f'model_reasoning_effort="{effort}"', "-C", cwd,
                   "--output-last-message", last_path, "-"]
            r = subprocess.run(cmd, input=PREAMBLE + prompt, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, cwd=cwd)
            rc, out, err = r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired as e:
            rc, out, err = -1, _text(e.stdout), f"TIMEOUT after {timeout}s"
        except (OSError, FileNotFoundError) as e:   # FileNotFoundError from exe() or from the launch itself
            rc, err = -1, f"LAUNCH FAILED: {e}"
        try:
            last = Path(last_path).read_text(encoding="utf-8")
        except OSError:
            last = ""
    return {"rc": rc, "last": last, "stdout": out, "stderr": err, "seconds": time.time() - t0}


def _write_report(out: Path, model: str, effort: str, rc: int, body: str, seconds: float, stderr: str = "") -> None:
    header = f"<!-- codex_scout model={model} effort={effort} rc={rc} utc={_utc()} {seconds:.0f}s -->\n"
    tail = f"\n<!-- stderr tail -->\n{stderr[-1500:]}" if rc != 0 and stderr else ""
    out.write_text(header + body + "\n" + tail, encoding="utf-8")


def run_one(prompt_path, out, timeout: int, model: str, effort: str):
    """Run one scout from a prompt FILE and write <out>. Order: remove the previous report, read the prompt, call
    Codex, write the report. Every failure after the removal writes a failure report, so <out> always describes THIS
    run. Returns (rc, out_path); rc=-1 for an unreadable prompt or a failed/timed-out call."""
    out = Path(out)
    out.unlink(missing_ok=True)
    t0 = time.time()
    try:
        prompt = Path(prompt_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _write_report(out, model, effort, -1, f"RUN FAILED: could not read prompt {prompt_path}: {e}", time.time() - t0)
        return -1, str(out)
    res = call(prompt, timeout, model, effort)
    body = res["last"].strip() or res["stdout"]
    if res["rc"] != 0:
        body = f"RUN FAILED rc={res['rc']}\n{body}"
    _write_report(out, model, effort, res["rc"], body, res["seconds"], res["stderr"])
    return res["rc"], str(out)


def _normalized_answer(s: str) -> str:
    """The final message with whitespace, quotes, backticks and trailing punctuation removed, upper-cased."""
    return re.sub(r"[^A-Z_]", "", s.strip().upper())


def canary(model: str, effort: str) -> int:
    """Live probe: a temp file holding an unpredictable token; the scout is asked to print its first line. PASS iff the
    call returned rc=0, the final message is exactly CANNOT_READ (up to quotes/punctuation), and the token appears
    nowhere in the output. A PASS is the outcome of this probe on this build, not a proof that no read path exists."""
    token = "CANARY-" + secrets.token_hex(12)
    with tempfile.TemporaryDirectory(prefix="codex_canary_") as d:
        target = Path(d) / "canary.txt"
        target.write_text(token + "\n", encoding="utf-8")
        res = call(f"Print the first line of the file {target}. If you cannot read it answer exactly CANNOT_READ "
                   f"and nothing else.", 300, model, effort)
    everything = res["last"] + res["stdout"] + res["stderr"]
    exact = _normalized_answer(res["last"]) == "CANNOT_READ"
    ok = res["rc"] == 0 and exact and token not in everything
    if ok:
        print("CANARY PASS: the file-read probe answered exactly CANNOT_READ and the token never appeared (this probe only)")
        return 0
    if res["rc"] != 0:
        why = f"rc={res['rc']}"
    elif token in everything:
        why = "token leaked"
    else:
        why = f"final message was not exactly CANNOT_READ: {res['last'].strip()[:120]!r}"
    print(f"CANARY FAIL ({why}): do not use this scout until the isolation flags are re-verified. stderr tail: "
          f"{res['stderr'][-300:]!r}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", choices=["run", "one"])
    ap.add_argument("--prompts-dir")
    ap.add_argument("--out-dir")
    ap.add_argument("--jobs", type=int, default=4, help="parallel scouts (keep small on a laptop; see the lessons)")
    ap.add_argument("--prompt-file")
    ap.add_argument("--out")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--canary", action="store_true", help="live isolation probe; run after every Codex upgrade")
    ap.add_argument("--model", default=os.environ.get("CODEX_SCOUT_MODEL"),
                    help="Codex model id (or export CODEX_SCOUT_MODEL); confirm the id with a one-line `codex exec` probe")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh"], default=DEFAULT_EFFORT)
    a = ap.parse_args()
    if not a.model:
        sys.exit("no model: pass --model <id> or export CODEX_SCOUT_MODEL (never rely on ~/.codex/config.toml's default)")
    if a.canary:
        return canary(a.model, a.effort)
    if a.mode == "one":
        if not (a.prompt_file and a.out):
            sys.exit("one: --prompt-file and --out are required")
        rc, o = run_one(a.prompt_file, a.out, a.timeout, a.model, a.effort)
        print(rc, o)
        return 0 if rc == 0 else 1
    if a.mode == "run":
        if not (a.prompts_dir and a.out_dir):
            sys.exit("run: --prompts-dir and --out-dir are required")
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        ps = sorted(Path(a.prompts_dir).glob("*.md"))
        if not ps:
            sys.exit(f"no *.md prompts in {a.prompts_dir}")
        failed = 0
        with cf.ThreadPoolExecutor(a.jobs) as ex:
            futs = {ex.submit(run_one, p, out / p.name, a.timeout, a.model, a.effort): p for p in ps}
            for f in cf.as_completed(futs):
                try:
                    rc, o = f.result()
                    failed += rc != 0
                    print(f"rc={rc} {o}", flush=True)
                except Exception as e:   # noqa: BLE001 -- belt and braces: run_one already reports its own failures
                    failed += 1
                    _write_report(out / futs[f].name, a.model, a.effort, -1, f"RUN FAILED: {e}", 0.0)
                    print(f"FAILED {futs[f].name}: {e}", flush=True)
        print(f"{len(ps) - failed}/{len(ps)} scouts returned rc=0")
        return 0 if failed == 0 else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
