#!/usr/bin/env python
"""memory_lint.py -- validate a Claude Code per-project memory directory (one fact per file + MEMORY.md index).

Memory lives OUTSIDE the repo (home dir, not git), so it cannot go through
ops/githooks/pre_commit_check.py (that hook only validates staged repo files). This is a
standalone, read-only linter: it never edits memory files, never auto-fixes anything,
and exits non-zero only on a HARD finding (bad YAML / name mismatch / invalid type /
inconsistent status fields, or MEMORY.md itself absent); a file missing FROM the index and a
broken [[link]] are WARN-only
(memory curation style is to leave gaps, not an error class) and never force exit 1.

Checks:
  1. YAML frontmatter parses (--- ... --- block at the top of the file).
  2. `name:` in frontmatter matches the filename stem.
  3. `metadata.type` is one of the valid values (OBSERVED_TYPES below, taken from a full
     the lessons/README.md format: feedback / reference / project / user).
  4. The new optional `status: current | superseded` + `superseded_by: <name>` fields
     this task adds: status, if present, must be one of {current, superseded};
     superseded_by must be present iff status == superseded; superseded_by must name
     an existing memory file (by stem, with or without .md).
  5. MEMORY.md pointers: every `[label](target.md)` link in MEMORY.md (a `#fragment` or a title after the
     path is allowed and ignored) must resolve to a
     file that exists in the dir (HARD-adjacent but reported as WARN per the task spec,
     since a dangling pointer does not corrupt memory, just misdirects a reader).
  6. Memory files that exist but are not referenced anywhere in MEMORY.md (WARN: "not indexed").
  7. `[[wiki-link]]` targets inside memory file bodies that do not resolve to any file in
     the dir (WARN only, per the task).

Usage:
    PYTHONUTF8=1 python tools/memory_lint.py --dir <memory dir> [--json]     (or MEMORY_DIR=<dir>)
Exit code: 0 if no HARD findings, 1 if any HARD finding exists (WARN findings never
affect the exit code).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Claude Code keeps per-project memory under ~/.claude/projects/<project>/memory/. No personal default here:
# pass --dir, or set MEMORY_DIR in the environment.
DEFAULT_MEMORY_DIR = Path(os.environ["MEMORY_DIR"]) if os.environ.get("MEMORY_DIR") else None
OBSERVED_TYPES = {"feedback", "reference", "project", "user"}
VALID_STATUS = {"current", "superseded"}
# The closing delimiter's trailing newline is OPTIONAL: a file whose frontmatter block is the entire
# file content (ends right after the closing `---`, no body, no final newline) is valid YAML-frontmatter
# shape and must not be reported as "no frontmatter found".
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
# Local markdown links: destination = a .md path, then an optional #fragment, then an optional title in any of the
# three CommonMark delimiters ("...", '...', (...)). External destinations (scheme://) are filtered by the caller.
MD_LINK_RE = re.compile(
    r"\[[^\]]*\]\(([^)#\s]+\.md)(?:#[^)\s]*)?(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\)"
)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def load_yaml_block(text: str):
    import yaml  # local import: this tool should still import/argparse cleanly without PyYAML present

    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, "no --- frontmatter block found at top of file"
    try:
        data = yaml.safe_load(m.group(1))
    except Exception as exc:  # noqa: BLE001
        return None, f"YAML does not parse: {exc}"
    if not isinstance(data, dict):
        return None, "frontmatter did not parse to a mapping"
    return data, None


def lint_file(path: Path, all_stems: set) -> list[dict]:
    findings = []

    def hard(msg):
        findings.append({"file": path.name, "severity": "HARD", "msg": msg})

    def warn(msg):
        findings.append({"file": path.name, "severity": "WARN", "msg": msg})

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        hard(f"could not read file: {exc}")
        return findings

    data, err = load_yaml_block(text)
    if err:
        hard(err)
        return findings  # nothing else is checkable without valid frontmatter

    name = data.get("name")
    if name is None:
        hard("frontmatter has no 'name' field")
    elif name != path.stem:
        hard(f"name '{name}' does not match filename stem '{path.stem}'")

    # 'metadata' itself is optional (some feedback-style memory files carry no metadata block at all --
    # e.g. bare `status`/`superseded_by` is still checkable without it), but WHEN a metadata block is
    # present, its 'type' key must be present and a valid string. Distinguish "key present with a bad/null
    # value" from "key absent" with `in metadata` -- `.get("type")` collapses both to None and silently
    # let `metadata: {type: null}` and `metadata: {}` both pass.
    metadata_present = "metadata" in data
    metadata = data.get("metadata")
    if metadata_present:
        if not isinstance(metadata, dict):
            hard(f"'metadata' is present but not a mapping (got {metadata!r})")
        elif "type" not in metadata:
            hard("metadata is present but has no 'type' key")
        else:
            mtype = metadata["type"]
            # mtype can legally be any YAML scalar/collection a sloppy author wrote (a list, a mapping,
            # null, ...); only a bare non-empty string can ever be a VALID type, so anything else is
            # reported as invalid rather than crashing the `in OBSERVED_TYPES` set-membership test on an
            # unhashable value.
            if not isinstance(mtype, str) or mtype not in OBSERVED_TYPES:
                hard(f"metadata.type {mtype!r} is not one of the observed valid types {sorted(OBSERVED_TYPES)}")

    # Same presence-vs-value distinction for status/superseded_by: `"status" in data` with a None value
    # (author wrote `status:` with nothing after it) is reported as an invalid value, not silently treated
    # as "status omitted" the way `.get("status") is not None` would.
    status_present = "status" in data
    status = data.get("status")
    if status_present and (not isinstance(status, str) or status not in VALID_STATUS):
        hard(f"status {status!r} is not one of {sorted(VALID_STATUS)}")
    is_superseded = status_present and status == "superseded"

    superseded_by_present = "superseded_by" in data
    superseded_by = data.get("superseded_by")
    has_target = isinstance(superseded_by, str) and superseded_by.strip() != ""
    if superseded_by_present and not has_target:
        hard(f"superseded_by is present but is not a non-empty string (got {superseded_by!r})")
    if is_superseded and not has_target:
        hard("status: superseded but superseded_by is missing or empty")
    if not is_superseded and superseded_by_present:
        hard(f"superseded_by {superseded_by!r} given but status is not 'superseded' "
            f"(status={status!r}, present={status_present})")
    if has_target and not _resolves(superseded_by, all_stems):
        hard(f"superseded_by '{superseded_by}' does not name an existing memory file")

    # [[wiki-link]] targets -- WARN only, per task spec.
    body = text[len(FRONTMATTER_RE.match(text).group(0)):] if FRONTMATTER_RE.match(text) else text
    for target in WIKILINK_RE.findall(body):
        if not _resolves(target.strip(), all_stems):
            warn(f"[[{target}]] does not resolve to any memory file")

    return findings


def _normalize_target(target: str) -> str:
    """Strip exactly one trailing '.md' (case-insensitive), nothing else. Deliberately NOT
    `Path(target).stem`: stem also eats a non-'.md' dot (e.g. 'topic.v2' -> 'topic', wrongly treating
    '.v2' as an extension) and silently discards any directory component (so a dangling link to
    'missing/foo.md' would wrongly resolve against an unrelated root-level 'foo.md'). All real memory
    files are flat (no subdirectories, no dots in the name), so a target that still contains a
    non-leading '/' or an extra '.' after this strip is, correctly, treated as not matching anything in
    `all_stems`. A leading './' (the one directory prefix that still means "this same directory") is
    stripped so `[x](./foo.md)` resolves the same as `[x](foo.md)`."""
    if target.startswith("./"):
        target = target[2:]
    return target[:-3] if target.lower().endswith(".md") else target


def _resolves(target: str, all_stems: set) -> bool:
    return _normalize_target(target) in all_stems


def lint_memory_index(memory_dir: Path, all_stems: set) -> list[dict]:
    findings = []
    index_path = memory_dir / "MEMORY.md"
    if not index_path.is_file():
        findings.append({"file": "MEMORY.md", "severity": "HARD", "msg": "MEMORY.md not found in memory dir"})
        return findings

    text = index_path.read_text(encoding="utf-8", errors="replace")
    referenced_stems = set()
    for target in MD_LINK_RE.findall(text):
        if "://" in target:          # an external URL that happens to end in .md is not a memory pointer
            continue
        stem = _normalize_target(target.strip())
        referenced_stems.add(stem)
        if stem not in all_stems:
            findings.append({
                "file": "MEMORY.md",
                "severity": "WARN",
                "msg": f"points to missing file '{target}'",
            })

    for stem in sorted(all_stems - referenced_stems - {"MEMORY"}):
        findings.append({
            "file": f"{stem}.md",
            "severity": "WARN",
            "msg": "not referenced anywhere in MEMORY.md (not indexed)",
        })

    return findings


def run(memory_dir: Path) -> dict:
    if not memory_dir.is_dir():
        return {
            "memory_dir": str(memory_dir),
            "error": f"memory dir not found: {memory_dir}",
            "findings": [],
            "hard_count": 0,
            "warn_count": 0,
        }

    md_files = sorted(p for p in memory_dir.glob("*.md") if p.name != "MEMORY.md")
    # "MEMORY" only counts as a resolvable stem if MEMORY.md actually exists -- otherwise a dangling
    # `[[MEMORY]]` or `superseded_by: MEMORY` would wrongly resolve even though the file is missing
    # (caught separately, as a HARD finding, by lint_memory_index below).
    all_stems = {p.stem for p in md_files}
    if (memory_dir / "MEMORY.md").is_file():
        all_stems.add("MEMORY")

    findings: list[dict] = []
    for p in md_files:
        findings.extend(lint_file(p, all_stems))
    findings.extend(lint_memory_index(memory_dir, all_stems))

    hard_count = sum(1 for f in findings if f["severity"] == "HARD")
    warn_count = sum(1 for f in findings if f["severity"] == "WARN")
    return {
        "memory_dir": str(memory_dir),
        "files_checked": len(md_files),
        "findings": findings,
        "hard_count": hard_count,
        "warn_count": warn_count,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", type=Path, default=DEFAULT_MEMORY_DIR, help="memory directory to lint")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--selftest", action="store_true", help="run against a throwaway fixture dir")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.dir is None:
        ap.error("no memory dir: pass --dir <path> or set MEMORY_DIR")

    result = run(args.dir)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"memory_lint: {result.get('memory_dir')}")
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            return 1
        print(f"  files checked: {result['files_checked']}")
        for f in result["findings"]:
            print(f"  [{f['severity']}] {f['file']}: {f['msg']}")
        print(f"  -- {result['hard_count']} HARD, {result['warn_count']} WARN")
    return 1 if result.get("hard_count", 0) or "error" in result else 0


def _selftest() -> int:
    """Builds a throwaway memory dir with one of each finding type and checks the linter catches them."""
    import shutil
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="memlint_fixture_"))
    try:
        (d / "good.md").write_text(
            "---\nname: good\nmetadata:\n  type: reference\n---\nbody with [[nowhere]] link.\n",
            encoding="utf-8",
        )
        (d / "bad-yaml.md").write_text("---\nname: [unclosed\n---\nbody\n", encoding="utf-8")
        (d / "name-mismatch.md").write_text(
            "---\nname: totally-different\nmetadata:\n  type: project\n---\nbody\n", encoding="utf-8"
        )
        (d / "bad-type.md").write_text(
            "---\nname: bad-type\nmetadata:\n  type: nonsense\n---\nbody\n", encoding="utf-8"
        )
        (d / "bad-status.md").write_text(
            "---\nname: bad-status\nmetadata:\n  type: feedback\nstatus: superseded\n---\nno superseded_by\n",
            encoding="utf-8",
        )
        (d / "unindexed.md").write_text(
            "---\nname: unindexed\nmetadata:\n  type: feedback\n---\nnot in MEMORY.md\n", encoding="utf-8"
        )
        (d / "fragonly.md").write_text(
            "---\nname: fragonly\nmetadata:\n  type: reference\n---\nindexed only via a #fragment link\n", encoding="utf-8"
        )
        (d / "MEMORY.md").write_text(
            "# idx\n- [good](good.md)\n- [missing](missing.md)\n- [frag](fragonly.md#section)\n"
            "- [gone](gone.md#x \"title\")\n- [gone2](gone2.md 'single')\n- [gone3](gone3.md#y (paren))\n"
            "- [ext](https://example.org/page.md)\n", encoding="utf-8"
        )

        result = run(d)
        msgs = [f["msg"] for f in result["findings"]]
        checks = [
            ("YAML does not parse" in " ".join(msgs), "bad-yaml not caught"),
            (any("does not match filename stem" in m for m in msgs), "name mismatch not caught"),
            (any("not one of the observed valid types" in m for m in msgs), "bad type not caught"),
            (any("superseded_by is missing or empty" in m for m in msgs), "bad status not caught"),
            (any("not referenced anywhere in MEMORY.md" in m for m in msgs), "unindexed not caught"),
            (any("points to missing file" in m for m in msgs), "dangling MEMORY.md pointer not caught"),
            (any("does not resolve to any memory file" in m for m in msgs), "dangling [[link]] not caught"),
            (any("gone.md" in m for m in msgs), "dangling pointer with #fragment and title not caught"),
            (any("gone2.md" in m for m in msgs), "dangling pointer with a single-quoted title not caught"),
            (any("gone3.md" in m for m in msgs), "dangling pointer with a parenthesized title not caught"),
            (not any("page.md" in m for m in msgs), "external .md URL wrongly treated as a memory pointer"),
            (not any(f["file"] == "fragonly.md" and "not indexed" in f["msg"] for f in result["findings"]),
             "file indexed only via a #fragment link wrongly reported as unindexed"),
        ]
        ok = True
        for passed, label in checks:
            print(("PASS" if passed else "FAIL"), label)
            ok = ok and passed
        return 0 if ok else 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
