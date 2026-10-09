#!/usr/bin/env python
"""fleet_ledger.py -- one row per agent fan-out: what it cost and what it produced (the `fleet` skill, section 5).

Why: a multi-agent fan-out can spend millions of subagent tokens in a day with nobody tracking cost per
ACCEPTED finding. The standing industry advice is to compare "cost per validated outcome", not price per
token; this ledger makes that visible and computable instead of anecdotal.

  python tools/fleet_ledger.py add --run R --purpose P --agents N --claude-tokens T --reviewer-calls A \
      --accepted K --vein V --research-accepted RK [--banked REF] [--notes TEXT] [--date YYYY-MM-DD]
  python tools/fleet_ledger.py summary
  python tools/fleet_ledger.py mvt [--flags-only]
  python tools/fleet_ledger.py --selftest

Ledger: ops/fleet_ledger.tsv (tab-separated, append-only, committed; override the path via --ledger or
the LEDGER module attribute for a different project layout).

MVT (minimum viable test / "when to stop digging a vein"): a standing research-management rule says
leave a line of work at the PROGRAM'S AVERAGE yield, not when it goes to zero -- but that rule is inert
until something actually measures per-line yield. `vein` tags the line of work; `research_accepted`
counts accepted RESEARCH findings only (verdicts, registry figures, instruments that feed a line),
excluding harness/tooling fixes, so harness work can never prop up a vein's measured yield. `mvt` prints
accepted research findings per million Claude tokens by vein against the program average, and FLAGS a
vein whose last two tagged runs both sit below that average. A flag means "review whether this vein is
still worth its cost", never an auto-stop -- sizing/stopping judgment stays with the person running the
program. `?` = not tagged (a legacy or deliberately-mixed row); those rows are excluded from the rates.
Rows logged with zero Claude tokens (e.g. a scouting-only pass) count in totals but have no rate.
"""
import argparse
import csv
import datetime as dt
import sys
import tempfile
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "ops" / "fleet_ledger.tsv"  # <repo>/tools/.. -> <repo>/ops/
COLS = ["date", "run", "purpose", "agents", "claude_tokens", "reviewer_calls", "accepted", "banked", "notes",
        "vein", "research_accepted"]
NO_FLAG = {"harness"}


def _rows(path):
    return list(csv.DictReader(path.open(encoding="utf-8"), delimiter="\t"))


def add(a, path=None):
    path = path or LEDGER
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        if new:
            w.writerow(COLS)
        w.writerow([a.date or dt.date.today().isoformat(), a.run, a.purpose, a.agents, a.claude_tokens,
                    a.reviewer_calls, a.accepted, a.banked or "", a.notes or "", a.vein,
                    "?" if a.research_accepted is None else a.research_accepted])
    print(f"logged {a.run} -> {path}")


def summary(_, path=None):
    path = path or LEDGER
    if not path.exists():
        sys.exit("no ledger yet")
    rows = _rows(path)
    tot_t = tot_k = 0
    print(f"{'date':10s} {'run':28s} {'vein':16s} {'agents':>6s} {'claude_tok':>11s} {'rev':>5s} {'acc':>4s} {'tok/accepted':>13s}  purpose")
    for r in rows:
        t, k = int(r["claude_tokens"]), int(r["accepted"])
        tot_t += t
        tot_k += k
        per = f"{t / k:,.0f}" if k else "n/a"
        print(f"{r['date']:10s} {r['run'][:28]:28s} {(r.get('vein') or '?')[:16]:16s} {r['agents']:>6s} {t:>11,d} "
              f"{r.get('reviewer_calls', ''):>5s} {k:>4d} {per:>13s}  {r['purpose'][:60]}")
    print(f"TOTAL claude tokens {tot_t:,}  accepted {tot_k}  -> {tot_t / tot_k:,.0f} tokens per accepted finding" if tot_k else "")


def mvt_table(rows):
    """Return (program_rate, {vein: dict}, flagged_veins). Rates are research findings per 1M Claude tokens."""
    tagged = [r for r in rows if (r.get("vein") or "?") != "?" and (r.get("research_accepted") or "?") != "?"
              and int(r["claude_tokens"]) > 0]
    tot_t = sum(int(r["claude_tokens"]) for r in tagged)
    tot_k = sum(int(r["research_accepted"]) for r in tagged)
    prog = tot_k / tot_t * 1e6 if tot_t else 0.0
    veins = {}
    for r in tagged:
        v = veins.setdefault(r["vein"], {"runs": [], "tok": 0, "acc": 0})
        t, k = int(r["claude_tokens"]), int(r["research_accepted"])
        v["tok"] += t
        v["acc"] += k
        v["runs"].append((r["date"], r["run"], k / t * 1e6))
    flagged = []
    for name, v in veins.items():
        v["rate"] = v["acc"] / v["tok"] * 1e6
        last2 = v["runs"][-2:]
        # harness work produces no research findings by definition; it is shown but never MVT-flagged
        v["flag"] = name not in NO_FLAG and len(last2) == 2 and all(rate < prog for _, _, rate in last2)
        if v["flag"]:
            flagged.append(name)
    return prog, veins, sorted(flagged)


