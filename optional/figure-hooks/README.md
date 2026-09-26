# figure-hooks — keep retracted numbers from coming back

**Add when:** a number gets misquoted twice. Until then `facts/registry.yaml` alone is enough.

Why this exists: in the source repo one wrong figure (throughput quoted as earnings, ~50x too high) was
deleted, re-entered a ledger a week later, and was caught again at a review gate. Prose does not hold a
correction. A registry of *retracted values* plus a hook that scans every reply for them does.

| file | hook | what it does | token cost |
|---|---|---|---|
| `figure_tripwire.py` | Stop | scans the reply just produced for a retracted value stated without a correction marker nearby; warns (default) or makes Claude correct itself (`FIGURE_TRIPWIRE=block`). Fires once per session/figure/value. | 0 unless it fires |
| `figure_jit.py` | UserPromptSubmit | measure-only by default: logs what it WOULD inject to `ops/jit_figures.jsonl`. After you've measured the hit rate, `HARNESS_JIT=inject` adds matched figures to context. | 0 in measure mode |
| `figures_lib.py` | — | shared registry code + `--figure <name>` fail-closed lookup + `--selftest` | — |
| `test_figures.py` | — | regression suite: every tripwire false positive/negative that shipped, the number tokenizer, `verify_entry`, and both hooks end to end | — |

Install:
1. Copy the four `.py` files to `<repo>/tools/hooks/`, and `figures.example.yaml` to `<repo>/ops/figures.yaml`.
2. Merge `settings.snippet.json` into `.claude/settings.json` (keep the existing keys). Needs `pip install pyyaml`.
3. `python tools/hooks/test_figures.py` and `python tools/hooks/figures_lib.py --selftest`, then `--figures`.
   The shipped example entry shows STALE until you point it at a real source. A real entry says OK only when
   its non-blank `source_quote` appears VERBATIM in `source_file`, its `value` is non-blank, and the value
   shares at least one number with the quote (sign and scale kept: `-2.8` is not `2.8`, `0.4k` is not `0.4`;
   bare years do not count). That last rule is a sanity filter, not proof. The JIT hook shows only verified
   values; the tripwire offers a correction only from a verified entry.
4. Probe: ask Claude to write a sentence containing a retracted value; you should see the warning.

Note on the original in the source repo: an earlier version of its JIT hook read the
prompt only from a `user_message` field. Claude Code sends `prompt`, so that version never logged a row. Both
the original and this port now read `prompt` first.

Retraction markers (`RETRACTION_MARKERS` in `figures_lib.py`) are found in the full reply at a word start,
so "avoid" is not VOID and "uncorrected" is not CORRECTED; a bare "correct" is not a marker. Each occurrence
of a retracted value needs its own marker within 150 characters, across line breaks.
