---
name: in-sample-mean-normalization-hides-the-contrast
description: A statistic measured relative to its OWN in-sample mean/baseline carries ALL of that mean's shrinkage with it, so it cannot isolate a later, specific event (e.g. "did decay accelerate after X?") — pre-state the actual contrast you mean (e.g. a difference-in-slopes before vs. after X) before computing anything, or the headline number answers a different question than the one asked.
metadata:
  type: feedback
---

**What happened:** a test asked "does decay accelerate after some specific later event (e.g. publication, a rule
change, a platform migration)?" The implementation measured the statistic's drop relative to its own in-sample mean
and called that "the post-event effect." But in-sample-to-out-of-sample shrinkage happens anyway, for everything,
regardless of whether the later event occurred — so a number built by comparing to the in-sample mean already
contains that generic shrinkage baked in, and cannot by itself say whether anything EXTRA happened at the event.

**Why:** "relative to the in-sample mean" and "relative to a published/baseline estimate" are not the same reference
point as "relative to what would have happened anyway without the event" — conflating them makes ordinary shrinkage
look like event-specific decay, or can mask real event-specific decay inside noise from the baseline comparison.

**How to apply:**
- Before computing a "did X happen after event E" statistic, write down the actual CONTRAST you mean as an equation:
  typically a difference between two already-estimated quantities (e.g. `slope_after - slope_before`, or
  `effect_post_E - effect_in_sample`), not a single number measured against one baseline.
- A statistic normalized against an in-sample mean, a backtest's own average, or a single published estimate is a
  LEVEL, not a contrast; label it as a level, and compute the actual before/after (or treated/control) difference
  separately if that is the real question.
- Pre-state the contrast in the design doc before any data is read, exactly like a kill condition — a contrast
  decided after seeing the number is as capable of fooling the author as any other post-hoc choice.