def mvt(a, path=None):
    path = path or LEDGER
    if not path.exists():
        sys.exit("no ledger yet")
    rows = _rows(path)
    prog, veins, flagged = mvt_table(rows)
    if a.flags_only:
        print(", ".join(flagged) if flagged else "none")
        return
    untagged = sum(1 for r in rows if (r.get("vein") or "?") == "?" or (r.get("research_accepted") or "?") == "?")
    print(f"program average: {prog:.2f} accepted research findings per 1M Claude tokens "
          f"({len(rows) - untagged} tagged rows; {untagged} untagged rows excluded)")
    print(f"{'vein':18s} {'runs':>4s} {'claude_tok':>11s} {'research_acc':>12s} {'per_1M':>7s}  last-2 run rates  flag")
    for name, v in sorted(veins.items(), key=lambda kv: -kv[1]["rate"]):
        last = " / ".join(f"{rate:.2f}" for _, _, rate in v["runs"][-2:])
        print(f"{name[:18]:18s} {len(v['runs']):>4d} {v['tok']:>11,d} {v['acc']:>12d} {v['rate']:>7.2f}  "
              f"{last:16s}  {'MVT REVIEW' if v['flag'] else ''}")
    if flagged:
        print("MVT REVIEW (not a stop): last two runs below the program average -> " + ", ".join(flagged))


def selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

    tmp = Path(tempfile.mkdtemp()) / "ledger.tsv"
    base = dict(purpose="p", agents=1, reviewer_calls=0, accepted=1, banked="", notes="", date="2026-10-08")
    for run, vein, tok, ra in [("a1", "good", 1_000_000, 4), ("a2", "good", 1_000_000, 4),
                               ("b1", "bad", 1_000_000, 0), ("b2", "bad", 1_000_000, 0),
                               ("c1", "onerun", 1_000_000, 0), ("h1", "harness", 1_000_000, 0),
                               ("x1", "?", 5_000_000, None), ("z1", "scout", 0, 3)]:
        add(argparse.Namespace(run=run, vein=vein, claude_tokens=tok, research_accepted=ra, **base), tmp)
    prog, veins, flagged = mvt_table(_rows(tmp))
    check("program average = 8 research findings / 6M tokens (untagged and zero-token rows excluded)",
          abs(prog - 8 / 6) < 1e-9)
    check("vein above average is not flagged", not veins["good"]["flag"])
    check("vein with two runs below average is flagged", "bad" in flagged)
    check("a vein with one run below average is not flagged yet", "onerun" not in flagged)
    check("untagged row never appears as a vein", "?" not in veins)
    check("zero-token reviewer-only row is excluded from rates", "scout" not in veins)
    check("harness vein is shown but never flagged", "harness" in veins and "harness" not in flagged)
    legacy = _rows(LEDGER) if LEDGER.exists() else []
    check("real ledger parses with the vein columns", all("vein" in r for r in legacy))
    print("fleet_ledger selftest: " + ("all checks passed" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add")
    for c in ["run", "purpose"]:
        p.add_argument(f"--{c}", required=True)
    for c in ["agents", "claude-tokens", "reviewer-calls", "accepted"]:
        p.add_argument(f"--{c}", type=int, required=True)
    p.add_argument("--vein", required=True, help="line of work, e.g. pricing, data-collection, harness; ? if mixed")
    p.add_argument("--research-accepted", type=int, default=None,
                   help="accepted RESEARCH findings only (exclude harness fixes); omit if unknown")
    p.add_argument("--banked")
    p.add_argument("--notes")
    p.add_argument("--date")
    p.set_defaults(fn=add)
    sp.add_parser("summary").set_defaults(fn=summary)
    m = sp.add_parser("mvt")
    m.add_argument("--flags-only", action="store_true")
    m.set_defaults(fn=mvt)
    a = ap.parse_args()
    a.fn(a)
