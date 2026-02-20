# Plan: Stager --verbose flag

## Summary

Add `--verbose` support to the stager so operators can diagnose connectivity
issues at bootstrap time. When passed, the stager emits the resolved DNS
address, per-slice progress, and retry events to stderr. The flag is also
forwarded to the exec'd universal client unchanged, enabling end-to-end
verbose diagnostics from a single flag. Changes are confined to two files
and add minimal bytes to the compressed one-liner.

## Problem

The stager currently produces no output. When a deployment fails at bootstrap
— wrong resolver, blocked middlebox, stale server — the operator has no way
to distinguish "nothing reached the server" from "slices are arriving but
failing crypto" from "resolver discovery picked the wrong address". The client
already supports `--verbose`, but that is unreachable if the stager itself
silently fails.

## Goal

After implementation:

- `--verbose` is detected by the stager arg parser without being consumed
  from `_sa` (so it continues to be forwarded to the exec'd client).
- When verbose is active, the stager emits to stderr:
  - The resolved DNS address (`resolver <addr>`) immediately after the
    resolver is determined, showing whether discovery or an explicit
    `--resolver` argument was used.
  - Per-slice progress (`[N/T]`) after each slice is successfully fetched.
  - Retry notification (`retry N`) each time a slice fetch raises an
    exception and the loop retries.
- No output is produced when `--verbose` is absent (existing behavior
  unchanged).
- The one-liner size increase after minification + zlib + base64 is
  negligible.

## Design

### Template change (`_STAGER_SUFFIX`)

The arg-parsing section already walks `_sa` consuming `--psk` and `--resolver`.
Add a single read-only check after `_sa` is set:

```python
verbose = "--verbose" in _sa
```

`verbose` is not removed from `_sa`, so it flows through to `] + _sa` at the
exec handoff and the client receives it unmodified.

Add three instrumentation points, following the template's coding discipline
(one statement per line, 4-space indent, no inline comments, no comprehensions):

**After resolver address is determined** (after both the discovery and
explicit-resolver branches have set `addr`):
```python
if verbose:
    sys.stderr.write("resolver %s\n" % repr(addr))
```

**After a slice is successfully stored** (after `slices[si] = _process_slice(...)`):
```python
if verbose:
    sys.stderr.write("[%d/%d]\n" % (si + 1, TOTAL_SLICES))
```

**In the `except Exception` retry handler** (before `time.sleep(1)`):
```python
if verbose:
    sys.stderr.write("retry %d\n" % si)
```

Total lines added to `_STAGER_SUFFIX`: 7. No new imports — `sys` is already
imported at the top of `_STAGER_PREFIX`.

### Minifier rename table (`stager_minify.py`)

`verbose` must be added to `_RENAME_TABLE` so the minifier shrinks it to a
two-character name. Insert it in the 7-char name group alongside `slices`,
`handle`, `result`, `output`, `run_fn`:

```python
("verbose", "dj"),
```

`dj` is the only gap in the `d*` namespace (`dk` = `PAYLOAD_TOTAL_SLICES`,
`di` = `PAYLOAD_TOKEN_LEN` — `dj` is unassigned). Longest-first ordering is
maintained since all surrounding names are also 6–7 characters.

### Output format

Verbose lines are written to stderr, not stdout, to avoid interfering with
any downstream pipe on the exec'd client's output. Format is minimal ASCII,
no timestamps, no log levels — suitable for a bootstrap script.

Example output with two slices:
```
resolver ('192.168.1.1', 53)
[1/2]
[2/2]
```

Example with one retry:
```
resolver ('192.168.1.1', 53)
retry 0
[1/2]
[2/2]
```

## Affected Components

- `dnsdle/stager_template.py` (`_STAGER_SUFFIX`): add `verbose` detection and
  three instrumented `sys.stderr.write` calls.
- `dnsdle/stager_minify.py` (`_RENAME_TABLE`): add `("verbose", "dj")` entry.
- `doc/architecture/CLIENT_GENERATION.md`: update the Stager Integration
  section to document `--verbose` flag, forwarding behavior, and stderr
  output format.
