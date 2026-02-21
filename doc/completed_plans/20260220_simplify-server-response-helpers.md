# Plan: Simplify Server Response Helpers

## Summary

Three independent simplifications to `dnsdle/server.py` reduce code and fix a
counter bug: merging three near-identical response builder functions into one,
replacing a manual dict-merge loop with `dict.update`, and removing the complex
re-parse-and-SERVFAIL fallback for unhandled exceptions in the serve loop.

## Problem

1. **Three near-identical response builders.**
   `_runtime_fault_response`, `_miss_response`, and `_nodata_response` (lines
   46–76) differ only in `rcode` and `classification`.  Each repeats the same
   `dnswire.build_response` + `_build_log` pattern.  Any change to the shared
   call shape (e.g. adding a parameter) must be made three times.

2. **Manual dict-merge loop in `_build_log`.**
   Lines 37–38 iterate over `context.items()` and assign each key to `record`
   one by one.  This is equivalent to `record.update(context)` but more verbose.

3. **Unnecessary re-parse in unhandled-exception handler.**
   Lines 432–453 in the serve loop catch any unexpected exception from
   `handle_request_message`, then re-parse the raw datagram and, if that
   succeeds, build and send a SERVFAIL response.  `handle_request_message`
   already handles every expected error path internally (returning
   `(None, None)` for unparseable input and structured `(response, log)` pairs
   for all classified errors).  The only way this outer except fires is a
   genuine code bug.  Sending SERVFAIL in that case is actively harmful: the
   generated client treats any non-NOERROR rcode as a non-retryable contract
   violation and aborts immediately
   (`doc/architecture/ERRORS_AND_INVARIANTS.md`, Non-Retryable Contract
   Violations).  Dropping the datagram instead causes a DNS timeout, which the
   client handles as a retryable transport event.  The fallback also
   double-counts `runtime_fault` (once at the catch site, once when the
   fallback log record's classification is counted at line 474).

## Goal

- `_runtime_fault_response`, `_miss_response`, and `_nodata_response` are
  replaced by a single `_classified_response` function; all call sites updated.
- `_build_log` uses `record.update(context)` instead of an explicit loop.
- The unhandled-exception handler in `serve_runtime` logs the fault and
  continues without re-parsing or sending a SERVFAIL.

## Design

### 1. Merge three response builders into `_classified_response`

```python
def _classified_response(request, config, rcode, classification, reason_code, context):
    response = dnswire.build_response(
        request,
        rcode,
        answer_bytes=None,
        include_opt=_include_opt(config),
        edns_size=config.dns_edns_size,
    )
    return response, _build_log(classification, reason_code, context)
```

Call-site replacements (all in `handle_request_message`):
- `_miss_response(request, config, ...)` →
  `_classified_response(request, config, DNS_RCODE_NXDOMAIN, "miss", ...)`
- `_nodata_response(request, config, ...)` →
  `_classified_response(request, config, DNS_RCODE_NOERROR, "miss", ...)`
- `_runtime_fault_response(request, config, ...)` →
  `_classified_response(request, config, DNS_RCODE_SERVFAIL, "runtime_fault", ...)`

The hardcoded SERVFAIL in `serve_runtime` (line 446–452) is removed as part of
item 3, so no further call-site change is needed there.

### 2. Simplify `_build_log` dict merge

Replace:
```python
if context:
    for key, value in context.items():
        record[key] = value
```
With:
```python
if context:
    record.update(context)
```

### 3. Simplify unhandled-exception handler

Replace the current block (lines 432–453):
```python
except Exception as exc:
    counters["runtime_fault"] += 1
    emit_record(_build_log("runtime_fault", "unhandled_request_exception", {"message": str(exc)}))
    try:
        request = dnswire.parse_request(datagram)
    except dnswire.DnsParseError:
        counters["dropped"] += 1
        continue
    response_bytes = dnswire.build_response(
        request, DNS_RCODE_SERVFAIL, answer_bytes=None,
        include_opt=_include_opt(config), edns_size=config.dns_edns_size,
    )
    log_record = _build_log("runtime_fault", "servfail_fallback", None)
```
With:
```python
except Exception as exc:
    counters["runtime_fault"] += 1
    emit_record(_build_log("runtime_fault", "unhandled_request_exception", {"message": str(exc)}))
    continue
```

The datagram is dropped (no response sent).  The fault is still logged and
counted.  Dropping is preferable to SERVFAIL because the generated client
treats non-NOERROR responses as non-retryable contract violations that cause
immediate abort; a dropped datagram instead produces a retryable DNS timeout.
This also fixes a counter bug: the current code increments `runtime_fault`
once at the catch site (line 433) and again when the fallback log record's
classification is counted (line 474).

The `response_bytes` / `log_record` variables are only assigned in two paths
after the except block: the normal return from `handle_request_message` (which
always succeeds or returns `(None, None)`) and the now-removed fallback.  With
the fallback gone, `continue` is the only control flow out of the except block,
so no subsequent code referencing those variables is reachable from the except
path.

Update `doc/architecture/ERRORS_AND_INVARIANTS.md` response matrix item 5 to
distinguish classified runtime faults (SERVFAIL, handled within
`handle_request_message`) from unhandled exceptions in the serve loop (drop,
no response).

## Affected Components

- `dnsdle/server.py`: replace three response builders with
  `_classified_response`, simplify `_build_log` loop, simplify
  unhandled-exception handler in `serve_runtime`
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: update response matrix item 5
  to distinguish classified runtime faults from unhandled serve-loop exceptions

## Execution Notes

Executed 2026-02-20.  All three plan items implemented as designed with no
deviations.

1. Replaced `_runtime_fault_response`, `_miss_response`, `_nodata_response`
   with single `_classified_response(request, config, rcode, classification,
   reason_code, context)`.  Updated all 11 call sites in
   `handle_request_message`.

2. Replaced manual `for key, value in context.items()` loop in `_build_log`
   with `record.update(context)`.

3. Simplified unhandled-exception handler in `serve_runtime`: removed
   re-parse + SERVFAIL fallback, replaced with `continue` (drop datagram).
   Fixes runtime_fault double-count bug.

4. Updated `doc/architecture/ERRORS_AND_INVARIANTS.md` response matrix: split
   old item 5 into item 5 (classified runtime faults, SERVFAIL) and item 6
   (unhandled serve-loop exceptions, drop).

Commit: <hash>
