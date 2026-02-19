# Publish Pipeline

This document defines the v1 startup pipeline that transforms configured input
files into immutable publish artifacts consumed by:
- server request handling
- generated client metadata emission

It is the source-of-truth contract for compression, hashing, slicing, and
manifest construction.

---

## Goals

1. Produce deterministic publish bytes and metadata for fixed inputs.
2. Enforce strict bounds from DNS payload capacity.
3. Keep publish state immutable while serving.
4. Fail fast on any contract or invariant violation.

---

## Inputs

Required validated inputs:
- `files` list from `doc/architecture/CONFIG.md`
- `compression_level` (`0..9`)
- mapping config (`mapping_seed`, `file_tag_len`, `dns_max_label_len`)
- wire config (`domain`, `response_label`, `dns_edns_size`)
- `max_ciphertext_slice_bytes` from
  `doc/architecture/CNAME_PAYLOAD_FORMAT.md`
- fixed profile ids (`crypto_profile=v1`, `wire_profile=v1`)

All inputs must be validated before this pipeline starts.

---

## Output Contract

The pipeline emits one immutable publish object per input file.

Required fields per publish object:
- `file_id`
- `file_version`
- `file_tag`
- `plaintext_sha256`
- `compressed_size`
- `total_slices`
- `slice_bytes_by_index` (0-based contiguous array)
- `slice_tokens` (same cardinality/order as `slice_bytes_by_index`)
- `crypto_profile`
- `wire_profile`

Invariants:
1. `len(slice_bytes_by_index) == total_slices`
2. `len(slice_tokens) == total_slices`
3. every `slice_bytes_by_index[i]` length is `> 0`
4. `sum(len(slice_bytes_by_index[i])) == compressed_size`

---

## Deterministic Processing Order

For each configured file, process in this exact order:

1. Read plaintext bytes from disk.
2. Compute plaintext hash and identity fields.
3. Validate `file_version` uniqueness across all configured files.
4. Compress plaintext with deterministic settings.
5. Compute slice geometry from `max_ciphertext_slice_bytes`.
6. Split compressed bytes into canonical ordered slices.
7. Derive deterministic mapping identifiers (`file_tag`, `slice_tokens`).
8. Build immutable publish object and lookup tables.

No step may be skipped or reordered.

---

## Hash and Identity Fields

### Plaintext Hash

- `plaintext_sha256 = sha256(plaintext_bytes).hexdigest().lower()`

### File Version

v1 defines:
- `file_version = plaintext_sha256`

This binds mapping identity to file content only.
Within one launch, `file_version` must be unique across configured files.

### File ID

`file_id` must be deterministic from `file_version` only (not from input path,
input order, or total file set).

v1 rule:
- `file_id = sha256("dnsdle:file-id:v1|" + file_version).hexdigest()[:16]`

Launch invariant:
- `file_id` collisions across configured files are startup errors.

---

## Compression Contract

Compression is mandatory in v1.

v1 compression algorithm:
- zlib stream (RFC 1950 wrapper + DEFLATE payload)
- level = configured `compression_level` (`0..9`)
- no preset dictionary

Determinism rule:
- fixed plaintext bytes + fixed `compression_level` must produce identical
  `compressed_bytes` across runs in the same implementation profile.

Failure rules:
- compression failure is a fatal startup error.
- empty `compressed_bytes` is a fatal startup error.

---

## Slice Geometry

Inputs:
- `compressed_size = len(compressed_bytes)`
- `max_ciphertext_slice_bytes` from
  `doc/architecture/CNAME_PAYLOAD_FORMAT.md`

v1 crypto profile is length-preserving per slice, so:
- `max_plain_slice_bytes = max_ciphertext_slice_bytes`

Constraints:
- `max_plain_slice_bytes > 0`
- `compressed_size > 0`

Total slice count:
- `total_slices = ceil(compressed_size / max_plain_slice_bytes)`

Startup fails if:
- `total_slices <= 0`
- `total_slices` cannot be represented in required metadata fields

---

## Slice Split Contract

Slice array is built by contiguous chunking in ascending index order:

- `slice[0] = compressed_bytes[0 : max_plain_slice_bytes]`
- `slice[1] = compressed_bytes[max_plain_slice_bytes : 2*max_plain_slice_bytes]`
- ...
- `slice[n-1]` is the final remainder chunk

Rules:
- no overlap
- no gaps
- stable order (index defines canonical order)
- each slice length must be `> 0`

`compressed_size` must equal concatenated slice length exactly.

---

## Mapping Integration

After slices are built, derive mapping identifiers from
`doc/architecture/QUERY_MAPPING.md`:

- derive deterministic `file_tag` from `(mapping_seed, file_version)`
- derive deterministic `slice_token[i]` from
  `(mapping_seed, file_version, i)`

Required properties:
- mapping for one file depends only on `(mapping_seed, file_version)`
- mapping does not depend on file path, startup time, or other hosted files
- token cardinality/order matches slice array cardinality/order

---

## Manifest Build and Freezing

After all fields are computed:
1. build per-file publish object
2. build one global server lookup map keyed by `(file_tag, slice_token)`
   across all published files
3. validate all manifest invariants
4. freeze publish state as immutable before serving starts

No runtime mutation of published slice bytes is allowed after freeze.

---

## Fail-Fast Conditions

Any of the following must fail startup:
- unreadable file or read failure
- hash/compression/slicing failure
- duplicate `file_version` across configured files
- `max_ciphertext_slice_bytes <= 0`
- empty compressed output
- any manifest length mismatch
- mapping derivation failure within DNS limits
- `file_id` collision
- lookup-map collision for `(file_tag, slice_token)`

Partial publish state must not be served.

---

## Logging Requirements

Minimum per-file startup log fields:
- `file_id`
- `file_version`
- `file_tag`
- `compressed_size`
- `total_slices`
- `max_ciphertext_slice_bytes`

Sensitive data that must not be logged:
- raw plaintext bytes
- raw PSK or derived keys
- full source path in network-facing request logs

---

## Related Docs

- `doc/architecture/ARCHITECTURE.md`
- `doc/architecture/CONFIG.md`
- `doc/architecture/QUERY_MAPPING.md`
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`
- `doc/architecture/CRYPTO.md`
- `doc/architecture/SERVER_RUNTIME.md`
- `doc/architecture/CLIENT_GENERATION.md`
