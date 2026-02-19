# Plan: Fix compat.py Usage Gaps

## Summary

Two source files bypass `compat.py` helpers where they should not. `client_generator.py`
calls `.encode("ascii")` directly instead of `to_ascii_bytes()`, causing an unnecessary
decode-then-re-encode roundtrip on Python 2 `str` values. `mapping.py` uses
`to_ascii_bytes(str(slice_index))` instead of `to_ascii_int_bytes()`, diverging from the
pattern used consistently in `cname_payload.py`. Both are fixed in a single small change.

## Problem

- **`dnsdle/client_generator.py` lines 186 and 202**: raw `.encode("ascii")` calls on a
  `str`/`unicode` value. On Python 2, calling `.encode("ascii")` on an already-bytes `str`
  triggers a silent ASCII-decode-then-re-encode roundtrip. `compat.to_ascii_bytes()` avoids
  this by short-circuiting on `binary_type` values. Neither call imports anything from
  `compat`.

- **`dnsdle/mapping.py` line 30**: `to_ascii_bytes(str(slice_index))` converts an integer to
  ASCII bytes by going through Python's `str()` first instead of using
  `to_ascii_int_bytes(slice_index, "slice_index")`. Every analogous conversion in
  `cname_payload.py` uses `to_ascii_int_bytes`; `mapping.py` is the only outlier.

## Goal

- `client_generator.py` uses `to_ascii_bytes()` for both ASCII-encoding sites; no raw
  `.encode("ascii")` calls remain in server-side code.
- `mapping.py` uses `to_ascii_int_bytes()` for the `slice_index` → bytes conversion,
  consistent with `cname_payload.py`.
- No behaviour change in Python 3; Python 2 avoids the unnecessary str encode roundtrip.

## Design

### `dnsdle/client_generator.py`

Add `to_ascii_bytes` to the imports from `dnsdle.compat`. Replace both `.encode("ascii")`
call sites:

```python
# line 186 — validation guard (inside try/except)
# before:
source.encode("ascii")
# after:
to_ascii_bytes(source)

# line 202 — write to binary file handle
# before:
handle.write(source_text.encode("ascii"))
# after:
handle.write(to_ascii_bytes(source_text))
```

### `dnsdle/mapping.py`

Add `to_ascii_int_bytes` to the imports from `dnsdle.compat`. Replace the one call site:

```python
# _derive_slice_digest — line 30
# before:
slice_index_bytes = to_ascii_bytes(str(slice_index))
# after:
slice_index_bytes = to_ascii_int_bytes(slice_index, "slice_index")
```

The behaviour is identical for the non-negative ints that arrive from `range()`; the only
change is using the canonical helper.

## Affected Components

- `dnsdle/client_generator.py`: add `to_ascii_bytes` import; replace two raw
  `.encode("ascii")` calls.
- `dnsdle/mapping.py`: add `to_ascii_int_bytes` import; replace one
  `to_ascii_bytes(str(...))` call with `to_ascii_int_bytes`.
