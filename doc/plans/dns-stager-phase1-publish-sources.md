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
- Cross-set uniqueness enforcement is supported: callers can pass
  `seen_plaintext_sha256` and `seen_file_ids` sets accumulated from a prior
  `build_publish_items()` call so that content hashes and file IDs are unique
  across both sets.
- `generate_client_artifacts()` includes the `"source"` field in its returned
  artifact dicts so callers can access the generated client source text
  without re-reading from disk.
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
    item_label,
):
```

`item_label` is a string used in error context (e.g. `"file_index=0"` or
`"source=dnsdl_..._linux.py"`).

Rewrite `build_publish_items()` to call `_build_single_publish_item()` per
file, keeping its disk-read loop. `build_publish_items_from_sources()` calls
the same helper per source entry.

### 3. Include `"source"` in `generate_client_artifacts()` return

`_build_artifacts()` (client_generator.py:260-268) already includes
`"source": source_text` in its internal artifact dicts. But
`generate_client_artifacts()` (lines 470-480) strips it when building the
returned `generated` list.

Change: include `"source": artifact["source"]` in the dicts appended to
`generated`. This is the only change to client_generator.py.

## Affected Components

- `dnsdle/publish.py`:
  - Extract `_build_single_publish_item()` private helper from the per-file
    loop body in `build_publish_items()`.
  - Rewrite `build_publish_items()` to use the extracted helper, preserving
    its current behavior exactly.
  - Add `build_publish_items_from_sources()` using the same helper.
- `dnsdle/client_generator.py`:
  - Include `"source"` field in the dicts returned by
    `generate_client_artifacts()`.
- `dnsdle/__init__.py`:
  - Add import for `build_publish_items_from_sources` (unused until Phase 2,
    but validates the import path).
