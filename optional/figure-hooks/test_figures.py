#!/usr/bin/env python
"""Regression tests for the figure hooks (ported 2026-09-25 from the source repo's test_figure_tripwire.py).

Every tripwire case is a bug that actually shipped. The filter has been wrong in BOTH directions, and each time
the failure looked like success:

  - too LOOSE: "44%" matched inside "144%"; ~~struck-through~~ values read as live
  - too TIGHT: a marker anywhere on the line exempted the match, which silently exempted a real live claim
  - too TIGHT: markers were matched case-sensitively, so lowercase "retracted" in prose never matched
  - too TIGHT: a window slice cut "uncorrected" into "corrected", and "avoid" contained VOID

Rule this encodes: any change to RETRACTION_MARKERS, the window, the tokenizer or verify_entry must keep BOTH a
known true positive and a known false positive pinned.

    python tools/hooks/test_figures.py          (exit 0 = all pass)
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_lib as fl                                       # noqa: E402

FIGS = {
    "varianta_earnings": {
        "value": "gross ~$0.4k/yr median",
        "retracted_values": ["$22k/yr", "22k/yr"],
        "retraction_note": "notional throughput, not earnings",
    },
    "cbse_as_tight": {"value": "net -0.371bp", "retracted_values": ["+7.07bp"]},
    "wide_book_as_ramp": {"value": "+10.87bp", "retracted_values": ["by 44%"]},
    "toy_trailing": {"value": "$5", "retracted_values": ["$12"]},
}

CASES = [
    # (should_fire, label, text)
    (True,  "bare claim",
     "Strategy X earns about $22k/yr so it clears the bar."),
    (False, "next to UPPERCASE retraction",
     "The ~$22k/yr figure is RETRACTED -- it is notional throughput, not profit."),
    (False, "next to lowercase retraction",
     "STATE.md:388 uses `beats $22k/yr` as a filter. It is the only bare "
     "retracted value left in the always-on tier."),
    (False, "correct figure only",
     "Strategy X grosses about $0.4k/yr at median sizing."),
    (False, "markdown strikethrough",
     "| ~~wide~~ | ~~62.77bp~~ | ~~**+7.07bp** CI [+0.97,+11.35]~~ |"),
    (False, "short needle must not match inside a longer number",
     "Realized vol was approximately 144% into the risk-off."),
    (True,  "marker present but FAR away on a long line",
     "The reciprocal test ran and was gated SOUND, with an enumerated census of "
     "966 pair-venues of which 647 had at least 120 clean days, and the decisive "
     "contingency restricted to the 16 pairs that beats $22k/yr is empty, which "
     "means the population supports no such cell " + ("padding. " * 12)
     + "Separately something was corrected elsewhere."),
    (True,  "the ordinary word 'correct' is not a retraction marker",
     "The correct estimate is $22k/yr."),
    (True,  "a marked mention does not exempt a separate live claim on the same line",
     "The old $22k/yr was RETRACTED. " + ("filler words here. " * 12) + "Live today: $22k/yr."),
    (True,  "a previous-line marker counts only within the window",
     "RETRACTED: old claim\n" + "x" * 400 + " Live: $22k/yr."),
    (False, "retracted needle must not match as the head of a longer number",
     "The pilot spent $123 on data."),
    (True,  "control: bare claim fires",
     "Strategy X earns $22k/yr."),
    (False, "marker two lines above, within 150 chars (window spans lines)",
     "RETRACTED\n\nStrategy X $22k/yr figure."),
    (False, "needle must not match a longer decimal or grouped number",
     "Spent $12.5 then $12,000 on data."),
    (True,  "'avoid' is not the marker VOID",
     "Avoid overstating returns: $22k/yr."),
    (True,  "'uncorrected' is not the marker CORRECTED",
     "The uncorrected figure $22k/yr stands."),
    (True,  "a window edge must not cut 'uncorrected' into 'corrected'",
     "uncorrected" + (" " * 141) + "$22k/yr"),
    (True,  "'do NOTHING' is not a marker",
     "We do NOTHING to adjust $22k/yr."),
    (True,  "'do NOT_x' is not a marker either",
     "We do NOT_adjust $22k/yr."),
    (False, "needle must not match before a Unicode digit",
     "Spent $12٣ and $12.٥ on data."),
    # kit phrase markers: a phrase must also END at a word end
    (True,  "'was wrongly' is not the phrase marker 'was wrong'",
     "The analyst was wrongly credited with $22k/yr."),
    (False, "phrase marker 'was wrong' at a word end",
     "The $22k/yr figure was wrong."),
    (False, "INCORRECT is a marker",
     "INCORRECT: $22k/yr is throughput."),
]


def verify_cases(fails: list) -> None:
    """verify_entry must refuse an empty quote and a value its quote does not carry (numbers matched whole)."""
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    Path(path).write_text("net is ~$400/yr on committed capital", encoding="utf-8")
    probes = [
        (False, "empty source_quote", {"source_file": path, "source_quote": "", "value": "$400/yr"}),
        (False, "blank source_quote", {"source_file": path, "source_quote": "   ", "value": "$400/yr"}),
        (False, "value not carried by its quote", {"source_file": path, "source_quote": "~$400/yr on committed",
                                                   "value": "$999/yr"}),
        (False, "substring number ($40 inside $400)", {"source_file": path, "source_quote": "~$400/yr on committed",
                                                       "value": "$40/yr"}),
        (True,  "value supported by its quote", {"source_file": path, "source_quote": "~$400/yr on committed",
                                                 "value": "~$400/yr net"}),
        (False, "missing value", {"source_file": path, "source_quote": "~$400/yr on committed"}),
        (False, "blank value", {"source_file": path, "source_quote": "~$400/yr on committed", "value": "  "}),
        (False, "value whose only number is glued to a sign", {"source_file": path,
                                                               "source_quote": "net is ~$400/yr", "value": "USD−2.8"}),
        (False, "padded quote is not verbatim", {"source_file": path, "source_quote": "  net is ~$400  ",
                                                 "value": "$400"}),
        (False, "missing source file", {"source_file": path + ".nope", "source_quote": "net is", "value": "$400"}),
    ]
    # the tokenizer keeps sign and scale, reads commas only in groups of three, and drops bare years; a number
    # glued to a sign it did not capture yields nothing rather than a silently positive token
    tok = [("-2.8%", {"-2.8"}), ("−2.8%", {"-2.8"}), ("+2.8%", {"2.8"}), ("0.4k/yr", {"0.4k"}),
           ("$1,000/event", {"1000"}), (".4", {"0.4"}), ("4,5", {"4"}), ("10bp", {"10"}),
           ("in 2026 at 1.14", {"1.14"}), ("-$977k/yr", {"-977k"}), ("USD−2.8", set()), ("USD−$5", set())]
    for text, want in tok:
        got = fl._numbers(text)
        print(f"  {'ok  ' if got == want else 'FAIL'} tokenize {text!r} -> {sorted(got)}")
        if got != want:
            fails.append((f"tokenize {text!r}", want, got))
    _ok, why = fl.verify_entry({"source_file": path, "source_quote": "net is", "value": 0})
    zero_ok = why != "no value recorded"
    print(f"  {'ok  ' if zero_ok else 'FAIL'} verify does not treat a numeric zero value as blank")
    if not zero_ok:
        fails.append(("numeric zero value", True, zero_ok))
    for expect, label, entry in probes:
        ok, _ = fl.verify_entry(entry)
        print(f"  {'ok  ' if ok == expect else 'FAIL'} verify {'passes' if expect else 'refuses'} {label}")
        if ok != expect:
            fails.append((label, expect, ok))
    os.unlink(path)


def _run_hook(module, event: dict, figs: dict) -> str:
    """Run a hook's main() with a patched registry and stdin; return what it printed."""
    orig_load, orig_stdin = fl.load_figures, sys.stdin
    fl.load_figures = lambda *a, **k: figs
    sys.stdin = io.StringIO(json.dumps(event))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            module.main()
    finally:
        fl.load_figures, sys.stdin = orig_load, orig_stdin
    return buf.getvalue()


