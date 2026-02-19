# Plan: Client Crypto and Parser Parity (v1)

## Summary
Implement a focused client-side parity layer for v1 encrypted CNAME slices so the client path can parse DNS/CNAME responses, validate payload invariants, verify MAC, decrypt ciphertext, and validate reconstructed output identity. This phase is intentionally narrow: it builds the protocol-correct decode/crypto core used by generated clients, without implementing full generated-client artifact emission or long-running download orchestration. The result is a deterministic, fail-fast reference implementation of the client verification path that exactly matches current server payload construction.

## Problem
Server-side payload construction now encrypts slice bytes and MACs ciphertext-bound metadata, but there is no corresponding implemented client decode/decrypt/verify core in the codebase. Without this parity layer, generated clients cannot reliably validate or reconstruct slices from runtime responses, and protocol regressions can occur when server/client crypto logic drifts.

## Goal
After implementation:
- A client-side core can decode CNAME payload labels into binary record bytes.
- Binary record invariants are validated (`profile`, `flags`, lengths, trunc-MAC size).
- MAC is verified against `(file_id, publish_version, slice_index, total_slices, compressed_size, ciphertext)`.
- Ciphertext is deterministically decrypted using the same derivation inputs as server encryption.
- Reassembly/decompress/hash validation core returns verified plaintext bytes or fail-fast errors.
- The implementation is Python 2.7/3.x compatible and standard-library-only.

## Design
### Scope
In scope:
1. DNS response CNAME payload extraction/parsing helpers for client use.
2. Payload record parse + MAC verification + decrypt helpers with strict invariants.
3. Reassembly/verification helpers (ordered join, compressed-size check, zlib decompress, plaintext hash check).
4. Architecture-doc alignment for exact client parity contract.

Out of scope:
- generated-client file emission
- full retry scheduler/domain rotation loop
- output path/write behavior
- execution behavior (still explicitly non-goal)

### 1. Introduce shared client parity helpers
Create a dedicated module for client-side protocol parity (for example `dnsdle/client_payload.py` or equivalent) that exposes:
- DNS response helper(s) to extract required CNAME target labels for a requested name.
- Payload decode helper:
  - validate response suffix (`response_label` + selected base domain)
  - join payload labels
  - base32-decode lowercase/no-pad form
- Binary record parse helper:
  - parse `profile`, `flags`, `cipher_len_u16`, `ciphertext`, `mac_trunc8`
  - enforce exact length/invariant checks
- Crypto verification/decrypt helper:
  - derive `enc_key` and `mac_key` with existing constants
  - recompute MAC over metadata + ciphertext and compare constant-time
  - derive deterministic keystream and XOR-decrypt ciphertext

Fail-fast model:
- no fallback parser modes
- unknown profile, nonzero flags, malformed lengths, base32 errors, MAC mismatch, and decrypt-context mismatches are fatal errors.

### 2. Reassembly and final integrity helpers
Add helper(s) that consume validated per-index slice plaintext bytes and enforce:
1. exact index coverage `[0, total_slices-1]`
2. duplicate-index byte equality
3. ordered reassembly by ascending index
4. compressed-length match against `compressed_size`
5. zlib decompression success
6. plaintext SHA-256 match against embedded `plaintext_sha256`

This keeps generator/runtime code thin later by reusing one audited verification core.

### 3. Error taxonomy and deterministic behavior
Define explicit client-core exception classes/reason codes (or equivalent stable error mapping) aligned with architecture categories:
- parse/format violation
- crypto verification failure
- reconstruction/hash/decompress failure

Determinism requirement:
- identical inputs produce identical parse/decrypt outputs or identical failure class.

### 4. Documentation alignment
Update architecture docs to reflect concrete parity behavior and where validation happens:
- client-side payload parse invariants and decode steps
- MAC/decrypt ordering
- reconstruction/hash enforcement details
- stable failure classification boundaries for this phase

### 5. Validation approach
Use deterministic unit coverage for parity core:
1. known-good server-built record roundtrip (decode -> verify -> decrypt == original slice)
2. malformed record length/flags/profile rejection cases
3. MAC mismatch rejection case
4. wrong metadata context rejection case
5. full reassembly/decompress/hash success and failure cases

Also run a targeted live-path check:
- obtain one runtime CNAME response from current server path and verify client core decodes/decrypts exactly to canonical startup slice bytes.

## Affected Components
- `dnsdle/cname_payload.py`: factor/reuse shared crypto primitives as needed so client/server derivations cannot drift.
- `dnsdle/compat.py`: add missing base32 decode/no-pad helpers (if needed) for client payload decoding with Python 2/3 parity.
- `dnsdle/dnswire.py`: add safe response-side helper(s) required to extract/validate client CNAME answer payloads.
- `dnsdle/constants.py`: extend payload/crypto constants only if needed to avoid hard-coded client parse values.
- `dnsdle/client_payload.py` (new): client parity core for payload decode, verify, decrypt, and record invariant checks.
- `dnsdle/client_reassembly.py` (new): deterministic reassembly/decompress/hash verification helpers.
- `unit_tests/test_client_payload_parity.py` (new): deterministic parse/MAC/decrypt parity tests and malformed-input rejection tests.
- `unit_tests/test_client_reassembly.py` (new): index coverage, duplicate handling, decompress, and final hash verification tests.
- `doc/architecture/CLIENT_RUNTIME.md`: align runtime phase details with implemented parity core boundaries.
- `doc/architecture/CLIENT_GENERATION.md`: clarify generated client should call parity helpers/embedded equivalent logic exactly.
- `doc/architecture/CRYPTO.md`: clarify client verification/decrypt ordering and parity requirements.
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`: clarify client-side binary record parse expectations and fatal validation cases.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: align failure class mapping for parse/crypto/reassembly violations.
