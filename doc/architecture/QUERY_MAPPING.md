# Query Mapping

This document defines how client query names map to published file slices.

Goals:
- keep client query names short
- avoid leaking file names and slice indexes in QNAMEs
- support out-of-order and retry-heavy retrieval
- keep mapping launch-scoped so cache reuse across launches is harmless

---

## Scope

This document covers:
- launch-scoped naming identifiers
- query token generation
- server/client mapping tables
- QNAME format
- cache and TTL behavior tied to mapping

Crypto binding for mapping fields is defined in `doc/architecture/CRYPTO.md`.

---

## Design Summary

At server startup:
1. Generate a random `publish_id`.
2. Build canonical slice tables for all served files.
3. Assign each served slice a short opaque `slice_token`.
4. Generate per-file clients with embedded token maps.

On the wire, clients query only opaque tokens:
- no plaintext file names
- no plaintext slice indexes

---

## Mapping Domain

A mapping entry is keyed by:
- `publish_id`
- `slice_token`

Each key resolves to:
- `file_id`
- `file_version`
- `slice_index`

Invariant:
- within a running server instance, mapping is immutable

---

## QNAME Format

v1 request name:

`<slice_token>.<publish_id>.<base_domain>`

Where:
- `slice_token` is opaque and launch-scoped
- `publish_id` is opaque and launch-scoped
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

## Token Alphabet and Length

Allowed token alphabet:
- lowercase letters `a-z`
- digits `0-9`

Constraints:
- choose the shortest token length that can represent all served slices with
  collision-safe assignment
- keep `publish_id` short and fixed-length
- both `slice_token` and `publish_id` must be `<= dns_max_label_len`
- keep total QNAME length within DNS limits

The server must fail startup if valid tokens cannot be assigned under current
length constraints.

---

## Token Assignment

Token assignment is random per launch and independent of:
- file path
- file name
- slice index value
- file ordering on CLI

Requirements:
1. Use cryptographically strong randomness.
2. Reject collisions until each slice has a unique token.
3. Store forward lookup map (`token -> slice identity`).
4. Emit generated client metadata with reverse lookup (`index -> token`) for
   that target file.

The same input files across two launches must produce different token spaces
unless randomness coincidentally repeats.

---

## Generated Client Mapping

Each generated client is file-specific and embeds:
- `publish_id`
- `file_id` and `file_version`
- `total_slices`
- ordered token list indexed by expected `slice_index`

Download loop behavior:
- pick missing `slice_index`
- map to `slice_token`
- query `<slice_token>.<publish_id>.<base_domain>`
- verify returned slice against embedded metadata and crypto rules

This supports out-of-order fetch and repeat retries without exposing index
values in QNAMEs.

---

## Server Lookup Flow

For each query:
1. Parse labels and match `<slice_token>.<publish_id>.<base_domain>`.
2. Reject if `publish_id` is unknown for current process.
3. Resolve `slice_token` in launch mapping table.
4. Retrieve canonical slice bytes for resolved file/version/index.
5. Return deterministic CNAME answer for that slice.

Unknown or malformed mapping keys must be rejected explicitly; no fallback to
other files, versions, or indexes is allowed.

---

## Caching and TTL

Short query names help response capacity because DNS responses include the
question section; smaller QNAMEs leave more bytes for CNAME payload.

Caching policy:
- mapping is launch-scoped through `publish_id`
- each restart uses a fresh `publish_id`
- old cached entries from prior launches do not match new names

TTL guidance:
- set explicit low TTL for slice answers
- do not rely on resolver defaults

Even with low TTL, some resolvers clamp minimum cache durations; launch-scoped
`publish_id` remains the primary cache-isolation mechanism.

---

## Security and Privacy Notes

This mapping scheme reduces direct metadata leakage in query names:
- no file paths on wire
- no raw slice indexes on wire

It does not hide:
- total query count
- timing patterns
- target base domain

---

## Invariants

1. `publish_id` is unique per server launch.
2. `slice_token` is unique within one `publish_id` namespace.
3. mapping tables are immutable while serving.
4. one mapping key resolves to exactly one canonical slice identity.
5. identical query name always produces the same slice payload for a running
   server instance.
6. all parse, bounds, and mapping failures are explicit hard failures.
