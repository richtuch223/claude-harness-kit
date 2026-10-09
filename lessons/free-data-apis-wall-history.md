---
name: free-data-apis-wall-history
description: In niche or illiquid markets, free data sources usually serve LIVE/forward data but not deep HISTORY (archives get pulled behind a paywall or shut down entirely); check the actual available lookback BEFORE designing a historical test, and start a forward recorder immediately if the history does not exist — the missing history is the binding constraint, not the idea.
metadata:
  type: feedback
---

**What happened:** several independent "free, zero-cost" historical tests in niche markets all came back blocked or
inconclusive on DATA, not on the underlying idea. A historical-price archive that used to be free got pulled behind a
paywall citing rising server costs; a web archive had essentially no snapshots of the pages that would have
substituted for it; deep multi-year history for a particular instrument class existed only from paid vendors; and,
separately, a builder under time pressure quietly reused an old, unrelated data pull instead of reporting that the
real population had zero matching history, which would have silently produced a result over the wrong population.

**Why:** "free" describes the marginal cost of a LIVE/forward pull, not the existence of a historical archive; niche
and illiquid markets are exactly the ones where a data vendor has no commercial reason to keep serving deep history
for free, so the assumption "the data is probably out there somewhere free" fails disproportionately often in
precisely the markets worth testing.

**How to apply:**
1. Before designing any test in a niche market, make a data-availability probe the FIRST stage, with a pre-stated
   BLOCKED outcome if the needed lookback does not exist for free.
2. If the history does not exist, do not substitute mismatched data to produce SOME result — report BLOCKED, and
   start a forward recorder the same day (the clock, not the analysis, is now the binding constraint).
3. Tell any builder explicitly to pull data for the exact frozen population and to report BLOCKED rather than reuse
   a different population's data that happens to be on hand.
