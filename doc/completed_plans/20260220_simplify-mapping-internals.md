# Plan: Simplify mapping.py Internals

## Summary

Three unnecessary-complexity issues exist inside `mapping.py`: a parallel list
that shadows data already associated with each entry, a single-use helper
function that adds a layer of indirection to a sort key, and a defensive
`None`-guard on a code path that cannot be reached by construction.  All
changes are internal to `apply_mapping` and its private helpers with no impact
on callers.

## Problem

1. **`max_len_by_index` parallel list (lines 67, 108, 134).**  A separate list
   is allocated and maintained in lockstep with `entries` solely to carry one
   `max_token_len` value per entry.  This is a structural smell — the value
   belongs on the entry dict itself.  The promotion loop later retrieves it via
   `max_len_by_index[promote_idx]` instead of from the entry it describes.

2. **`_entry_sort_key` single-use helper with double-lambda indirection (lines
   55–60, 112).**  The function is called in exactly one place, already wrapped
   in a lambda: `key=lambda idx: _entry_sort_key(entries[idx])`.  The named
   helper adds an extra call frame and an extra file-level symbol for three dict
   lookups that are clear inline.

3. **Unreachable `promote_idx is None` guard (lines 125–130).**  `canonical_order`
   is `sorted(range(len(entries)), ...)` — a permutation of all entry indices.
   `colliding_files` is a non-empty subset of those same indices (the `while`
   loop only iterates when `colliding_files` is non-empty).  Therefore
   `next(idx for idx in canonical_order if idx in colliding_files)` always
   succeeds; `promote_idx` can never be `None`.  The guard is dead code that
   would only be reached by a logic bug in this module, but reports it as a
   `StartupError` instead of exposing the bug.

## Goal

- `max_len_by_index` is removed; `max_token_len` is stored directly on the
  entry dict and read back from there during promotion.
- `_entry_sort_key` is removed; the sort tuple is inlined in `apply_mapping`.
- The unreachable `promote_idx is None` block is replaced with
  `assert promote_idx is not None`, consistent with the project's fail-fast
  invariant principle.

## Design

### 1. Store `max_token_len` on the entry dict

In `apply_mapping`, remove the `max_len_by_index = []` declaration and the
`max_len_by_index.append(max_token_len)` call.  Add instead:

```python
entry["max_token_len"] = max_token_len
```

In the promotion loop, replace:

```python
max_len = max_len_by_index[promote_idx]
```

with:

```python
max_len = entry["max_token_len"]
```

The extra key is internal to `apply_mapping`'s returned dicts; no external
caller reads `max_token_len` (callers access only `file_id`, `file_tag`,
`slice_token_len`, `slice_tokens`, `plaintext_sha256`, `source_filename`).

### 2. Inline `_entry_sort_key`

Delete the `_entry_sort_key` function.  Replace the sort in `apply_mapping`:

```python
canonical_order = sorted(
    range(len(entries)),
    key=lambda idx: (
        entries[idx]["file_tag"],
        entries[idx]["file_id"],
        entries[idx]["publish_version"],
    ),
)
```

### 3. Replace unreachable guard with assertion

Replace:

```python
if promote_idx is None:
    raise StartupError(
        "mapping",
        "mapping_collision",
        "collision set could not be resolved deterministically",
    )
```

with:

```python
assert promote_idx is not None
```

## Affected Components

- `dnsdle/mapping.py`: remove `max_len_by_index` parallel list and store
  `max_token_len` on the entry dict; delete `_entry_sort_key` and inline the
  sort tuple; replace unreachable `promote_idx is None` guard with assertion

## Execution Notes

Executed 2026-02-20.  All plan items implemented as designed with no deviations.

1. Removed `max_len_by_index` parallel list.  `max_token_len` is now stored
   directly on each entry dict (`entry["max_token_len"] = max_token_len`).
   The promotion loop reads it as `entry["max_token_len"]`.

2. Deleted `_entry_sort_key` helper.  Sort key is inlined as a lambda
   returning `(entries[idx]["file_tag"], entries[idx]["file_id"],
   entries[idx]["publish_version"])`.

3. Replaced unreachable `if promote_idx is None: raise StartupError(...)`
   with `assert promote_idx is not None`.

Commit: <hash>
