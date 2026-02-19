# Errors and Invariants

This document defines v1 failure behavior and invariant enforcement for:
- server startup and request handling
- generated client download runtime
- generator output contract

The policy is fail-fast on contract violations and deterministic on known miss
paths.

---

## Error Classes

### Startup Errors (Server)

Startup errors are fatal and must prevent listener startup.

Examples:
- invalid config values or bounds
- unreadable input files
- duplicate `plaintext_sha256` across configured input files
- invalid deterministic mapping derivation
- `(file_tag, slice_token)` lookup-key collision across published files
- unsupported profile values
- slice budget computation failure

### Request Misses (Server)

A request miss is a valid DNS request that does not map to a served slice.
Misses are not process-fatal.

Examples:
- unknown/unconfigured base domain
- unknown `file_tag`
- unknown `slice_token` under a known `file_tag`
- invalid parseable query envelope (`QR=1`, non-query opcode, invalid section
  counts)
- unsupported qname shape for mapping
- unsupported qtype/class for v1 flow

### Runtime Faults (Server)

Runtime faults are internal failures during request handling.

Examples:
- missing manifest entry after successful lookup
- encoding failure for a supposedly valid slice record
- invariant mismatch in publish state

### Retryable Transport Events (Client)

Retryable events consume retry budget and do not immediately abort.

Examples:
- DNS timeout/no response
- UDP receive timeout
- socket I/O interruption where retry is possible

### Non-Retryable Contract Violations (Client)

Contract violations terminate the run immediately.

Examples:
- DNS message parse failure after receipt
- unexpected payload shape for required CNAME answer
- CNAME binary record invariant failure
- MAC/decrypt mismatch
- duplicate-slice mismatch
- final reconstruction/hash mismatch

---

## Server Response Matrix

For parseable DNS requests in v1:

1. **Valid mapped slice request**
- Response: `RCODE=NOERROR`
- Answer section: exactly one IN CNAME answer with deterministic payload
- TTL: configured `ttl`

2. **CNAME-chase follow-up request**
- Detection: qname matches
  `<payload_labels>.<response_label>.<selected_base_domain>` with qtype `A`,
  where selected base domain is configured
- Response: `RCODE=NOERROR`
- Answer section: exactly one IN `A` answer
- TTL: configured `ttl`

3. **Deterministic miss**
- Response: `RCODE=NXDOMAIN`
- Answer section: empty
- Behavior must be deterministic for identical request name and current publish
  state.

4. **Internal runtime fault**
- Response: `RCODE=SERVFAIL`
- Answer section: empty
- Log as internal error with reason code.

For unparseable/garbled datagrams (cannot safely parse request envelope), the
server may drop silently.

No fallback remap is allowed for any miss path.

---

## Server Validation Order

For each incoming request:
1. Parse DNS message envelope.
2. Validate parseable query-envelope invariants (`QR=0`, query opcode,
   section-count policy).
3. Classify follow-up shape
   (`<payload_labels>.<response_label>.<selected_base_domain>`, qtype `A`) for
   configured domains before slice-mapping evaluation.
4. Validate qname/class/qtype shape for v1 slice flow.
5. Validate suffix and mapping fields (`slice_token`, `file_tag`).
6. Resolve mapping to canonical slice identity.
7. Build deterministic slice record.
8. Encode and return deterministic CNAME answer.

Any failure before step 6 is a deterministic miss unless the request is not
parseable.
Any failure at or after step 6 caused by internal inconsistency is a runtime
fault (`SERVFAIL`).

---

## Client Failure Semantics

Generated client failure classes map to exit codes from
`doc/architecture/CLIENT_GENERATION.md`:

- `2` usage/CLI error:
  - invalid runtime flag value
  - invalid output path argument
- `3` DNS/transport exhaustion:
  - retries/timeouts exhausted without full slice set
  - no new slice acquired for longer than no-progress timeout (default `60`
    seconds)
- `4` parse/format violation:
  - DNS response cannot be parsed as expected
  - CNAME record shape/fields violate v1 format contract
