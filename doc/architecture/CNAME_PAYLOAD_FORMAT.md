# CNAME Payload Format

This document defines the v1 wire format for serving one encrypted slice per
DNS CNAME answer.

It is the contract between:
- query routing (`doc/architecture/QUERY_MAPPING.md`)
- cryptographic verification (`doc/architecture/CRYPTO.md`)

---

## Goals

1. Maximize usable slice bytes in each CNAME response.
2. Keep response parsing deterministic and fail-fast.
3. Keep request QNAMEs short and opaque.
4. Keep payload materialization deterministic for fixed startup state.

---

## Label Length Cap (Configurable)

The server exposes a configurable label-length cap:
- `dns_max_label_len`

Bounds:
- minimum: 16
- maximum: 63 (DNS protocol hard limit)

The effective payload label cap for CNAME encoding is:
- `effective_label_cap = dns_max_label_len`

Startup must fail if `dns_max_label_len` is outside `[16, 63]`.

---

## Record Model

For each valid slice query, the server returns exactly one CNAME answer that
encodes exactly one slice record.

The CNAME target has this shape:

`<payload_labels>.<response_label>.<selected_base_domain>`

Where:
- `<payload_labels>` is base32 text for the binary slice record
- `<response_label>` is a fixed discriminator label for responses
- `<selected_base_domain>` is one configured domain from `domains`

`<response_label>` must never be valid as a client `slice_token` so resolver
follow-up traffic cannot be misparsed as client slice requests.

---

## Binary Slice Record (v1)

Before DNS text encoding, the server builds this binary record:

```
+--------+--------+----------------+-------------------+-------------+
| Byte 0 | Byte 1 | Bytes 2..3     | Bytes 4..N-9      | Bytes N-8.. |
+--------+--------+----------------+-------------------+-------------+
|profile | flags  | slice_len_u16  | slice bytes       | mac_trunc8   |
+--------+--------+----------------+-------------------+-------------+
```

Field definitions:
- `profile` (1 byte): crypto profile id. v1 value is `0x01`.
- `flags` (1 byte): reserved. v1 requires `0x00`.
- `slice_len_u16` (2 bytes, big-endian): canonical slice-byte length.
- `slice bytes`: canonical bytes from startup publish state for this slice.
- `mac_trunc8` (8 bytes): truncated HMAC-SHA256 over slice metadata and payload.

Invariants:
1. `slice_len_u16` must equal actual slice-byte length.
2. `slice_len_u16` must be greater than zero.
3. Reserved flags must be zero.
4. Unknown `profile` is a hard failure.

---

## MAC Binding

The transmitted MAC field authenticates:
- `file_id`
- `publish_version`
- `slice_index`
- `total_slices`
- `compressed_size`
- `slice bytes`

The server derives per-file MAC key material from `psk`, `file_id`, and
`publish_version`, then emits an 8-byte truncated HMAC-SHA256 value.

Any MAC mismatch is fatal for client validation.

---

## DNS Text Encoding

Encoding steps:
1. Base32-encode the binary slice record.
2. Strip padding (`=`) characters.
3. Lowercase output text.
4. Split into labels of at most `effective_label_cap` characters.
5. Append `.<response_label>.<selected_base_domain>` where selected domain is
   the matched request suffix from configured `domains`.

Decoding steps:
1. Validate suffix `.<response_label>.<selected_base_domain>` for one
   configured domain.
2. Join payload labels.
3. Base32-decode (accept lowercase form).
4. Parse binary record and validate profile/flags/length invariants.

The server must emit canonical lowercase/no-padding encoding so duplicate
replies for the same slice are byte-stable at the DNS text layer.

---

## Size Budget and Slice Capacity

Server startup must compute a strict maximum ciphertext size per slice from DNS
name limits and DNS packet-size limits.

Inputs:
- maximum DNS name length (255 bytes including label lengths)
- configured `effective_label_cap` (16..63)
- configured `dns_edns_size` (default `1232`)
- fixed suffix length for `.<response_label>.<selected_base_domain>`
  using longest configured domain
- DNS message envelope terms (header, echoed question, one CNAME answer,
  optional OPT additional RR)
- binary record overhead (4-byte header + 8-byte truncated MAC)

Startup algorithm:
1. Compute max payload base32 characters that fit remaining CNAME target-name
   budget under longest configured domain.
2. Compute packet-size estimate for slice responses and enforce:
   - packet limit is `dns_edns_size` when `dns_edns_size > 512`
   - packet limit is `512` when `dns_edns_size = 512` (classic mode)
3. Use the largest payload size that satisfies both name and packet limits.
4. Convert base32 capacity to max raw binary record bytes.
5. Subtract fixed binary overhead to get `max_ciphertext_slice_bytes`.
6. Fail startup if `max_ciphertext_slice_bytes <= 0`.

Packet-size estimate is conservative in v1:
- include DNS header, echoed question, and one CNAME answer
- include OPT RR only when `dns_edns_size > 512`
- do not assume CNAME target suffix compression savings during startup budget
  calculation

All file slicing for the launch must use `max_ciphertext_slice_bytes` or
smaller.

---

## Server Response Rules

For a valid request:
1. Resolve query to exactly one slice identity.
2. Build deterministic binary slice record for that identity.
3. Encode deterministic CNAME target text.
4. Return one IN CNAME answer with configured TTL.

For invalid requests:
- return a deterministic miss behavior (defined in
  `doc/architecture/ERRORS_AND_INVARIANTS.md`).

No fallback mapping or alternate slice substitution is allowed.

---

## Client Validation Rules

For each response:
1. Question/answer name must match request routing expectation.
2. CNAME suffix must match expected response suffix.
3. Record parse checks must pass (`profile`, flags, lengths).
4. MAC must validate against mapped slice metadata.
5. Stored bytes for duplicate slice index must match exactly.

Any violation is a hard failure for that transfer session.

---

## Caching and Determinism

Caching may cause duplicate delivery of the same CNAME answer.
This is safe only if responses are deterministic per mapped slice identity.

Required property:
- same `(file_tag, slice_token)` in one server process always yields the same
  CNAME target text and TTL.
- with unchanged mapping, crypto, and wire inputs (`mapping_seed`,
  `publish_version`, `compression_level`, `psk`, configured domain set,
  `response_label`,
  `dns_max_label_len`, profile ids, `ttl`, and implementation profile from
  `doc/architecture/PUBLISH_PIPELINE.md`), derived `(file_tag, slice_token)`
  and CNAME target text/TTL remain stable across restarts.

---

## Versioning

Any change to:
- binary record layout
- profile id semantics
- base32 canonicalization rules
- suffix parse rules

requires a new profile version and simultaneous update of:
- server encoder
- generated client decoder
- related architecture docs
