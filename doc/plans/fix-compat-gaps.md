# Plan: Fix compat.py Usage Gaps

## Summary

Three concerns: two files call `.encode("ascii")` directly instead of `to_ascii_bytes()`
(`client_generator.py` and `__init__.py`); `mapping.py` uses `to_ascii_bytes(str(x))`
instead of `to_ascii_int_bytes()`; and `compat.py` exports `string_types` which is never
imported by any module and should be removed. All changes are in a single small commit.

## Problem

- **`dnsdle/client_generator.py` line 186**: validation-only `.encode("ascii")` call inside a
  try/except guard (result is discarded; the purpose is to verify the source is ASCII).
  On Python 2, the implicit decode-then-re-encode roundtrip is what validates. Replacing
  with `to_ascii_bytes()` would silently skip validation on Python 2 (short-circuits on
  `binary_type`). The correct replacement is `to_ascii_text()` which validates by decoding
  `str` → `unicode` on Python 2, and is a no-op on Python 3 (`str` is already text).

- **`dnsdle/client_generator.py` line 202**: raw `.encode("ascii")` for byte conversion
  (writing to a binary file handle). `to_ascii_bytes()` is the correct replacement here.
  Neither call imports anything from `compat`.

- **`dnsdle/__init__.py` line 98**: same raw `.encode("ascii")` pattern, also unguarded by
  `to_ascii_bytes()`:
  ```python
  (artifact["filename"], artifact["source"].encode("ascii"))
  ```

- **`dnsdle/mapping.py` line 30**: `to_ascii_bytes(str(slice_index))` converts an integer to
  ASCII bytes by going through Python's `str()` first instead of using
  `to_ascii_int_bytes(slice_index, "slice_index")`. Every analogous conversion in
  `cname_payload.py` uses `to_ascii_int_bytes`; `mapping.py` is the only outlier.

- **`dnsdle/compat.py` `string_types`**: defined as `(str, unicode)` / `(str, bytes)` but
  never imported by any other module. It is dead export surface with no callers.

## Goal

- No raw `.encode("ascii")` calls remain in server-side code outside of `compat.py` and
  `client_template.py` (which contains its own self-contained `_to_ascii_bytes()` helper
  since generated client scripts cannot import from `dnsdle.compat`).
- `mapping.py` uses `to_ascii_int_bytes()` for integer → bytes conversion, consistent with
  `cname_payload.py`.
- `compat.py` no longer exports `string_types`; its definition is removed entirely.
- No behaviour change on Python 3; Python 2 avoids the unnecessary str encode roundtrip.

## Design

### `dnsdle/client_generator.py`

Add `to_ascii_bytes` and `to_ascii_text` to the imports from `dnsdle.compat`. Replace both
`.encode("ascii")` call sites:

```python
# line 186 — validation guard (inside try/except, result discarded)
# before:
source.encode("ascii")
# after:
to_ascii_text(source)

# line 202 — write to binary file handle
# before:
handle.write(source_text.encode("ascii"))
# after:
handle.write(to_ascii_bytes(source_text))
```

### `dnsdle/__init__.py`

Add `to_ascii_bytes` to the imports from `dnsdle.compat`. Replace the one call site:

```python
# line 98 — build sources list for client publish items
# before:
(artifact["filename"], artifact["source"].encode("ascii"))
# after:
(artifact["filename"], to_ascii_bytes(artifact["source"]))
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

### `dnsdle/compat.py`

Remove the `string_types` assignment from both branches of the `if PY2` block. No other
file imports it.

```python
# remove from the PY2 branch:
string_types = (str, unicode)

# remove from the else branch:
string_types = (str, bytes)
```

## Affected Components

- `dnsdle/compat.py`: remove dead `string_types` export from both `if PY2` / `else` branches.
- `dnsdle/client_generator.py`: add `to_ascii_bytes` and `to_ascii_text` imports; replace
  line 186 validation guard with `to_ascii_text()`, line 202 conversion with
  `to_ascii_bytes()`.
- `dnsdle/__init__.py`: add `to_ascii_bytes` import; replace one raw `.encode("ascii")` call.
- `dnsdle/mapping.py`: add `to_ascii_int_bytes` import; replace one
  `to_ascii_bytes(str(...))` call with `to_ascii_int_bytes`.
