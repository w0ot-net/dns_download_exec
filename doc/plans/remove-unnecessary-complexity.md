# Plan: Remove unnecessary complexity across codebase

## Summary

Five areas of the codebase do things in a more complicated way than necessary.
Simplifying them removes ~75 lines while improving readability.  All changes
are internal -- no wire protocol, CLI, or behavioral changes.

## Problem

1. **Redundant parallel identity index** -- `RuntimeState` stores two
   identity-keyed dicts (`slice_bytes_by_identity` and
   `publish_meta_by_identity`) that serve overlapping purposes.  The server
   looks up both, then checks that `total_slices == len(slice_table)`, which is
   true by construction.
2. **Nested convergence loop** -- `build_startup_state` uses a two-phase nested
   loop (inner loop for user files, outer loop for user+client) plus a manual
   snapshot-based stability invariant check.  The client source is constant
   across iterations, so a single flat loop that always includes the client
   suffices.
3. **Defensive helper on namedtuple field** -- `_domain_labels` in budget.py
   uses `getattr(config, "longest_domain_labels", None)` followed by a None
   check, but `config` is always a `Config` namedtuple where the field is
   guaranteed to exist.
4. **Path-escape checks on constant-derived paths** -- `client_generator.py`
   runs two `_is_within_dir` checks on paths built from hardcoded string
   constants (`"dnsdle_v1"` and `"dnsdle_universal_client.py"`) that can never
   contain path traversal characters.
5. **Stdlib roundtrip verification** -- `stager_generator.py` decodes and
   decompresses a base64+zlib payload and compares it to the original input,
   verifying that deterministic stdlib functions are deterministic.

## Goal

Eliminate the five complexity sources listed above.  After implementation:
- `RuntimeState` has one identity-keyed dict instead of two
- `build_startup_state` uses a single flat convergence loop
- budget.py accesses `config.longest_domain_labels` directly
- `client_generator.py` has no `_is_within_dir` helper or calls
- `stager_generator.py` has no roundtrip verification block
- all existing tests pass unchanged
- no wire/CLI/behavioral changes

## Design

### 1. Merge `publish_meta_by_identity` into `slice_bytes_by_identity`

Rename the field to `slice_data_by_identity`.  Its values become
`(slice_table, compressed_size)` tuples instead of bare `slice_table` tuples.

**state.py:**
- Remove `publish_meta_by_identity` from `RuntimeState` fields.
- Rename `slice_bytes_by_identity` to `slice_data_by_identity`.
- In `build_runtime_state`, store `(publish_item.slice_bytes_by_index,
  publish_item.compressed_size)` as the value.  Remove the separate
  `publish_meta_by_identity` dict and its population.

**server.py `handle_request_message`:**
- Replace the two separate `.get()` calls and the `total_slices !=
  len(slice_table)` consistency check with a single lookup:
  ```python
  slice_data = runtime_state.slice_data_by_identity.get(identity)
  if slice_data is None:
      return _classified_response(...)
  slice_table, compressed_size = slice_data
  ```
- Derive `total_slices = len(slice_table)` directly.

### 2. Flatten the two-phase convergence loop

Replace the nested loop structure in `build_startup_state` with a single flat
loop.  Key observations enabling this:
- `build_client_source()` always returns the same content regardless of budget
  or token length.
- `generate_client_artifacts(config)` writes the client to disk and returns
  the source.  It can be called once before the loop.
- The mapping function is deterministic: adding the client to the publish set
  cannot change user file mappings (user items always appear first and
  canonical-order promotion is deterministic by `(file_tag, file_id,
  publish_version)` sort).  The snapshot stability invariant check confirms
  this but is redundant.

New structure:
```python
generation_result = generate_client_artifacts(config)
client_filename = generation_result["filename"]
client_bytes = encode_ascii(generation_result["source"])

query_token_len = 4
for _iteration in range(10):
    max_ciphertext_slice_bytes, budget_info = compute_max_ciphertext_slice_bytes(
        config, query_token_len=query_token_len
    )
    publish_items = build_publish_items(config, max_ciphertext_slice_bytes)
    seen_sha256 = set(item["plaintext_sha256"] for item in publish_items)
    seen_ids = set(item["file_id"] for item in publish_items)
    client_items = build_publish_items_from_sources(
        [(client_filename, client_bytes)],
        config.compression_level,
        max_ciphertext_slice_bytes,
        seen_sha256,
        seen_ids,
    )
    combined_mapped = apply_mapping(list(publish_items) + client_items, config)
    realized = max(item["slice_token_len"] for item in combined_mapped)
    # ... debug log ...
    if realized <= query_token_len:
        break
    query_token_len = realized
else:
    raise StartupError(...)
```

This eliminates:
- The inner `while True` convergence loop
- The `user_file_snapshot` dict and its 14-line invariant check
- The `_max_slice_token_len` helper function

### 3. Remove `_domain_labels` helper in budget.py

Delete the `_domain_labels` function.  Replace its two call sites
(lines 56, 67, 82) with `config.longest_domain_labels` directly (already
a tuple from config construction).

### 4. Remove `_is_within_dir` and its calls in client_generator.py

Delete the `_is_within_dir` function (lines 16-27) and the two call sites
with their associated `StartupError` raises (lines 96-102 and 109-115).

### 5. Remove roundtrip verification in stager_generator.py

Delete lines 85-91 in `generate_stager` (the `roundtrip = ...` block and
its comparison).  Also remove the earlier ASCII-decodability check of the
base64 payload (lines 76-83) since `base64.b64encode` always produces ASCII.

## Affected Components

- `dnsdle/state.py`: remove `publish_meta_by_identity` field; rename
  `slice_bytes_by_identity` to `slice_data_by_identity`; update
  `build_runtime_state` population logic
- `dnsdle/server.py`: update `handle_request_message` to use merged lookup;
  remove redundant consistency checks
- `dnsdle/__init__.py`: flatten convergence loop; remove
  `_max_slice_token_len` helper; remove stability invariant check
- `dnsdle/budget.py`: remove `_domain_labels` function; inline direct
  attribute access at call sites
- `dnsdle/client_generator.py`: remove `_is_within_dir` function and both
  call sites
- `dnsdle/stager_generator.py`: remove roundtrip verification and ASCII
  check blocks
- `doc/architecture/ARCHITECTURE.md`: update Runtime State Model section to
  reflect merged identity index (remove mention of separate
  `slice_bytes_by_identity`)
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: remove references to
  `publish_meta_missing` and `slice_table_length_mismatch` runtime fault
  codes that no longer exist
