#!/usr/bin/env python
"""Pre-commit check (hardened after a two-seat cross-family panel review found that an earlier version's
compile / YAML / selftest checks read the WORKING TREE instead of the staged blob; updated 2026-09-25 with the
fixes from two further review rounds: `git diff --text` so -diff/binary-attributed files are scanned, quoted keys,
compile on the staged BYTES, strict-UTF-8 YAML, registry keyed on `facts`, bounded taskkill).
Blocks a commit on:

  1. a staged .py whose STAGED content does not compile (heredoc-corrupted scripts);
  2. a staged script that HAS a --selftest and fails it;
  3. a staged facts/registry.yaml whose STAGED content does not parse, has the wrong shape, or has duplicate ids;
  4. a secret in the staged diff, or a staged *.env / key file;
  5. a staged .py or registry that ALSO has unstaged edits (the hook would otherwise test content that is not
     what gets committed) -- stage or stash the rest first;
  6. any git command failing (the hook never reports "ok" on content it could not read);
  7. a staged file over PRECOMMIT_MAX_MB (default 5 MB): heavy data never enters git.

Installed per clone with `git config core.hooksPath ops/githooks`. Never bypass with --no-verify: fix the cause.
SKIP_SELFTEST=1 skips step 2 only (for a selftest that is known-slow); everything else always runs.
"""
import os
import re
import subprocess
import sys

