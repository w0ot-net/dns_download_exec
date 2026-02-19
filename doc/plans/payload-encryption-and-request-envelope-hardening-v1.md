# Plan: Payload Encryption and Request Envelope Hardening (v1)

## Summary
Implement two high-priority runtime hardening changes in one clean-break phase: (1) restore deterministic per-slice encryption in CNAME payload records, and (2) enforce strict DNS request-envelope validation before any mapping lookup. The intent is to bring runtime behavior back in line with the v1 crypto and DNS contracts and eliminate acceptance of malformed-but-parseable requests. The outcome is deterministic encrypted slice serving with fail-fast miss classification for invalid request headers.

## Problem
Current runtime behavior has two contract violations:
1. `dnsdle/cname_payload.py` emits raw slice bytes plus MAC, so payloads are authenticated but not encrypted.
2. `dnsdle/server.py` accepts parseable requests with invalid query-envelope semantics (for example `QR=1`, non-query opcode, non-zero answer/authority counts) and can still serve data.

These behaviors conflict with architecture intent in `doc/architecture/CRYPTO.md` and request-envelope expectations in `doc/architecture/DNS_MESSAGE_FORMAT.md`.

## Goal
After implementation:
- CNAME payload records carry ciphertext (not plaintext slice bytes) and deterministic MAC over metadata plus ciphertext.
- Encryption and authentication remain deterministic for a fixed `(psk, file_id, publish_version, slice_index, slice_bytes)` input.
- Server rejects invalid parseable query envelopes before follow-up/slice classification.
- Invalid envelopes are deterministic miss paths (`NXDOMAIN`) with stable reason codes.
- Architecture docs are aligned to exact implementation behavior with no compatibility shim language.

## Design
### Scope and constraints
- Scope is limited to server-side payload construction and request-envelope validation.
- Keep Python 2.7/3.x standard-library-only implementation.
- Prefer invariants over fallbacks; malformed envelopes never reach mapping resolution.
- Clean break: no compatibility mode for prior MAC-only wire output.

### 1. Restore deterministic per-slice encryption
Implement explicit encryption in `dnsdle/cname_payload.py`:
1. Derive `enc_key` from `psk`, `file_id`, and `publish_version` using HMAC-SHA256 domain separation.
2. Build deterministic per-slice keystream from `(file_id, publish_version, slice_index, counter)` using HMAC-SHA256 block expansion.
3. XOR keystream with canonical slice bytes to produce ciphertext.
4. Keep record structure shape stable (`profile`, `flags`, `len_u16`, payload bytes, truncated MAC), but payload bytes become ciphertext bytes.
5. Compute MAC over bound metadata plus ciphertext (`file_id`, `publish_version`, `slice_index`, `total_slices`, `compressed_size`, `ciphertext`).

Determinism rule:
- identical inputs must produce identical ciphertext and MAC.

Fail-fast rules:
- invalid numeric bounds, empty PSK, or out-of-range indexes raise encoding errors; server maps these to runtime fault where applicable.

### 2. Harden request-envelope validation before routing
Add deterministic pre-routing envelope checks in `dnsdle/server.py` (using parsed header fields from `dnsdle/dnswire.py`):
1. Require query semantics (`QR=0`, opcode=`QUERY`).
2. Require `QDCOUNT=1`.
3. Require `ANCOUNT=0` and `NSCOUNT=0`.
4. Constrain additional-section count to v1-acceptable values (`ARCOUNT` bounds policy), with explicit reason code on violation.
5. Keep existing qtype/qclass checks and domain/mapping classification after envelope validation.

Classification policy:
- parseable envelope violations are deterministic misses with stable reason codes.
- unparseable datagrams continue to be dropped.

### 3. Reason-code and logging alignment
Add/standardize request miss reason codes for envelope violations (for example `invalid_query_flags`, `unsupported_opcode`, `invalid_query_section_counts`, `invalid_additional_count`) and ensure emitted records remain machine-parseable with `classification`, `phase`, and `reason_code`.

### 4. Architecture-doc synchronization
Update docs in the same change to match behavior exactly:
- Define concrete v1 encryption construction and key/keystream derivation.
- Define that binary record payload bytes are ciphertext bytes.
- Define explicit parseable-envelope validation requirements in request handling.
- Ensure error matrix and validation order reflect new fail-fast envelope checks.

### 5. Validation approach (no test-file changes in this phase)
Run deterministic/manual validation against runtime behavior:
1. Same startup inputs + same query => identical CNAME payload bytes across retries.
2. Ciphertext segment differs from plaintext slice bytes for non-trivial slice input.
3. Invalid parseable headers (`QR=1`, non-query opcode, non-zero AN/NS counts, out-of-policy ARCOUNT) => `NXDOMAIN` miss responses, never served slices.
4. Valid request envelopes still serve `NOERROR` + one CNAME for mapped slices.

## Affected Components
- `dnsdle/cname_payload.py`: implement deterministic encryption plus MAC-over-ciphertext record construction.
- `dnsdle/constants.py`: add/adjust crypto derivation label constants for encryption stream derivation; keep overhead/profile invariants coherent.
- `dnsdle/server.py`: enforce strict request-envelope validation before follow-up/slice routing and add stable miss reason codes.
- `dnsdle/dnswire.py`: expose/normalize envelope fields or helpers needed by server-side query-envelope validation.
- `doc/architecture/CRYPTO.md`: specify concrete v1 encryption + MAC construction and deterministic inputs.
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`: update binary record semantics to ciphertext payload bytes and MAC binding text.
- `doc/architecture/DNS_MESSAGE_FORMAT.md`: codify required query-envelope invariants and miss handling for parseable envelope violations.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: update request-validation order and reason taxonomy to include envelope checks.
- `doc/architecture/SERVER_RUNTIME.md`: align request handling pipeline to include strict envelope validation before domain/mapping classification.
- `doc/architecture/ARCHITECTURE.md`: align high-level transport/security wording with encrypted-slice serving reality.