def hook_cases(fails: list) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="figtest_"))
    (tmp / "doc.md").write_text("net is ~$400/yr on committed capital", encoding="utf-8")
    good = {"aliases": ["alpha"], "value": "~$400/yr", "source_file": str(tmp / "doc.md"),
            "source_quote": "~$400/yr on committed", "retracted_values": ["$22k/yr"]}
    stale = {"aliases": ["beta"], "value": "$999/yr", "source_file": str(tmp / "doc.md"),
             "source_quote": "$999/yr is gone"}

    def check(label, ok, got=None):
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        if not ok:
            fails.append((label, True, got))

    # tripwire: an unusable state dir must not swallow the warning
    import figure_tripwire as tw
    orig_state = tw.STATE
    blocker = tmp / "state_is_a_file"
    blocker.write_text("x", encoding="utf-8")
    tw.STATE = blocker                                  # mkdir on an existing FILE raises OSError
    try:
        out = _run_hook(tw, {"session_id": "t1", "last_assistant_message": "It earns $22k/yr."}, {"earn": good})
    finally:
        tw.STATE = orig_state
    check("tripwire still warns when its state dir is unusable", "RETRACTED" in out and "~$400/yr" in out, out)
    tw.STATE = tmp / "state"
    try:
        stale_r = dict(stale, retracted_values=["$22k/yr"])
        out = _run_hook(tw, {"session_id": "t2", "last_assistant_message": "It earns $22k/yr."}, {"earn": stale_r})
        check("tripwire never offers an unverified value as the correction",
              "RETRACTED" in out and "$999/yr" not in out and "could not be verified" in out, out)
        out2 = _run_hook(tw, {"session_id": "t2", "last_assistant_message": "It earns $22k/yr."}, {"earn": stale_r})
        check("tripwire fires once per session/figure/value", out2.strip() == "", out2)
        state_file = tw.STATE / "t2.txt"
        check("tripwire state file is written as utf-8",
              state_file.exists() and "earn|$22k/yr" in state_file.read_text(encoding="utf-8"))
    finally:
        tw.STATE = orig_state

    # JIT: inject mode shows only verified values
    import figure_jit as jit
    orig_log, orig_mode = jit.LOG, os.environ.get("HARNESS_JIT")
    jit.LOG = tmp / "jit.jsonl"
    os.environ["HARNESS_JIT"] = "inject"
    try:
        out = _run_hook(jit, {"session_id": "t3", "prompt": "how much do alpha and beta make?"},
                        {"good": good, "stale": stale})
    finally:
        jit.LOG = orig_log
        if orig_mode is None:
            os.environ.pop("HARNESS_JIT", None)
        else:
            os.environ["HARNESS_JIT"] = orig_mode
    check("JIT injects the verified value", "~$400/yr" in out, out)
    check("JIT never injects an unverified value", "$999/yr" not in out, out)
    row = json.loads((tmp / "jit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    check("JIT logs the stale figure as skipped", row.get("stale_skipped") == ["stale"], row)


def main() -> int:
    fails = []
    for expect, label, text in CASES:
        hits = list(fl.find_bare_retractions(text, FIGS))
        ok = bool(hits) == expect
        print(f"  {'ok  ' if ok else 'FAIL'} {'fires' if expect else 'silent':6s} {label}")
        if not ok:
            fails.append((label, expect, [h[1] for h in hits]))
    ci_hits = list(fl.find_bare_retractions("The $22k/yr figure was retracted as throughput.", FIGS))
    print(f"  {'ok  ' if not ci_hits else 'FAIL'} markers compared case-insensitively")
    if ci_hits:
        fails.append(("case-insensitive markers", False, [h[1] for h in ci_hits]))
    verify_cases(fails)
    hook_cases(fails)
    print()
    if fails:
        for label, expect, got in fails:
            print(f"  FAILED: {label!r} expected {expect!r}, got {got!r}")
        return 1
    print(f"  all pass ({len(CASES)} tripwire cases + tokenizer/verify/hook probes) -- filter pinned both ways.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
