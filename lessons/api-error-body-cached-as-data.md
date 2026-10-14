---
name: api-error-body-cached-as-data
description: A parseable JSON reply is not proof of valid data — an API's error object ("busy", rate limit, auth) was cached and parsed as if it were the payload, and a None response was written to a cache. Validate shape and required keys before anything is parsed, cached, or appended to a ledger.
metadata:
  type: feedback
---

Three hits in one week in the source program. A delegated builder "saved the error body as though it was API data".
A filings-search collector crashed with `KeyError: 'hits'` on a reply that was an error object, not a result set. A
code gate on a third script found failed and `None` responses being written to its cache, so every later run read the
failure back as a quiet empty result. In each case the HTTP call "succeeded" and `json.loads` succeeded; the data was
wrong anyway.

**Why:** APIs return errors as 200-with-a-body more often than as HTTP errors, and a cache turns one transient failure
into a permanent wrong answer. The check has to sit between the fetch and the first use, and a failed check must be a
retryable failure, never a value.

**How to apply:**
- Every fetch that feeds a cache, a ledger, or a statistic goes through `tools/fetch_guard.py check_json(raw,
  expect="dict"|"list", require_keys=(...), nonempty=True)` first. It rejects empty bodies, non-JSON, top-level error
  keys (`error`, `errors`, `error_message`, `errorMessage`, `fault`), wrong top-level type, missing nested keys, and
  empty collections where data was required.
- `FetchInvalid` is retryable: log it, back off, never cache it, never write it as data.
- Put the required keys in the call (`require_keys=("hits.total.value",)`), not in a comment: the guard then fails on
  the day the vendor renames a field instead of silently returning zeros.
- The fleet contract's trap list names this failure; a builder that fetches anything cites the guard call in its
  report.
