# Plan: Simplify publish.py Internals

## Summary

Three small unnecessary-complexity issues exist inside `publish.py`: a redundant
`item_context` parameter that always duplicates `source_filename`, an
inconsistent named-variable pattern in one error path, and `source_filename`
passed redundantly into `_log_publish_item_built` when it is already in the
`item` dict.  All changes are internal to `publish.py` with no external call
sites affected.

## Problem

1. **`_build_single_publish_item` carries a redundant `item_context`
   parameter.**  The parameter is always `{"source_filename": source_filename}`
   at the only call site (line 171).  `source_filename` is already a separate
   parameter of the same function, so `item_context` conveys no new information
   and only exists to pre-build a dict the caller could omit.

2. **Inconsistent error-context construction in `_build_single_publish_item`.**
   The `compression_failed` path assigns `ctx = dict(item_context)` and then
   raises with `ctx` without adding anything to it — the named variable does
   nothing.  The `compression_empty` path (two lines later) passes
   `dict(item_context)` inline.  Both paths do the same thing in different
   styles.

3. **`source_filename` passed redundantly to `_log_publish_item_built`.**
   The caller (line 175) passes `{"source_index": source_index,
   "source_filename": source_filename}` as `extra_context`.  But
   `item["source_filename"]` already holds the same value.  `extra_context`
   only needs to carry `source_index`, which is not in `item`.

## Goal

- `_build_single_publish_item` has no `item_context` parameter; each error path
  constructs `{"source_filename": source_filename, ...}` inline.
- All error paths in `_build_single_publish_item` use a consistent inline
  style; no intermediate `ctx` variable.
- `_log_publish_item_built` reads `source_filename` from `item` directly;
  `extra_context` carries only `source_index`.

## Design

### 1. Remove `item_context` parameter from `_build_single_publish_item`

Drop the `item_context` parameter.  Replace each use with an inline dict keyed
on `source_filename`, adding any extra keys on the same expression:

- `duplicate_plaintext_sha256`:
  ```python
  raise StartupError(
      "publish", "duplicate_plaintext_sha256",
      "duplicate file content detected",
      {"source_filename": source_filename, "plaintext_sha256": plaintext_sha256},
  )
  ```
- `compression_failed`:
  ```python
  raise StartupError(
      "publish", "compression_failed",
      "compression failed: %s" % exc,
      {"source_filename": source_filename},
  )
  ```
- `compression_empty`:
  ```python
  raise StartupError(
      "publish", "compression_empty",
      "compression produced empty output",
      {"source_filename": source_filename},
  )
  ```
- `file_id_collision`:
  ```python
  raise StartupError(
      "publish", "file_id_collision",
      "file_id collision detected across publish set",
      {"source_filename": source_filename, "file_id": file_id},
  )
  ```

Update the sole call site in `build_publish_items_from_sources` to drop the
`item_context` keyword argument.

### 2. No separate action needed for finding 2

Removing `item_context` (finding 1) eliminates the inconsistent `ctx` variable
pattern at the same time — all paths become inline dicts with a uniform style.

### 3. Simplify `_log_publish_item_built` and its call site

Change the function signature from `(item, extra_context)` to
`(item, source_index)` and build the event dict directly:

```python
def _log_publish_item_built(item, source_index):
    if not logger_enabled("debug"):
        return
    log_event("debug", "publish", {
        "phase": "publish",
        "classification": "diagnostic",
        "reason_code": "publish_item_built",
        "file_id": item["file_id"],
        "publish_version": item["publish_version"],
        "plaintext_sha256": item["plaintext_sha256"],
        "compressed_size": item["compressed_size"],
        "total_slices": item["total_slices"],
        "source_filename": item["source_filename"],
        "source_index": source_index,
    })
```

Update the call site to pass `source_index` directly:
```python
_log_publish_item_built(item, source_index)
```

## Affected Components

- `dnsdle/publish.py`: remove `item_context` parameter from
  `_build_single_publish_item` and update all internal error paths; simplify
  `_log_publish_item_built` signature and its call site

## Execution Notes

Executed 2026-02-20.  All plan items implemented as designed with no deviations.

1. Removed `item_context` parameter from `_build_single_publish_item`.  All
   four error paths (`duplicate_plaintext_sha256`, `compression_failed`,
   `compression_empty`, `file_id_collision`) now construct inline dicts with
   `source_filename` directly.  Eliminated intermediate `ctx` variables.

2. Changed `_log_publish_item_built(item, extra_context)` to
   `_log_publish_item_built(item, source_index)`.  Reads `source_filename`
   from `item["source_filename"]` instead of `extra_context`.  Builds the
   full event dict inline.

3. Updated the sole call site in `build_publish_items_from_sources` to drop
   the `item_context` kwarg and pass `source_index` directly.

Commit: fcb4606
