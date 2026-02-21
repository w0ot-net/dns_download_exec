# Plan: Simplify byte-handling indirection and minor cleanups

## Summary

Replace the `byte_value`/`iter_byte_values` indirection layer with direct
`bytearray` usage, which handles Py2/3 byte iteration natively.  Inline the
single-use `_is_printable_ascii` helper and apply two minor cleanups.  Net
removal: ~35 lines and two utility functions, with no behaviour change.

## Problem

`byte_value` and `iter_byte_values` exist to abstract Py2/3 differences when
reading individual bytes from a `bytes` object.  However, `bytearray` already
provides this natively: indexing and iterating a `bytearray` yields `int`
values in both Py2 and Py3.  The stager template's own `_decode_name` already
uses this simpler pattern.  The current code routes every byte access through
one or two extra function calls, adding indirection and code volume without
benefit.

Additionally:

- `constant_time_equals` wraps `hmac.compare_digest` in a `try/except` that
  can never trigger (inputs are already validated as `bytes` by the preceding
  `is_binary` check).
- `_is_printable_ascii` is a 5-line function called exactly once.
- `build_response` in `dnswire.py` aliases a variable to itself on the next
  line.
- `_record_is_required` accepts a `level_name` parameter it never reads.

## Goal

After implementation:

1. `byte_value` and `iter_byte_values` are removed from `compat.py` and from
   the universal-client extraction list.
2. All former call sites use `bytearray` indexing/iteration instead.
3. `constant_time_equals` has no unnecessary `try/except`.
4. `_is_printable_ascii` is inlined.
5. `build_response` has no redundant alias.
6. `_record_is_required` has no unused parameter.
7. All existing tests continue to pass.

## Design

All changes are mechanical substitutions with identical semantics.

### Phase 1 -- byte-handling simplification

Applied as a single atomic commit since every change is interdependent (removing
the functions requires all call sites to be updated first).

**compat.py**

- `_xor_bytes` callers and `constant_time_equals` are the only consumers of
  `iter_byte_values`.  After switching them to `bytearray`, remove
  `iter_byte_values` (function + extract markers).
- `iter_byte_values` is the only consumer of `byte_value` within `compat.py`.
  `dnswire._decode_name` and `client_runtime._parse_slice_record` also import
  it directly.  After switching those to `bytearray`, remove `byte_value`
  (function + extract markers).
- `constant_time_equals`: remove the `try/except` around `hmac.compare_digest`
  and replace the `iter_byte_values` fallback with `bytearray` zip.

**cname_payload.py** -- `_xor_bytes`

Replace:

```python
out = bytearray(len(left_bytes))
for index, (a, b) in enumerate(zip(iter_byte_values(left_bytes), iter_byte_values(right_bytes))):
    out[index] = a ^ b
return bytes(out)
```

With:

```python
return bytes(bytearray(a ^ b for a, b in zip(bytearray(left_bytes), bytearray(right_bytes))))
```

Remove `iter_byte_values` import.

**dnswire.py** -- `_decode_name`

Convert `message` to `bytearray` once at the top of the function (matching the
stager template pattern).  Replace `byte_value(message[offset])` with
`ba[offset]`.  Remove `byte_value` import.

**client_runtime.py** -- `_parse_slice_record`

Replace `byte_value(record[N])` with indexing a `bytearray` built from
`record`.  Remove `byte_value` import.

**client_standalone.py**

Remove `"byte_value"` and `"iter_byte_values"` from `_COMPAT_EXTRACTIONS`.

### Phase 2 -- minor cleanups

**config.py** -- inline `_is_printable_ascii`

Replace the function definition and its single call site with:

```python
if not all(32 <= ord(ch) <= 126 for ch in seed):
```

**dnswire.py** -- `build_response`

Remove the redundant alias `question_bytes = raw_question_bytes`; use
`raw_question_bytes` throughout.

**logging_runtime.py** -- `_record_is_required`

Remove the unused `level_name` parameter and update the single call site in
`_do_emit`.

### Phase 3 -- documentation

Update `doc/architecture/CLIENT_GENERATION.md` to remove `byte_value` and
`iter_byte_values` from the compat extraction list (line 48), changing the
count from 10 to 8.

## Affected Components

- `dnsdle/compat.py`: remove `byte_value`, `iter_byte_values`; simplify `constant_time_equals`
- `dnsdle/cname_payload.py`: simplify `_xor_bytes`; remove `iter_byte_values` import
- `dnsdle/dnswire.py`: simplify `_decode_name` and `build_response`; remove `byte_value` import
- `dnsdle/client_runtime.py`: simplify `_parse_slice_record`; remove `byte_value` import
- `dnsdle/client_standalone.py`: remove two entries from `_COMPAT_EXTRACTIONS`
- `dnsdle/config.py`: inline `_is_printable_ascii`
- `dnsdle/logging_runtime.py`: remove unused `level_name` param from `_record_is_required`
- `doc/architecture/CLIENT_GENERATION.md`: update extraction function list

## Execution Notes

Executed 2026-02-21.  All three phases implemented as planned with no deviations.

**Phase 1** (`5926c49`): Removed `byte_value` and `iter_byte_values` from
`compat.py`.  Updated all call sites (`cname_payload._xor_bytes`,
`dnswire._decode_name`, `client_runtime._parse_slice_record`) to use
`bytearray` directly.  Simplified `constant_time_equals` by removing
unreachable `try/except`.  Removed both entries from `_COMPAT_EXTRACTIONS`
in `client_standalone.py`.  Care taken to convert `bytearray` slices back
to `bytes` at API boundaries (`_parse_slice_record` return values) to
maintain `is_binary` compatibility.

**Phase 2** (`7908a80`): Inlined `_is_printable_ascii` in `config.py`.
Removed redundant `question_bytes` alias in `dnswire.build_response`.
Removed unused `level_name` parameter from
`logging_runtime._record_is_required` and its call site.

**Phase 3** (`92ca6ea`): Updated `CLIENT_GENERATION.md` extraction list
(10 -> 8 functions).
