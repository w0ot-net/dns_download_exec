# Client Runtime

This document defines v1 runtime behavior for the universal download client.

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
2. runtime initialization (parameter derivation)
3. download loop
4. reconstruction and verification
5. output write and exit

Any fatal error in a phase exits immediately with the defined exit code.

---

## CLI Contract

The universal client accepts all parameters via CLI arguments.

Required arguments:
- `--psk secret`
- `--domains base1,base2,...`
- `--mapping-seed seed`
- `--publish-version version`
- `--total-slices n`
- `--compressed-size n`
- `--sha256 hex`
- `--token-len n`

Optional arguments:
- `--resolver host:port`
- `--out path`
- `--file-tag-len n` (default: `4`)
- `--response-label label` (default: `r-x`)
- `--dns-max-label-len n` (default: `40`)
- `--dns-edns-size n` (default: `512`)
- `--timeout seconds` (default: `3.0`)
- `--no-progress-timeout seconds` (default: `60`)
- `--max-rounds n` (default: `64`)
- `--query-interval ms` (default: `50`; `0` disables)
- `--verbose` (enable progress and diagnostic logging to stderr)

Validation rules:
- `--psk` must be present and non-empty.
- `--domains` must contain at least one valid domain.
- numeric arguments must parse and be positive (except `--query-interval`
  which allows `0`).

CLI validation failures exit with code `2`.

---

## Runtime Initialization

Before issuing DNS requests, client must:
1. Parse CLI arguments.
2. Derive `file_id` from `--publish-version`:
   `sha256("dnsdle:file-id:v1|" + publish_version).hexdigest()[:16]`
3. Derive `file_tag` from `--mapping-seed`, `--publish-version`, and
   `--file-tag-len`.
4. Validate derived identifiers and domain labels.
5. Derive per-file crypto context from `--psk`.
6. Initialize missing-index set `[0 .. total_slices-1]`.
7. Initialize slice storage, progress timer, and retry counters.

Any invariant or crypto context setup failure is fatal.

---

## Resolver Behavior

Resolver selection order:
1. use `--resolver` if provided
2. otherwise discover system resolver at runtime:
   - Windows (`sys.platform == "win32"`): `nslookup`-based discovery
   - Unix/Linux: `/etc/resolv.conf` parsing

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
2. derive `slice_token` from `(mapping_seed, publish_version, index, token_len)`
3. select domain suffix from `domains` using deterministic index policy
4. build query name `<slice_token>.<file_tag>.<selected_base_domain>`
5. send DNS query (include OPT when `dns_edns_size > 512`)
6. wait for response subject to request timeout
7. on timeout/no-response/TC/DNS envelope error, update retry state and continue
8. extract and validate CNAME payload labels
9. parse binary slice record
10. verify crypto and decrypt slice
11. store slice if new index; verify equality if duplicate index
12. when a new index is stored, reset no-progress timer
13. sleep `query_interval` ms before next iteration

Loop termination failures:
- max-rounds exhausted
- retry budget exhausted
- no-progress timeout reached

These failures exit with code `3`.

Domain-selection policy:
- initialize `domain_index = 0` at process start
- each request uses `domains[domain_index]`
- on retryable transport events only, advance:
  `domain_index = (domain_index + 1) % len(domains)`
- on valid DNS responses, keep `domain_index` unchanged

Query pacing:
- after each successful query-response cycle, sleep `query_interval` ms
- default interval is `50` ms (~20 queries/sec)
- set to `0` to disable pacing
- retryable errors use their own sleep and do not add the pacing delay

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

Validation is split into two stages with different retry behavior.

**Stage 1 -- DNS envelope (retryable on failure):**
1. validate response ID, QR flag, opcode, rcode
2. validate question echo and section structure
3. locate required IN CNAME answer matching query name

DNS envelope errors (including non-NOERROR rcode) are caught alongside
transport errors and retried up to `MAX_CONSECUTIVE_TIMEOUTS` (`128`).
This is intentional: a bad response from a recursive resolver should not
be fatal.

**Stage 2 -- payload and crypto (fatal on failure):**
4. extract and validate CNAME payload labels and suffix
5. parse binary record fields and invariants
6. verify MAC using derived crypto context and mapped metadata
7. decrypt ciphertext payload
8. validate duplicate-slice consistency if index already stored

Classification:
- transport timeout/no-response: retryable
- TC (truncated) response: retryable
- DNS envelope parse error (stage 1): retryable
- payload parse/format mismatch (stage 2): fatal (exit `4`)
- crypto mismatch (stage 2): fatal (exit `5`)

---

## Reconstruction and Verification

When no indices remain missing:
1. reassemble compressed bytes in ascending index order
2. verify compressed length equals `compressed_size`
3. decompress bytes
4. compute plaintext SHA-256
5. compare against `sha256`

Failures in this phase exit with code `6`.

---

## Output Write Behavior

Write policy:
- write final plaintext only after all verification steps pass
- if `--out` provided, write exactly to that path
- if `--out` omitted, write to `<tempdir>/dnsdle_<file_id>`

Output failures exit with code `7`.

Client must not execute downloaded bytes in v1.

---

## Logging

Client is silent by default. Pass `--verbose` to enable diagnostic output on
stderr.

When verbose, minimum runtime logs:
- startup metadata summary
- per-round progress (`received`, `missing`)
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

1. `mapping_seed` is non-empty and `token_len` is positive within
   `dns_max_label_len`. Each slice token is derived at runtime.
2. Each index derives exactly one query token via deterministic HMAC.
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
