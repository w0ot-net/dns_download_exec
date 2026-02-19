# Plan: Client Query Pacing

## Context

When downloading through a recursive resolver (e.g. 8.8.8.8), the generated
client fires queries as fast as the loop can iterate -- zero delay between
successful responses.  At ~85 queries/sec this overwhelms the resolver, which
begins returning TC (truncated) responses.  The client treats TC as a fatal
parse error (exit 4), so the download dies after ~113 of ~25,000 slices.

Two problems compound:

1. No inter-query pacing -- the client saturates the resolver.
2. TC is classified as fatal (`ClientError(EXIT_PARSE)`) instead of retryable,
   so a single truncated response kills the entire download.

## Goal

After implementation:

- The download loop sleeps a configurable interval between each query,
  preventing resolver overload.
- TC responses are retryable (sleep + domain rotation) instead of fatal.
- Both behaviors are tunable at generation time (embedded constants) and at
  runtime (CLI overrides).

## Affected Components

- `dnsdle/constants.py` -- new default constant
- `dnsdle/client_template.py` -- template constant, pacing sleep in download
  loop, TC reclassified as retryable
- `dnsdle/client_generator.py` -- substitute new template placeholder
- `doc/architecture/CLIENT_RUNTIME.md` -- document pacing and TC retry behavior

## Changes

### 1. Add default constant

`dnsdle/constants.py`:

```python
GENERATED_CLIENT_DEFAULT_QUERY_INTERVAL_MS = 50
```

50 ms = ~20 queries/sec, well within typical recursive resolver rate limits.

### 2. Template: embed constant and add CLI arg

`dnsdle/client_template.py` (`_TEMPLATE_PREFIX`):

- Add `QUERY_INTERVAL_MS = @@QUERY_INTERVAL_MS@@` alongside the other
  embedded timing constants.

`_TEMPLATE_SUFFIX`:

- `_build_parser`: add `--query-interval` argument (default from embedded
  `QUERY_INTERVAL_MS`, parsed as non-negative int).
- `_parse_runtime_args`: parse and pass through to `_download_slices`.
- `_download_slices` signature: add `query_interval_ms` parameter.

### 3. Download loop pacing

In `_download_slices`, after each query+response cycle (successful or
duplicate), sleep `query_interval_ms` milliseconds before the next iteration:

```python
if query_interval_ms > 0:
    time.sleep(float(query_interval_ms) / 1000.0)
```

The sleep goes after the full response processing for each slice, right before
the loop continues to the next `slice_index`.  It does NOT apply after
retryable errors (those already sleep via `_retry_sleep`).

### 4. Reclassify TC as retryable

In `_parse_response_for_cname`, change the TC flag check from:

```python
if flags & DNS_FLAG_TC:
    raise ClientError(EXIT_PARSE, "parse", "response sets TC")
```

to:

```python
if flags & DNS_FLAG_TC:
    raise RetryableTransport("response truncated (TC)")
```

This means TC triggers `_retry_sleep()`, domain rotation, and
`consecutive_timeouts` tracking -- identical to socket timeout handling.
The download survives transient TC responses and only fails if TC persists
past `MAX_CONSECUTIVE_TIMEOUTS`.

### 5. Generator substitution

`dnsdle/client_generator.py` (`_render_client_source`):

Add to the `replacements` dict:

```python
"QUERY_INTERVAL_MS": int(GENERATED_CLIENT_DEFAULT_QUERY_INTERVAL_MS),
```

### 6. Documentation

`doc/architecture/CLIENT_RUNTIME.md`:

- **Download Loop** section: document inter-query pacing behavior and the
  `--query-interval` CLI override.
- **Response and Payload Validation** section: document that TC responses are
  retryable transport events, not fatal parse errors.

## Verification

1. Generate a client, confirm `QUERY_INTERVAL_MS = 50` appears in output.
2. Run client with `--query-interval 0` to disable pacing (fast local test).
3. Run client with `--query-interval 100` against a recursive resolver and
   confirm steady ~10 qps throughput with no TC failures.
4. Inject a TC response in tests and confirm retry behavior (not fatal exit).

## Execution Notes

Executed 2026-02-19.

All plan items implemented as specified with one deviation:

- **Deviation**: extended the `try/except RetryableTransport` block in the
  download loop to also cover `_parse_response_for_cname()`.  The plan moved
  the TC raise to `RetryableTransport`, but the original try block only
  wrapped `_send_dns_query()`.  Without this fix, TC exceptions from response
  parsing would propagate uncaught.
- Added `_parse_non_negative_int` helper for `--query-interval` validation
  (allows 0 to disable pacing, unlike `_parse_positive_int`).
