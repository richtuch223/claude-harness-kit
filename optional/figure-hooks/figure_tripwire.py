#!/usr/bin/env python
"""Stop hook: did the reply just state a RETRACTED number?

Output-time checking. A prompt-time hook only helps when the prompt names a known alias; a Stop hook sees
the text actually produced, so its coverage does not depend on guessing the topic.

Modes (env FIGURE_TRIPWIRE):
  warn  (default) -- show the user a warning (systemMessage); the turn stands.
  block           -- return decision=block with the correction as the reason, so Claude corrects itself.
Loop guard: fires at most once per (session, figure, value), and never when stop_hook_active is set.
Fail-open and silent on any error: a broken tripwire must never break a turn.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
STATE = Path(tempfile.gettempdir()) / "harness_figure_tripwire"


def last_assistant_text(ev: dict) -> str:
    """Prefer the field if the harness provides it; otherwise read the last assistant turn from the transcript."""
    msg = ev.get("last_assistant_message")
    if msg:
        return msg if isinstance(msg, str) else json.dumps(msg)
    path = ev.get("transcript_path")
    if not path or not Path(path).is_file():
        return ""
    last = ""
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = row.get("message") or {}
        if row.get("type") == "assistant" or m.get("role") == "assistant":
            content = m.get("content")
            if isinstance(content, str):
                last = content
            elif isinstance(content, list):
                text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
                if text.strip():
                    last = text
    return last


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    if ev.get("stop_hook_active"):
        return 0
    try:
        import figures_lib as fl
        msg = last_assistant_text(ev)
        if not msg.strip():
            return 0
        figs = fl.load_figures()
        hits = list(fl.find_bare_retractions(msg, figs))
    except Exception:
        return 0
    if not hits:
        return 0

    sid = re.sub(r"[^\w.-]", "_", str(ev.get("session_id", "nosession")))
    marker = STATE / f"{sid}.txt"
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        fired = (set(marker.read_text(encoding="utf-8", errors="replace").splitlines())
                 if marker.exists() else set())
    except (OSError, ValueError):
        fired = set()                       # unreadable loop-guard state: warn anyway, never crash the hook
    fresh, note = [], []
    for name, val, _ln, _line in hits:
        key = f"{name}|{val}"
        if key in fired:
            continue
        fresh.append(key)
        e = figs[name]
        try:
            ok, why = fl.verify_entry(e)
        except Exception as exc:  # noqa: BLE001 - a broken check must not certify a number
            ok, why = False, f"verification crashed: {exc}"
        if ok:                              # only a verified entry is offered as THE correction
            note.append(f"- You wrote {val!r}, which is RETRACTED. {name} is: {e.get('value')}"
                        + (f" ({e['retraction_note']})" if e.get("retraction_note") else ""))
        else:
            note.append(f"- You wrote {val!r}, which is RETRACTED for {name}. The registry's current value "
                        f"could not be verified ({why}); re-read the source before quoting any figure.")
    if not fresh:
        return 0
    try:
        marker.write_text("\n".join(sorted(fired | set(fresh))), encoding="utf-8")
    except (OSError, ValueError):
        pass                                # the warning still goes out; only the once-per-session guard is lost
    text = ("Retracted figure used (ops/figures.yaml):\n" + "\n".join(note)
            + "\nCorrect it if it changes anything the user would act on. "
              "Check: python tools/hooks/figures_lib.py --figure <name>")
    if os.environ.get("FIGURE_TRIPWIRE", "warn").lower() == "block":
        print(json.dumps({"decision": "block", "reason": text}))
    else:
        print(json.dumps({"systemMessage": text}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
