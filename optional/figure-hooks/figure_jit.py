#!/usr/bin/env python
"""UserPromptSubmit hook: just-in-time figure injection -- MEASURE-ONLY by default.

Alias matching cannot be the safety mechanism ("how much does that make?" names no alias), so first
measure the hit rate before anything depends on it.

  measure (default) -- inject NOTHING; append one JSONL row per prompt to ops/jit_figures.jsonl recording
                       what it WOULD have injected and whether the prompt looked like it wanted a number.
  inject            -- emit the matched figures as additionalContext. Enable with HARNESS_JIT=inject.

The real coverage metric: of prompts that plausibly wanted a figure, how often did we match?
Fail-open and silent: this sits on the prompt's critical path.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
LOG = HERE.parents[1] / "ops" / "jit_figures.jsonl"
MAX_INJECT = 4

# Deliberately broad: it is the DENOMINATOR of the coverage metric, so over-counting understates the hit rate.
NUMERIC_INTENT = re.compile(
    r"\$\d|\d+\s?%|\bhow (much|many)\b|\bwhat.{0,12}(number|figure|value)\b|\bmetric\b|\bbenchmark\b"
    r"|\bcost\b|\brevenue\b|\blatency\b|\baccuracy\b|\brecall\b|\bprecision\b", re.I)


def match_aliases(prompt: str, figs: dict) -> tuple[list[str], int]:
    """Figures whose aliases appear in the prompt, in registry order. Short aliases need word boundaries."""
    low = prompt.lower()
    hits = []
    for name, e in figs.items():
        for a in e.get("aliases", []) + [name]:
            al = a.lower()
            found = (al in low if " " in al or len(al) > 8
                     else re.search(r"(?<![\w-])" + re.escape(al) + r"(?![\w-])", low))
            if found:
                hits.append(name)
                break
    return hits[:MAX_INJECT], max(0, len(hits) - MAX_INJECT)


def _verified(fl, entry: dict) -> bool:
    try:
        return fl.verify_entry(entry)[0]
    except Exception:  # noqa: BLE001 - a broken check must not certify a number
        return False


def main() -> int:
    t0 = time.time()
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = ev.get("prompt") or ev.get("user_message") or ""   # Claude Code sends `prompt`
    if not prompt.strip():
        return 0
    try:
        import figures_lib as fl
        figs = fl.load_figures()
        matched, dropped = match_aliases(prompt, figs)
        # Never inject a figure as "authoritative" when its pinned quote no longer appears in its source.
        kept = [n for n in matched if _verified(fl, figs[n])]
        stale = [n for n in matched if n not in kept]
    except Exception:
        return 0
    mode = os.environ.get("HARNESS_JIT", "measure").lower()
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"session": str(ev.get("session_id", ""))[:8], "mode": mode,
                                 "prompt_chars": len(prompt), "numeric_intent": bool(NUMERIC_INTENT.search(prompt)),
                                 "matched": kept, "stale_skipped": stale, "collisions_dropped": dropped,
                                 "elapsed_ms": round((time.time() - t0) * 1000, 1)}) + "\n")
    except Exception:
        pass
    if mode != "inject" or not kept:
        return 0
    # `kept` holds only entries whose quote verified above: an unsubstantiated number is never shown.
    lines = ["Figures relevant to this prompt (ops/figures.yaml; only quote-verified values are shown):"]
    for n in kept:
        e = figs[n]
        lines.append(f"  - {n}: {e.get('value')}")
        if e.get("retracted_values"):
            lines.append(f"    NOT {', '.join(e['retracted_values'])}"
                         + (f" -- {e['retraction_note']}" if e.get("retraction_note") else ""))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
