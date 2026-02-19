# Cryptography

This document defines cryptographic requirements for DNS slice delivery.

The design must support:
- out-of-order slice retrieval
- repeated requests for the same slice
- deterministic server responses per slice index
- fail-fast verification

---

## Scope

The server publishes one or more files. For each published file, the server:
- compresses file bytes
- splits compressed bytes into numbered slices
- serves each slice by DNS query index

The generated client retrieves slices in any order, with retries as needed,
then reassembles and verifies output.

---

## Threat Model (v1)

This design protects against:
- accidental corruption
- malicious tampering of slice content or metadata
- replay of stale slices from a different publish version

This design does not attempt to hide:
- query timing
- requested slice index
- total slice count behavior

---

## Required Properties

1. Slice independence
Each slice must be decryptable and verifiable without requiring prior slices.

2. Order independence
Any retrieval order must yield the same final reconstructed byte stream.

3. Retry idempotence
Repeated retrieval of the same slice index must produce identical bytes.

4. Domain separation
Different files or versions must never share keystream/MAC context.

5. Fail-fast behavior
Any verification mismatch is a hard failure, not a warning.

---

## File Identity

Each hosted file has immutable identity metadata embedded in the generated
client and enforced by the server:
- `file_id` (stable small integer or fixed tag)
- `publish_version` (compressed-publish discriminator)
- `total_slices`
- `compressed_size`
- `plaintext_sha256` (hex)

`publish_version` must change whenever served ciphertext changes.

---

## Key Derivation

Inputs:
- `psk`: operator-provided shared secret (non-empty)
- `file_id`
- `publish_version`

Derive per-file keys (HKDF-like expansion using HMAC-SHA256):
- `enc_key = HMAC_SHA256(psk, "dnsdle-enc-v1|" + file_id + "|" + publish_version)`
- `mac_key = HMAC_SHA256(psk, "dnsdle-mac-v1|" + file_id + "|" + publish_version)`

Invariant:
- A key context is bound to exactly one `(file_id, publish_version)`.

---

## Nonce / Stream Input

Because slices are requested out of order and retried, nonce input cannot depend
on send order.

Per-slice nonce input:
- `file_id`
- `publish_version`
- `slice_index`

Deterministic v1 keystream construction:
- `block[i] = HMAC_SHA256(enc_key, "dnsdle-enc-stream-v1|" + file_id + "|" + publish_version + "|" + slice_index + "|" + i)`
- concatenate blocks and truncate to `len(slice_bytes)`
- `ciphertext = slice_bytes XOR keystream`

Invariant:
- For a given `(file_id, publish_version, slice_index)`, encryption output is
  deterministic and stable across retries.

---

## Slice Authentication

Each served slice includes ciphertext plus authentication data.

MAC input must bind metadata and payload:
- `file_id`
- `publish_version`
- `slice_index`
- `total_slices`
- `compressed_size`
- `ciphertext`

Any MAC mismatch is fatal for that transfer session.

---

## Client Acceptance Rules

For each expected `slice_index` in `[0, total_slices - 1]`:

1. Validate index bounds before processing payload.
2. Verify MAC before accepting slice bytes.
3. If index is new, store decrypted bytes for that index.
4. If index already exists, new bytes and MAC must match prior stored value.
5. Any mismatch at an already-stored index is fatal.

After all indices are present:
1. Reassemble slices in strict index order.
2. Validate total reassembled compressed byte length equals `compressed_size`.
3. Decompress.
4. Compute SHA-256 over plaintext and compare to `plaintext_sha256`.
5. On mismatch, fail transfer and discard output file.

---

## Server Behavior Rules

1. Reject out-of-range slice requests.
2. For valid requests, derive deterministic ciphertext from canonical slice
   bytes for the requested index only.
3. Never emit variant encodings for the same slice index in one
   `(file_id, publish_version)` context.
4. If runtime state violates invariants (missing slice table, wrong bounds,
   key context mismatch), fail request path immediately.

---

## Algorithm Agility

The wire profile must carry an explicit crypto profile identifier, for example:
- `crypto_profile = "v1"`

Future profiles (for different ciphers or record layouts) must use a new
profile id and must not silently reinterpret v1 records.

---

## Python Compatibility Constraints

Implementation must support Python 2.7 and Python 3.x with standard library
only. Compatibility aliases and byte/text coercion helpers should be
centralized in `dnsdle/compat.py` so crypto/payload code paths avoid duplicated
interpreter-branching logic. Architecture-level crypto requirements are fixed;
implementation details must preserve the invariants in this document.
