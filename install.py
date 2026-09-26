#!/usr/bin/env python
"""Copy kit/ into a target repo root. Never overwrites an existing file: collisions are listed so you can
merge them by hand (e.g. an existing CLAUDE.md or .claude/settings.json).

  python install.py /path/to/repo             copy
  python install.py /path/to/repo --dry-run   list what would be copied / skipped
  python install.py --selftest                offline checks
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent / "kit"


def unsafe(target: Path, rel: Path) -> bool:
    """True if the destination, or any parent inside the target, is a symlink (it could redirect the write
    outside the repo), including a dangling one."""
    p = target / rel
    while p != target:
        if p.is_symlink():
            return True
        p = p.parent
    return False


def plan(kit: Path, target: Path) -> tuple[list[Path], list[Path]]:
    new, clash = [], []
    for src in sorted(p for p in kit.rglob("*") if p.is_file()):
        rel = src.relative_to(kit)
        dest = target / rel
        (clash if (dest.exists() or dest.is_symlink() or unsafe(target, rel)) else new).append(rel)
    return new, clash


def copy_new(src: Path, dest: Path) -> bool:
    """Exclusive create: a file that appeared since planning is never overwritten."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(dest, "xb") as out:
            out.write(src.read_bytes())
    except FileExistsError:
        return False
    shutil.copymode(src, dest)
    return True


def install(kit: Path, target: Path, dry: bool) -> int:
    if not target.is_dir():
        print(f"target is not a directory: {target}")
        return 2
    new, clash = plan(kit, target)
    for rel in list(new):
        if not dry and not copy_new(kit / rel, target / rel):
            new.remove(rel)
            clash.append(rel)
            continue
        print(f"  {'would copy' if dry else 'copied'}  {rel.as_posix()}")
    for rel in clash:
        print(f"  SKIPPED (exists, merge by hand)  {rel.as_posix()}   <- kit/{rel.as_posix()}")
    print(f"\n{len(new)} file(s) {'to copy' if dry else 'copied'}, {len(clash)} skipped.")
    if dry:
        return 0
    hook = target / "ops" / "githooks" / "pre-commit"
    if hook.exists() and os.name != "nt":
        hook.chmod(hook.stat().st_mode | 0o111)       # zip extraction drops the executable bit on POSIX
    try:
        is_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=target,
                                capture_output=True, text=True).returncode == 0
    except OSError:                                   # git not installed
        is_git = False
    print("\nNext steps (README.md 'Install'):")
    print("  1. grep -rn FILL CLAUDE.md docs .claude     and fill the markers")
    print("  2. append gitignore.snippet to .gitignore; if there is no .env yet: cp .env.example .env "
          "(otherwise copy only the missing keys into your existing .env)")
    if is_git:
        print("  3. git config core.hooksPath ops/githooks   (per clone)")
    else:
        print("  3. target is not a git repo: `git init`, then git config core.hooksPath ops/githooks")
    print("  4. in Claude Code, from the repo root: /plugin install hookify@claude-plugins-official")
    print("  5. set VERIFY_PROVIDER in .env; python tools/verify.py --selftest")
    print("  6. probe every guard (README 'Verify the install')")
    return 0


def selftest() -> int:
    import tempfile
    fails = []

    def check(label, ok):
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            fails.append(label)

    root = Path(tempfile.mkdtemp(prefix="install_selftest_"))
    kit, target = root / "kit", root / "repo"
    (kit / ".claude").mkdir(parents=True)
    (kit / "CLAUDE.md").write_text("kit version", encoding="utf-8")
    (kit / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    target.mkdir()
    (target / "CLAUDE.md").write_text("THEIR version", encoding="utf-8")
    new, clash = plan(kit, target)
    check("existing file detected as a clash", [c.as_posix() for c in clash] == ["CLAUDE.md"])
    check("new file planned", [n.as_posix() for n in new] == [".claude/settings.json"])
    install(kit, target, dry=True)
    check("dry run writes nothing", not (target / ".claude").exists())
    install(kit, target, dry=False)
    check("new file copied", (target / ".claude" / "settings.json").exists())
    check("existing file NOT overwritten", (target / "CLAUDE.md").read_text(encoding="utf-8") == "THEIR version")
    (kit / "late.md").write_text("kit", encoding="utf-8")
    (target / "late.md").write_text("appeared after planning", encoding="utf-8")
    check("exclusive create refuses a file that appeared late", copy_new(kit / "late.md", target / "late.md") is False
          and (target / "late.md").read_text(encoding="utf-8") == "appeared after planning")
    print(f"selftest: {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if len(args) != 1:
        print(__doc__)
        sys.exit(2)
    sys.exit(install(KIT, Path(args[0]).resolve(), "--dry-run" in sys.argv))
