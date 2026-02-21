# Plan: Flatten RuntimeState to a single lookup table

## Summary

Replace the two-level lookup (`lookup_by_key` -> identity -> `slice_data_by_identity`)
with a single flat `lookup_by_key` that maps directly to all data the request handler
needs.  This removes the `slice_data_by_identity` field, eliminates two impossible
error paths in `handle_request_message`, and simplifies both state construction and
the hot path.

## Problem

Every DNS request traverses two lookup tables:

1. `lookup_by_key[(file_tag, slice_token)]` returns `(file_id, publish_version, slice_index)`
2. `slice_data_by_identity[(file_id, publish_version)]` returns `(slice_table, compressed_size)`

The second lookup and subsequent `slice_table[slice_index]` indexing can never fail
by construction -- `build_runtime_state` populates both tables from the same source
data in the same loop.  The `identity_missing` and `slice_index_out_of_bounds` error
paths in `handle_request_message` are fallbacks for impossible states, violating the
project invariant of preferring invariants to fallbacks.

## Goal

- `RuntimeState` has no `slice_data_by_identity` field.
- `lookup_by_key` maps `(file_tag, slice_token)` directly to
  `(file_id, publish_version, slice_index, slice_bytes, total_slices, compressed_size)`.
- `handle_request_message` performs a single dict lookup per request.
- The two impossible error paths (`identity_missing`, `slice_index_out_of_bounds`) are
  removed.

## Design

### state.py

Remove `slice_data_by_identity` from the `RuntimeState` namedtuple.

In `build_runtime_state`, change the lookup value from:
```python
lookup[key] = (publish_item.file_id, publish_item.publish_version, index)
```
to:
```python
lookup[key] = (
    publish_item.file_id,
    publish_item.publish_version,
    index,
    publish_item.slice_bytes_by_index[index],
    publish_item.total_slices,
    publish_item.compressed_size,
)
```

Remove the `slice_data_by_identity` dict, its population loop, and the
`duplicate_publish_identity` check (this invariant is already enforced upstream
by `prepare_publish_sources` which rejects duplicate `file_id` values via
`seen_file_ids`).

Remove `slice_data_by_identity` from the `RuntimeState(...)` constructor call.

### server.py

In `handle_request_message`, replace the current two-hop lookup (lines 148-177):

```python
identity_value = runtime_state.lookup_by_key.get(key)
if identity_value is None:
    ...
file_id, publish_version, slice_index = identity_value
...
identity = (file_id, publish_version)
slice_data = runtime_state.slice_data_by_identity.get(identity)
if slice_data is None:
    ...  # impossible
slice_table, compressed_size = slice_data
total_slices = len(slice_table)
if slice_index < 0 or slice_index >= total_slices:
    ...  # impossible
slice_bytes = slice_table[slice_index]
```

With a single destructure:

```python
entry = runtime_state.lookup_by_key.get(key)
if entry is None:
    return _classified_response(request, config, DNS_RCODE_NXDOMAIN, "miss", "mapping_not_found", request_context)

file_id, publish_version, slice_index, slice_bytes, total_slices, compressed_size = entry
```

The debug log for `mapping_resolved` stays, using `file_id` and `slice_index` from
the flat entry.

### doc/architecture/ARCHITECTURE.md

Update the "Runtime State Model" section (line 206) to remove the reference to
`slice_data_by_identity`.

## Affected Components

- `dnsdle/state.py`: remove `slice_data_by_identity` from `RuntimeState` namedtuple; flatten lookup values in `build_runtime_state`; remove `slice_data_by_identity` dict and its `duplicate_publish_identity` check
- `dnsdle/server.py`: replace two-hop lookup in `handle_request_message` with single destructure; remove `identity_missing` and `slice_index_out_of_bounds` error paths
- `doc/architecture/ARCHITECTURE.md`: update Runtime State Model to reflect single lookup table

## Execution Notes

Executed 2026-02-21.

Implemented as planned with one minor deviation in the architecture doc: the
review identified that the existing "two main state classes" count was wrong
(three bullets listed). Rather than just removing `slice_data_by_identity`
from the second bullet, the section was rewritten to three accurate bullets:
immutable publish state (now including `lookup_by_key`), network service
state, and per-request transient state.

Changes:
- `dnsdle/state.py`: removed `slice_data_by_identity` field from
  `RuntimeState` namedtuple; removed identity dict, its population loop, and
  the `duplicate_publish_identity` check from `build_runtime_state`; flattened
  lookup values to 6-tuples containing `(file_id, publish_version, index,
  slice_bytes, total_slices, compressed_size)`
- `dnsdle/server.py`: replaced two-hop lookup in `handle_request_message`
  with single flat destructure; removed `identity_missing` and
  `slice_index_out_of_bounds` error paths (14 lines removed)
- `doc/architecture/ARCHITECTURE.md`: rewrote Runtime State Model bullets to
  reflect single lookup table and correct the bullet count
