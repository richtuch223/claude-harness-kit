#!/usr/bin/env python
"""PreCompact hook: write a pre-compaction checkpoint to ops/checkpoints/<UTC>.md.

Why PreCompact. Context compaction is exactly the event that can silently drop the unwritten state that lives only
in the conversation: the kill rule you agreed on, the finding you rejected, the next action. Claude Code's
`PreCompact` hook fires before both manual `/compact` and automatic compaction (payload on stdin carries
`hook_event_name` and `trigger` in {"manual","auto"}). This hook writes what can be derived mechanically (last
commit, working-tree state, recently touched folders, frozen specs, retracted numbers) and leaves HEADED BLANKS for
what cannot (objective, phase, next action), so the next session knows what to ask for. If a Claude Code build
rejects the `PreCompact` key, wire it to `SessionStart` instead: the checkpoint then says so in its own header line.

Fail-open: a broken checkpoint writer must never block compaction or session start. Any exception is caught,
printed to stderr, and the hook exits 0.

Time budget: the hook runner kills a hook at its configured timeout (settings.snippet.json sets 30 s). Every git call
and every scan loop draw on ONE overall budget (BUDGET_S = 20 s) so the checkpoint is always written with the time
left; a section that ran out of budget says so instead of blocking the file. The budget bounds the loops and the
git subprocess timeouts; it cannot interrupt a single blocked filesystem call, which is why 10 s of slack remain
below the hook timeout.

Repo root precedence: --repo, then $CLAUDE_PROJECT_DIR (set by Claude Code for hooks), then the git toplevel of the
cwd, then the cwd.

Usage (settings.json, see settings.snippet.json):
    PYTHONUTF8=1 python optional/checkpoint-writer/checkpoint_writer.py     # or wherever you copied it
Manual test:
    python checkpoint_writer.py --selftest
    echo '{"hook_event_name": "PreCompact", "trigger": "manual"}' | python checkpoint_writer.py --repo .
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

BUDGET_S = 20.0                 # total wall-clock budget; keep below the hook timeout in settings.snippet.json
GIT_CALL_S = 5.0                # cap per git call (also bounded by the remaining budget)
WATCH_DIRS = ("docs", "reviews", "ops", "experiments")     # top-level folders whose recently touched subfolders are listed
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "data_local", "data", ".tox", ".mypy_cache"}

TEMPLATE = """# Checkpoint {utc}

_Written by `checkpoint_writer.py` on event `{event_name}`{fallback_note}._

## Objective
<!-- headed blank: not mechanically derivable -- fill from the session's stated goal -->

## Phase
<!-- headed blank: e.g. "designing", "running the frozen spec", "writing the result" -->

## Last commit
{last_commit}

## Working tree
{worktree}

## Recently touched folders
{touched}

## Frozen specs (SPEC*.md, not .draft)
{specs}

## Retracted numbers (facts/registry.yaml, status: retracted)
{retracted}

