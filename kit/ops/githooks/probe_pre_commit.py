"""Probe suite for ops/githooks/pre_commit_check.py (2026-09-25). Builds throwaway git repos, stages one bad (or good)
thing each, and asserts the hook blocks it FOR THE RIGHT REASON. Run after any edit to the hook:
    PYTHONUTF8=1 python ops/githooks/probe_pre_commit.py
Exit 0 = every probe as expected."""
import os, subprocess, sys, shutil, tempfile, pathlib
SRC = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent; NL = chr(10); Q = chr(34)
def sh(*a, cwd, check=True, env=None):
    r = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise SystemExit(f"SETUP FAILED: {a}: {r.stderr}")
    return r
def fresh():
    d = pathlib.Path(tempfile.mkdtemp(prefix="pcprobe_"))
    sh("git", "init", "-q", cwd=d); sh("git", "config", "user.email", "p@x", cwd=d); sh("git", "config", "user.name", "p", cwd=d)
    (d/"ops/githooks").mkdir(parents=True)
    shutil.copy(SRC/"pre_commit_check.py", d/"ops/githooks/"); shutil.copy(SRC/"pre-commit", d/"ops/githooks/")
    sh("git", "config", "core.hooksPath", "ops/githooks", cwd=d)
    sh("git", "add", "-A", cwd=d); sh("git", "commit", "-qm", "base", cwd=d)
    return d
def case(name, files, expect, after_add=None, env=None):
    """expect = None (must be allowed) or a substring the BLOCK message must contain."""
    d = fresh()
    try:
        for p, c in files.items():
            f = d/p; f.parent.mkdir(parents=True, exist_ok=True)
            (f.write_bytes if isinstance(c, bytes) else f.write_text)(c)
        sh("git", "add", "-A", cwd=d)
        for p, c in (after_add or {}).items(): (d/p).write_text(c)
        r = sh("git", "commit", "-qm", name, cwd=d, check=False, env=dict(os.environ, **(env or {})))
        out = r.stdout + r.stderr
        ok = (r.returncode == 0) if expect is None else (r.returncode != 0 and expect in out)
        print(("PASS" if ok else "FAIL"), name, "| blocked" if r.returncode else "| allowed", "" if ok else out[-500:])
        return ok, out
    finally:
        shutil.rmtree(d, ignore_errors=True)
AK = "AKIA" + "ABCDEFGHIJKLMNOP"
_reg = SRC.parent.parent/"facts"/"registry.yaml"   # the kit ships facts/registry.yaml two levels up
KIT_REGISTRY = _reg.read_bytes() if _reg.exists() else ("facts:" + NL + "- id: example" + NL).encode()
cases = [
 ("clean", {"a.py": "x = 1" + NL}, None),
 ("syntax-error", {"b.py": "def (:" + NL}, "DOES NOT COMPILE"),
 ("key-assignment", {"c.py": "api_key = " + Q + "A"*24 + Q + NL}, "key-like assignment"),
 ("quoted-json-key", {"c.json": "{" + Q + "api_key" + Q + ": " + Q + "A"*24 + Q + "}" + NL}, "key-like assignment"),
 ("application-key", {"k.txt": "application_key: " + "Z"*30 + NL}, "key-like assignment"),
 ("env-file", {".env": "X=1" + NL}, "SECRET FILE"),
 ("env-example-allowed", {".env.example": "X=" + NL}, None),
 ("registry-dupes", {"facts/registry.yaml": "facts:" + NL + "- id: x" + NL + "- id: x" + NL}, "DUPLICATE REGISTRY IDS"),
 ("registry-dupes-after-other-list", {"facts/registry.yaml": "meta: []" + NL + "facts:" + NL + "- id: x" + NL + "- id: x" + NL}, "DUPLICATE REGISTRY IDS"),
 ("kit-registry-as-shipped", {"facts/registry.yaml": KIT_REGISTRY}, None),
 ("registry-real-shape-ok", {"facts/registry.yaml": "facts:" + NL + "- id: x" + NL + "- id: y" + NL}, None),
 ("registry-facts-not-list", {"facts/registry.yaml": "meta: []" + NL + "facts: invalid" + NL}, "WRONG SHAPE"),
 ("yaml-invalid-utf8", {"facts/registry.yaml": b"facts:" + bytes([10]) + b"- id: x" + bytes([255, 10])}, "YAML DOES NOT PARSE"),
 ("big-file", {"big.bin": b"0" * 6_000_000}, "FILE TOO LARGE"),
 ("nul-file-with-secret", {"blob.bin": b"x" + bytes(1) + AK.encode() + bytes([10])}, "AWS access key"),
 ("minus-diff-attribute-secret", {".gitattributes": "creds.txt -diff" + NL, "creds.txt": AK + NL}, "AWS access key"),
 ("non-utf8-python", {"w.py": b"x = " + bytes([34, 255, 34]) + b"\n"}, "DOES NOT COMPILE"),
 ("selftest-fails", {"t.py": "import sys" + NL + "if '--selftest' in sys.argv: sys.exit(3)" + NL}, "SELFTEST FAILED"),
 ("selftest-passes", {"t.py": "import sys" + NL + "if '--selftest' in sys.argv: sys.exit(0)" + NL}, None),
 ("staged-py-with-unstaged-edit", {"v.py": "x = 1" + NL}, "UNSTAGED EDITS", {"v.py": "x = 3" + NL}),
]
res = [case(*c)[0] for c in cases]
ok, out = case("skip-selftest-message", {"t.py": "import sys" + NL + "if '--selftest' in sys.argv: sys.exit(3)" + NL}, None, env={"SKIP_SELFTEST": "1"})
m = "selftests NOT run" in out; print("PASS" if m else "FAIL", "skip-selftest-says-so"); res += [ok, m]
print(sum(res), "/", len(res), "probes as expected")
sys.exit(0 if all(res) else 1)
