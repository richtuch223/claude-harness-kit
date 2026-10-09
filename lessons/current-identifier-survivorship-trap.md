---
name: current-identifier-survivorship-trap
description: Never build a historical population by resolving entities through a CURRENT identifier list (today's ticker table, today's active-user list, today's SKU catalog) — it silently drops everything that no longer exists under that identifier, and can flip a backtest from PASS to KILL once the dead are added back.
metadata:
  type: feedback
---

**What happened:** a historical event population (companies that did X in some past year) was built by joining each
event to a security through a CURRENT ticker/identifier table. Entities that were later delisted, renamed, acquired,
or merged away do not appear in a current-only table, so they silently fell out as "unresolved" rather than being
counted. On a re-check, the majority of the original population fell into that unresolved bucket; recovering the
missing (now-dead) entities from a point-in-time or delisted-inclusive source changed a statistically significant
PASS into a result on the other side of zero. The in-sample training population used the same current-only join and
carried the identical, undetected bias.

**Why:** "current identifier list" and "still existed" are the SAME condition for anything measured after the fact,
so filtering on identifier resolvability is really filtering on survival — and survival is correlated with the
outcome you are usually trying to measure (things that failed, delisted, or were acquired away are disproportionately
the ones a naive join drops).

**How to apply:**
- Resolve historical entities through a point-in-time or delisted/inactive-inclusive source, never a current-only
  lookup table, whenever the population spans more than a very recent window.
- Report the unresolved/no-match share explicitly, and treat a large unresolved bucket as an INSTRUMENT problem
  (NEEDS_DATA), not as a neutral exclusion you can ignore.
- Before trusting any historical population's result, ask: "would an entity that no longer exists under its original
  identifier have been silently dropped by this join?" If yes, re-run with a delisted/inactive-inclusive source
  before believing the number.