- `5` crypto verification failure:
  - MAC mismatch
  - decrypt/auth context mismatch
- `6` reconstruction/hash/decompress failure:
  - compressed size mismatch
  - decompression failure
  - plaintext hash mismatch
- `7` output write failure:
  - final file cannot be persisted

Retry policy:
- only transport-level misses/timeouts are retryable
- no-progress timeout is terminal
- format/crypto/invariant violations are non-retryable fatal

Parity-core boundary:
- `dnsdle/client_payload.py` is the authority for parse/format (`4`) and
  crypto verification (`5`) failures.
- `dnsdle/client_reassembly.py` is the authority for reconstruction/decompress
  and final hash failures (`6`).

---

## Invariants

### Config and Startup

1. Config is immutable after successful startup validation.
2. All required config fields are present and within documented bounds.
3. All input files exist and are readable before listener startup.
4. Deterministic mapping parameters (`mapping_seed`, tag/token lengths) are
   valid and sufficient for all published slices.
5. `plaintext_sha256` values are unique across configured files in one launch.

### Mapping and Routing

1. Same `(mapping_seed, publish_version, slice_index)` always yields the same
   `slice_token` when mapping materialization constraints are unchanged.
2. Same `(mapping_seed, publish_version)` always yields the same `file_tag`
   when
   mapping materialization constraints are unchanged.
3. Mapping keys resolve to exactly one canonical slice identity.
4. Base domain is a route qualifier and is not part of mapping identity.
5. No silent fallback to other file/version/index is allowed.

### Slice Serving

1. Same mapped slice identity always yields the same CNAME payload text within
   a running process.
2. With unchanged mapping, crypto, and wire inputs (`mapping_seed`,
   `publish_version`, `compression_level`, `psk`, configured domain set,
   `response_label`, `dns_max_label_len`, profile ids, `ttl`, and
   implementation profile from `doc/architecture/PUBLISH_PIPELINE.md`), payload
   identity is stable across restarts.
3. Server never emits multiple slice answers for one mapped request in v1.

### Client Assembly

1. `TOTAL_SLICES` defines exact required index set `[0, TOTAL_SLICES-1]`.
2. Duplicate index bytes must be identical.
3. No-progress timer resets only when a new slice index is successfully stored.
4. Client fails when no-progress timeout is reached.
5. Final compressed length must equal embedded `COMPRESSED_SIZE`.
6. Final plaintext hash must equal embedded `PLAINTEXT_SHA256_HEX`.
7. Client never executes downloaded bytes in v1.

### Generator Contract

1. Exactly one standalone `.py` artifact per `(file, target_os)`.
2. No sidecar artifacts for runtime dependencies.
3. Embedded constants must be internally consistent.

Any invariant breach is fatal for the current operation context.

---

## Logging Requirements

Minimum server log fields on request handling paths:
- timestamp (`ts_unix_ms`)
- level (`level`)
- category (`category`)
- phase (`server` for runtime request paths)
- classification (`served`, `followup`, `miss`, `runtime_fault`)
- stable reason code
- request key context when available (`file_tag`, `slice_token`)

Shutdown logging:
- classification `shutdown`
- phase `server`
- deterministic stop reason code and counters
- level `INFO`
- category `server`

Minimum generated-client log fields on failure:
- phase (`dns`, `parse`, `crypto`, `reassembly`, `write`)
- classification (`retryable`, `fatal`)
- exit code

Logs must avoid leaking source file paths in network-facing contexts.
Logs must never include raw PSK/key material or raw payload bytes.
`ERROR` and lifecycle events (`server_start`, `shutdown`) must not be
suppressed by category filters, sampling, or rate limits.

Detailed logging schema and suppression rules are defined in
`doc/architecture/LOGGING.md`.

---

## Compatibility Policy

Changing any item below is a breaking contract change:
- miss/error response matrix semantics
- retryable vs non-retryable classification
- invariant set that affects acceptance/rejection behavior
- exit code meaning

Breaking changes require synchronized updates to:
- server runtime logic
- generated client template
- architecture docs referencing these contracts
