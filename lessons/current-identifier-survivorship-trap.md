---
name: current-identifier-survivorship-trap
description: Never build a historical population by resolving entities through a CURRENT identifier list (today's ticker table, today's active-user list, today's SKU catalog) — it silently drops everything that no longer exists under that identifier, and can flip a backtest from PASS to KILL once the dead are added back.
metadata:
  type: feedback
---

**How it happens (illustrative):** suppose a historical event population (companies that did X in some past year) is
built by joining each event to a security through a CURRENT ticker/identifier table. Entities later delisted, renamed,
acquired or merged away do not appear in a current-only table, so they silently fall out as "unresolved" instead of
being counted. If a large share of the population lands in that bucket, recovering the missing (now-dead) entities from
a point-in-time or delisted-inclusive source can move a significant result to the other side of zero, and any
in-sample population built with the same join carries the identical bias.

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
