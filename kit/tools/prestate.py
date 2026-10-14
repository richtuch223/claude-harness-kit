#!/usr/bin/env python
"""prestate.py -- pre-statement (pre-registration) helper for research probes.

ACCIDENTAL-ERROR GUARD, NOT A TRUST BOUNDARY. There is no git hook here and no enforcement: a
determined author can still rewrite the log, point at a different log, or never run this tool at
all. What it buys is cheap: a content-addressed, append-only record of what rule was frozen when,
so an honest mistake (restating a threshold from memory while writing the implementation, losing
the original text, forgetting an amendment happened) becomes visible to a reviewer instead of
silent. Motivation: a design review of an earlier sketch of this idea found that a rule can be
silently restated UPSTREAM of any code gate (the code gate only ever sees the script, never the
original prose), that amendments can be excluded from the hash chain, and that a broken hash
chain would lose the frozen copy forever. This tool fixes exactly those three things and
nothing else: content-addressed storage (so a frozen copy can never silently disappear -- it is
named by its own hash), an append-only log with an explicit AMEND record (so an amendment is a
first-class, reason-carrying entry, not a second freeze that erases the first), and a `show`/
`conform` pair that always compares against ROOT, not just the latest version, so a skeptic sees
every change made since the original.

Subcommands
  freeze  RULE_FILE --log LOG [--new-root]
  amend   NEW_RULE_FILE --log LOG --reason "TEXT"
  show    --log LOG
  verify  --log LOG (--text-file F | --script S --var NAME)
  conform --log LOG --script S [--extra F ...] [--model M] [--provider P] [--dry-run]
  prestate.py --selftest

Importable: normalize(), sha256_hex(), freeze(), amend(), current_sha(), verify_text().
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ----------------------------------------------------------------------------------- normalization

def normalize(text: str) -> str:
    """CRLF -> LF, strip trailing whitespace per line, strip outer blank lines. Deterministic:
    always ends with exactly one trailing '\\n' unless the normalized body is empty."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    body = "\n".join(lines)
    return body + "\n" if body else ""


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_file(path: Path) -> str:
    raw = path.read_bytes().decode("utf-8")
    return normalize(raw)


# ----------------------------------------------------------------------------------- repo root

def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for p in (cur, *cur.parents):
        if (p / ".git").exists():
            return p
    return start.resolve()


