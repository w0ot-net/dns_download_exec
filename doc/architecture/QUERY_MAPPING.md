# Query Mapping

This document defines how client query names map to published file slices.

Goals:
- keep client query names short
- avoid leaking file names and slice indexes in QNAMEs
- support out-of-order and retry-heavy retrieval
- keep mapping deterministic for compatibility across restarts
- allow operator-controlled remapping via `mapping_seed`

---

## Scope

This document covers:
- deterministic naming identifiers
- query token generation
- server/client mapping tables
- QNAME format
- cache and TTL behavior tied to mapping

Crypto binding for mapping fields is defined in `doc/architecture/CRYPTO.md`.

---

## Design Summary

Mappings use a two-layer deterministic model:
- identity layer: digest derivation from (`mapping_seed`, `file_version`,
  `slice_index`)
- materialization layer: token-string truncation/length constraints from config
  (`file_tag_len`, `dns_max_label_len`, DNS name limits)

At server startup:
1. Build canonical slice tables for all served files.
2. Derive deterministic `file_tag` per file.
3. Derive deterministic `slice_token` per slice.
4. Generate per-file clients with embedded deterministic mapping outputs.

On the wire, clients query only opaque tokens:
- no plaintext file names
- no plaintext slice indexes

---

## Mapping Domain

A mapping entry is keyed by:
- `file_tag`
- `slice_token`

Each key resolves to:
- `file_id`
- `file_version`
- `slice_index`

Invariant:
- mapping is deterministic for fixed identity inputs and fixed materialization
  constraints.

---

## QNAME Format

v1 request name:

`<slice_token>.<file_tag>.<base_domain>`

Where:
- `slice_token` is opaque and deterministic
- `file_tag` is opaque and deterministic
- `base_domain` is operator configured

Normalization rules:
- lowercase only
- no trailing dot in stored `base_domain`
- DNS label and full-name length limits must always be enforced

Configurable label cap:
- `dns_max_label_len` controls maximum label length for generated names
- valid range is `[16, 63]`
- startup fails if out of range

---

## Identifier Derivation

Allowed token alphabet:
- lowercase letters `a-z`
- digits `0-9`

Deterministic inputs:
- `mapping_seed` (operator config, default `0`)
- `file_version` (content identity)
- `slice_index`

Deterministic derivation:
- `seed_bytes = ascii_bytes(mapping_seed)`
- `file_version_bytes = ascii_bytes(file_version)` where `file_version` is
  exactly 64 lowercase hex chars
- `slice_index_bytes = ascii_bytes(base10(slice_index))` with no sign and no
  leading zeros (except `0`)
- `file_digest = HMAC_SHA256(seed_bytes,
  b"dnsdle:file:v1|" + file_version_bytes)`
- `slice_digest[i] = HMAC_SHA256(seed_bytes,
  b"dnsdle:slice:v1|" + file_version_bytes + b"|" + slice_index_bytes)`

`trunc_token(...)` means:
- encode digest with RFC 4648 base32
- lowercase output
- strip `=` padding
- truncate to configured/derived length

Determinism model:
- HMAC digest inputs define stable identity values.
- final token strings additionally depend on configured length constraints.
- for fixed digest inputs and fixed constraints, token output is stable.

Encoding definition:
- `digest_text = base32_lower_no_pad(digest)` where:
  - `base32` is RFC 4648
  - alphabet is `a-z2-7`
  - `a-z2-7` is a subset of the allowed query alphabet `[a-z0-9]`
  - output is lowercase and has no `=` padding
- `file_tag = digest_text(file_digest)[:file_tag_len]`
- `slice_token[i] = digest_text(slice_digest[i])[:slice_token_len]`

Length selection:
- `file_tag` uses configured `file_tag_len`
- `slice_token` uses the shortest collision-safe deterministic length allowed
  by DNS constraints

Length constraints:
- `len(file_tag) <= dns_max_label_len`
- `len(slice_token) <= dns_max_label_len`
- full QNAME must satisfy DNS name-length limits
- base32 text length from one SHA-256 digest is 52 chars; if required token
  length exceeds available digest text length, startup fails

The server must fail startup if valid deterministic identifiers cannot be
constructed within configured limits.
The server must also fail startup if duplicate `file_version` values are
present across configured files, because that would produce identical mapping
inputs for different file entries.

---

## Collision Handling

Requirements:
1. Derivation must be deterministic.
2. Composite mapping-key collisions (`file_tag`, `slice_token`) are not
   allowed within or across files in one launch.
3. Resolve collisions by increasing token length (up to limits), never by
   adding randomness.
4. Store forward lookup map
   (`(file_tag, slice_token) -> canonical slice identity`).
5. Emit generated client metadata with reverse lookup (`index -> token`) for
   that target file.
6. Validate global uniqueness of every `(file_tag, slice_token)` key across
   the full launch before serving requests.

Grouping invariant:
- mapping identity for one file depends only on
  `(mapping_seed, file_version)`.
- token materialization output additionally depends on fixed length constraints
  for the launch (`file_tag_len`, `dns_max_label_len`, DNS name limits).
- with duplicate `file_version` entries rejected at startup, one mapping key
  always resolves to one canonical file context.

---

## Generated Client Mapping

Each generated client is file-specific and embeds:
- `file_tag`
- `file_id` and `file_version`
- `total_slices`
- ordered token list indexed by expected `slice_index`

Download loop behavior:
- pick missing `slice_index`
- map to `slice_token`
- query `<slice_token>.<file_tag>.<base_domain>`
- verify returned slice against embedded metadata and crypto rules

This supports out-of-order fetch and repeat retries without exposing index
values in QNAMEs.

---

## Server Lookup Flow

For each query:
1. Parse labels and match `<slice_token>.<file_tag>.<base_domain>`.
2. Reject if `file_tag` is unknown for current process.
3. Resolve composite key `(file_tag, slice_token)` in deterministic mapping
   table.
4. Retrieve canonical slice bytes for resolved file/version/index.
5. Return deterministic CNAME answer for that slice.

Unknown or malformed mapping keys must be rejected explicitly; no fallback to
other files, versions, or indexes is allowed.

---

## Caching and TTL

Short query names help response capacity because DNS responses include the
question section; smaller QNAMEs leave more bytes for CNAME payload.

Caching policy:
- deterministic names come from mapping identity inputs plus fixed
  materialization constraints
- keeping `mapping_seed` stable preserves client compatibility across restarts
- changing `mapping_seed` remaps identifiers and invalidates old clients

TTL guidance:
- set explicit low TTL for slice answers
- do not rely on resolver defaults

Even with low TTL, some resolvers clamp minimum cache durations.

---

## Security and Privacy Notes

This mapping scheme reduces direct metadata leakage in query names:
- no file paths on wire
- no raw slice indexes on wire

It does not hide:
- total query count
- timing patterns
- target base domain
- long-term linkability when `mapping_seed` stays constant

To reduce cross-run linkability, rotate `mapping_seed`.

---

## Invariants

1. same identity inputs `(mapping_seed, file_version, slice_index)` and same
   materialization constraints always yield the same `slice_token`.
2. same identity inputs `(mapping_seed, file_version)` and same
   materialization constraints always yield the same `file_tag`.
3. `slice_token` is unique within one `file_tag` namespace.
4. mapping tables are immutable while serving.
5. one mapping key resolves to exactly one canonical slice identity.
6. identical query name always produces the same slice payload for a running
   server instance.
7. duplicate `file_version` entries are rejected at startup.
8. all parse, bounds, and mapping failures are explicit hard failures.
