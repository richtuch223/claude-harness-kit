#!/usr/bin/env python
"""SessionStart hook: print a short DERIVED brief of live state; Claude Code adds stdout to context.

Derived from git + docs/STATE.md, so it cannot drift the way a hand-written summary does. It always prints
how many threads it loaded: a derived surface that silently loads nothing looks healthy while being blind.
Fail-open: any error prints one line and exits 0, never blocking session start.

  python tools/hooks/session_brief.py              print the brief
  python tools/hooks/session_brief.py --selftest   offline checks
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # installed at <repo>/tools/hooks/
GIT_TIMEOUT_S = 4          # 3 git calls x 4 s stays well inside the 20 s hook timeout in settings.snippet.json


def _max_chars() -> int:
    try:
        return max(500, int(os.environ.get("BRIEF_MAX_CHARS", "4000")))
    except ValueError:
        return 4000


MAX_CHARS = _max_chars()
THREAD_RE = re.compile(r"^###\s+(.+?)\s+[—-]+\s+`(live|waiting|blocked|parked|done)`(.*)$")


def git(repo: Path, *args: str):
    """stdout of a git command, or None if it failed / timed out (never mistaken for empty output)."""
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=GIT_TIMEOUT_S)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def parse_state(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    threads, blocked, in_blocked = [], [], False
    for line in text.splitlines():
        m = THREAD_RE.match(line)
        if m:
            threads.append((m.group(1).strip(), m.group(2), m.group(3).strip(" ,")))
        if line.startswith("## "):
            in_blocked = "blocked" in line.lower()
        elif in_blocked and line.strip().startswith("- [ ]"):
            blocked.append(line.strip()[5:].strip())
    return threads, blocked


def build(repo: Path) -> str:
    out = []
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    status = git(repo, "status", "--porcelain")
    dirty = "UNKNOWN (git status failed)" if status is None else str(len([ln for ln in status.splitlines() if ln.strip()]))
    out.append(f"SESSION BRIEF (derived) - branch {branch}, {dirty} uncommitted file(s)")
    log = git(repo, "log", "--oneline", "-5")
    if log:
        out.append("recent commits:\n" + "\n".join("  " + ln for ln in log.splitlines()))
    state = repo / "docs" / "STATE.md"
    if state.is_file():
        threads, blocked = parse_state(state.read_text(encoding="utf-8", errors="replace"))
        active = [t for t in threads if t[1] in ("live", "waiting", "blocked")]
        out.append(f"threads: {len(threads)} loaded from docs/STATE.md, {len(active)} active")
        out.extend(f"  [{s}] {name}{(' - ' + tail) if tail else ''}" for name, s, tail in active)
        if blocked:
            out.append(f"blocked on a maintainer ({len(blocked)}):")
            out.extend("  - " + b for b in blocked)
    else:
        out.append("threads: 0 loaded (docs/STATE.md missing)")
    text = "\n".join(out)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n  ... [brief truncated; read docs/STATE.md]"


def selftest() -> int:
    fails = []

    def check(label, ok):
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            fails.append(label)

    sample = ("# STATE\n\n## Threads\n\n### 001_x — `live`, running now\nbody\n\n### 002_y — `done`\n\n"
              "## Blocked on a maintainer\n- [ ] pick a vendor — blocks 003\n- [x] already done\n")
    threads, blocked = parse_state(sample)
    check("two threads parsed", len(threads) == 2)
    check("status read", threads[0][1] == "live" and threads[1][1] == "done")
    check("only open blocked items", blocked == ["pick a vendor — blocks 003"])
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="brief_selftest_"))
    brief = build(tmp)                                   # not a git repo, no STATE.md: must not crash
    check("missing STATE.md is reported, not silent", "0 loaded" in brief)
    check("failed git status is UNKNOWN, not clean", "UNKNOWN" in brief)
    os.environ["BRIEF_MAX_CHARS"] = "not-a-number"
    check("bad BRIEF_MAX_CHARS falls back instead of crashing", _max_chars() == 4000)
    print(f"selftest: {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        print(build(REPO))
    except Exception as exc:                            # never block session start
        print(f"session brief failed: {exc}")
    sys.exit(0)