SELFTEST_TIMEOUT_S = 300
GIT_TIMEOUT_S = 60
YAML_FILES = ("facts/registry.yaml",)
SECRET_PATTERNS = [
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key block"),
    (r"(?i)\b(api[_-]?key|api[_-]?token|secret|passw(?:or)?d|client[_-]?secret|application[_-]?key)\b['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+]{20,}", "key-like assignment"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
    (r"\bsk-[A-Za-z0-9_\-]{20,}\b", "sk- style API key"),
    (r"\bAIza[0-9A-Za-z_\-]{30,}\b", "Google API key"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}\b", "GitHub token"),
]
SECRET_FILENAMES = re.compile(r"(?i)(^|/)(\.env(\..*)?|[^/]*\.env|.*\.pem|id_rsa.*|.*\.p12|.*\.pfx)$")
# Heavy data never enters git, so a staged file above this size is blocked outright. That is the real
# invariant behind the hookify heavy-data rule (which can only pattern-match commands), and it means every
# file that CAN be committed is small enough to be secret-scanned in full.
try:
    MAX_STAGED_BYTES = max(1, int(os.environ.get("PRECOMMIT_MAX_MB", "5"))) * 1_000_000
except ValueError:
    MAX_STAGED_BYTES = 5_000_000
ALLOWED_ENV_TEMPLATES = re.compile(r"(?i)(^|/)\.env\.example$")


class GitFailed(RuntimeError):
    pass


def git(*args, binary=False):
    """Run git; any failure is an exception, never empty output (a silent failure let the hook print 'ok')."""
    try:
        r = subprocess.run(["git", *args], capture_output=True, timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise GitFailed(f"git {' '.join(args)} timed out after {GIT_TIMEOUT_S}s") from exc
    if r.returncode != 0:
        raise GitFailed(f"git {' '.join(args)} failed ({r.returncode}): {r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def staged_blob(path):
    """The content that will actually be committed."""
    return git("show", f":{path}", binary=True).decode("utf-8", "replace")


def has_unstaged_edits(path):
    """`git diff --quiet` exits 1 on differences, 0 on none, anything else on failure -- never read a failure
    as "clean"."""
    r = subprocess.run(["git", "diff", "--quiet", "--", path], capture_output=True, timeout=GIT_TIMEOUT_S)
    if r.returncode not in (0, 1):
        raise GitFailed(f"git diff --quiet -- {path} failed ({r.returncode})")
    return r.returncode == 1


def staged_paths():
    """NUL-delimited, so git's quoting of unusual filenames (non-ASCII, quotes) cannot hide a path."""
    raw = git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z", binary=True)   # T: symlink<->file
    return [p for p in raw.decode("utf-8", "replace").split(chr(0)) if p]


def staged_size(path):
    return int(git("cat-file", "-s", f":{path}").strip())


def export_index():
    """Snapshot the STAGED tree into a temp dir, so selftests import the helpers that will actually be
    committed, not unstaged working-tree versions."""
    import tempfile
    snap = tempfile.mkdtemp(prefix="precommit_index_")
    git("checkout-index", "-a", "-f", f"--prefix={snap.replace(os.sep, '/')}/")
    return snap


def added_lines(diff_text):
    """Added content lines of a unified diff. Only real file headers are skipped: an added line whose own
    text starts with '++' shows up as '+++...' and must still be scanned."""
    out, in_header = [], False
    for ln in diff_text.splitlines():
        if ln.startswith("diff --git "):
            in_header = True
            continue
        if ln.startswith("@@"):
            in_header = False
            continue
        if in_header:
            continue                       # index/---/+++/mode lines of the file header
        if ln.startswith("+"):
            out.append(ln[1:])
    return out


def run_selftest(path, env, cwd):
    """Run `path --selftest` inside `cwd` (the staged-index snapshot) with a wall-clock ceiling that kills the
    whole process tree (a child holding the output pipes would otherwise make the timeout hang)."""
    extra = {} if os.name == "nt" else {"start_new_session": True}   # own process group, killable as a unit
    proc = subprocess.Popen([sys.executable, os.path.join(cwd, path), "--selftest"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, cwd=cwd, **extra)
    try:
        out, _ = proc.communicate(timeout=SELFTEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
            except (subprocess.TimeoutExpired, OSError):
                pass                                  # the timeout is still reported; the kill is best-effort
        else:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # the whole group, not just the direct child
            except OSError:
                proc.kill()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return None, ""
    return proc.returncode, out.decode("utf-8", "replace")


def main():
    # Recursion guard: this file contains the string it searches for, so it must never run ITSELF with --selftest.
    if "--selftest" in sys.argv or os.environ.get("HARNESS_PRECOMMIT_ACTIVE") == "1":
        return 0
    os.environ["HARNESS_PRECOMMIT_ACTIVE"] = "1"
    problems = []
    try:
        os.chdir(git("rev-parse", "--show-toplevel").strip())
        staged = staged_paths()
    except GitFailed as exc:
        return fail([f"GIT FAILED, nothing was checked: {exc}"])

    for path in staged:
        if SECRET_FILENAMES.search(path) and not ALLOWED_ENV_TEMPLATES.search(path):
            problems.append(f"SECRET FILE staged: {path} (keys live outside the repo)")

    checkable = [p for p in staged if p.endswith(".py") or p in YAML_FILES]
    dirty = []
    for path in checkable:
        try:
            if has_unstaged_edits(path):
                dirty.append(path)
        except subprocess.TimeoutExpired:
            problems.append(f"GIT TIMED OUT checking unstaged edits: {path}")
        except GitFailed as exc:
            problems.append(f"GIT FAILED checking unstaged edits, not verified: {exc}")
    for path in dirty:
        problems.append(f"UNSTAGED EDITS on a staged file: {path} -- the hook checks what will be committed; "
                        f"`git add {path}` or `git stash push -- {path}` first")

    snapshot = None
    for path in (p for p in staged if p.endswith(".py")):
        try:
            raw_src = git("show", f":{path}", binary=True)
            compile(raw_src, path, "exec")         # in-memory, on the STAGED BYTES (no lossy decode first)
            source = raw_src.decode("utf-8", "replace")
        except (SyntaxError, ValueError) as exc:
            problems.append(f"DOES NOT COMPILE (staged content): {path}\n      {exc}")
            continue
        except GitFailed as exc:
            problems.append(f"GIT FAILED reading staged {path}: {exc}")
            continue
        if os.environ.get("SKIP_SELFTEST") == "1" or path in dirty:
            continue                               # dirty: already blocked above; do not test the wrong content
        if path.replace("\\", "/").startswith("ops/githooks/"):
            continue                               # never execute the hook's own files
        if re.search(r"""['"]--selftest['"]""", source):
            env = dict(os.environ, PYTHONUTF8="1")
            if snapshot is None:
                try:
                    snapshot = export_index()
                except GitFailed as exc:
                    problems.append(f"GIT FAILED exporting the staged tree, selftests NOT run: {exc}")
                    snapshot = ""                  # disables further selftests; compile checks still run
            if not snapshot:
                continue
            rc, out = run_selftest(path, env, snapshot)
            if rc is None:
                problems.append(f"SELFTEST TIMED OUT (> {SELFTEST_TIMEOUT_S}s; kill of the process tree attempted, check for strays): {path}")
            elif rc != 0:
                tail = "\n      ".join(out.strip().splitlines()[-6:])
                problems.append(f"SELFTEST FAILED: {path}\n      {tail}")
    if snapshot:
        import shutil
        shutil.rmtree(snapshot, ignore_errors=True)

    touched_yaml = [p for p in YAML_FILES if p in staged]
    if touched_yaml:
        try:
            import yaml
        except ImportError:
            problems.append("pyyaml missing - cannot check the registry (pip install pyyaml)")
            yaml = None
        for path in touched_yaml if yaml else []:
            try:
                doc = yaml.safe_load(git("show", f":{path}", binary=True).decode("utf-8"))   # strict: bad bytes block
            except GitFailed as exc:
                problems.append(f"GIT FAILED reading staged {path}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - any parse failure blocks
                problems.append(f"YAML DOES NOT PARSE (staged content): {path}\n      {str(exc).splitlines()[0]}")
                continue
            if path == "facts/registry.yaml":
                if isinstance(doc, list):
                    entries = doc
                elif isinstance(doc, dict) and "facts" in doc:
                    entries = doc["facts"] if isinstance(doc["facts"], list) else None
                elif isinstance(doc, dict) and sum(isinstance(v, list) for v in doc.values()) == 1:
                    entries = next(v for v in doc.values() if isinstance(v, list))
                elif doc is None:
                    entries = []
                else:
                    entries = None
                if entries is None:
                    problems.append(f"REGISTRY HAS WRONG SHAPE: {path} must be a list or a mapping holding a list")
                    continue
                ids = [str(e.get("id")) for e in entries if isinstance(e, dict) and e.get("id") is not None]
                dupes = sorted({i for i in ids if ids.count(i) > 1})
                if dupes:
                    problems.append(f"DUPLICATE REGISTRY IDS (one home per figure): {', '.join(dupes[:8])}")

    try:
        added = added_lines(git("diff", "--cached", "--text", "-U0", "--no-color"))
    except GitFailed as exc:
        return fail(problems + [f"GIT FAILED reading the staged diff, secrets NOT scanned: {exc}"])
    for line in added:
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, line):
                problems.append(f"POSSIBLE SECRET ({label}) in a staged line starting: {line.strip()[:24]}...")
                break
    # Whole-blob pass: size cap for every staged file, and a secret scan of any blob holding a NUL byte (git
    # shows those as "Binary files differ", so their content never reaches the added-line scan above).
    for path in staged:
        try:
            size = staged_size(path)
            if size > MAX_STAGED_BYTES:
                problems.append(f"FILE TOO LARGE for git ({size / 1e6:.1f} MB > {MAX_STAGED_BYTES // 1_000_000} MB): "
                                f"{path} -- heavy data lives in data_local/ or DATA_ROOT")
                continue
            blob = git("show", f":{path}", binary=True)
        except (GitFailed, ValueError) as exc:
            problems.append(f"GIT FAILED reading staged {path}, NOT scanned: {exc}")
            continue
        if b"\x00" not in blob:
            continue                               # text: already covered by the added-line scan
        text = blob.decode("utf-8", "replace")
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, text):
                problems.append(f"POSSIBLE SECRET ({label}) inside binary staged file: {path}")
                break

    if problems:
        return fail(problems)
    skipped = " (SKIP_SELFTEST=1: selftests NOT run)" if os.environ.get("SKIP_SELFTEST") == "1" else ""
    print(f"pre-commit check ok ({len(staged)} staged file(s); checks ran on staged content){skipped}")
    return 0


def fail(problems):
    print("\npre-commit check BLOCKED this commit:\n", file=sys.stderr)
    for item in problems:
        print("  - " + item, file=sys.stderr)
    print("\nFix the cause and commit again. Do not use --no-verify.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
