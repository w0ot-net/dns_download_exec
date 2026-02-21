# Plan: Simplify DNS Response Parsing and Logging Level Inference

## Summary

Two independent cleanups: remove the dead ns/ar parse and trailing-bytes check
from `_parse_response_for_cname` in `client_runtime.py`, and replace the
full-entry `_LEVEL_FROM_CLASSIFICATION` dict in `logging_runtime.py` with two
small frozensets covering only the non-default cases.  Both changes reduce code
and maintenance surface without altering observable behaviour.

## Problem

### Issue 2 — `_parse_response_for_cname` parses sections it discards

`client_runtime.py:166-169` calls `_consume_rrs` twice for the authority and
additional sections, discards both results, then raises if `offset !=
len(message)`.  DNS over UDP delivers exactly one datagram per `recvfrom`, so
trailing bytes cannot indicate corruption.  The check also rejects valid
responses from resolvers that append extension data (e.g. EDNS OPT records in
the additional section).  The stager's equivalent (`_parse_cname` in
`stager_template.py`) correctly skips these sections entirely.

### Issue 3 — Logging level inference is overbuilt for three cases

`logging_runtime.py` keeps an 11-entry dict to map classification strings to
log levels.  Eight of those entries map to the default (`"info"`).  Any new
info-level classification requires a new dict entry or silently falls back to
`"info"` via `.get(...)`, making the dict both redundant and misleading.

## Goal

- `_parse_response_for_cname` stops parsing ns/ar sections and never raises on
  trailing bytes; only answer RRs are examined.
- `logging_runtime.py` derives levels from two frozensets of exceptional
  classifications; everything else defaults to `"info"` without requiring a
  registry entry.

## Design

### client_runtime.py

Delete the three lines that call `_consume_rrs` for `nscount` / `arcount` and
the trailing-bytes assertion.  The `nscount` and `arcount` variables are
already extracted from the header at the top of the function; they can remain
(they are used to validate the header parse) but the subsequent `_consume_rrs`
calls on them must be removed.

Before (lines 166-169):
```python
    _, offset = _consume_rrs(offset, nscount)
    _, offset = _consume_rrs(offset, arcount)
    if offset != len(message):
        raise ClientError(EXIT_PARSE, "parse", "trailing bytes in response message")
```

After: delete all four lines.

### logging_runtime.py

Replace `_LEVEL_FROM_CLASSIFICATION` dict and `_record_level` with:

```python
_ERROR_CLASSIFICATIONS = frozenset(("startup_error", "runtime_fault"))
_WARN_CLASSIFICATIONS  = frozenset(("miss",))

def _record_level(record):
    c = str(record.get("classification", "")).lower()
    if c in _ERROR_CLASSIFICATIONS:
        return "error"
    if c in _WARN_CLASSIFICATIONS:
        return "warn"
    return "info"
```

No call sites of `_record_level` change; `emit_record` in `RuntimeLogger`
calls it unchanged.

## Affected Components

- `dnsdle/client_runtime.py`: remove ns/ar `_consume_rrs` calls and
  trailing-bytes check from `_parse_response_for_cname`
- `dnsdle/logging_runtime.py`: replace `_LEVEL_FROM_CLASSIFICATION` dict with
  `_ERROR_CLASSIFICATIONS` / `_WARN_CLASSIFICATIONS` frozensets and rewrite
  `_record_level`