# ----------------------------------------------------------------------------------- log line (de)serialization
# Line shapes (one per PRESTATE entry, append-only, never rewritten):
#   PRESTATE v1 kind=ROOT  sha256=<hex> utc=<iso Z> file=<relpath>
#   PRESTATE v1 kind=AMEND sha256=<hex> root=<hex> prev=<hex> utc=<iso Z> reason="<escaped>" diff=<relpath>
# The bareword alternative must NOT start with an unescaped quote : without `[^\s"]` up front, a field truncated mid-quote (e.g. a log cut off
# after `diff="prestate_frozen/partial`) backtracks onto `\S+` and is silently accepted as a
# present, nonempty value instead of failing to match at all -- which would otherwise correctly
# trip the "missing required field" check.
_TOKEN_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|[^\s"]\S*)')
# Every character `str.splitlines()` treats as a line break  -- parse_log uses splitlines(), so a --reason guard that only checks \n/\r misses
# \v, \f, \x1c-\x1e, and the Unicode separators.
_LINE_BREAK_CHARS = re.compile("[\r\n\v\f\x1c\x1d\x1e\x85  ]")


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def _format_kv(key: str, val: str) -> str:
    # Quote on ANY whitespace character, not just ASCII space/tab : Python's `\s` (used by the bareword regex below) matches Unicode whitespace
    # too (e.g. U+00A0 NBSP), so a value containing NBSP but no plain space/tab/quote used to be
    # written unquoted and then silently truncated at the first NBSP on the next parse.
    needs_quote = (val == "") or ('"' in val) or bool(re.search(r"\s", val))
    return f'{key}="{_escape(val)}"' if needs_quote else f"{key}={val}"


def format_line(kind: str, fields: dict) -> str:
    parts = ["PRESTATE", "v1", f"kind={kind}"]
    for k, v in fields.items():
        parts.append(_format_kv(k, str(v)))
    return " ".join(parts)


def parse_line(line: str) -> dict | None:
    """Returns None only for a line that is not a PRESTATE record at all (blank, or unrelated
    text). A line that LOOKS like a PRESTATE record but fails to parse into a complete, valid
    ROOT/AMEND entry returns a {"kind": "MALFORMED"} marker instead of silently vanishing (
    review round 4, finding 3, 2026-10-05): a record truncated down to bare `PRESTATE v1` used to
    return None and disappear from every downstream check as though it had never existed."""
    s = line.strip()
    if not s:
        return None
    if not s.startswith("PRESTATE"):
        return None
    toks = s.split(None, 2)
    if len(toks) < 3 or toks[0] != "PRESTATE" or toks[1] != "v1":
        # The version token is validated, not just captured : `PRESTATE v2 ...` or a corrupted `PRESTATE garbage ...` used to parse fine
        # under v1 field semantics instead of being flagged as unsupported/malformed.
        return {"kind": "MALFORMED", "_raw": s}
    rest = toks[2]
    out: dict = {"_version": toks[1]}
    seen: set = set()
    pos = 0
    for m in _TOKEN_RE.finditer(rest):
        gap = rest[pos:m.start()]
        # Require (near-)full consumption of `rest`, not just that SOME tokens matched somewhere
        # in it : two complete records accidentally
        # joined onto one physical line (e.g. by a corrupted/pre-existing file missing a newline)
        # leave non-whitespace text between fields -- that must be treated as corruption, not
        # silently skipped by `findall`.
        if gap.strip():
            return {"kind": "MALFORMED", "_raw": s}
        key = m.group(1)
        if key in seen:
            # A repeated key is itself a corruption signal (most often: two whole records joined
            # onto one line, both contributing a `kind=`/`sha256=`/etc.) -- "last value wins" would
            # silently pick one and hide the other instead of flagging it.
            return {"kind": "MALFORMED", "_raw": s}
        seen.add(key)
        raw = m.group(2)
        out[key] = _unescape(raw[1:-1]) if (raw.startswith('"') and raw.endswith('"')) else raw
        pos = m.end()
    if rest[pos:].strip():
        # Leftover text after the last recognized field -- also corruption, not to be ignored.
        return {"kind": "MALFORMED", "_raw": s}
    if out.get("kind") not in ("ROOT", "AMEND"):
        return {"kind": "MALFORMED", "_raw": s}
    return out


def parse_log(log: Path) -> list[dict]:
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        e = parse_line(line)
        if e is not None:
            out.append(e)
    return out


def active_chain(entries: list[dict]) -> list[dict]:
    """Entries from (and including) the LAST kind=ROOT line onward. --new-root starts a fresh
    study in the same append-only file; older studies stay in the file but are no longer active."""
    last_root = None
    for i, e in enumerate(entries):
        if e.get("kind") == "ROOT":
            last_root = i
    if last_root is None:
        return []
    return entries[last_root:]


def _segments(entries: list[dict]) -> list[list[dict]]:
    """Split the whole file's entries into one list per study: everything from one ROOT line up to
    (not including) the next ROOT line. The LAST segment is what `active_chain` returns. Any
    entries before the first ROOT form a leading pseudo-segment (should normally be empty)."""
    segs: list[list[dict]] = []
    current: list[dict] = []
    for e in entries:
        if e.get("kind") == "ROOT":
            if current:
                segs.append(current)
            current = [e]
        else:
            current.append(e)
    if current:
        segs.append(current)
    return segs


def append_line(log: Path, line: str) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        existing = log.read_bytes()
        if existing and not existing.endswith(b"\n"):
            # Guard against concatenating onto an externally-truncated last line : without this, a log whose last line lost its
            # trailing newline (editor save, crash mid-write) would have the NEXT appended
            # record glued onto the same physical line, and parse_line's last-key-wins behavior
            # would then silently overwrite that prior record's fields.
            with open(log, "ab") as fh:
                fh.write(b"\n")
    with open(log, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------------- content-addressed storage

def frozen_dir(log: Path) -> Path:
    return log.parent / "prestate_frozen"


def store_content_addressed(log: Path, normalized_text: str, sha: str) -> Path:
    """Write <frozen_dir>/<sha>.txt if absent. If present, re-hash it and refuse (die) on a
    mismatch -- that would mean the frozen copy was tampered with (its name is supposed to BE
    its hash)."""
    d = frozen_dir(log)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha}.txt"
    data = normalized_text.encode("utf-8")
    if p.exists():
        existing = p.read_bytes()
        if hashlib.sha256(existing).hexdigest() != sha:
            die(f"integrity error: existing frozen copy {p} does not re-hash to its own "
                f"filename -- it was modified after being written. Refusing to overwrite it.")
        # same sha already on disk: fine, nothing to do (content-addressed idempotency).
    else:
        p.write_bytes(data)
    return p


def read_frozen(log: Path, sha: str) -> str:
    """Read a content-addressed frozen copy and re-hash it before returning : `amend` and `show` both call this to build a diff / display text, and
    neither used to check integrity first, so a transiently-tampered ROOT could get baked into a
    permanent diff file even if the tampering was later reverted. Verifying HERE, at the one choke
    point every reader goes through, closes that for every caller at once instead of requiring each
    call site to remember to check."""
    p = frozen_dir(log) / f"{sha}.txt"
    if not p.exists():
        die(f"frozen copy missing: {p} (sha256={sha})")
    data = p.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        die(f"integrity error: frozen copy {p} re-hashes to {got}, not {sha} -- refusing to use it.")
    return data.decode("utf-8")


def check_integrity(log: Path) -> list[str]:
    """Every sha256 ever logged (ROOT or AMEND, across the whole file, not just the active chain)
    must have a frozen copy on disk that re-hashes to its own filename. Also surfaces any
    MALFORMED record found ANYWHERE in the file , not
    just one inside the active chain -- `parse_line` no longer silently drops a line that looks
    like a PRESTATE record but fails to parse. AND checks required fields on EVERY ROOT/AMEND
    record in the whole file, not just the active chain : `check_chain_links` only ever walks the active chain, so a record missing its
    sha256 entirely (e.g. truncated to bare `PRESTATE v1 kind=AMEND`) sitting in an OLDER,
    superseded study (before a later --new-root) was invisible to both checks -- this one used to
    `continue` past it for lacking a hash to verify, rather than flagging the missing hash itself."""
    problems = []
    for e in parse_log(log):
        if e.get("kind") == "MALFORMED":
            problems.append(f"MALFORMED PRESTATE record: {e.get('_raw', '?')!r}")
            continue
        kind = e.get("kind")
        required = ROOT_REQUIRED_FIELDS if kind == "ROOT" else AMEND_REQUIRED_FIELDS if kind == "AMEND" else None
        if required is not None:
            missing = [f for f in required if not e.get(f)]
            if missing:
                problems.append(f"{kind} record sha256={e.get('sha256', '?')} is missing required "
                                 f"field(s): {', '.join(missing)} (whole-file scan)")
        sha = e.get("sha256")
        if not sha:
            continue
        p = frozen_dir(log) / f"{sha}.txt"
        if not p.exists():
            problems.append(f"MISSING frozen copy for sha256={sha} (expected {p})")
            continue
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        if got != sha:
            problems.append(f"TAMPERED frozen copy {p}: re-hashes to {got}, not {sha}")
    return problems


ROOT_REQUIRED_FIELDS = ("sha256", "seq", "utc", "file")
AMEND_REQUIRED_FIELDS = ("sha256", "seq", "root", "prev", "utc", "reason", "diff")


def check_chain_links(log: Path) -> list[str]:
    """Walk the active chain's records and confirm (1) every required field for that record's
    kind is present -- a line truncated after `prev=<hash>`  still has everything a bare sha/root/prev check inspects, so completeness must be
    checked explicitly, not inferred from the fields that happen to parse -- (2) each AMEND's
    declared `root` matches the chain's ROOT sha and its declared `prev` matches the sha of the
    entry immediately before it (catches a deleted or reordered log line that `check_integrity`
    alone would miss, since file-level hashes can all still be valid while the chain between them
    is broken), and (3) `seq` increments by exactly 1 per record : content-hash links alone cannot detect deleting a PAIR of amendments whose net
    effect returns to a content state that was already on the chain (e.g. ROOT A -> AMEND B ->
    AMEND-back-to-A -> AMEND C: the "back to A" amendment's sha IS root_sha by construction, so
    deleting it and B still leaves AMEND C's prev==root_sha looking perfectly valid). A plain
    monotonic sequence counter is immune to that: it tracks RECORD COUNT, not content, so deleting
    any INTERNAL record leaves a gap regardless of what the surrounding content happens to hash to.

    ACCEPTED, UNFIXABLE-WITHOUT-AN-EXTERNAL-ANCHOR LIMITATION : deleting the LOG'S OWN TRAILING record(s) outright (not an internal one) is
    undetectable by this or any purely-local, append-only log format: a file with its last N lines
    removed is byte-for-byte indistinguishable from a file that never had them, whether the
    "record" is this tool's PRESTATE line, a git commit object, or any other self-contained
    append-only structure. Closing this requires an anchor OUTSIDE the file itself -- e.g.
    committing the log to git (so a truncation shows up as an unstaged diff against a committed
    tip) or periodically publishing its CURRENT sha somewhere else. The task that commissioned
    this tool explicitly scoped out a git hook / trust boundary ("ACCIDENTAL-ERROR GUARD... NOT A
    TRUST BOUNDARY... no git hook"), so this is accepted rather than patched: commit prestate_log.txt
    (and prestate_frozen/) to git as a matter of practice and a truncated tail becomes visible in
    `git diff` / `git log -p` on that path, which is a far stronger anchor than anything this
    tool could build on its own.

    Validates EVERY segment in the file (every ROOT's full history, not just the active chain) --
     review round 6, finding 1, 2026-10-05: a prior design validated only the active chain, so
    a broken prev-link or seq-gap in an OLDER study became invisible once `--new-root` started a
    new one (`freeze(..., new_root=True)` never checks prior history, and `check_integrity`'s
    whole-file scan checks content hashes and required fields but not the LINKS between records).
    Starting a legitimate new study must not be a way to make an already-broken study's corruption
    stop being reported."""
    entries = parse_log(Path(log))
    problems: list[str] = []
    for seg in _segments(entries):
        if seg[0].get("kind") != "ROOT":
            problems.append(f"{len(seg)} record(s) found before any ROOT line (orphaned data?): "
                             f"kinds={[e.get('kind') for e in seg]}")
            continue
        problems.extend(_validate_segment(seg))
    return problems


def _validate_segment(chain: list[dict]) -> list[str]:
    """Validate ONE study's segment (a ROOT followed by zero or more AMENDs): required fields,
    root/prev linkage, and the seq counter. See `check_chain_links` for why each check exists."""
    problems: list[str] = []
    root = chain[0]
    missing_root = [f for f in ROOT_REQUIRED_FIELDS if not root.get(f)]
    if missing_root:
        problems.append(f"ROOT sha256={root.get('sha256', '?')} is missing required field(s): "
                         f"{', '.join(missing_root)} (truncated/corrupted log line?)")
    if not root.get("sha256"):
        # Without the anchor hash there is nothing to compare any AMEND's root/prev against
        # .
        return problems
    root_sha = root["sha256"]
    prev = root_sha

    def _seq_int(e: dict) -> int | None:
        try:
            return int(e.get("seq", ""))
        except ValueError:
            return None

    root_seq = _seq_int(root)
    if root.get("seq") is not None and root_seq != 0:
        problems.append(f"ROOT seq={root.get('seq')!r} is not 0 (non-integer or tampered)")
    expected_seq = 0
    for e in chain[1:]:
        sha = e.get("sha256", "?")
        if e.get("kind") != "AMEND":
            problems.append(f"unexpected kind={e.get('kind')!r} (sha256={sha}) in a chain after "
                             f"ROOT {root_sha} -- expected AMEND")
            continue
        missing = [f for f in AMEND_REQUIRED_FIELDS if not e.get(f)]
        if missing:
            problems.append(f"AMEND sha256={sha} is missing required field(s): {', '.join(missing)} "
                             f"(truncated/corrupted log line?)")
        if e.get("root") != root_sha:
            problems.append(f"AMEND sha256={sha} declares root={e.get('root')}, but its chain's "
                             f"ROOT is {root_sha}")
        if e.get("prev") != prev:
            problems.append(f"AMEND sha256={sha} declares prev={e.get('prev')}, but the previous "
                             f"entry in the chain is {prev} -- history may be broken or reordered")
        expected_seq += 1
        got_seq = _seq_int(e)
        if e.get("seq") is not None and got_seq != expected_seq:
            problems.append(f"AMEND sha256={sha} declares seq={e.get('seq')!r}, expected {expected_seq} "
                             f"-- one or more records are missing/reordered/duplicated (a content-hash "
                             f"match alone cannot catch this if the deleted records' net content "
                             f"change happens to cancel out)")
        prev = sha
    return problems


# ----------------------------------------------------------------------------------- die / errors
def die(msg: str, code: int = 2) -> "NoReturn":  # noqa: F821 (py<3.11 has no NoReturn builtin import issue)
    print(f"prestate: {msg}", file=sys.stderr)
    sys.exit(code)


# ----------------------------------------------------------------------------------- core API (importable)

def freeze(rule_file: Path, log: Path, new_root: bool = False, repo_root: Path | None = None) -> dict:
    """Freeze RULE_FILE as the ROOT of LOG's active study. Returns {sha, frozen_path, warned}."""
    rule_file = Path(rule_file)
    log = Path(log)
    # repo_root defaults off CWD, not the log's own location: the log (and its prestate_frozen/
    # sibling) need not live inside the repo tree (e.g. a scratch test dir), but this tool is run
    # from the repo root like every other src/tools/ script, and RULE_FILE normally does live in it.
    repo_root = repo_root or find_repo_root(Path.cwd())
    entries = parse_log(log)
    has_root = any(e.get("kind") == "ROOT" for e in entries)
    warned = False
    if has_root and not new_root:
        die(f"LOG already has a ROOT line ({log}). Pass --new-root to start a different study "
            f"(the old study's history is kept, not erased), or use `amend` to change the "
            f"current study's rule.")
    if has_root and new_root:
        print(f"prestate: WARNING -- starting a NEW ROOT in {log}; the previous study's entries "
              f"stay in the file (append-only) but are no longer the active chain.", file=sys.stderr)
        warned = True
    normalized = normalize_file(rule_file)
    sha = sha256_hex(normalized)
    frozen_path = store_content_addressed(log, normalized, sha)
    try:
        rel = os.path.relpath(rule_file.resolve(), repo_root)
    except ValueError:
        rel = str(rule_file.resolve())
    if _LINE_BREAK_CHARS.search(rel):
        # Same hazard as a multi-line --reason, but for the `file=` field : a path containing a line-break character (e.g. an exotic
        # filename with U+2028) would get quoted by _format_kv but still corrupt the one-
        # record-per-line format when parse_log's splitlines() reads it back.
        die(f"refusing to freeze: the rule file's path contains a line-break character ({rel!r}); "
            f"rename it or pass a path without one.")
    append_line(log, format_line("ROOT", {"sha256": sha, "seq": 0, "utc": utc_now(), "file": rel}))
    return {"sha": sha, "frozen_path": str(frozen_path), "warned": warned}


def amend(new_rule_file: Path, log: Path, reason: str, allow_noop: bool = False) -> dict:
    """Amend LOG's active study's current rule to NEW_RULE_FILE. Returns {sha, prev, root,
    diff_path}."""
    new_rule_file = Path(new_rule_file)
    log = Path(log)
    # Reject every line-break character `str.splitlines()` recognizes, not just \n/\r : parse_log splits the log file with splitlines(), so a
    # reason containing e.g. U+2028 LINE SEPARATOR would pass a \n/\r-only guard and still corrupt
    # the one-record-per-line format on the next read.
    if _LINE_BREAK_CHARS.search(reason):
        die("--reason must be a single line: it contains a line-break character "
            "(not just \\n/\\r -- also \\v, \\f, \\x1c-\\x1e, U+2028/U+2029/U+0085 all split a "
            "line when the log is re-read). Put detail in a linked doc and reference it here.")
    if len(reason.strip()) < 15:
        die(f"--reason must be >= 15 characters (got {len(reason.strip())}): explain WHY, not just THAT.")
    entries = parse_log(log)
    chain = active_chain(entries)
    if not chain:
        die(f"no ROOT in {log} -- run `freeze` first.")
    # Refuse to extend an already-broken OR already-untrustworthy chain : check BOTH the record links (a prior AMEND
    # with a wrong `prev`/`root`/`seq` or a truncated record) AND file-level integrity (a missing
    # or corrupted frozen copy of a PRIOR amendment, not just ROOT -- `read_frozen` below only
    # ever touches ROOT's frozen file, so without this upfront check a corrupted intermediate
    # amendment's content would go unnoticed here even though `verify`/`conform` would later
    # reject the same history).
    problems = check_integrity(log) + check_chain_links(log)
    if problems:
        die("refusing to amend -- the active chain is already broken or untrustworthy:\n  " + "\n  ".join(problems))
    root_sha = chain[0]["sha256"]
    prev_entry = chain[-1]
    prev_sha = prev_entry["sha256"]
    prev_seq = int(prev_entry.get("seq", 0))
    root_text = read_frozen(log, root_sha)
    normalized = normalize_file(new_rule_file)
    new_sha = sha256_hex(normalized)
    # No-op guard (2026-10-14): two studies each logged an AMEND whose content equalled the current head,
    # because a text replace had failed silently and `amend` ran anyway. A record that changes nothing is
    # noise in the audit trail and hides the failed edit (lessons/amend-after-a-failed-edit-records-a-no-op).
    if new_sha == prev_sha and not allow_noop:
        die(f"refusing to amend -- the new rule text is identical to the current head ({prev_sha[:12]}). "
            "The edit before this amend probably did not apply (failed text replace?). Chain the edit and "
            "the amend in one command, or pass --allow-noop to record it anyway.")
    store_content_addressed(log, normalized, new_sha)
    diff_lines = list(difflib.unified_diff(
        root_text.splitlines(keepends=True), normalized.splitlines(keepends=True),
        fromfile=f"ROOT:{root_sha[:12]}", tofile=f"AMEND:{new_sha[:12]}"))
    # Filename keyed on (root_sha, new_sha), not new_sha alone :
    # two different ROOTs amended to textually-identical content would otherwise collide on one
    # file and silently overwrite each other's ROOT->NEW diff.
    diff_rel = f"prestate_frozen/{root_sha[:12]}_{new_sha}.diff_vs_root.txt"
    (log.parent / diff_rel).write_text("".join(diff_lines) if diff_lines else
                                       "(no textual difference from ROOT)\n", encoding="utf-8")
    append_line(log, format_line("AMEND", {
        "sha256": new_sha, "seq": prev_seq + 1, "root": root_sha, "prev": prev_sha, "utc": utc_now(),
        "reason": reason.strip(), "diff": diff_rel,
    }))
    return {"sha": new_sha, "prev": prev_sha, "root": root_sha, "diff_path": str(log.parent / diff_rel)}


def current_sha(log: Path) -> str | None:
    """Returns None (never raises) when there is no active chain, or when its last record is
    malformed/incomplete and lacks a sha256 ."""
    chain = active_chain(parse_log(Path(log)))
    return chain[-1].get("sha256") if chain else None


def verify_text(text: str, log: Path) -> str:
    """Returns 'OK' (matches current), 'STALE' (matches an earlier logged version), or
    'NOMATCH'. Does not itself run the integrity check (see check_integrity) -- callers should
    also run that (cmd_verify does); this function only guards itself against crashing ."""
    log = Path(log)
    entries = parse_log(log)
    chain = active_chain(entries)
    if not chain:
        return "NOMATCH"
    sha = sha256_hex(normalize(text))
    cur = chain[-1].get("sha256")
    if cur is not None and sha == cur:
        return "OK"
    if any(e.get("sha256") == sha for e in chain):
        return "STALE"
    return "NOMATCH"


def _target_names(target: ast.expr):
    """Yield every Name id bound by an assignment target, recursing through Tuple/List/Starred
    so `a, RULE = (...)` is recognized as touching `RULE` even though it isn't itself a Name."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _target_names(elt)


_DEF_LIKE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_skip_defs(node):
    """Like ast.walk, but never descends INTO a function/class/lambda BODY -- those don't execute
    merely by being defined, and this tool never executes code to learn whether/when they run. The
    def/class/lambda NODE ITSELF is still yielded : a
    prior version also skipped yielding it, so a NESTED `def RULE(): ...` or `class RULE: ...`
    (inside an if/for/while/try/with block) was invisible even to the name check in
    `_other_binding_lineno` -- only a top-level one (checked separately, directly against
    `tree.body`) was caught. The def/class statement's OWN name binding happens at module-execution
    time regardless of nesting depth; only its BODY's execution is conditional/deferred."""
    yield node
    if isinstance(node, _DEF_LIKE):
        return  # already yielded above; never descend into what it defines
    for child in ast.iter_child_nodes(node):
        yield from _walk_skip_defs(child)


def _other_binding_lineno(tree: ast.Module, var: str):
    """Find the line of ANY binding to `var` outside a simple, direct ast.Module.body statement:
    a for-loop target, a `with ... as`/`except ... as` target, a comprehension target, a walrus
    (`:=`) target, or an Assign/AnnAssign/AugAssign that is NESTED inside an if/for/while/try/with
    block rather than a direct top-level statement. Returns None if there are none.

    This is the fail-closed half of extraction : a prior design tried to OPTIMISTICALLY RESOLVE a nested
    assignment (e.g. one branch of an `if`) as though it were an ordinary candidate literal, which
    is unsound -- without executing the script there is no way to know which branch of an `if`
    actually runs, so `if False: RULE = "..."` was wrongly treated the same as an unconditional
    rebind. The correct, SAFE answer to "can this rebind `var`" when the rebind is not a direct
    top-level statement is "treat it as invalidating", never "treat it as authoritative"."""
    top_ids = {id(n) for n in tree.body}
    for node in _walk_skip_defs(tree):
        if node is tree:
            continue
        lineno = getattr(node, "lineno", "?")
        if isinstance(node, (ast.For, ast.AsyncFor)):
            if var in set(_target_names(node.target)):
                return lineno
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None and var in set(_target_names(item.optional_vars)):
                    return lineno
        elif isinstance(node, ast.ExceptHandler):
            if node.name == var:
                return lineno
        elif isinstance(node, ast.comprehension):
            if var in set(_target_names(node.target)):
                return lineno
        elif isinstance(node, ast.NamedExpr):  # walrus `var := ...`
            if isinstance(node.target, ast.Name) and node.target.id == var:
                return lineno
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A NESTED def/class named `var` (not a direct top-level statement, which the primary
            # loop already handles) still binds `var` the moment the module runs, regardless of
            # whether its BODY ever executes .
            if id(node) not in top_ids and node.name == var:
                return lineno
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and id(node) not in top_ids:
            if isinstance(node, ast.Assign):
                names = {n for t in node.targets for n in _target_names(t)}
            else:
                names = set(_target_names(node.target)) if isinstance(node.target, ast.Name) else set()
                if isinstance(node.target, ast.Name):
                    names = {node.target.id}
            if var in names:
                return lineno
    return None


def extract_script_var(script: Path, var: str) -> str:
    """Find a MODULE-LEVEL assignment `var = "..."` or `var: T = "..."` via `ast`, WITHOUT
    executing the script. Only a DIRECT top-level `ast.Module.body` plain or annotated assignment
    whose value is a literal (ast.literal_eval) can ever resolve to a value; if more than one such
    statement exists, the LAST one in source order wins. Everything else FAILS CLOSED instead of
    silently returning an earlier value: a non-literal rebind (`RULE = RULE.replace(...)`), an
    augmented assignment (`RULE += ...`), a composite/destructuring target (`a, RULE = (...)`),
    AND -- critically -- ANY binding to `var` that is not a direct top-level statement: a loop
    target, a `with`/`except ... as` target, a comprehension or walrus target, or an assignment
    nested inside an if/for/while/try/with block. The last category exists even though some of
    those bindings `might` be unconditional (e.g. `if True: RULE = "..."`), because determining
    WHICH branch of an `if` actually executes requires evaluating the condition -- i.e. partially
    executing the script -- which this tool deliberately never does; treating any such nested
    rebind as authoritative would be unsound . Known,
    ACCEPTED residual gaps  -- enumerating literally
    every Python binding construct is unbounded scope for a tool whose own task explicitly scoped
    it to "plain, annotated assignments" and that deliberately never executes the script: (a) a
    rebind inside a function or CLASS body is invisible here by design (never executes merely by
    being defined); if that is the ONLY assignment to `var`, extraction correctly fails with "not
    found" rather than silently succeeding; (b) `import`/`from...import` binding `var`, `del var`,
    a `match`/`case` capture pattern, or a side-effecting default value in a nested `def`'s
    signature (itself a form of (a)) are NOT detected and so do not invalidate an earlier top-level
    literal. The practical scope this tool targets -- a probe script with a single top-level
    `PRESTATED_RULE = "..."`-style constant -- does not exercise any of these forms;
    scripts that do should be reviewed by a human/the cross-family review gate rather than
    trusted to `verify --script` alone."""
    source = Path(script).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(script))
    found = None
    invalidated_at = None
    for node in tree.body:  # DIRECT top-level statements only -- see docstring
        lineno = getattr(node, "lineno", "?")
        value_node = None
        if isinstance(node, ast.Assign):
            simple_hit = any(isinstance(t, ast.Name) and t.id == var for t in node.targets)
            composite_hit = any(not isinstance(t, ast.Name) and var in set(_target_names(t))
                                 for t in node.targets)
            if composite_hit:
                found, invalidated_at = None, lineno
                continue
            if not simple_hit:
                continue
            value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if not (isinstance(node.target, ast.Name) and node.target.id == var):
                continue
            value_node = node.value
        elif isinstance(node, ast.AugAssign):
            if not (isinstance(node.target, ast.Name) and node.target.id == var):
                continue
            found, invalidated_at = None, lineno
            continue
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # The DEFINITION STATEMENT ITSELF binds its name at module level the moment the module
            # runs  -- distinct from (and not covered
            # by) the already-accepted limitation that ASSIGNMENTS inside the body are invisible.
            # `def RULE(): ...` or `class RULE: ...` rebinds `RULE` to something that is never a
            # string literal, so it must invalidate just like any other non-literal rebind.
            if node.name != var:
                continue
            found, invalidated_at = None, lineno
            continue
        else:
            continue
        try:
            val = ast.literal_eval(value_node)
        except (ValueError, SyntaxError):
            found, invalidated_at = None, lineno
            continue
        if isinstance(val, str):
            found, invalidated_at = val, None
        else:
            found, invalidated_at = None, lineno
    other_line = _other_binding_lineno(tree, var)
    if other_line is not None:
        invalidated_at = other_line
    if invalidated_at is not None:
        die(f"module-level `{var}` in {script}:{invalidated_at} is reassigned via something "
            f"this tool cannot statically and SAFELY resolve to a string literal (non-literal "
            f"expression, augmented assignment, a composite/destructuring target, or any binding "
            f"-- loop/with/except/comprehension/walrus target or nested assignment -- that is not "
            f"a direct top-level statement); AST-only extraction refuses to guess its value rather "
            f"than silently reporting a stale earlier literal.")
    if found is None:
        die(f"no module-level string assignment to `{var}` found in {script} (AST-only; the "
            f"script was never executed).")
    return found


# ----------------------------------------------------------------------------------- conform (cross-family spec-conformance gate)

CONFORM_FOCUS = (
    "SPEC CONFORMANCE. One file's lines each start literally with 'PRESTATE v1 kind=' -- that is "
    "the prestate LOG; read it for every amendment's reason, in order. The files BEFORE the log "
    "are this study's distinct rule texts in CHRONOLOGICAL order: the FIRST is the original ROOT "
    "rule; the LAST is the CURRENT rule (what applies now); any in between are intermediate "
    "amended versions the study passed through, which may differ from both ROOT and CURRENT (e.g. "
    "a later amendment can revert back toward ROOT's wording). The file(s) AFTER the log are the "
    "implementation, then any extras. EVERY divergence between the CURRENT rule (the LAST rule "
    "text, not any earlier one) and what the code actually computes is a BLOCKER, with ONE "
    "exception: it is not a BLOCKER if a logged amendment's reason explicitly justifies the "
    "implementation's CURRENT behavior itself -- NOT merely because an amendment mentions the "
    "same topic or because an EARLIER, now-superseded rule text once permitted it. A threshold "
    "that changed 10 -> 20 -> 10 across amendments means CURRENT requires 10: code still using 20 "
    "is a BLOCKER even though an earlier amendment's reason discusses 20, because that amendment "
    "no longer describes the current rule. Check: population/eligibility filters, entry/exit "
    "timing, thresholds, cost constants, statistics, PASS/KILL/NEEDS_DATA branches, exclusions. "
    "Also flag any cost constant not sourced from a measurement, and a missing instrument-null "
    "branch."
)


DEFAULT_CONFORM_PROVIDER = "codex-cli"
DEFAULT_CONFORM_MODEL = "terra"  # a different-family cross-family reviewer tier alias (see tools/verify.py)


def build_conform_cmd(log: Path, script: Path, extras: list[str], out: Path, repo_root: Path,
                       provider: str = DEFAULT_CONFORM_PROVIDER, model: str = DEFAULT_CONFORM_MODEL) -> list[str]:
    log = Path(log)
    # Refuse to hand a tampered or chain-broken frozen file to the reviewer as though it were the
    # trusted ROOT/CURRENT rule .
    problems = check_integrity(log) + check_chain_links(log)
    if problems:
        die("refusing to build the conform command -- the log/frozen files are not trustworthy:\n  "
            + "\n  ".join(problems))
    entries = parse_log(log)
    chain = active_chain(entries)
    if not chain:
        die(f"no ROOT in {log} -- nothing to conform against.")
    root_sha = chain[0]["sha256"]
    cur_sha = chain[-1]["sha256"]
    # Every DISTINCT frozen text in the chain, ROOT first then CURRENT last, deduped in between
    # : sending only ROOT and CURRENT leaves every
    # INTERMEDIATE amendment represented solely by a hash + reason + diff-path reference in the
    # log, which does not tell the reviewer what that intermediate state actually computed -- for
    # a history A -> B -> A it previously could not distinguish "never amended" from "amended to B
    # and back", even though the FOCUS prompt below explicitly asks it to account for every change.
    shas_in_order = [root_sha] + [e["sha256"] for e in chain[1:] if e.get("kind") == "AMEND"]
    seen_shas: set = set()
    shas_deduped = []
    for sha in shas_in_order:
        if sha in seen_shas:
            continue
        seen_shas.add(sha)
        shas_deduped.append(sha)
    # Dedup-by-first-occurrence can leave CURRENT (shas_in_order[-1]) stranded in the MIDDLE of
    # the list when a later amendment reverted content back to an EARLIER state's exact text
    #  never gets its own
    # trailing slot, and CONFORM_FOCUS's "the last rule file is CURRENT" becomes a lie pointing the
    # reviewer at B as if it were current). If CURRENT's sha isn't already last, re-affirm it as
    # the final file (duplicating the path if needed) rather than reordering -- reordering could
    # move ROOT (always shas_deduped[0]) out of its guaranteed first position instead.
    if shas_deduped[-1] != cur_sha:
        shas_deduped.append(cur_sha)
    files = [str((frozen_dir(log) / f"{sha}.txt").resolve()) for sha in shas_deduped]
    # The log itself carries every amendment's REASON : the
    # FOCUS prompt below asks the reviewer to say whether a logged amendment covers each
    # divergence, which the frozen texts alone don't explain (no rationale attached to a diff).
    # This is a deliberate addition beyond the task's literal `<ROOT> <CURRENT> <S> <extras>`
    # file list, placed after the frozen texts and before the implementation so ROOT still comes
    # first (see the --dry-run selftest assertion).
    files.append(str(log.resolve()))
    files.append(str(Path(script).resolve()))
    files.extend(str(Path(e).resolve()) for e in extras)
    return [
        "python", str((repo_root / "tools" / "verify.py")),
        "review", "-p", provider, "-m", model,
        "--files", *files,
        "--focus", CONFORM_FOCUS,
        "-o", str(out),
        "--timeout", "900",
    ]


def _quote_for_print(args: list[str]) -> str:
    out = []
    for a in args:
        if a == "" or any(c in a for c in ' "\t'):
            out.append('"' + a.replace('"', '\\"') + '"')
        else:
            out.append(a)
    return " ".join(out)


# ----------------------------------------------------------------------------------- CLI commands

def cmd_freeze(a) -> int:
    info = freeze(Path(a.rule_file), Path(a.log), new_root=a.new_root)
    print(f"prestate: ROOT sha256={info['sha']} frozen at {info['frozen_path']}")
    return 0


def cmd_amend(a) -> int:
    info = amend(Path(a.new_rule_file), Path(a.log), a.reason, allow_noop=getattr(a, "allow_noop", False))
    print(f"prestate: AMEND sha256={info['sha']} (prev={info['prev']}, root={info['root']}) "
          f"diff={info['diff_path']}")
    return 0


def cmd_show(a) -> int:
    log = Path(a.log)
    entries = parse_log(log)
    chain = active_chain(entries)
    if not chain:
        print(f"prestate: no ROOT in {log}")
        return 1
    # `show` is a read-only review aid: it still displays what it can (a skeptic needs to SEE the
    # problem), but it must not present a broken chain as though it were trustworthy  -- print the problems up front and return nonzero.
    problems = check_integrity(log) + check_chain_links(log)
    rc = 0
    if problems:
        print(f"prestate: WARNING -- {len(problems)} integrity/chain problem(s) (showing best-effort anyway):")
        for p in problems:
            print(f"  - {p}")
        rc = 1
    root = chain[0]
    amends = [e for e in chain[1:] if e.get("kind") == "AMEND"]
    cur = chain[-1]
    if not root.get("sha256") or not cur.get("sha256"):
        print("prestate: ROOT or CURRENT record is missing its sha256 -- cannot display frozen "
              "text or a diff; see the problems above.")
        return 1
    print(f"ROOT sha256:    {root['sha256']}  (file={root.get('file')}, utc={root.get('utc')})")
    if cur is root:
        print(f"CURRENT sha256: {cur['sha256']}  (= ROOT, no amendments)")
    else:
        print(f"CURRENT sha256: {cur['sha256']}  (via AMEND utc={cur.get('utc')} "
              f"reason=\"{cur.get('reason')}\")")
    print(f"AMENDMENTS ({len(amends)}):")
    for i, e in enumerate(amends, 1):
        # .get(), not e['sha256'] : an intermediate
        # amendment missing its own hash must not crash this best-effort display after the
        # problem was already reported above.
        print(f"  {i}. sha256={e.get('sha256', '?')} utc={e.get('utc')} reason=\"{e.get('reason')}\" "
              f"diff={e.get('diff')}")
    root_text = read_frozen(log, root["sha256"])
    cur_text = read_frozen(log, cur["sha256"])
    print("--- DIFF ROOT -> CURRENT ---")
    diff = list(difflib.unified_diff(
        root_text.splitlines(keepends=True), cur_text.splitlines(keepends=True),
        fromfile=f"ROOT:{root['sha256'][:12]}", tofile=f"CURRENT:{cur['sha256'][:12]}"))
    sys.stdout.write("".join(diff) if diff else "(no textual difference)\n")
    return rc


def cmd_verify(a) -> int:
    log = Path(a.log)
    # Reject BOTH given rather than silently preferring --text-file : a stale/wrong --script would otherwise go unchecked whenever --text-file
    # happened to match, which is exactly the kind of silent-pass this tool exists to prevent.
    if a.text_file and (a.script or a.var):
        die("verify takes --text-file F, OR --script S --var NAME -- not both.")
    if a.text_file:
        text = Path(a.text_file).read_text(encoding="utf-8")
        source_desc = f"--text-file {a.text_file}"
    elif a.script and a.var:
        text = extract_script_var(Path(a.script), a.var)
        source_desc = f"--script {a.script} --var {a.var}"
    else:
        die("verify needs --text-file F, or --script S --var NAME")
    status = verify_text(text, log)
    print(f"prestate: verify ({source_desc}) vs {log}: {status}")
    problems = check_integrity(log) + check_chain_links(log)
    if problems:
        print(f"prestate: INTEGRITY FAILURE ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
    ok = (status == "OK") and not problems
    return 0 if ok else 1


def cmd_conform(a) -> int:
    log = Path(a.log)
    repo_root = find_repo_root(Path.cwd())
    # Resolved to absolute: the subprocess below runs with cwd=repo_root, so a relative `out`
    # built from a relative --log would otherwise land somewhere other than the log's own
    # directory .
    out = (log.parent / f"conform_{Path(a.script).stem}.md").resolve()
    provider = getattr(a, "provider", None) or DEFAULT_CONFORM_PROVIDER
    model = getattr(a, "model", None) or DEFAULT_CONFORM_MODEL
    cmd = build_conform_cmd(log, Path(a.script), a.extra or [], out, repo_root, provider, model)
    if a.dry_run:
        print(_quote_for_print(cmd))
        return 0
    print(f"prestate: running conform gate -> {out}", file=sys.stderr)
    r = subprocess.run(cmd, cwd=str(repo_root))
    print(f"prestate: conform gate exit={r.returncode}, output at {out}")
    return r.returncode


# ----------------------------------------------------------------------------------- selftest

def selftest() -> None:
    with tempfile.TemporaryDirectory(prefix="prestate_selftest_") as td:
        td = Path(td)
        log = td / "prestate_log.txt"

        rule_v1 = td / "rule_v1.txt"
        rule_v1.write_text("\n\nPASS if x >= 10.\nKILL if x < 0.   \n\n", encoding="utf-8")

        # 1. freeze -> verify(file) OK
        info1 = freeze(rule_v1, log)
        assert verify_text(rule_v1.read_text(encoding="utf-8"), log) == "OK", "freeze/verify round-trip failed"
        a = argparse.Namespace(log=str(log), text_file=str(rule_v1), script=None, var=None)
        assert cmd_verify(a) == 0, "cmd_verify should exit 0 on exact match"

        # 2. CRLF variant verifies ok (same logical content, different line endings / blank padding)
        rule_crlf = td / "rule_v1_crlf.txt"
        rule_crlf.write_bytes(b"PASS if x >= 10.\r\nKILL if x < 0.\r\n\r\n")
        assert verify_text(rule_crlf.read_text(encoding="utf-8"), log) == "OK", "CRLF-normalized text should still verify OK"

        # 3. edited text fails
        edited = "PASS if x >= 20.\nKILL if x < 0.\n"
        assert verify_text(edited, log) == "NOMATCH", "edited rule text should NOT verify"

        # 4. amend with short reason refused
        rule_v2 = td / "rule_v2.txt"
        rule_v2.write_text("PASS if x >= 20.\nKILL if x < 0.\n", encoding="utf-8")
        try:
            amend(rule_v2, log, "too short")
            raised = False
        except SystemExit as e:
            raised = True
            assert e.code == 2, f"short-reason amend should exit 2, got {e.code}"
        assert raised, "amend with a < 15 char reason must be refused"

        # 4b. amend ok -> verify old = STALE, new = ok
        info2 = amend(rule_v2, log, "raised the PASS threshold after a sizing re-check")
        assert verify_text(rule_v1.read_text(encoding="utf-8"), log) == "STALE", "old rule should be STALE after amendment"
        assert verify_text(rule_v2.read_text(encoding="utf-8"), log) == "OK", "amended rule should verify OK"
        assert current_sha(log) == info2["sha"]

        # 4c. no-op amend (same text as the current head) refused by default; allowed with allow_noop.
        #     Runs on its OWN log so the main chain used by the later checks is untouched.
        noop_log = td / "noop_log.txt"
        freeze(rule_v1, noop_log)
        info_n1 = amend(rule_v2, noop_log, "first real change on the no-op probe log")
        try:
            amend(rule_v2, noop_log, "a second amend with unchanged text that must be refused")
            raised = False
        except SystemExit as e:
            raised = True
            assert e.code == 2, f"no-op amend should exit 2, got {e.code}"
        assert raised, "an amend whose text equals the current head must be refused by default"
        assert current_sha(noop_log) == info_n1["sha"], "a refused no-op amend must not move the head"
        assert len(active_chain(parse_log(noop_log))) == 2, "a refused no-op amend must not append a record"
        info_n2 = amend(rule_v2, noop_log, "explicit no-op amendment recorded on purpose", allow_noop=True)
        assert info_n2["sha"] == info_n1["sha"] and len(active_chain(parse_log(noop_log))) == 3, "allow_noop must record it"
        assert not check_integrity(noop_log) and not check_chain_links(noop_log), "an allowed no-op must leave a valid chain"

        # 5. show prints a diff containing the changed line
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_show(argparse.Namespace(log=str(log)))
        out = buf.getvalue()
        assert rc == 0
        assert "+PASS if x >= 20." in out, f"show diff should contain the new threshold line; got:\n{out}"
        assert "-PASS if x >= 10." in out, f"show diff should contain the old threshold line; got:\n{out}"

        # 6. verify --script: annotated assignment + plain assignment, script never executed
        script = td / "fake_script.py"
        script.write_text(
            'raise SystemExit("executed")\n'
            'X: str = "PASS if x >= 20.\\nKILL if x < 0."\n'
            'Y = "PASS if x >= 20.\\nKILL if x < 0."\n',
            encoding="utf-8",
        )
        val_x = extract_script_var(script, "X")
        val_y = extract_script_var(script, "Y")
        assert val_x == "PASS if x >= 20.\nKILL if x < 0.", val_x
        assert val_y == val_x
        # the module-level `raise SystemExit("executed")` must never have fired: if ast parsing
        # had executed the file, this whole selftest() call would already have exited by now.
        assert verify_text(val_x, log) == "OK"
        va = argparse.Namespace(log=str(log), text_file=None, script=str(script), var="X")
        assert cmd_verify(va) == 0, "verify --script --var X should match CURRENT and exit 0"

        # 7. tamper a frozen copy -> integrity failure
        tampered_path = frozen_dir(log) / f"{info1['sha']}.txt"
        original_bytes = tampered_path.read_bytes()
        tampered_path.write_bytes(original_bytes + b"TAMPERED")
        problems = check_integrity(log)
        assert problems, "tampering a frozen copy must be caught by check_integrity"
        vb = argparse.Namespace(log=str(log), text_file=None, script=str(script), var="X")
        assert cmd_verify(vb) == 1, "verify must exit 1 when integrity check fails, even if the text matches CURRENT"
        tampered_path.write_bytes(original_bytes)  # restore for cleanliness
        assert not check_integrity(log), "integrity should be clean again after restoring the frozen copy"

        # 8. second freeze without --new-root refused
        rule_v3 = td / "rule_v3.txt"
        rule_v3.write_text("PASS if y >= 1.\n", encoding="utf-8")
        try:
            freeze(rule_v3, log, new_root=False)
            raised2 = False
        except SystemExit as e:
            raised2 = True
            assert e.code == 2, f"second freeze without --new-root should exit 2, got {e.code}"
        assert raised2, "a second freeze without --new-root must be refused"
        # --new-root DOES succeed (separately, on its own log) and warns.
        log2 = td / "prestate_log2.txt"
        freeze(rule_v1, log2)
        import io as _io2
        from contextlib import redirect_stderr
        errbuf = _io2.StringIO()
        with redirect_stderr(errbuf):
            freeze(rule_v3, log2, new_root=True)
        assert "WARNING" in errbuf.getvalue(), "freeze --new-root should print a warning"
        assert current_sha(log2) == sha256_hex(normalize_file(rule_v3)), "new ROOT should become the active chain"

        # 9. conform --dry-run command contains the root copy path first
        repo_root = find_repo_root(Path.cwd())
        fake_impl = td / "impl.py"
        fake_impl.write_text("# pretend implementation\n", encoding="utf-8")
        out_md = log.parent / "conform_impl.md"
        cmd = build_conform_cmd(log, fake_impl, [], out_md, repo_root)
        files_idx = cmd.index("--files")
        root_copy_path = str((frozen_dir(log) / f"{info1['sha']}.txt").resolve())
        assert cmd[files_idx + 1] == root_copy_path, (
            f"the ROOT frozen copy must be the FIRST path after --files; got {cmd[files_idx + 1]!r}")
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rc2 = cmd_conform(argparse.Namespace(log=str(log), script=str(fake_impl), extra=[], dry_run=True))
        assert rc2 == 0
        assert root_copy_path in buf2.getvalue()
        # the log itself (carrying amendment reasons) must also be in the files list 
        assert str(log.resolve()) in cmd, "conform must hand the reviewer the log, not just ROOT/CURRENT text"

        # 10.  gate #1: extract_script_var fails closed on a non-literal reassignment instead
        # of silently returning an earlier literal.
        bad_script = td / "bad_reassign.py"
        bad_script.write_text('Z = "PASS if x >= 10."\nZ = Z.replace("10", "20")\n', encoding="utf-8")
        try:
            extract_script_var(bad_script, "Z")
            raised3 = False
        except SystemExit as e:
            raised3 = True
            assert e.code == 2
        assert raised3, "a non-literal reassignment must fail closed, not silently return the earlier literal"

        # 11.  gate #5: amend refuses a multi-line reason outright (would corrupt the log format).
        try:
            amend(rule_v3, log2, "line one of the reason\nline two of the reason")
            raised4 = False
        except SystemExit as e:
            raised4 = True
            assert e.code == 2
        assert raised4, "amend must refuse a reason containing a newline"

        # 12.  gate #4: two different ROOTs amended to IDENTICAL content must not collide on
        # the same diff filename.
        log3a, log3b = td / "log3a.txt", td / "log3b.txt"
        freeze(rule_v1, log3a)
        freeze(rule_v3, log3b)  # a different ROOT text
        same_target = td / "same_target.txt"
        same_target.write_text("identical amendment target text\n", encoding="utf-8")
        d1 = amend(same_target, log3a, "collision probe amendment number one")
        d2 = amend(same_target, log3b, "collision probe amendment number two")
        assert d1["sha"] == d2["sha"], "test setup: both amendments should hash identically"
        assert d1["diff_path"] != d2["diff_path"], "diffs from different ROOTs to the same text must not share a filename"
        assert Path(d1["diff_path"]).exists() and Path(d2["diff_path"]).exists()

        # 13.  gate #2/#3: conform refuses to build a command over a tampered or chain-broken
        # log instead of silently feeding bad frozen text to the reviewer.
        tampered_path.write_bytes(original_bytes + b"TAMPERED-AGAIN")
        try:
            build_conform_cmd(log, fake_impl, [], out_md, repo_root)
            raised5 = False
        except SystemExit as e:
            raised5 = True
            assert e.code == 2
        assert raised5, "conform must refuse to build a command when check_integrity finds a problem"
        tampered_path.write_bytes(original_bytes)  # restore

        broken_log = td / "broken_chain_log.txt"
        root_text_raw = normalize_file(rule_v1)
        root_sha_bc = sha256_hex(root_text_raw)
        store_content_addressed(broken_log, root_text_raw, root_sha_bc)
        amend_text_raw = normalize_file(rule_v2)
        amend_sha_bc = sha256_hex(amend_text_raw)
        store_content_addressed(broken_log, amend_text_raw, amend_sha_bc)
        append_line(broken_log, format_line("ROOT", {"sha256": root_sha_bc, "seq": 0, "utc": utc_now(), "file": "x.txt"}))
        # hand-craft a broken `prev` pointer (does not match root_sha_bc, the only prior entry) --
        # every OTHER field (including `seq`) is filled in correctly, so this isolates exactly one
        # kind of corruption (the prev link) rather than also tripping the separate
        # required-fields check that check_integrity now also runs (round-5 fix #3).
        append_line(broken_log, format_line("AMEND", {
            "sha256": amend_sha_bc, "seq": 1, "root": root_sha_bc, "prev": "0" * 64, "utc": utc_now(),
            "reason": "hand-crafted to break the prev link on purpose", "diff": "n/a",
        }))
        link_problems = check_chain_links(broken_log)
        assert link_problems, "a mismatched `prev` pointer must be caught by check_chain_links"
        assert not check_integrity(broken_log), "test setup: file-level integrity should be clean even though the chain link is broken"

        # 14.  review round 2 #1: `var += ...` (AugAssign) invalidates extraction instead of
        # silently returning an earlier literal.
        aug_script = td / "aug_reassign.py"
        aug_script.write_text('W = "PASS if x >= 10."\nW += " extra"\n', encoding="utf-8")
        try:
            extract_script_var(aug_script, "W")
            raised6 = False
        except SystemExit as e:
            raised6 = True
            assert e.code == 2
        assert raised6, "an AugAssign (`+=`) to the tracked var must fail closed, not return the earlier literal"

        # 15.  review round 2 #2: read_frozen re-hashes before returning, so `amend` (which
        # reads ROOT through it to build the diff) refuses a tampered ROOT instead of baking a
        # wrong diff into history.
        log4 = td / "log4.txt"
        freeze(rule_v1, log4)
        chain4 = active_chain(parse_log(log4))
        root4_sha = chain4[0]["sha256"]
        root4_path = frozen_dir(log4) / f"{root4_sha}.txt"
        orig4 = root4_path.read_bytes()
        root4_path.write_bytes(orig4 + b"TAMPERED")
        try:
            amend(rule_v2, log4, "attempt an amend while ROOT is tampered")
            raised7 = False
        except SystemExit as e:
            raised7 = True
            assert e.code == 2
        assert raised7, "amend must refuse to build a diff against a tampered ROOT (via read_frozen)"
        root4_path.write_bytes(orig4)  # restore

        # 16.  review round 2 #3: a record truncated after `prev=` (missing utc/reason/diff)
        # is caught by check_chain_links even though its sha256/root/prev all still parse fine.
        trunc_log = td / "truncated_log.txt"
        root5_text = normalize_file(rule_v1)
        root5_sha = sha256_hex(root5_text)
        store_content_addressed(trunc_log, root5_text, root5_sha)
        amend5_text = normalize_file(rule_v2)
        amend5_sha = sha256_hex(amend5_text)
        store_content_addressed(trunc_log, amend5_text, amend5_sha)
        append_line(trunc_log, format_line("ROOT", {"sha256": root5_sha, "seq": 0, "utc": utc_now(), "file": "x.txt"}))
        # hand-write a line truncated right after prev=<hash> -- no seq/utc/reason/diff at all.
        with open(trunc_log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"PRESTATE v1 kind=AMEND sha256={amend5_sha} root={root5_sha} prev={root5_sha}\n")
        trunc_problems = check_chain_links(trunc_log)
        assert trunc_problems, "a record missing seq/utc/reason/diff must be flagged, not accepted just because sha/root/prev parse"
        # check_integrity ALSO flags the same missing-field problem now (round-5 fix #3: required
        # fields are checked on every record in the whole file, not only via check_chain_links'
        # active-chain walk) -- both the content-addressed files being fine AND the record shape
        # being broken are simultaneously true here, and both checks must say so.
        assert check_integrity(trunc_log), "check_integrity must also flag the same incomplete record (whole-file required-fields check)"

        # 17.  review round 2 #4: verify rejects --text-file AND --script/--var together
        # instead of silently preferring --text-file.
        vc = argparse.Namespace(log=str(log), text_file=str(rule_v2), script=str(script), var="X")
        try:
            cmd_verify(vc)
            raised8 = False
        except SystemExit as e:
            raised8 = True
            assert e.code == 2
        assert raised8, "verify must refuse --text-file together with --script/--var, not silently pick one"

        # 18.  review round 3 #1: a tuple/destructuring target touching the tracked var
        # invalidates extraction instead of silently returning an earlier plain literal.
        tuple_script = td / "tuple_reassign.py"
        tuple_script.write_text('RULE = "A"\nRULE, other = ("B", 0)\n', encoding="utf-8")
        try:
            extract_script_var(tuple_script, "RULE")
            raised9 = False
        except SystemExit as e:
            raised9 = True
            assert e.code == 2
        assert raised9, "a destructuring target touching the tracked var must fail closed"
        # ... and a rebind nested inside an `if` block fails closed too, EVEN when the branch is
        # literally `if True` : resolving which branch
        # "actually" runs would require evaluating the condition, i.e. partially executing the
        # script, which this tool never does -- so ANY nested rebind invalidates, unconditionally.
        if_script = td / "if_reassign.py"
        if_script.write_text('RULE = "A"\nif True:\n    RULE = "B"\n', encoding="utf-8")
        try:
            extract_script_var(if_script, "RULE")
            raised_if = False
        except SystemExit as e:
            raised_if = True
            assert e.code == 2
        assert raised_if, "a rebind nested inside ANY if-block (even `if True`) must fail closed, not resolve"
        # a for-loop target rebinding the tracked var must fail closed too (round-4 finding #2).
        for_script = td / "for_reassign.py"
        for_script.write_text('RULE = "A"\nfor RULE in ["B"]:\n    pass\n', encoding="utf-8")
        try:
            extract_script_var(for_script, "RULE")
            raised_for = False
        except SystemExit as e:
            raised_for = True
            assert e.code == 2
        assert raised_for, "a for-loop target touching the tracked var must fail closed"

        # 19.  review round 3 #2: a field truncated mid-quote (no closing quote) must not be
        # silently accepted as a present value by the `\S+` bareword fallback.
        parsed_bad = parse_line('PRESTATE v1 kind=AMEND sha256=abc root=def prev=def utc=2026-01-01T00:00:00Z '
                                 'reason="ok reason text here" diff="prestate_frozen/partial')
        assert "diff" not in parsed_bad, f"an unterminated quoted field must not parse as present: {parsed_bad}"

        # 20.  review round 3 #3: append_line inserts a missing trailing newline before
        # appending, so a record is never glued onto a prior line lacking its own newline.
        no_nl_log = td / "no_trailing_newline_log.txt"
        root6_text = normalize_file(rule_v1)
        root6_sha = sha256_hex(root6_text)
        store_content_addressed(no_nl_log, root6_text, root6_sha)
        with open(no_nl_log, "wb") as fh:  # ROOT line with NO trailing newline, by construction
            fh.write(format_line("ROOT", {"sha256": root6_sha, "seq": 0, "utc": utc_now(),
                                           "file": "x.txt"}).encode("utf-8"))
        amend(rule_v2, no_nl_log, "appended after a log missing its final newline on purpose")
        entries6 = parse_log(no_nl_log)
        assert len(entries6) == 2 and entries6[0]["kind"] == "ROOT" and entries6[1]["kind"] == "AMEND", (
            f"the ROOT record must survive intact, not get overwritten by the appended AMEND: {entries6}")

        # 21.  review round 3 #4: amend refuses to extend an already-broken chain.
        try:
            amend(rule_v3, broken_log, "try to amend on top of a chain with a bad prev pointer")
            raised10 = False
        except SystemExit as e:
            raised10 = True
            assert e.code == 2
        assert raised10, "amend must refuse to extend a chain that check_chain_links already flags"

        # 22.  review round 3 #4 (show side): show still displays best-effort but returns 1 and
        # prints the problem when the chain is broken, instead of presenting it as trustworthy.
        buf3 = io.StringIO()
        with redirect_stdout(buf3):
            rc3 = cmd_show(argparse.Namespace(log=str(broken_log)))
        shown = buf3.getvalue()
        assert rc3 == 1, "show must return nonzero when check_chain_links finds a problem"
        assert "WARNING" in shown and "prev" in shown.lower(), f"show must print the chain problem; got:\n{shown}"

        # 23.  review round 4 #3: a record truncated down to bare "PRESTATE v1" (no fields at
        # all) is surfaced as MALFORMED instead of silently vanishing from every check.
        malformed_log = td / "malformed_log.txt"
        freeze(rule_v1, malformed_log)
        with open(malformed_log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("PRESTATE v1\n")  # truncated before any key=value field, including `kind=`
        assert any(e.get("kind") == "MALFORMED" for e in parse_log(malformed_log)), "a bare 'PRESTATE v1' line must parse as MALFORMED, not vanish"
        malformed_problems = check_integrity(malformed_log)
        assert any("MALFORMED" in p for p in malformed_problems), "check_integrity must report the MALFORMED record"

        # 24.  review round 4 #4: `seq` catches a deleted amendment PAIR whose net content
        # change coincidentally returns to a prior hash (content-hash links alone cannot).
        seq_log = td / "seq_log.txt"
        freeze(rule_v1, seq_log)  # ROOT = A, seq=0
        amend(rule_v2, seq_log, "first amendment, change A to B")       # seq=1
        amend(rule_v1, seq_log, "revert B back to the original A text")  # seq=2, sha == root sha (content back to A)
        amend(rule_v3, seq_log, "third amendment, change back-to-A to C")  # seq=3
        # hand-delete the middle two AMEND lines (keep ROOT seq=0 and the last AMEND seq=3): since
        # "revert to A" has sha==root_sha, the last AMEND's prev==root_sha regardless, so a
        # prev-only check would see ROOT->AMEND(prev=root_sha) and call it fine.
        lines = seq_log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 4
        (seq_log.parent / "seq_log_tampered.txt").write_text(lines[0] + "\n" + lines[3] + "\n", encoding="utf-8")
        tampered_seq_log = seq_log.parent / "seq_log_tampered.txt"
        # content-hash-only reasoning would see this as valid (prev matches); seq must catch it.
        seq_problems = check_chain_links(tampered_seq_log)
        assert any("seq=" in p for p in seq_problems), (
            f"deleting a content-reverting amendment pair must be caught via the seq gap: {seq_problems}")

        # 25.  review round 4 #5: amend refuses when an intermediate (non-ROOT) amendment's
        # frozen file is missing, not just when ROOT's is.
        mid_log = td / "mid_log.txt"
        freeze(rule_v1, mid_log)
        info_mid = amend(rule_v2, mid_log, "intermediate amendment whose file we will delete")
        (frozen_dir(mid_log) / f"{info_mid['sha']}.txt").unlink()
        try:
            amend(rule_v3, mid_log, "attempt a further amendment after deleting the middle file")
            raised_mid = False
        except SystemExit as e:
            raised_mid = True
            assert e.code == 2
        assert raised_mid, "amend must refuse when a PRIOR (non-ROOT) amendment's frozen file is missing"

        # 26.  review round 4 #7: a reason containing U+2028 LINE SEPARATOR (not \n/\r) is
        # still rejected, since parse_log's splitlines() would still split on it.
        try:
            amend(rule_v3, log2, "adjusted threshold after checking the inputs further")
            raised_u2028 = False
        except SystemExit as e:
            raised_u2028 = True
            assert e.code == 2
        assert raised_u2028, "a reason containing U+2028 must be rejected, not just \\n/\\r"

        # 27.  review round 4 #6: verify_text / cmd_show must not crash (KeyError) when the
        # active chain's CURRENT record is missing its sha256 -- they should degrade cleanly.
        nocur_log = td / "nocur_log.txt"
        freeze(rule_v1, nocur_log)
        with open(nocur_log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("PRESTATE v1 kind=AMEND root=x prev=x utc=2026-01-01T00:00:00Z "
                     "reason=\"no sha256 on this record on purpose\" diff=n/a\n")
        assert verify_text("anything", nocur_log) == "NOMATCH", "verify_text must degrade to NOMATCH, not crash, when CURRENT lacks sha256"
        buf4 = io.StringIO()
        with redirect_stdout(buf4):
            rc4 = cmd_show(argparse.Namespace(log=str(nocur_log)))
        assert rc4 == 1, "show must report failure (not crash) when CURRENT is missing sha256"

        # 28.  review round 5 #2: two complete records accidentally joined onto one physical
        # line (no separating newline) parse as MALFORMED, not as "the second record, silently
        # replacing the first" via last-key-wins.
        joined = ('PRESTATE v1 kind=ROOT sha256=' + 'a' * 64 + ' seq=0 utc=2026-01-01T00:00:00Z file=x.txt'
                  'PRESTATE v1 kind=ROOT sha256=' + 'b' * 64 + ' seq=0 utc=2026-01-01T00:00:00Z file=y.txt')
        parsed_joined = parse_line(joined)
        assert parsed_joined is not None and parsed_joined.get("kind") == "MALFORMED", (
            f"two records joined onto one line must parse as MALFORMED, not silently pick one: {parsed_joined}")
        # a duplicate key within an otherwise well-formed line is caught the same way.
        dup_key_line = ('PRESTATE v1 kind=ROOT sha256=' + 'a' * 64 + ' seq=0 utc=2026-01-01T00:00:00Z '
                         'file=x.txt sha256=' + 'b' * 64)
        assert parse_line(dup_key_line).get("kind") == "MALFORMED", "a duplicate key on one line must be MALFORMED, not last-value-wins"

        # 29.  review round 5 #3: check_integrity flags a record missing sha256 ANYWHERE in the
        # file, including one sitting in an OLDER, superseded study (before a later --new-root)
        # that check_chain_links (active-chain-only) would never look at.
        orphan_log = td / "orphan_log.txt"
        freeze(rule_v1, orphan_log)  # study 1's ROOT
        with open(orphan_log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("PRESTATE v1 kind=AMEND\n")  # truncated amendment, left orphaned in study 1
        freeze(rule_v3, orphan_log, new_root=True)  # study 2 supersedes study 1 as the active chain
        # check_chain_links now validates EVERY segment, not just the active one (round-6 fix #1):
        # the orphaned truncated record in superseded study 1 must still be flagged.
        orphan_link_problems = check_chain_links(orphan_log)
        assert any("missing required field" in p for p in orphan_link_problems), (
            f"check_chain_links must flag the orphaned record even though it precedes the active chain: {orphan_link_problems}")
        orphan_problems = check_integrity(orphan_log)
        assert any("missing required field" in p for p in orphan_problems), (
            f"check_integrity must still catch the orphaned truncated record via the whole-file scan: {orphan_problems}")

        # 30.  review round 5 #5: a reason containing U+00A0 NBSP (not a plain ASCII space) is
        # quoted on write and round-trips intact, instead of being silently truncated at the NBSP.
        log5 = td / "log5.txt"
        freeze(rule_v1, log5)
        nbsp_reason = "raised threshold after review of the inputs"
        amend(rule_v2, log5, nbsp_reason)
        got_reason = active_chain(parse_log(log5))[-1]["reason"]
        assert got_reason == nbsp_reason, f"an NBSP-separated reason must round-trip exactly; got {got_reason!r}"

        # 31.  review round 6 #1: starting a legitimate new study does not hide an
        # already-broken PRIOR study's chain-link corruption from check_chain_links.
        two_study_log = td / "two_study_log.txt"
        s1_root_text = normalize_file(rule_v1)
        s1_root_sha = sha256_hex(s1_root_text)
        store_content_addressed(two_study_log, s1_root_text, s1_root_sha)
        s1_amend_text = normalize_file(rule_v2)
        s1_amend_sha = sha256_hex(s1_amend_text)
        store_content_addressed(two_study_log, s1_amend_text, s1_amend_sha)
        append_line(two_study_log, format_line("ROOT", {"sha256": s1_root_sha, "seq": 0, "utc": utc_now(), "file": "x.txt"}))
        append_line(two_study_log, format_line("AMEND", {  # bad prev, otherwise complete
            "sha256": s1_amend_sha, "seq": 1, "root": s1_root_sha, "prev": "f" * 64, "utc": utc_now(),
            "reason": "study 1's deliberately broken prev pointer", "diff": "n/a",
        }))
        freeze(rule_v3, two_study_log, new_root=True)  # study 2 starts clean and becomes active
        two_study_problems = check_chain_links(two_study_log)
        assert any("f" * 64 in p for p in two_study_problems), (
            f"a new --new-root study must not hide an older study's broken prev link: {two_study_problems}")

        # 32.  review round 6 #2: conform's file list includes EVERY distinct intermediate
        # frozen text (A -> B -> A -> C keeps B's text, not just A and C).
        abac_log = td / "abac_log.txt"
        freeze(rule_v1, abac_log)                              # A
        amend(rule_v2, abac_log, "first amendment A to B")      # B
        amend(rule_v1, abac_log, "revert B back to A")          # back to A (same sha as ROOT)
        amend(rule_v3, abac_log, "third amendment to C")        # C
        abac_cmd = build_conform_cmd(abac_log, fake_impl, [], log.parent / "conform_abac.md", repo_root)
        b_sha = sha256_hex(normalize_file(rule_v2))
        assert str((frozen_dir(abac_log) / f"{b_sha}.txt").resolve()) in abac_cmd, (
            "conform must include B's frozen text, not just ROOT(A) and CURRENT(C)")

        # 33.  review round 6 #3: freeze refuses a rule file path containing a line-break
        # character instead of writing a ROOT record that corrupts the log on the next read.
        weird_name_dir = td / "weird"
        weird_name_dir.mkdir()
        weird_path = weird_name_dir / ("line break.txt")
        try:
            weird_path.write_text("PASS if z >= 1.\n", encoding="utf-8")
            can_create = True
        except OSError:
            can_create = False  # some filesystems reject U+2028 in a filename outright -- fine either way
        if can_create:
            weird_log = td / "weird_log.txt"
            try:
                freeze(weird_path, weird_log, repo_root=weird_name_dir)
                raised_weird = False
            except SystemExit as e:
                raised_weird = True
                assert e.code == 2
            assert raised_weird, "freeze must refuse a rule-file path containing a line-break character"

        # 34.  review round 6 #4: the record version token is validated, not merely captured --
        # "PRESTATE v2 ..." and a corrupted version token both parse as MALFORMED, not as v1.
        v2_line = 'PRESTATE v2 kind=ROOT sha256=' + 'c' * 64 + ' seq=0 utc=2026-01-01T00:00:00Z file=x.txt'
        assert parse_line(v2_line).get("kind") == "MALFORMED", "an unrecognized version token must be MALFORMED, not silently accepted as v1"
        garbage_version_line = 'PRESTATE garbage kind=ROOT sha256=' + 'd' * 64 + ' seq=0 utc=2026-01-01T00:00:00Z file=x.txt'
        assert parse_line(garbage_version_line).get("kind") == "MALFORMED", "a corrupted version token must be MALFORMED"

        # 35.  review round 7 #2: a top-level `def RULE(): ...` or `class RULE: ...` rebinds
        # the tracked name at module level and must invalidate extraction, not leave an earlier
        # literal looking current.
        def_script = td / "def_reassign.py"
        def_script.write_text('RULE = "A"\ndef RULE():\n    pass\n', encoding="utf-8")
        try:
            extract_script_var(def_script, "RULE")
            raised_def = False
        except SystemExit as e:
            raised_def = True
            assert e.code == 2
        assert raised_def, "a top-level `def RULE` must invalidate extraction of RULE"
        class_script = td / "class_reassign.py"
        class_script.write_text('RULE = "A"\nclass RULE:\n    pass\n', encoding="utf-8")
        try:
            extract_script_var(class_script, "RULE")
            raised_class = False
        except SystemExit as e:
            raised_class = True
            assert e.code == 2
        assert raised_class, "a top-level `class RULE` must invalidate extraction of RULE"
        # a def/class with a DIFFERENT name must not false-positive-invalidate an unrelated var.
        unrelated_def_script = td / "unrelated_def.py"
        unrelated_def_script.write_text('RULE = "A"\ndef OTHER():\n    pass\n', encoding="utf-8")
        assert extract_script_var(unrelated_def_script, "RULE") == "A", "an unrelated def must not invalidate RULE"

        # 36.  review round 7 #1: conform's command includes a FOCUS that correctly describes
        # the actual file order for a multi-version history (A -> B -> A -> C): every distinct
        # rule text chronologically, then the log, then the implementation.
        assert "CHRONOLOGICAL" in abac_cmd[abac_cmd.index("--focus") + 1], "the focus text must describe the actual chronological file order"

        # 37.  review round 8 #1: A -> B -> A (revert to the EXACT original text) must still
        # put CURRENT (= A's content) LAST in conform's file list, not stranded in the middle
        # because A's FIRST occurrence (as ROOT) already claimed that dedup slot.
        aba_log = td / "aba_log.txt"
        freeze(rule_v1, aba_log)                              # A
        amend(rule_v2, aba_log, "amend A to B for this probe")  # B
        amend(rule_v1, aba_log, "revert B back to exactly A")   # back to A -- CURRENT == ROOT's content
        aba_cmd = build_conform_cmd(aba_log, fake_impl, [], log.parent / "conform_aba.md", repo_root)
        files_idx2 = aba_cmd.index("--files")
        focus_idx2 = aba_cmd.index("--focus")
        aba_files = aba_cmd[files_idx2 + 1:focus_idx2]
        log_path2 = str(aba_log.resolve())
        rule_files = aba_files[:aba_files.index(log_path2)]  # just the rule-text files, before the log/impl/extras
        a_sha = sha256_hex(normalize_file(rule_v1))
        a_path = str((frozen_dir(aba_log) / f"{a_sha}.txt").resolve())
        assert rule_files[0] == a_path, f"ROOT (A) must still be first: {rule_files}"
        assert rule_files[-1] == a_path, f"CURRENT (also A, after the revert) must be LAST, not stranded mid-list: {rule_files}"
        b_sha2 = sha256_hex(normalize_file(rule_v2))
        assert str((frozen_dir(aba_log) / f"{b_sha2}.txt").resolve()) in rule_files, "B must still appear as an intermediate"

        # 38.  review round 8 #2: a NESTED def/class (inside an if-block, not a direct
        # top-level statement) named `var` must also invalidate extraction -- the previous
        # `_walk_skip_defs` fix skipped yielding the def/class node itself, not just its body.
        nested_def_script = td / "nested_def.py"
        nested_def_script.write_text('RULE = "A"\nif True:\n    def RULE():\n        pass\n', encoding="utf-8")
        try:
            extract_script_var(nested_def_script, "RULE")
            raised_nested_def = False
        except SystemExit as e:
            raised_nested_def = True
            assert e.code == 2
        assert raised_nested_def, "a NESTED def RULE (inside an if-block) must invalidate extraction, not just a top-level one"

        # 39.  review round 9 #1: the FOCUS text explicitly rules out an earlier, now-superseded
        # amendment excusing a CURRENT divergence (a threshold that changed 10->20->10 must still
        # flag code using 20 as a BLOCKER, even though some amendment's reason discusses 20).
        assert "10 -> 20 -> 10" in CONFORM_FOCUS and "no longer describes the current rule" in CONFORM_FOCUS, (
            "the focus text must explicitly reject 'an old amendment mentioned it' as covering a CURRENT divergence")

        # 40.  review round 9 #2: current_sha() returns None (never raises) when the active
        # chain's last record is malformed/incomplete and lacks sha256.
        nocur_log2 = td / "nocur_log2.txt"
        freeze(rule_v1, nocur_log2)
        with open(nocur_log2, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("PRESTATE v1 kind=AMEND root=x prev=x utc=2026-01-01T00:00:00Z "
                     "reason=\"no sha256 here either\" diff=n/a\n")
        assert current_sha(nocur_log2) is None, "current_sha must return None, not crash, when the last record lacks sha256"

    print("selftest OK")


# ----------------------------------------------------------------------------------- argparse wiring

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="run the built-in selftest and exit")
    sub = ap.add_subparsers(dest="command")

    p_freeze = sub.add_parser("freeze", help="freeze a rule file as the ROOT of a study")
    p_freeze.add_argument("rule_file")
    p_freeze.add_argument("--log", required=True)
    p_freeze.add_argument("--new-root", action="store_true", help="start a NEW study in this log (old history kept)")

    p_amend = sub.add_parser("amend", help="amend the active study's current rule")
    p_amend.add_argument("new_rule_file")
    p_amend.add_argument("--log", required=True)
    p_amend.add_argument("--reason", required=True, help=">= 15 characters; explain WHY")
    p_amend.add_argument("--allow-noop", action="store_true",
                         help="record an AMEND whose text equals the current head (refused by default)")

    p_show = sub.add_parser("show", help="print ROOT, CURRENT, amendments, and the ROOT->CURRENT diff")
    p_show.add_argument("--log", required=True)

    p_verify = sub.add_parser("verify", help="check rule text against the log's CURRENT sha")
    p_verify.add_argument("--log", required=True)
    p_verify.add_argument("--text-file")
    p_verify.add_argument("--script")
    p_verify.add_argument("--var")

    p_conform = sub.add_parser("conform", help="run the cross-family spec-conformance gate (ROOT + CURRENT + impl)")
    p_conform.add_argument("--log", required=True)
    p_conform.add_argument("--script", required=True)
    p_conform.add_argument("--extra", action="append", default=[])
    p_conform.add_argument("--provider", default=None,
                           help=f"cross-family review provider (default {DEFAULT_CONFORM_PROVIDER!r})")
    p_conform.add_argument("--model", default=None,
                           help=f"cross-family review model/tier alias (default {DEFAULT_CONFORM_MODEL!r})")
    p_conform.add_argument("--dry-run", action="store_true")

    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0

    if not a.command:
        ap.print_help()
        return 2

    if a.command == "verify" and not (a.text_file or (a.script and a.var)):
        die("verify needs --text-file F, or --script S --var NAME")

    dispatch = {
        "freeze": cmd_freeze, "amend": cmd_amend, "show": cmd_show,
        "verify": cmd_verify, "conform": cmd_conform,
    }
    return dispatch[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
