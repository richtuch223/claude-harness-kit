---
name: newest-first-order-makes-first-print-a-lookahead
description: "Most REST trade endpoints return NEWEST-FIRST; any \"first print\" / \"first fill\" construction on an unsorted pull silently uses the LAST print and its state, which is a look-ahead. Sort by parsed timestamp before any first/last logic, and have the verifier check row order."
metadata:
  type: feedback
---

**What happened:** a tail tape (`prints.jsonl`) was written straight from an exchange's trades endpoint, which pages
newest-first per market. The analysis took the "first print at a tick" as the fill and measured moneyness there. In file
order that is the LAST print, so the distance-to-spot condition was evaluated at the latest state: markets where spot had
drifted into the strike after the real fill got a late print that failed the filter and dropped out of the cell. The cell read
+58% [+15, +98] and passed a pre-stated bar; the honest chronological construction is +32% [-34, +93] with a -149% week. A
fresh verifier seat caught it (an ascending/descending pair check over 113k rows); the author's cross-family code review
had not.

**Why:** an ordering assumption is invisible in the code (the loop says "first"), and every dedupe-by-key-keep-first idiom inherits
it. It is the same class as [[api-page-cap-drops-the-losses]]: the transport's default silently selects the sample.

**How to apply:**
- Sort every trade/print/event tape by its parsed timestamp before ANY first/last/once-per-key logic, and assert monotonicity in
  the script (count descending vs ascending consecutive pairs; print it).
- When conditioning a fill on state (moneyness, time-to-close, book depth), the fill must be the first print that satisfied the
  rule AT ITS OWN TIME, never the first row.
- Verifier prompts for any "first print" construction must include "check the row order of the input file".
