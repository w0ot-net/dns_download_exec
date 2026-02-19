# Plan: Phase 1 -- Publish Sources Infrastructure

## Summary

Add the ability to publish in-memory source text through the existing publish
pipeline, and expose generated client source text from the generation API.
These are additive changes with no impact on existing behavior. They provide
the foundation that Phase 2 (two-phase startup) builds on.

## Prerequisites

None. This is the first phase.

## Goal

After implementation:

- `dnsdle/publish.py` exposes `build_publish_items_from_sources()` which
  accepts in-memory `(source_filename, plaintext_bytes)` pairs and produces
  publish item dicts through the same compress/hash/slice pipeline as
  `build_publish_items()`.
- Cross-set uniqueness enforcement is supported: both `build_publish_items()`
  and `build_publish_items_from_sources()` accept optional
  `seen_plaintext_sha256` and `seen_file_ids` sets. Callers share a single
  pair of sets across both calls so that content hashes and file IDs are
  unique across both publish passes (the sets are mutated in place).
- `generate_client_artifacts()` includes the `"source"` and `"filename"`
  fields in its returned artifact dicts so callers can access the generated
  client source text without re-reading from disk and can identify
  artifacts by their base filename (without the managed directory prefix).
- No functional change to the existing startup flow, server, or client
  generation.

## Design

### 1. `build_publish_items_from_sources()` in `dnsdle/publish.py`

Signature:

```python
def build_publish_items_from_sources(
    sources,
    compression_level,
    max_ciphertext_slice_bytes,
    seen_plaintext_sha256=None,
    seen_file_ids=None,
):
```

- `sources`: iterable of `(source_filename, plaintext_bytes)` pairs. Each
  `plaintext_bytes` is a `bytes` object (the raw content to publish).
  `source_filename` is a string used as the `"source_filename"` field in the
  returned publish item dict.
- `compression_level`: zlib compression level (0..9).
- `max_ciphertext_slice_bytes`: maximum bytes per ciphertext slice (from
  budget computation).
- `seen_plaintext_sha256`: optional `set` of SHA-256 hex strings already
  consumed by a prior publish pass. When provided, the function checks
  uniqueness against this set AND adds new entries to it (mutates in place).
  When `None`, an internal set is used.
- `seen_file_ids`: optional `set` of file_id strings already consumed.
  Same mutation semantics as `seen_plaintext_sha256`.

Processing per source entry (identical to `build_publish_items()` except no
disk I/O):

1. `plaintext_sha256 = sha256(plaintext_bytes).hexdigest()`
2. Check `plaintext_sha256` not in `seen_plaintext_sha256`; add it.
3. `compressed_bytes = zlib.compress(plaintext_bytes, compression_level)`
4. `publish_version = sha256(compressed_bytes).hexdigest()`
5. `file_id = _derive_file_id(publish_version)`
6. Check `file_id` not in `seen_file_ids`; add it.
7. `slice_bytes_by_index = _chunk_bytes(compressed_bytes, max_ciphertext_slice_bytes)`
8. Build and append publish item dict.

Returns: list of publish item dicts (same schema as `build_publish_items()`).

Raises `StartupError` on:
- `max_ciphertext_slice_bytes <= 0`
- duplicate `plaintext_sha256` (within sources or cross-set)
- duplicate `file_id` (within sources or cross-set)
- compression failure or empty compression output

### 2. Refactor shared logic in `publish.py`

The per-item processing in `build_publish_items()` (lines 56-111) and the
new `build_publish_items_from_sources()` share the same hash/compress/slice
pipeline. Extract a private helper:

```python
def _build_single_publish_item(
    source_filename,
    plaintext_bytes,
    compression_level,
    max_ciphertext_slice_bytes,
    seen_plaintext_sha256,
    seen_file_ids,
    item_context,
):
```

`item_context` is a dict merged into `StartupError` context for any error
raised by the helper (e.g. `{"file_index": 0}` from the disk caller or
`{"source_filename": "dnsdl_..._linux.py"}` from the sources caller). This
preserves structured error output.

The helper returns the publish item dict. Callers are responsible for
diagnostic logging (the existing `log_event` call in `build_publish_items()`
stays in its disk-read loop; `build_publish_items_from_sources()` adds its
own logging with source-appropriate context).

Rewrite `build_publish_items()` to call `_build_single_publish_item()` per
file, keeping its disk-read loop and per-item logging. Add optional
`seen_plaintext_sha256=None` and `seen_file_ids=None` parameters to
`build_publish_items()` (same semantics as `build_publish_items_from_sources()`
-- when `None`, an internal set is used; when provided, the caller's sets are
mutated in place). This keeps the two functions symmetric and lets Phase 2
callers share a single pair of sets across both publish passes without
reconstructing them from returned items.
`build_publish_items_from_sources()` calls the same helper per source entry.

### 3. Include `"source"` and `"filename"` in `generate_client_artifacts()` return

`_build_artifacts()` (client_generator.py:260-268) already includes
`"source": source_text` and `"filename": filename` in its internal
artifact dicts. But `generate_client_artifacts()` (lines 470-480) strips
both when building the returned `generated` list (it transforms
`filename` into the full `path`).

Change: include `"source": artifact["source"]` and
`"filename": artifact["filename"]` in the dicts appended to `generated`.
Phase 2 uses `artifact["filename"]` as the `source_filename` when
publishing client scripts, and Phase 5 uses it to match generation
artifacts to their corresponding client publish items.

## Affected Components

- `dnsdle/publish.py`:
  - Extract `_build_single_publish_item()` private helper from the per-file
    loop body in `build_publish_items()`.
  - Rewrite `build_publish_items()` to use the extracted helper. Add optional
    `seen_plaintext_sha256` and `seen_file_ids` parameters (default `None`)
    for cross-set uniqueness; existing callers are unaffected.
  - Add `build_publish_items_from_sources()` using the same helper.
- `dnsdle/client_generator.py`:
  - Include `"source"` and `"filename"` fields in the dicts returned by
    `generate_client_artifacts()`.
