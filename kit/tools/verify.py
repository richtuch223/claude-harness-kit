#!/usr/bin/env python
"""verify.py -- different-family cross-review gate (ported from the source repo's ow_verify.py).

Sends code (a git diff, or named files) plus a literal task to a NON-CLAUDE model behind any
OpenAI-compatible chat endpoint (OpenRouter by default; Gemini / DeepSeek / Groq / local Ollama presets;
the Gemini and Codex CLIs via OAuth) and writes the reviewer's report to a file. On the HTTP providers the
model has NO tools, NO shell, NO filesystem: it only sees what this script sends. That is the sandbox.
The CLI providers run an agent that HAS tools: codex-cli is launched with its shell, exec and web tools switched
off under --strict-config (a read-only sandbox alone blocks writes and network, NOT file reads); gemini-cli is
NOT hardened against file reads (see call_gemini_cli).

Modes
  review  --uncommitted | --base REF | --commit SHA | --files F [F ...]     code review
  refute  --claim TEXT | --claim-file F   [--files F ...]                   refutation vote
  ask     PROMPT | --prompt-file F  [--files F ...]                         free-form (rare)
  --selftest                                                                offline synthetic checks

Examples (run from the repo root)
  python tools/verify.py review --uncommitted --focus "leakage in the new split" -o <scratch>/review.md
  python tools/verify.py review --files src/pkg/split.py -p gemini -o <scratch>/review.md
  python tools/verify.py refute --claim-file <scratch>/claim.txt --files experiments/001_x/run.py -o <scratch>/refute.md

Credentials: VERIFY_API_KEY, else the provider's own env var (OPENROUTER_API_KEY / GEMINI_API_KEY /
DEEPSEEK_API_KEY / GROQ_API_KEY). Never hardcode a key. --dry-run prints the exact payload and sends nothing.
Sealed path patterns (never sent off-box) are read from tools/verify_sealed.txt, one regex per line.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEALED_FILE = HERE / "verify_sealed.txt"

# ----------------------------------------------------------------------------- providers / tiers
PROVIDERS = {
    # name: (base_url, key env var, default model)
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "deepseek/deepseek-v4-pro-0813"),
    "deepseek":   ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
    "groq":       ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "local":      ("http://localhost:11434/v1", "", "qwen3-coder:30b"),  # Ollama / llama.cpp / LM Studio
    # Gemini: a DIFFERENT FAMILY, which is what the gate needs. Free AI Studio key; flash tier on free keys.
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-3.5-flash"),
    # gemini-cli: Google OAuth via `npm i -g @google/gemini-cli`, run `gemini` once to log in. Shells out from a
    # scratch cwd with the material on stdin; auto-approve flags are never passed. NOT hardened against file
    # reads: its read tools may still reach files outside the scratch cwd. Do not use it where the reviewer must
    # not see local secrets.
    "gemini-cli": ("", "", ""),
    # codex-cli: ChatGPT subscription via `npm i -g @openai/codex` + `codex login`. `-s read-only` blocks writes
    # and network but NOT reads; the shell/exec/web tools are switched off (CODEX_NO_TOOLS) so the reviewer cannot
    # read files. `-m a,b` runs a panel.
    "codex-cli":  ("", "", ""),
}
CLI_PROVIDERS = ("gemini-cli", "codex-cli")
# Codex aliases carried over from the source program; confirm against `/model` inside `codex` after login.
CODEX_TIERS = {"astra": "gpt-6-astra", "sol": "gpt-5.6-sol", "terra": "gpt-5.6-terra", "luna": "gpt-5.6-luna"}
# Flags that would hand a different-family model write access. Never passed; refused if they arrive via -m.
CODEX_FORBIDDEN = ("danger-full-access", "--dangerously-bypass-approvals-and-sandbox", "workspace-write")
# Tool switches verified on codex-cli 0.155.1 with a canary file outside the cwd: `-s read-only` alone let the
# reviewer read it back verbatim; with these three off it answered CANNOT_READ. Always passed together with
# --strict-config, so a key renamed in a future Codex FAILS the call instead of silently handing the shell back.
CODEX_NO_TOOLS = ("-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", "tools.web_search=false")
# OpenRouter tier aliases. Ids drift; if one 404s, check /api/v1/models and update here.
TIERS = {
    "soul":  "deepseek/deepseek-v4-pro-0813",   # hard review / refutation vote (1M ctx)
    "terra": "moonshotai/kimi-k2.7-code",       # second family for a diversity vote
    "luna":  "openai/gpt-oss-120b",             # cheap first pass
}
TEXT_EXT = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".sh", ".js", ".ts",
            ".tsx", ".sql", ".csv", ".tsv"}

SYSTEM = (
    "You are an independent code reviewer for a research / data codebase. You are a DIFFERENT "
    "model family from the author; your job is to catch what the author's self-review structurally misses: "
    "temporal lookahead (test-period rows or statistics computed over the full span reaching the training "
    "side), target leakage or contamination, duplicate entities spanning a split, off-by-one and window "
    "boundary errors, wrong column or wrong join key, silent NaN or empty-group drops, metric implementation "
    "errors (wrong denominators, ties, empty groups), confidence intervals at the wrong unit, guards that "
    "fail open instead of closed, and results that do not reconcile with the code's own other outputs. For any first/last/"
    "once-per-key construction, CHECK THE ROW ORDER of the input: is the tape sorted by parsed timestamp before "
    "that logic runs, or does file order (often newest-first from an API) silently decide it? You have no tools: "
    "reason ONLY from the text provided. Be literal and specific: cite file and line. Do not speculate beyond "
    "the material given; if something cannot be determined from it, say so explicitly. "
    "If you find nothing, say NO FINDINGS and name exactly what you inspected."
)
REVIEW_FORMAT = (
    "Output format:\n"
    "1. SUMMARY (2-3 lines)\n"
    "2. FINDINGS -- numbered, each with severity [BLOCKER|MAJOR|MINOR], file:line, the exact defect, "
    "and why it matters for correctness. BLOCKER = the number downstream is wrong or leaked.\n"
    "3. NO-FINDINGS AREAS -- what you checked and found clean.\n"
    "4. QUESTIONS -- anything you could not determine from the material.\n"
)
REFUTE_FORMAT = (
    "Try to REFUTE the claim. Check specifically for: temporal leakage, multiplicity/fishing across variants, "
    "a trivial baseline that would match it, concentration in a few entities or days, an offline metric "
    "presented as a real-world outcome, and whether the quoted numbers can "
    "actually be produced by the code shown. Re-derive the key quantity from the code if the material allows.\n"
    "Output format: first line exactly `REFUTED`, `NOT-REFUTED`, or `CANNOT-DETERMINE`; then the single "
    "strongest reason; then every weakness found; then what evidence would change your call."
)


# ----------------------------------------------------------------------------- helpers
def die(msg: str, code: int = 2) -> None:
    print(f"verify: {msg}", file=sys.stderr)
    sys.exit(code)


def load_sealed(path: Path = SEALED_FILE) -> list[re.Pattern]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(re.compile(line))
    return out


def git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def check_sealed(path: str, sealed: list[re.Pattern]) -> None:
    p = path.replace("\\", "/")
    for rx in sealed:
        if rx.search(p):
            die(f"REFUSED: `{path}` matches a sealed pattern in {SEALED_FILE.name} and must never be sent off-box.")


def read_file(path: Path, cap: int, sealed: list[re.Pattern]) -> str:
    check_sealed(str(path), sealed)
    if not path.exists():
        die(f"file not found: {path}")
    if path.suffix and path.suffix.lower() not in TEXT_EXT:
        return f"[skipped non-text file {path}]"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > cap:
        text = text[:cap] + f"\n... [TRUNCATED: {len(text) - cap} chars omitted; ask for a narrower slice]\n"
    return text


def numbered(text: str) -> str:
    return "\n".join(f"{i + 1:5d}| {line}" for i, line in enumerate(text.splitlines()))


def collect_material(a: argparse.Namespace, root: Path, sealed: list[re.Pattern]) -> list[str]:
    parts: list[str] = []
    cap = a.max_file_chars
    if a.uncommitted:
        diff = git(["diff", "HEAD", "--", "."], root)
        parts.append("### git diff HEAD (staged + unstaged, tracked files)\n```diff\n" + diff + "\n```")
        untracked = [u for u in git(["ls-files", "--others", "--exclude-standard"], root).splitlines() if u]
        for u in untracked:
            p = root / u
            if p.suffix.lower() in TEXT_EXT and p.stat().st_size < 2_000_000:
                parts.append(f"### untracked new file: {u}\n```\n{numbered(read_file(p, cap, sealed))}\n```")
    if a.base:
        diff = git(["diff", f"{a.base}...HEAD"], root)
        parts.append(f"### git diff {a.base}...HEAD\n```diff\n{diff}\n```")
    if a.commit:
        show = git(["show", "--format=medium", a.commit], root)
        parts.append(f"### git show {a.commit}\n```diff\n{show}\n```")
    for f in a.files or []:
        p = Path(f) if Path(f).is_absolute() else root / f
        parts.append(f"### file: {f}\n```\n{numbered(read_file(p, cap, sealed))}\n```")
    if not parts:
        die("nothing to send: give --uncommitted / --base / --commit / --files")
    return parts


def build_messages(a: argparse.Namespace, root: Path, sealed: list[re.Pattern]) -> list[dict]:
    material = "\n\n".join(collect_material(a, root, sealed))
    if a.mode == "review":
        task = "TASK: review the following code for correctness.\n"
        if a.focus:
            task += f"FOCUS: {a.focus}\n"
        task += REVIEW_FORMAT
    elif a.mode == "refute":
        claim = a.claim or Path(a.claim_file).read_text(encoding="utf-8")
        task = f"CLAIMED FINDING:\n{claim.strip()}\n\n{REFUTE_FORMAT}"
    else:
        task = a.prompt
    user = f"{task}\n\n--- MATERIAL ---\n\n{material}"
    total = len(user)
    if total > a.max_input_chars:
        die(f"payload is {total:,} chars > --max-input-chars {a.max_input_chars:,}; narrow the scope "
            f"(fewer files, --base instead of full history, or raise the cap for a 1M-context model).")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def resolve_model(a: argparse.Namespace, default: str) -> str:
    m = a.model or os.environ.get("VERIFY_MODEL") or default
    return TIERS.get(m, m)


def call(base_url: str, key: str, payload: dict, timeout: int) -> dict:
    try:
        import requests
    except ImportError:
        die("`pip install requests` first (it is in the dev extras)")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if "openrouter.ai" in base_url:
        headers["X-Title"] = "harness verify gate"
    last = None
    for attempt in range(3):
        try:
            r = requests.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:  # network / timeout
            last = f"request error: {e}"
        else:
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:800]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        wait = 5 * (attempt + 1)
        print(f"verify: {last} -- retry in {wait}s", file=sys.stderr)
        time.sleep(wait)
    die(f"API call failed: {last}", 3)


def call_gemini_cli(task: str, material: str, model: str | None, timeout: int, dry_run: bool) -> tuple[str, dict]:
    """Gemini CLI (OAuth). Task in -p, material on stdin, cwd = scratch dir.

    NOT hardened against file reads. A scratch cwd does not confine an agent's read tools (the same canary probe
    that caught Codex reading outside its cwd applies here), and no tool-off switch is passed. Prefer codex-cli or
    an HTTP provider when the machine holds secrets the reviewer must not see."""
    import shutil
    import tempfile
    exe = shutil.which("gemini") or shutil.which("gemini.cmd")
    if not exe and not dry_run:
        die("gemini CLI not found: `npm i -g @google/gemini-cli`, run `gemini` once to log in with Google, then retry")
    cmd = [exe or "gemini", "-p", task + "\n\nThe material to review is provided on standard input. Reason only from it.",
           "--output-format", "json"]
    if model:
        cmd += ["-m", model]
    if dry_run:
        return f"[dry-run] would run: {' '.join(cmd[:4])} ... (stdin {len(material):,} chars)", {}
    work = tempfile.mkdtemp(prefix="verify_gemini_")
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=material, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=work, timeout=timeout)
    except subprocess.TimeoutExpired:
        stall(work, "gemini-cli", timeout, t0)
    if r.returncode != 0:
        die(f"gemini CLI failed ({r.returncode}): {r.stderr[-800:]}", 3)
    out = r.stdout.strip()
    try:
        j = json.loads(out)
        return str(j.get("response") or j.get("text") or out), j.get("stats") or j.get("usage") or {}
    except json.JSONDecodeError:
        return out, {}


def call_codex_cli(task: str, material: str, model: str | None, reasoning: str, timeout: int,
                   dry_run: bool) -> tuple[str, dict]:
    """Codex CLI (ChatGPT OAuth). Whole prompt on stdin; the final message comes from --output-last-message.

    ISOLATION. The read-only sandbox blocks writes and network but NOT reads, and a scratch cwd does not confine
    them: a canary probe (codex-cli 0.155.1) showed the reviewer reading a file outside its cwd. The shell and exec
    tools and web search are therefore switched OFF (the same probe then answered CANNOT_READ), --strict-config
    makes a renamed key in a future Codex fail the call instead of silently restoring the shell, and --ephemeral
    keeps no session on disk."""
    import shutil
    import tempfile
    exe = shutil.which("codex.cmd") or shutil.which("codex")
    if not exe and not dry_run:
        die("codex CLI not found: `npm i -g @openai/codex`, then `codex login`, then retry")
    model = CODEX_TIERS.get(model, model) if model else None
    if model and any(f in model for f in CODEX_FORBIDDEN):
        die("refusing: forbidden Codex sandbox flag in the model argument")
    work = tempfile.mkdtemp(prefix="verify_codex_")
    last = os.path.join(work, "last_message.md")
    cmd = [exe or "codex", "exec", "--strict-config", *CODEX_NO_TOOLS, "-s", "read-only", "--skip-git-repo-check",
           "--ephemeral", "-C", work, "--output-last-message", last]
    if model:
        cmd += ["-m", model]
    if reasoning != "none":
        cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
    cmd.append("-")
    if dry_run:
        return f"[dry-run] would run: {' '.join(cmd)}  (stdin {len(task) + len(material):,} chars)", {}
    prompt = (task + "\n\nDo not run commands or read files: everything you need is below. Reason only from it."
              "\n\n--- MATERIAL ---\n\n" + material)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=work, timeout=timeout)
    except subprocess.TimeoutExpired:
        stall(work, f"codex-cli:{model or 'default'}", timeout, t0)
    if r.returncode != 0:
        die(f"codex CLI failed ({r.returncode}): {(r.stderr or r.stdout)[-800:]}", 3)
    try:
        out = Path(last).read_text(encoding="utf-8").strip()
    except OSError:
        out = ""
    return (out or r.stdout.strip()), {"model": model or "cli default"}


def stall(work: str, seat: str, timeout: int, t0: float) -> None:
    """Watchdog contract: a stalled reviewer must produce a logged failure, never a silent burn. Writes a
    result record beside the scratch dir, then exits 4. (The source program lost ~45 min to a reasoning-model
    stall that returned nothing.)"""
    rec = {"seat": seat, "exit_reason": "wall_clock_timeout", "timeout_s": timeout,
           "elapsed_s": round(time.time() - t0), "when": time.strftime("%Y-%m-%d %H:%M")}
    try:
        Path(work, "result.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    except OSError:
        pass
    die(f"STALLED: {seat} produced no answer within {timeout}s; record at {work}/result.json. "
        f"Re-scope smaller or lower --reasoning.", 4)


def describe_input(a: argparse.Namespace) -> str:
    bits = []
    if a.uncommitted:
        bits.append("--uncommitted")
    if a.base:
        bits.append(f"--base {a.base}")
    if a.commit:
        bits.append(f"--commit {a.commit}")
    if a.files:
        bits.append("--files " + " ".join(a.files))
    return " ".join(bits)


def write_report(a: argparse.Namespace, header: str, content: str) -> None:
    report = header + content.strip() + "\n"
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(report, encoding="utf-8")
        print(f"verify: wrote {a.out} ({len(content):,} chars)", file=sys.stderr)
        print(content[:1200] + ("\n... [see report file]" if len(content) > 1200 else ""))
    else:
        print(report)


# ----------------------------------------------------------------------------- selftest
def selftest() -> int:
    """Offline synthetic checks. No network, no real data. Guards must fail CLOSED."""
    import tempfile
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
        if not ok:
            failures.append(name)

    tmp = Path(tempfile.mkdtemp(prefix="verify_selftest_"))
    sealed = [re.compile(r"data_local/holdout/"), re.compile(r"\.env$")]

    # 1. a sealed path is refused (fail closed)
    refused = False
    try:
        check_sealed("data_local/holdout/users_2026q4.parquet", sealed)
    except SystemExit:
        refused = True
    check("sealed path refused", refused)
    refused = False
    try:
        check_sealed("C:\\repo\\.env", sealed)          # backslashes normalised before matching
    except SystemExit:
        refused = True
    check("sealed .env refused (backslash path)", refused)
    passed = True
    try:
        check_sealed("src/pkg/split.py", sealed)
    except SystemExit:
        passed = False
    check("ordinary path allowed", passed)

    # 2. sealed file parser ignores comments and blanks
    sf = tmp / "sealed.txt"
    sf.write_text("# comment\n\n^secret/\n", encoding="utf-8")
    pats = load_sealed(sf)
    check("sealed file parsed (1 pattern)", len(pats) == 1 and pats[0].search("secret/x") is not None)
    check("missing sealed file yields empty list", load_sealed(tmp / "nope.txt") == [])

    # 3. build_messages includes the file, numbers lines, and the task
    f = tmp / "probe.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    ns = argparse.Namespace(mode="review", uncommitted=False, base=None, commit=None, files=[str(f)],
                            focus="leakage", claim=None, claim_file=None, prompt=None,
                            max_file_chars=200_000, max_input_chars=700_000)
    msgs = build_messages(ns, tmp, sealed)
    body = msgs[1]["content"]
    check("system prompt present", msgs[0]["role"] == "system" and "DIFFERENT" in msgs[0]["content"])
    check("file content line-numbered in payload", "    1| x = 1" in body and "    2| y = 2" in body)
    check("focus line present", "FOCUS: leakage" in body)

    # 4. the input cap trips (fail closed on an accidental dump)
    ns.max_input_chars = 10
    tripped = False
    try:
        build_messages(ns, tmp, sealed)
    except SystemExit:
        tripped = True
    check("input cap trips", tripped)

    # 5. a non-text file is skipped, not sent
    b = tmp / "weights.bin"
    b.write_bytes(b"\x00\x01")
    check("non-text file skipped", read_file(b, 1000, sealed).startswith("[skipped non-text"))

    # 6. refute mode needs a claim; tier alias resolves
    ns2 = argparse.Namespace(model="luna")
    check("tier alias resolves", resolve_model(ns2, "x") == TIERS["luna"])
    ns3 = argparse.Namespace(model=None)
    os.environ.pop("VERIFY_MODEL", None)
    check("default model used when no alias", resolve_model(ns3, "default-model") == "default-model")

    # 7. codex forbidden flag refused
    refused = False
    try:
        call_codex_cli("t", "m", "gpt-x --dangerously-bypass-approvals-and-sandbox", "low", 5, dry_run=True)
    except SystemExit:
        refused = True
    check("codex forbidden flag refused", refused)
    dry, _ = call_codex_cli("t", "m", None, "none", 5, dry_run=True)
    check("codex runs with its tools off under --strict-config (+ --ephemeral)",
          all(f in dry for f in ("--strict-config", "features.shell_tool=false", "features.unified_exec=false",
                                 "tools.web_search=false", "--ephemeral", "-s read-only")))

    print(f"selftest: {len(failures)} failure(s)")
    return 1 if failures else 0


# ----------------------------------------------------------------------------- main
def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["review", "refute", "ask"])
    ap.add_argument("prompt", nargs="?", help="ask mode: the literal prompt (put it BEFORE --files, or use --prompt-file)")
    ap.add_argument("--prompt-file", help="ask mode: file holding the prompt")
    ap.add_argument("--uncommitted", action="store_true", help="git diff HEAD + untracked text files")
    ap.add_argument("--base", help="git diff BASE...HEAD")
    ap.add_argument("--commit", help="git show SHA")
    ap.add_argument("--files", nargs="*", help="full file contents to include (line-numbered)")
    ap.add_argument("--focus", help="review mode: one-line focus instruction")
    ap.add_argument("--claim", help="refute mode: the claim text")
    ap.add_argument("--claim-file", help="refute mode: file holding the claim text")
    ap.add_argument("-p", "--provider", default=os.environ.get("VERIFY_PROVIDER", "codex-cli"), choices=PROVIDERS,
                    help="default codex-cli: the live seat (ChatGPT OAuth). Override with VERIFY_PROVIDER.")
    ap.add_argument("-m", "--model", help="model id, or tier alias soul|terra|luna (OpenRouter ids)")
    ap.add_argument("--reasoning", default="high", choices=["none", "low", "medium", "high"],
                    help="reasoning effort (`low` is better for flash-tier code review: high truncates the answer)")
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--max-file-chars", type=int, default=200_000)
    ap.add_argument("--max-input-chars", type=int, default=700_000, help="~175k tokens; guard against an accidental dump")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per request")
    ap.add_argument("-C", "--root", default=".", help="repo root")
    ap.add_argument("-o", "--out", help="write the report here (default: stdout only)")
    ap.add_argument("--dry-run", action="store_true", help="print the payload, send nothing")
    a = ap.parse_args()

    if a.mode == "ask" and a.prompt_file:
        a.prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    if a.mode == "ask" and not a.prompt:
        die("ask mode needs a prompt: `ask \"<prompt>\" --files ...` (prompt BEFORE --files, which is greedy) "
            "or `ask --prompt-file <f> --files ...`")
    if a.mode == "refute" and not (a.claim or a.claim_file):
        die("refute mode needs --claim or --claim-file")

    root = Path(a.root).resolve()
    sealed = load_sealed()
    base_url, key_env, default_model = PROVIDERS[a.provider]
    base_url = os.environ.get("VERIFY_BASE_URL", base_url)
    key = os.environ.get("VERIFY_API_KEY") or (os.environ.get(key_env) if key_env else "")
    model = resolve_model(a, default_model)

    messages = build_messages(a, root, sealed)
    reminder = "- REMINDER: advisory filter. Verify every finding against the real files before believing it.\n\n"
    if a.provider in CLI_PROVIDERS:
        task, _, material = messages[1]["content"].partition("\n\n--- MATERIAL ---\n\n")
        chars = len(task) + len(material)
        print(f"verify: provider={a.provider} (OAuth) model={a.model or 'cli default'} input~{chars:,} chars", file=sys.stderr)
        t0 = time.time()
        if a.provider == "codex-cli":
            models = [m.strip() for m in (a.model or "").split(",") if m.strip()] or [None]
            if len(models) == 1:
                content, usage = call_codex_cli(SYSTEM + "\n\n" + task, material, models[0], a.reasoning, a.timeout, a.dry_run)
            else:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(len(models)) as ex:
                    outs = list(ex.map(lambda m: call_codex_cli(SYSTEM + "\n\n" + task, material, m, a.reasoning,
                                                                a.timeout, a.dry_run)[0], models))
                content = "\n\n".join(f"{'=' * 30} PANEL SEAT: {m} {'=' * 30}\n\n{o.strip()}" for m, o in zip(models, outs))
                usage = {"panel": models}
        else:
            content, usage = call_gemini_cli(SYSTEM + "\n\n" + task, material, a.model, a.timeout, a.dry_run)
        if a.dry_run:
            print(content)
            return 0
        header = ("# verify report\n"
                  f"- mode: {a.mode} | provider: {a.provider} | model: {a.model or 'cli default'}\n"
                  f"- input: {describe_input(a)}\n"
                  f"- usage: {json.dumps(usage)[:200]} | {time.time() - t0:.0f}s | {time.strftime('%Y-%m-%d %H:%M')}\n"
                  + reminder)
        write_report(a, header, content)
        return 0

    payload: dict = {"model": model, "messages": messages, "temperature": a.temperature, "max_tokens": a.max_tokens}
    if a.reasoning != "none":
        if a.provider == "openrouter":
            payload["reasoning"] = {"effort": a.reasoning}
        else:
            payload["reasoning_effort"] = a.reasoning

    chars = sum(len(m["content"]) for m in messages)
    print(f"verify: provider={a.provider} model={model} input~{chars:,} chars (~{chars // 4:,} tok)", file=sys.stderr)
    if a.dry_run:
        print(json.dumps(payload, indent=2)[:20000])
        return 0
    if not key and a.provider != "local":
        hint = " (free key at aistudio.google.com)" if a.provider == "gemini" else ""
        die(f"no API key: set VERIFY_API_KEY or {key_env}{hint}")

    t0 = time.time()
    resp = call(base_url, key, payload, a.timeout)
    dt = time.time() - t0
    try:
        choice = resp["choices"][0]
        msg = choice["message"]
        content = msg.get("content")
    except (KeyError, IndexError, TypeError):
        die(f"unexpected response shape: {json.dumps(resp)[:1500]}", 3)
    # Reasoning models can spend the whole output budget thinking and return content=None with
    # finish_reason=length; surface that instead of crashing, and fall back to visible reasoning text.
    if not content:
        reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
        if choice.get("finish_reason") == "length":
            die(f"model hit max_tokens={a.max_tokens} before producing an answer (reasoning={len(reasoning):,} chars). "
                f"Re-run with --reasoning low/medium or a larger --max-tokens.", 3)
        if reasoning:
            content = "[NOTE: model returned no final content; the text below is its visible reasoning]\n\n" + reasoning
        else:
            die(f"empty completion: {json.dumps(resp)[:1500]}", 3)
    usage = resp.get("usage", {})
    header = (
        "# verify report\n"
        f"- mode: {a.mode} | provider: {a.provider} | model: {resp.get('model', model)}\n"
        f"- input: {describe_input(a)}\n"
        f"- usage: {usage.get('prompt_tokens', '?')} in / {usage.get('completion_tokens', '?')} out"
        f" | cost: {usage.get('cost', 'n/a')} | {dt:.0f}s | {time.strftime('%Y-%m-%d %H:%M')}\n"
        + reminder
    )
    write_report(a, header, content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
