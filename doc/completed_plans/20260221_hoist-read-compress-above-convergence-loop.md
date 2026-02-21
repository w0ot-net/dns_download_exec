# Plan: Hoist file reads and compression above the convergence loop

## Summary

The convergence loop in `dnsdle/__init__.py` re-reads payload files from disk
and re-compresses every source on every iteration, even though file contents
and compression results are iteration-invariant -- only slicing changes because
`max_ciphertext_slice_bytes` changes.  This plan splits publish-item
construction into a prepare phase (read, hash, compress, derive IDs -- done
once) and a slice phase (done per iteration), eliminating redundant I/O and
compression.  It also removes the awkward two-call pattern with `seen_*` set
forwarding between payload and client publish calls.

## Problem

1. `build_publish_items` reads all payload files and compresses them via
   `build_publish_items_from_sources` on every convergence iteration.
2. The client source is likewise re-compressed every iteration via a second
   call to `build_publish_items_from_sources`.
3. The caller must extract `seen_sha256`/`seen_ids` sets from the first call
   and pass them to the second call to enforce cross-set uniqueness.
4. All of this work (disk I/O, hashing, compression, ID derivation, uniqueness
   checks) is iteration-invariant and should happen exactly once.

## Goal

- File reads and compression happen exactly once, before the convergence loop.
- The convergence loop body only re-slices and re-maps.
- The two-call pattern with `seen_*` forwarding is eliminated.
- `build_publish_items` and the `seen_*` parameters on
  `build_publish_items_from_sources` are removed (clean break).
- Existing publish-item dict schema is preserved for downstream consumers
  (`apply_mapping`, `build_runtime_state`).

## Design

Split `_build_single_publish_item` into two stages:

### Stage 1: Prepare (once, before loop)

New private function `_prepare_single_source(source_filename, plaintext_bytes,
compression_level, seen_plaintext_sha256, seen_file_ids)` performs:

- `plaintext_sha256` computation and duplicate check
- Compression
- `publish_version` and `file_id` derivation and duplicate check
- Returns a dict containing `source_filename`, `plaintext_sha256`,
  `compressed_bytes`, `compressed_size`, `publish_version`, `file_id`

New public function `prepare_publish_sources(sources, compression_level)`
replaces `build_publish_items_from_sources` for the invariant work.  It
iterates over sources, calls `_prepare_single_source`, enforces uniqueness
internally (no `seen_*` parameters needed), and returns the list of prepared
dicts.  Logs each prepared item at debug level with the fields available at
prepare time (`file_id`, `publish_version`, `plaintext_sha256`,
`compressed_size`, `source_filename`, `source_index`).  The `total_slices`
field is dropped from this log record because it depends on the
iteration-dependent slice size; it is already reported downstream by
`apply_mapping`'s diagnostic log.

### Stage 2: Slice (per iteration, inside loop)

New public function `slice_prepared_sources(prepared_sources,
max_ciphertext_slice_bytes)` takes the prepared dicts, slices
`compressed_bytes` for each, and returns **new** publish-item dicts matching
the existing schema (with `slice_bytes_by_index` and `total_slices`, without
`compressed_bytes`).

Invariants:
- `slice_prepared_sources` must never mutate the prepared dicts.  The same
  prepared list is reused across convergence iterations, so each call must
  construct fresh output dicts from the prepared inputs.
- `slice_prepared_sources` validates `max_ciphertext_slice_bytes > 0`
  (moved from the removed `build_publish_items_from_sources`).

### File reading

New public function `read_payload_sources(config)` is extracted from the
file-reading portion of `build_publish_items`.  Returns a list of
`(filename, bytes)` tuples.

### Removed functions

- `build_publish_items` -- its file-reading moves to `read_payload_sources`;
  its processing moves to `prepare_publish_sources` + `slice_prepared_sources`.
- `_build_single_publish_item` -- replaced by `_prepare_single_source`.
- `build_publish_items_from_sources` -- replaced by `prepare_publish_sources`.

### Convergence loop rewrite (`__init__.py`)

Before the loop:
```python
payload_sources = read_payload_sources(config)
all_sources = payload_sources + [(client_filename, client_bytes)]
prepared = prepare_publish_sources(all_sources, config.compression_level)
```

Inside the loop (the only work that changes per iteration):
```python
publish_items = slice_prepared_sources(prepared, max_ciphertext_slice_bytes)
combined_mapped = apply_mapping(publish_items, config)
```

## Affected Components

- `dnsdle/publish.py`: Remove `build_publish_items`,
  `build_publish_items_from_sources`, `_build_single_publish_item`.  Add
  `read_payload_sources`, `prepare_publish_sources`,
  `slice_prepared_sources`, `_prepare_single_source`.  Remove `os` import
  (moves to `__init__.py` if needed, but `read_payload_sources` uses
  `os.path.basename` so the import stays).
- `dnsdle/__init__.py`: Replace imports; hoist source reading and preparation
  above the loop; simplify the loop body to slice + map only.

## Execution Notes

Executed 2026-02-21. No deviations from the plan.

- Removed `build_publish_items`, `build_publish_items_from_sources`,
  `_build_single_publish_item`, and `_log_publish_item_built` from
  `dnsdle/publish.py`.
- Added `_prepare_single_source`, `read_payload_sources`,
  `prepare_publish_sources`, and `slice_prepared_sources` to
  `dnsdle/publish.py`.  The `os` import was retained (used by
  `read_payload_sources`).
- `slice_prepared_sources` constructs fresh output dicts on each call;
  prepared dicts are never mutated.  The `max_ciphertext_slice_bytes > 0`
  guard was moved here from the removed `build_publish_items_from_sources`.
- The debug log in `prepare_publish_sources` omits `total_slices` (not
  known at prepare time); all other fields from the old log are preserved.
- In `dnsdle/__init__.py`: replaced the two old imports with the three new
  ones; hoisted `read_payload_sources` + `prepare_publish_sources` above the
  convergence loop; loop body now calls only `slice_prepared_sources` +
  `apply_mapping`.  The `seen_sha256`/`seen_ids` extraction and the
  cross-set forwarding to the second `build_publish_items_from_sources` call
  were eliminated.
- Validated: import succeeds for both modules; behavior is identical to
  before with file I/O and compression now occurring exactly once.
- Commit: `<see below>`.
