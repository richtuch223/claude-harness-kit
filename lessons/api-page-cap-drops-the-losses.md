---
name: api-page-cap-drops-the-losses
description: A per-market trade-history cap (a /trades endpoint with limit=1000 and no cursor) silently drops the EARLY prints of markets that later got busy - i.e. the seller's losing fills - and turned a fairly-priced tail into a fake 80% "kept"; a bias check must never select markets on the biased variable.
metadata:
  type: feedback
---

A tail-seller calibration on an exchange's hourly price ladders read ~80% of premium kept over 35 days (v1). The pull took
the latest 1,000 trades per market without paging. Markets where the tail WON generated thousands of late trades, so their
early cheap prints (the seller's losses) fell outside the window and winners were systematically missing. The paginated
re-pull (v2) read the tail as fairly priced (~1% kept, win rate ~ the tick). The intermediate "truncation check" sampled the
40 highest-tail-VOLUME markets - the very variable truncation corrupts (winners had LOW recorded tail volume) - and
understated the bias ten-fold.

**Why:** a page cap is a selection on recency; on a tape whose losers are the late-heavy markets, recency selection is
survivorship of the losers' losses. The cross-family reviewer (`cross-review`) flagged it as a MAJOR finding before the v2
tape existed - the gate earned its keep on its first funded run.

**How to apply:** (1) ALWAYS page trade/print histories to exhaustion (cursor or time windows) before any calibration, and
check each list endpoint's REAL page size: some cap silently below the `limit` you ask for, and some reject deep offsets;
(2) when checking a suspected truncation bias, sample markets by OUTCOME strata (winners vs losers), never by the truncated
quantity; (3) treat any "kept" figure from a capped pull as an upper bound until re-pulled. Related:
[[newest-first-order-makes-first-print-a-lookahead]].
