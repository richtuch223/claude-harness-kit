#!/usr/bin/env python
"""figures_lib.py -- the number registry used by the figure hooks (standalone port of the source repo's
command_center.py figure functions).

ops/figures.yaml maps a figure name to its live value, the aliases people use for it, a VERBATIM quote
that must still appear in its source doc, and `retracted_values` -- strings that must never be stated as
live claims. Retracted values are kept on purpose: the tripwire needs them to know what to look for.

  python tools/hooks/figures_lib.py --figures          list every figure, flag stale ones
  python tools/hooks/figures_lib.py --figure <name>    one figure, FAIL-CLOSED if its source disagrees
  python tools/hooks/figures_lib.py --selftest         offline checks
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # installed at <repo>/tools/hooks/
FIGURES_FILE = REPO / "ops" / "figures.yaml"

# A retracted value is legitimate NEXT TO ITS OWN RETRACTION (a correction has to name the wrong number).
# So each OCCURRENCE is judged on its own: it counts only when a marker sits within MARKER_WINDOW chars of IT,
# measured in the full text across line breaks. A bare "correct" ("the correct figure is $22k/yr") must NOT
# exempt a live claim, so the CORRECT stem is narrowed to CORRECTION / CORRECTED / INCORRECT.
RETRACTION_MARKERS = ("RETRACT", "CORRECTION", "CORRECTED", "INCORRECT", "VOID", "WITHDRAWN", "SUPERSEDE",
                      "was wrong", "never quote", "do not quote", "not profit", "unit error", "\u26d4", "~~")
# Markers match case-insensitively but only at a word START, so "avoid" does not contain VOID and "uncorrected"
# does not contain CORRECTED. Single-word markers stay stems (RETRACT covers "retracted"); multi-word phrases
# must also END at a word end, so "was wrongly" is not "was wrong". Non-letter markers match anywhere.
# They are located in the FULL text, never in a sliced window, so a window edge cannot manufacture a word start.
_MARKER_RES = [re.compile((r"(?<![A-Za-z])" if mk[:1].isalpha() else "") + re.escape(mk)
                          + (r"(?!\w)" if (" " in mk and mk[-1:].isalpha()) else ""), re.I)
               for mk in RETRACTION_MARKERS]
MARKER_WINDOW = 150


def load_figures(path: Path | None = None) -> dict:
    import yaml
    path = path or FIGURES_FILE
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# A number token: optional sign (ASCII or unicode minus), optional $, a whole number (commas only in groups of
# three) or a decimal, and an optional k / M / bn scale. The lookbehind stops ",000" or the "4" of "0.4" from
# being read as numbers of their own. Either a sign that stands at a token start, or no sign and nothing
# sign-like right before: a number glued to a sign it did not capture ("USD-2.8", "wide-40") yields NO token,
# never a silently positive one.
_NUM = re.compile(r"(?:(?<![\w.,])([+\-\u2212])|(?<![\w.,+\-\u2212$]))\s?(\$?)"
                  r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
                  r"(?:([kKmM])(?![A-Za-z])|(bn)\b)?")


def _numbers(text) -> set:
    """Whole numbers WITH sign and scale: "-2.8" != "2.8", "0.4k" != "0.4". Bare years are dropped, so a shared
    date cannot substantiate a value."""
    from decimal import Decimal, InvalidOperation
    out = set()
    for m in _NUM.finditer(str(text)):
        sign, dollar, num, scale, bn = m.groups()
        sign = sign or ""
        try:
            norm = f"{Decimal(num.replace(',', '')).normalize():f}"
        except InvalidOperation:
            continue
        scale = (scale or bn or "").lower()
        if not (sign or dollar or scale or "." in num) and num.isdigit() and 1900 <= int(num) <= 2099:
            continue
        out.add(("-" if sign in ("-", "\u2212") else "") + norm + scale)
    return out


def verify_entry(entry: dict, repo: Path = REPO) -> tuple[bool, str]:
    """Does the pinned verbatim quote still exist at its source, and does it support the value?

    The quote must be non-blank and present VERBATIM (an empty quote occurs in every file), the value must be
    non-blank, and a value that carries numbers must share at least one with its quote, sign and scale included,
    so a wrong number cannot ride on a correct quote. That last rule is a SANITY FILTER, not proof: a value is a
    summary and may carry numbers its quote does not."""
    quote = str(entry.get("source_quote") or "")
    if not quote.strip():
        return False, "source_quote missing or empty -- nothing pins this figure to its source"
    if entry.get("value") is None or not str(entry.get("value")).strip():
        return False, "no value recorded"
    src = Path(entry.get("source_file", ""))
    if not src.is_absolute():
        src = repo / src
    if not src.is_file():
        return False, f"source_file missing: {entry.get('source_file')}"
    if quote not in src.read_text(encoding="utf-8", errors="replace"):
        return False, f"source_quote NO LONGER PRESENT in {entry.get('source_file')} -- registry and source disagree"
    vtext = str(entry.get("value"))
    vnums = _numbers(vtext)
    if not vnums and any(ch.isdigit() for ch in vtext):
        return False, "value has digits but no unambiguous number (e.g. glued to a sign) -- cannot substantiate it"
    if vnums and not (vnums & _numbers(quote)):
        return False, (f"value shares no number with its source_quote ({sorted(vnums)[:4]} vs quote) -- "
                       f"the quote does not substantiate the value")
    return True, ""


def find_bare_retractions(text: str, figs: dict):
    """Yield (figure_name, retracted_value, line_no, line) for retracted values stated without a marker nearby.

    EVERY occurrence needs its own marker within MARKER_WINDOW chars of ITSELF, measured in the full text across
    line breaks: a marked historical mention must not exempt a separate live claim."""
    import bisect
    lines = text.splitlines()
    starts, pos = [], 0                     # line start offsets, so a window can span any number of lines
    for ln in text.splitlines(keepends=True):
        starts.append(pos)
        pos += len(ln)
    marks = sorted(m.start() for r in _MARKER_RES for m in r.finditer(text))
    seen = set()
    for name, e in figs.items():
        vals = [str(v) for v in (e.get("retracted_values") or []) if str(v).strip()]
        for val in sorted(vals, key=len, reverse=True):
            # "12" must match neither "123" nor "12.5" / "12,000" (Unicode digits included)
            tail = r"(?!\w|[.,]\d)" if val[-1:].isalnum() else ""
            pat = re.compile(r"(?<![\w.])" + re.escape(val) + tail)
            for m in pat.finditer(text):
                i = bisect.bisect_right(starts, m.start()) - 1
                if (i, name) in seen:
                    continue
                lo, hi = m.start() - MARKER_WINDOW, m.end() + MARKER_WINDOW
                k = bisect.bisect_left(marks, lo)
                if not (k < len(marks) and marks[k] < hi):
                    seen.add((i, name))
                    yield name, val, i + 1, lines[i] if 0 <= i < len(lines) else ""


def cmd_figures() -> int:
    figs = load_figures()
    if not figs:
        print(f"no figures at {FIGURES_FILE}")
        return 1
    for name, e in figs.items():
        ok, why = verify_entry(e)
        print(f"  {'OK   ' if ok else 'STALE'} {name:24s} {str(e.get('value'))[:80]}")
        if not ok:
            print(f"        ^ {why}")
    return 0


def cmd_figure(query: str) -> int:
    figs = load_figures()
    q = query.strip().lower()
    name, hit = q, figs.get(q)
    if not hit:
        for n, e in figs.items():
            if q in n.lower() or any(q in a.lower() for a in e.get("aliases", [])):
                name, hit = n, e
                break
    if not hit:
        print(f"no figure matches {query!r}; try --figures")
        return 1
    ok, why = verify_entry(hit)
    if not ok:          # FAIL CLOSED: never emit a number the source no longer substantiates
        print(f"REFUSING TO EMIT {name} -- {why}\n  registry says: {hit.get('value')}\n"
              f"  authority    : {hit.get('source_file')} (re-read it, then fix ops/figures.yaml)")
        return 1
    print(f"{name}: {hit.get('value')}")
    return 0


def selftest() -> int:
    import tempfile
    fails = []

    def check(label, ok):
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            fails.append(label)

    figs = {"earnings": {"value": "~$400/yr", "retracted_values": ["$22k/yr"], "aliases": ["strategy x"]}}
    check("bare retracted value is caught", len(list(find_bare_retractions("It earns $22k/yr.", figs))) == 1)
    check("value next to a marker is allowed",
          not list(find_bare_retractions("RETRACTED: the $22k/yr figure was throughput.", figs)))
    check("no false hit on a longer number", not list(find_bare_retractions("It earns $122k/yr.", figs)))
    check("clean text yields nothing", not list(find_bare_retractions("It earns ~$400/yr.", figs)))
    check("bare 'correct' does NOT exempt a live claim",
          len(list(find_bare_retractions("The correct estimate is $22k/yr.", figs))) == 1)
    num = {"n": {"value": "7", "retracted_values": ["12"]}}
    check("no trailing-digit false hit (12 vs 123)", not list(find_bare_retractions("we saw 123 rows", num)))
    check("trailing boundary still catches 12", len(list(find_bare_retractions("we saw 12 rows", num))) == 1)
    check("a marked occurrence does not exempt a separate far-away bare one on the same line",
          len(list(find_bare_retractions("RETRACTED $22k/yr." + " x" * 200 + " Live: $22k/yr.", figs))) == 1)

    tmp = Path(tempfile.mkdtemp(prefix="figures_selftest_"))
    (tmp / "doc.md").write_text("net is ~$400/yr on committed capital", encoding="utf-8")
    good = {"source_file": "doc.md", "source_quote": "~$400/yr on committed", "value": "~$400/yr"}
    bad = {"source_file": "doc.md", "source_quote": "$999/yr", "value": "$999/yr"}
    check("verify passes when the quote is present", verify_entry(good, tmp)[0])
    check("verify fails CLOSED when the quote is gone", not verify_entry(bad, tmp)[0])
    check("verify fails CLOSED on a missing source", not verify_entry({"source_file": "nope.md"}, tmp)[0])
    check("verify fails CLOSED on an empty quote", not verify_entry({"source_file": "doc.md", "source_quote": ""}, tmp)[0])
    check("verify fails CLOSED on an absent quote", not verify_entry({"source_file": "doc.md"}, tmp)[0])
    check("verify fails CLOSED on a missing value", not verify_entry({"source_file": "doc.md", "source_quote": "~$400/yr"}, tmp)[0])
    check("verify fails CLOSED when the value's number is not in the quote",
          not verify_entry({"source_file": "doc.md", "source_quote": "~$400/yr on committed", "value": "$999/yr"}, tmp)[0])
    check("a number that is only a substring of a source number does NOT verify (40 vs 400)",
          not verify_entry({"source_file": "doc.md", "source_quote": "~$400/yr on committed", "value": "$40/yr"}, tmp)[0])
    check("verify passes when the value's number is in the quote",
          verify_entry({"source_file": "doc.md", "source_quote": "~$400/yr on committed", "value": "~$400/yr net"}, tmp)[0])
    check("'uncorrected' is not the marker CORRECTED", len(list(find_bare_retractions("The uncorrected $22k/yr.", figs))) == 1)
    check("tokenizer keeps sign and scale, drops bare years",
          _numbers("-2.8% 0.4k/yr $1,000 in 2026") == {"-2.8", "0.4k", "1000"})
    check("a previous-line marker does not reach a far-away claim",
          len(list(find_bare_retractions("RETRACTED: old claim\n" + "x" * 400 + " Live: $22k/yr.", figs))) == 1)
    check("a previous-line marker still covers a claim at the start of the next line",
          not list(find_bare_retractions("RETRACTED, do not use:\n$22k/yr was throughput.", figs)))
    print(f"selftest: {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--figures" in sys.argv:
        sys.exit(cmd_figures())
    if "--figure" in sys.argv and sys.argv.index("--figure") + 1 < len(sys.argv):
        sys.exit(cmd_figure(sys.argv[sys.argv.index("--figure") + 1]))
    print(__doc__)
