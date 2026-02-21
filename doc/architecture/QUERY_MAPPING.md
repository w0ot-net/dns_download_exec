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
- identity layer: digest derivation from (`mapping_seed`, `publish_version`,
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
- `publish_version`
- `slice_index`

Invariant:
- mapping is deterministic for fixed identity inputs and fixed materialization
  constraints.

---

## QNAME Format

v1 request name:

`<slice_token>.<file_tag>.<selected_base_domain>`

Where:
- `slice_token` is opaque and deterministic
- `file_tag` is opaque and deterministic
- `selected_base_domain` is one configured domain from `domains`

Normalization rules:
- lowercase only
- no trailing dot in stored configured domains
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
- `publish_version` (compressed-publish identity)
- `slice_index`

Deterministic derivation:
- `seed_bytes = ascii_bytes(mapping_seed)`
- `publish_version_bytes = ascii_bytes(publish_version)` where
  `publish_version` is
  exactly 64 lowercase hex chars
- `slice_index_bytes = ascii_bytes(base10(slice_index))` with no sign and no
  leading zeros (except `0`)
- `file_digest = HMAC_SHA256(seed_bytes,
  b"dnsdle:file:v1|" + publish_version_bytes)`
- `slice_digest[i] = HMAC_SHA256(seed_bytes,
  b"dnsdle:slice:v1|" + publish_version_bytes + b"|" + slice_index_bytes)`

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
- full QNAME must satisfy DNS name-length limits for every configured domain
- startup sizing uses longest configured domain (`longest_domain_labels`) as the
  hard bound for all request-name capacity checks
- base32 text length from one SHA-256 digest is 52 chars; if required token
  length exceeds available digest text length, startup fails

The server must fail startup if valid deterministic identifiers cannot be
constructed within configured limits.
The server must also fail startup if duplicate `plaintext_sha256` values are
present across configured files (duplicate-content rejection), because that
would produce duplicate publish identity and mapping inputs for different file
entries.

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

Deterministic global collision resolution:
1. establish canonical file order by ascending
   `(file_tag, file_id, publish_version)`.
2. select minimal per-file token length that resolves local collisions.
3. detect global `(file_tag, slice_token)` collisions.
4. when collisions exist, promote exactly one file: the earliest colliding file
   in canonical order; increment only that file's token length by 1.
5. recompute that file's tokens and repeat.
6. fail startup if collisions remain when limits are reached.

Grouping invariant:
- mapping identity for one file depends only on
  `(mapping_seed, publish_version)`.
- token materialization output additionally depends on fixed length constraints
  for the launch (`file_tag_len`, `dns_max_label_len`, DNS name limits).
- when global key collisions occur, final materialized slice tokens also depend
  on deterministic promotion over the launch publish set.
- with duplicate-content entries rejected at startup and `publish_version`
  derived from compressed bytes, one mapping key always resolves to one
  canonical file context.

---

## Universal Client Mapping

The server generates a single universal client (`dnsdle_universal_client.py`)
that accepts all mapping parameters via CLI arguments:
- `--mapping-seed` and `--token-len`
- `--publish-version`
- `--total-slices`
- `--file-tag-len`

At runtime the client derives `file_id`, `file_tag`, and each slice token on
the fly using the same algorithms the server uses:
```
file_id       = sha256(b"dnsdle:file-id:v1|" + publish_version_bytes)[:16]
file_tag      = trunc_token(
    HMAC_SHA256(seed_bytes,
        b"dnsdle:file:v1|" + publish_version_bytes)
)[:file_tag_len]
slice_token[i] = trunc_token(
    HMAC_SHA256(seed_bytes,
        b"dnsdle:slice:v1|" + publish_version_bytes + b"|" + index_bytes)
)[:slice_token_len]
```

Download loop behavior:
- pick missing `slice_index`
- derive `slice_token` from `(mapping_seed, publish_version, slice_index)`
- query `<slice_token>.<file_tag>.<selected_base_domain>`
- verify returned slice against CLI-provided metadata and crypto rules

This supports out-of-order fetch and repeat retries without exposing index
values in QNAMEs. Client file size is bounded by code size alone, independent
of slice count.

---

## Server Lookup Flow

For each query:
1. Parse labels and match `<slice_token>.<file_tag>.<selected_base_domain>`
   where selected domain is in configured `domains`.
2. Resolve composite key `(file_tag, slice_token)` in deterministic mapping
   table. Reject if the key is not found.
3. Retrieve canonical slice bytes for resolved file/publish-version/index.
4. Return deterministic CNAME answer for that slice.

Unknown or malformed mapping keys must be rejected explicitly; no fallback to
other files, versions, or indexes is allowed.

---

## Caching and TTL

Short query names help response capacity because DNS responses include the
question section; smaller QNAMEs leave more bytes for CNAME payload.

Caching policy:
- deterministic names come from mapping identity inputs plus fixed
  materialization constraints and deterministic global-collision promotion
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
- target configured domain set
- long-term linkability when `mapping_seed` stays constant

To reduce cross-run linkability, rotate `mapping_seed`.

---

## Invariants

1. same identity inputs `(mapping_seed, publish_version, slice_index)` and same
   materialization constraints always yield the same `slice_token`.
2. same identity inputs `(mapping_seed, publish_version)` and same
   materialization constraints always yield the same `file_tag`.
3. `slice_token` is unique within one `file_tag` namespace.
4. mapping tables are immutable while serving.
5. one mapping key resolves to exactly one canonical slice identity.
6. identical query name always produces the same slice payload for a running
   server instance.
7. duplicate-content entries (`plaintext_sha256`) are rejected at startup.
8. unresolved global collisions at token-length limits fail startup.
9. all parse, bounds, and mapping failures are explicit hard failures.