## Next action
<!-- headed blank: not mechanically derivable -- fill from the session's plan -->
"""


class Budget:
    def __init__(self, seconds: float):
        self.deadline = time.monotonic() + seconds

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def spent(self) -> bool:
        return self.left() <= 0


def repo_root(explicit: str | None, budget: Budget) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           timeout=max(0.5, min(GIT_CALL_S, budget.left())))
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip()).resolve()
    except Exception:   # noqa: BLE001
        pass
    return Path.cwd().resolve()


def _git(repo: Path, budget: Budget, *args: str) -> str:
    if budget.spent():
        return "(skipped: time budget exhausted)"
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                           timeout=max(0.5, min(GIT_CALL_S, budget.left())))
    except Exception as exc:   # noqa: BLE001 -- fail-open
        return f"(git {' '.join(args)} failed: {type(exc).__name__})"
    if r.returncode != 0:
        return f"(git {' '.join(args)} failed: {r.stderr.strip()[:200]})"
    return r.stdout.strip()


def last_commit(repo: Path, budget: Budget) -> str:
    out = _git(repo, budget, "log", "-1", "--format=%H %ci %s")
    return f"`{out}`" if out and not out.startswith("(") else (out or "(no commits found)")


def worktree(repo: Path, budget: Budget) -> str:
    out = _git(repo, budget, "status", "--short")
    if out.startswith("("):
        return out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return "clean"
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    return f"{len(lines) - untracked} modified/staged, {untracked} untracked"


def touched(repo: Path, budget: Budget, limit: int = 8) -> str:
    if budget.spent():
        return "(skipped: time budget exhausted)"
    dirs = []
    for top in WATCH_DIRS:
        base = repo / top
        if not base.is_dir():
            continue
        try:
            dirs += [d for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")]
        except OSError:
            continue
    if not dirs:
        return "(none of " + ", ".join(WATCH_DIRS) + " has subfolders)"
    stamped = []
    for d in dirs:
        if budget.left() < 2.0:
            return "(scan cut short by the time budget)"
        try:
            stamped.append((d.stat().st_mtime, d))
        except OSError:
            continue
    stamped.sort(key=lambda t: t[0], reverse=True)
    out = []
    for _, d in stamped[:limit]:
        m = datetime.datetime.fromtimestamp(d.stat().st_mtime, tz=datetime.timezone.utc)
        out.append(f"- `{d.relative_to(repo).as_posix()}/` (touched {m.strftime('%Y-%m-%d %H:%M')} UTC)")
    return "\n".join(out)


def specs(repo: Path, budget: Budget, limit: int = 12) -> str:
    """Walk the repo for SPEC*.md (not .draft.), pruning heavy folders, stopping when the budget runs out."""
    files: list[Path] = []
    truncated = False
    for root, dnames, fnames in os.walk(repo):
        if budget.left() < 2.0:        # keep time to write the file
            truncated = True
            break
        dnames[:] = [d for d in dnames if d not in SKIP_DIRS and not d.startswith(".")]
        if len(files) >= 2000:          # a pathological tree: stop collecting, say so
            truncated = True
            break
        for f in fnames:
            if f.startswith("SPEC") and f.endswith(".md") and ".draft." not in f:
                files.append(Path(root) / f)
    if not files:
        return "(no SPEC*.md found)" + (" -- scan cut short by the time budget" if truncated else "")
    stamped = []
    for f in files:
        if budget.left() < 1.5:
            truncated = True
            break
        try:
            stamped.append((f.stat().st_mtime, f))
        except OSError:
            continue
    stamped.sort(key=lambda t: t[0], reverse=True)
    out = [f"- `{f.relative_to(repo).as_posix()}`" for _, f in stamped[:limit]]
    if len(files) > limit:
        out.append(f"- ... ({len(files) - limit} more not shown)")
    if truncated:
        out.append("- (scan cut short by the time budget; list may be incomplete)")
    return "\n".join(out)


def retracted(repo: Path, budget: Budget, limit: int = 15, max_bytes: int = 5_000_000) -> str:
    """Read-only scan of facts/registry.yaml for status: retracted ids. Never edits the registry."""
    reg = repo / "facts" / "registry.yaml"
    if not reg.is_file():
        return "(facts/registry.yaml not found)"
    if budget.spent():
        return "(skipped: time budget exhausted)"
    try:
        if reg.stat().st_size > max_bytes:
            return f"(skipped: registry larger than {max_bytes} bytes)"
    except OSError:
        return "(could not stat facts/registry.yaml)"
    try:
        import yaml   # local import: the hook stays importable without PyYAML
        data = yaml.safe_load(reg.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:   # noqa: BLE001
        return f"(could not parse facts/registry.yaml: {type(exc).__name__})"
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        return "(facts/registry.yaml has no top-level 'facts' list)"
    ids = [f.get("id", "?") for f in facts if isinstance(f, dict) and f.get("status") == "retracted"]
    if not ids:
        return "(none)"
    tail = f" ... ({len(ids) - limit} more)" if len(ids) > limit else ""
    return "\n".join(f"- `{i}`" for i in ids[:limit]) + tail


def build(repo: Path, payload: dict, budget: Budget) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    event = payload.get("hook_event_name") or "(unknown event -- no hook_event_name on stdin)"
    note = ""
    if event == "SessionStart":
        note = (" -- FALLBACK: fired on SessionStart, not PreCompact (either this build lacks the PreCompact hook, "
                "or it is wired and did not fire before this SessionStart)")
    return TEMPLATE.format(utc=now.strftime("%Y-%m-%d %H:%M:%S UTC"), event_name=event, fallback_note=note,
                           last_commit=last_commit(repo, budget), worktree=worktree(repo, budget),
                           touched=touched(repo, budget), specs=specs(repo, budget),
                           retracted=retracted(repo, budget))


def write_checkpoint(repo: Path, ckpt_dir: Path, payload: dict, budget: Budget | None = None,
                     stamp: str | None = None) -> Path:
    budget = budget or Budget(BUDGET_S)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    body = build(repo, payload, budget)
    stamp = stamp or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n = 0
    while True:   # O_EXCL: two events with the same stamp get distinct names instead of clobbering each other
        candidate = ckpt_dir / (f"{stamp}.md" if n == 0 else f"{stamp}-{n}.md")
        try:
            fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            continue
        break
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    return candidate


def main(argv: list[str]) -> int:
    try:
        budget = Budget(BUDGET_S)
        explicit = argv[argv.index("--repo") + 1] if "--repo" in argv else None
        repo = repo_root(explicit, budget)
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        out = write_checkpoint(repo, repo / "ops" / "checkpoints", payload, budget)
        try:
            shown = out.relative_to(repo).as_posix()
        except ValueError:
            shown = str(out)
        print(f"checkpoint written: {shown}")
        return 0
    except Exception:   # noqa: BLE001 -- fail-open: never block compaction or session start
        traceback.print_exc(file=sys.stderr)
        return 0


def _selftest() -> int:
    """Runs both events against a throwaway repo (never the real ops/checkpoints/)."""
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="checkpoint_selftest_"))
    ok = True
    try:
        (tmp / "docs").mkdir()
        (tmp / "docs" / "SPEC_probe.md").write_text("frozen\n", encoding="utf-8")
        (tmp / "docs" / "SPEC_other.draft.md").write_text("draft\n", encoding="utf-8")
        (tmp / "node_modules" / "pkg").mkdir(parents=True)
        (tmp / "node_modules" / "pkg" / "SPEC_vendor.md").write_text("must be pruned\n", encoding="utf-8")
        (tmp / "facts").mkdir()
        (tmp / "facts" / "registry.yaml").write_text(
            "facts:\n  - id: a.kept\n    status: current\n  - id: b.gone\n    status: retracted\n", encoding="utf-8")
        ck = tmp / "ops" / "checkpoints"
        for event in ("PreCompact", "SessionStart"):
            p = write_checkpoint(tmp, ck, {"hook_event_name": event, "trigger": "manual"})
            text = p.read_text(encoding="utf-8")
            checks = [
                (("FALLBACK" in text) == (event == "SessionStart"), "fallback note"),
                ("`docs/SPEC_probe.md`" in text, "frozen spec listed"),
                ("SPEC_other.draft.md" not in text, "draft spec excluded"),
                ("SPEC_vendor.md" not in text, "node_modules pruned"),
                ("`b.gone`" in text and "a.kept" not in text, "retracted id listed, current id not"),
                ("## Next action" in text, "headed blank present"),
            ]
            for passed, name in checks:
                print(("PASS " if passed else "FAIL ") + f"{event}: {name}")
                ok &= passed
        # collision: a FROZEN stamp forces the same name twice; the second gets a suffix, the first is untouched
        a = write_checkpoint(tmp, ck, {"hook_event_name": "PreCompact"}, stamp="20260101T000000Z")
        a_text = a.read_text(encoding="utf-8")
        b = write_checkpoint(tmp, ck, {"hook_event_name": "PreCompact"}, stamp="20260101T000000Z")
        coll = a.name == "20260101T000000Z.md" and b.name == "20260101T000000Z-1.md" \
            and a.read_text(encoding="utf-8") == a_text
        print(("PASS " if coll else "FAIL ") + "collision guard (frozen stamp, first file intact)")
        ok &= coll
        # exhausted budget: still writes a file, says so in the git sections
        p = write_checkpoint(tmp, ck, {"hook_event_name": "PreCompact"}, budget=Budget(0.0))
        text = p.read_text(encoding="utf-8")
        exhausted = text.count("time budget exhausted") >= 3 and "## Next action" in text
        print(("PASS " if exhausted else "FAIL ") + "exhausted budget still writes a checkpoint")
        ok &= exhausted
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main(sys.argv[1:]))
