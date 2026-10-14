"""fetch_guard.py -- validate a fetched API response BEFORE it is parsed into data or written to a cache.

Why: a delegated script in the source program saved an API error body as though it were data; the same week a
filings collector crashed on an error object that had no 'hits' key, and a code gate found failed/None responses
being cached. A parseable JSON object is not proof of valid data. Rule: every fetch that feeds a cache or a ledger
goes through check_json() first; a FetchInvalid is a retryable failure (never cached, never written as data).
See lessons/api-error-body-cached-as-data.md.

Usage:
    from tools.fetch_guard import check_json, FetchInvalid      (or a sys.path insert of tools/)
    js = check_json(raw, expect="dict", require_keys=("hits",))       # raises FetchInvalid
    rows = check_json(raw, expect="list", nonempty=True)

python tools/fetch_guard.py --selftest
"""
import json
import sys

# Keys that, at the top level of a JSON object, mean the server answered with an error instead of data.
ERROR_KEYS = ("error", "errors", "error_message", "errorMessage", "fault")


class FetchInvalid(ValueError):
    """The response is not usable data. Retry later; never cache it."""


def _reject_constant(name):
    raise ValueError(f"non-JSON constant {name}")


def _finite_float(s):
    f = float(s)
    if f != f or f in (float("inf"), float("-inf")):     # 1e400 parses to inf: not a number we can store
        raise ValueError(f"non-finite number {s}")
    return f


def check_json(raw, expect=None, require_keys=(), nonempty=False, max_snippet=160):
    """Parse `raw` (str/bytes) as JSON and validate its shape. Returns the parsed object.

    expect:       None | "dict" | "list" -- required top-level type.
    require_keys: for a dict, keys that must be present (nested paths as "a.b").
    nonempty:     a list/dict must have at least one element.
    Raises FetchInvalid with a short snippet of the body on any failure."""
    if raw is None:
        raise FetchInvalid("no response body (None)")
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")          # strict: a replacement character would silently alter a key
        except UnicodeDecodeError as e:
            raise FetchInvalid(f"body is not valid UTF-8 at byte {e.start}")
    text = raw.strip()
    snippet = text[:max_snippet].replace("\n", " ")
    if not text:
        raise FetchInvalid("empty response body")
    try:
        obj = json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)   # no NaN/inf, ever
    except ValueError:
        raise FetchInvalid(f"body is not JSON: {snippet!r}")
    if isinstance(obj, dict):
        bad = [k for k in ERROR_KEYS if k in obj and obj[k]]
        if bad:
            raise FetchInvalid(f"error object (key {bad[0]!r}): {snippet!r}")
    if expect == "dict" and not isinstance(obj, dict):
        raise FetchInvalid(f"expected a JSON object, got {type(obj).__name__}: {snippet!r}")
    if expect == "list" and not isinstance(obj, list):
        raise FetchInvalid(f"expected a JSON list, got {type(obj).__name__}: {snippet!r}")
    for path in require_keys:
        cur = obj
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise FetchInvalid(f"missing key {path!r}: {snippet!r}")
            cur = cur[part]
    if nonempty and isinstance(obj, (list, dict)) and len(obj) == 0:
        raise FetchInvalid("empty list/object where data was required")
    return obj


def _selftest():
    ok = 0

    def expect_invalid(raw, **kw):
        try:
            check_json(raw, **kw)
        except FetchInvalid:
            return True
        return False

    checks = [
        ("none_rejected", expect_invalid(None)),
        ("empty_rejected", expect_invalid("   ")),
        ("html_rejected", expect_invalid("<html>503 Service Unavailable</html>")),
        ("plain_text_rejected", expect_invalid("Unauthenticated")),
        ("error_object_rejected", expect_invalid('{"error": "rate limit"}', expect="dict")),
        ("error_without_required_key_rejected", expect_invalid('{"message": "busy"}', expect="dict", require_keys=("hits",))),
        ("nested_key_required", expect_invalid('{"hits": {}}', expect="dict", require_keys=("hits.total",))),
        ("wrong_type_rejected", expect_invalid('{"a": 1}', expect="list")),
        ("empty_list_rejected_when_nonempty", expect_invalid("[]", expect="list", nonempty=True)),
        ("valid_dict_passes", check_json('{"hits": {"total": {"value": 3}}}', expect="dict",
                                         require_keys=("hits.total.value",))["hits"]["total"]["value"] == 3),
        ("valid_list_passes", check_json(b'[{"date": "2026-10-09", "close": 1.0}]', expect="list", nonempty=True)[0]["close"] == 1.0),
        ("empty_list_allowed_by_default", check_json("[]", expect="list") == []),
        ("falsy_error_key_ignored", check_json('{"error": null, "data": [1]}', expect="dict")["data"] == [1]),
        ("invalid_utf8_rejected", expect_invalid(b'{"id": "\xff"}', expect="dict")),
        ("nan_rejected", expect_invalid("[NaN]", expect="list", nonempty=True)),
        ("nested_infinity_rejected", expect_invalid('{"a": {"b": [1, -Infinity]}}', expect="dict")),
        ("overflow_float_rejected", expect_invalid("[1e400]", expect="list")),
        ("ordinary_float_passes", check_json("[1.5e3]", expect="list")[0] == 1500.0),
    ]
    for name, passed in checks:
        print(("PASS " if passed else "FAIL ") + name)
        ok += bool(passed)
    print(f"SELFTEST {'OK' if ok == len(checks) else 'FAIL'} ({ok}/{len(checks)})")
    return ok == len(checks)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print(__doc__)
