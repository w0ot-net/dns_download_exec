# Client Runtime

This document defines v1 runtime behavior for generated download clients.

It covers process lifecycle, DNS query loop behavior, progress tracking,
validation, reconstruction, and exit semantics.

---

## Goals

1. Keep client runtime deterministic and fail-fast.
2. Support out-of-order and retry-heavy slice retrieval.
3. Provide explicit bounded retry behavior.
4. Ensure output is written only after full verification.

---

## Runtime Lifecycle

Client runtime has five phases:
1. CLI parse and validation
2. runtime initialization
3. download loop
4. reconstruction and verification
5. output write and exit

Any fatal error in a phase exits immediately with the defined exit code.

---

## CLI Validation

Required runtime input:
- `--psk secret`

Optional runtime inputs:
- `--resolver host:port`
- `--out path`
- `--timeout seconds`
- `--no-progress-timeout seconds`
- `--max-rounds n`

Validation rules:
- `--psk` must be present and non-empty.
- if `--out` is provided, it must be a valid non-empty path argument.
- numeric overrides must parse and be positive.
- unsupported flags are usage errors.

CLI validation failures exit with code `2`.

---

## Runtime Initialization

Before issuing DNS requests, client must:
1. Load embedded constants (`FILE_TAG`, `SLICE_TOKENS`, etc.).
2. Validate constant invariants (`len(SLICE_TOKENS) == TOTAL_SLICES`).
3. Resolve effective runtime knobs from defaults plus CLI overrides.
4. Derive per-file crypto context from runtime `--psk`.
5. Initialize missing-index set `[0 .. TOTAL_SLICES-1]`.
6. Initialize slice storage map keyed by index.
7. Initialize progress timer and retry counters.

Any invariant or crypto context setup failure is fatal.

---

## Resolver Behavior

Resolver selection order:
1. use `--resolver` if provided
2. otherwise use embedded default resolver mode

Runtime resolver handling rules:
- resolver endpoint must parse to valid host/port
- client uses UDP DNS request/response semantics
- response source mismatch policy must be strict

Resolver handling failures:
- invalid `--resolver` syntax is usage error (exit `2`)
- runtime resolver communication failure is transport exhaustion (exit `3`)

---

## Download Loop

Loop condition:
- continue while any slice index remains missing

Per-iteration steps:
1. choose next missing index
2. map index to `slice_token`
3. select domain suffix from `BASE_DOMAINS` using deterministic index policy
4. build query name `<slice_token>.<file_tag>.<selected_base_domain>`
5. send DNS query (include OPT when `DNS_EDNS_SIZE > 512`, default `1232`)
6. wait for response subject to request timeout
7. on timeout/no-response, update retry state and continue
8. on response, validate expected CNAME answer contract
9. explicit DNS miss responses (for example, `NXDOMAIN` or no required CNAME
   answer) are contract violations
10. decode CNAME payload record
11. verify crypto and decrypt slice
12. store slice if new index; verify equality if duplicate index
13. when a new index is stored, reset no-progress timer

Loop termination failures:
- max-rounds exhausted
- retry budget exhausted
- no-progress timeout reached

These failures exit with code `3`.

Domain-selection policy:
- initialize `domain_index = 0` at process start
- each request uses `BASE_DOMAINS[domain_index]`
- on retryable transport events only, advance:
  `domain_index = (domain_index + 1) % len(BASE_DOMAINS)`
- on valid DNS responses (new slice or valid duplicate), keep `domain_index`
  unchanged
- on restart, reset `domain_index` to `0`

---

## Progress Tracking

Progress definition:
- progress occurs only when a previously missing index is successfully stored

No-progress timer:
- starts at loop entry
- resets only on progress event
- default threshold is `60` seconds
- threshold may be overridden by `--no-progress-timeout`

If no progress occurs within threshold, runtime exits with code `3`.

---

## Response and Payload Validation

For each received response:
1. validate response-question association
2. validate required answer type and suffix contract
3. parse binary record fields and invariants
4. verify MAC using derived crypto context and mapped metadata
5. decrypt ciphertext payload
6. validate duplicate-slice consistency if index already stored

Parity helper ownership:
- `dnsdle/client_payload.py` enforces response-envelope rules (`ID`, `QR/TC`,
  opcode, `RCODE`, question echo), selects exactly one matching IN CNAME
  answer, validates payload record invariants, verifies MAC, and decrypts
  ciphertext. Envelope validation is recursive-DNS-compatible: `AA`, `RA`,
  and exact section counts are not checked.
- `dnsdle/client_reassembly.py` enforces duplicate-index equality, complete
  index coverage, ordered reassembly, compressed-size checks, decompression, and
  final plaintext hash verification.

Classification:
- transport timeout/no-response: retryable
- DNS miss response or parse/format mismatch: fatal (exit `4`)
- crypto mismatch: fatal (exit `5`)

No alternate wire parsing mode is allowed in v1.

---

## Reconstruction and Verification

When no indices remain missing:
1. reassemble compressed bytes in ascending index order
2. verify compressed length equals embedded `COMPRESSED_SIZE`
3. decompress bytes
4. compute plaintext SHA-256
5. compare against embedded `PLAINTEXT_SHA256_HEX`

Failures in this phase exit with code `6`.

---

## Output Write Behavior

Write policy:
- write final plaintext only after all verification steps pass
- if `--out` provided, write exactly to that path
- if `--out` omitted, write to deterministic temp-path pattern

Invalid `--out` argument is a CLI validation failure (exit `2`).
Output failures exit with code `7`.

Client must not execute downloaded bytes in v1.

---

## Logging

Minimum runtime logs:
- startup metadata summary (redacted where needed)
- per-round progress (`received`, `missing`, retries)
- fatal failure reason and exit code
- success message with output location

Logs must not include:
- raw PSK
- derived keys

---

## Exit Codes

- `0`: success
- `2`: usage/CLI error
- `3`: DNS/transport exhaustion (timeouts/retries/no-progress timeout)
- `4`: parse/format violation
- `5`: crypto verification failure
- `6`: reconstruction/hash/decompress failure
- `7`: output write failure

---

## Runtime Invariants

1. `SLICE_TOKENS` cardinality matches `TOTAL_SLICES`.
2. Each index maps to exactly one query token.
3. Duplicate index data must be byte-identical.
4. Progress timer resets only on new-index acquisition.
5. Final file is written only after successful full verification.
6. Runtime never executes downloaded content in v1.

---

## Related Docs

- `doc/architecture/CLIENT_GENERATION.md`
- `doc/architecture/CONFIG.md`
- `doc/architecture/QUERY_MAPPING.md`
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`
- `doc/architecture/CRYPTO.md`
- `doc/architecture/ERRORS_AND_INVARIANTS.md`
