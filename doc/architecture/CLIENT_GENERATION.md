# Client Generation

This document defines how the server generates per-file download clients.

v1 generation is deterministic from validated config and file publish metadata.
Each generated client is single-purpose: download one specific published file
for one target OS profile.

---

## Goals

1. Generate minimal Python client code (2.7/3.x, standard library only).
2. Embed all required metadata so runtime negotiation is unnecessary.
3. Keep download/verify behavior deterministic and fail-fast.
4. Keep generated client independent from server source tree at runtime.
5. Emit exactly one standalone Python file per generated client.

---

## Inputs

For each published file, generator input is:
- `base_domains` (canonical ordered list)
- `mapping_seed`
- `file_tag`
- `file_id`
- `publish_version`
- `total_slices`
- `compressed_size`
- `plaintext_sha256`
- ordered `slice_tokens` array (`slice_index -> token`)
- crypto profile metadata required by `doc/architecture/CRYPTO.md`
- wire/profile metadata required by `doc/architecture/CNAME_PAYLOAD_FORMAT.md`
- runtime knobs (timeouts, retry pacing, max attempts policy)
- `target_os` (`windows` or `linux`)

These fields are produced by the startup publish pipeline contract in
`doc/architecture/PUBLISH_PIPELINE.md`.

Global input:
- resolver target configuration (direct resolver or system resolver behavior)
- output directory for generated clients
- runtime PSK supplied by client operator

---

## Output Artifacts

For each published file and `target_os`, generate exactly one Python script
artifact.

Required properties:
- standalone executable with only stdlib imports
- ASCII-only source
- no dependency on repository-relative imports
- embeds immutable metadata constants for that one file contract
- no sidecar files (no separate config, manifest, or module files)

Suggested output naming:
- `dnsdl_<file_id>_<file_tag>_<target_os>.py`
- managed output boundary: `client_out_dir/dnsdle_v1/`

Generator ownership rules:
- only files inside `client_out_dir/dnsdle_v1/` matching managed filename
  pattern are pruned as stale
- generator must not delete or rewrite files outside that managed subdirectory
- reruns replace managed target filenames atomically and deterministically

Filename is not a protocol identifier and may change without wire impact.

---

## Generated Script Structure

The generated script must contain these sections:
1. `CONFIG CONSTANTS`: embedded metadata and runtime knobs.
2. `DNS ENCODE/DECODE HELPERS`: request name construction and CNAME payload
   parsing for v1 format.
3. `CRYPTO HELPERS`: key derivation from runtime PSK, decrypt, and MAC
   verification for v1.
4. `DOWNLOAD ENGINE`: retry loop and slice store.
5. `REASSEMBLY`: ordered reassembly, decompress, and final hash verify.
6. `OUTPUT WRITER`: write reconstructed plaintext to requested output path, or
   a default temp path when `--out` is omitted.
7. `CLI ENTRYPOINT`: parse minimal args and run.

All helper code must be inlined in the generated file; external package or
multi-file layouts are not allowed in v1.

When repository runtime helpers are used as references, generated logic must
remain behavior-identical to:
- `dnsdle/client_payload.py` for response-envelope validation, payload parse,
  MAC verification, and decrypt.
- `dnsdle/client_reassembly.py` for duplicate handling, reassembly,
  decompression, and final hash checks.

---

## Embedded Constants Contract

The following constants are required in generated code:
- `BASE_DOMAINS` (ordered list)
- `FILE_TAG`
- `FILE_ID`
- `PUBLISH_VERSION`
- `TARGET_OS`
- `TOTAL_SLICES`
- `COMPRESSED_SIZE`
- `PLAINTEXT_SHA256_HEX`
- `SLICE_TOKENS` (ordered by index)
- `CRYPTO_PROFILE`
- `WIRE_PROFILE`
- `RESPONSE_LABEL`
- `DNS_MAX_LABEL_LEN`
- `DNS_EDNS_SIZE`
- retry/timeouts constants

Invariant:
- `len(SLICE_TOKENS) == TOTAL_SLICES`
- PSK must not be embedded as a generated constant in v1

Any mismatch in generated constants is a generation-time failure.

---

## Runtime CLI Contract

Generated client should expose a small, stable CLI:
- `--psk secret` (required shared secret for v1 crypto profile)
- `--resolver host:port` (optional override)
- `--out path` (optional output path)
- `--timeout seconds` (optional request timeout override)
- `--no-progress-timeout seconds` (optional override)
- `--max-rounds n` (optional retry rounds cap)

`--psk` is mandatory and must be non-empty.

If `--out` is omitted, write to a process temp directory with a deterministic
name derived from `(file_id, publish_version, plaintext_sha256)`.

No execution flags are allowed in v1 (for example, no `--exec` or equivalent).

---

## Download Algorithm

1. Validate `--psk` and derive per-file keys before issuing requests.
2. Initialize missing set: all slice indices.
3. While missing set not empty:
   - choose next index from missing set (strategy is implementation detail)
   - map `index -> slice_token`
   - select domain suffix from `BASE_DOMAINS` by fixed deterministic policy
   - query `<slice_token>.<file_tag>.<selected_base_domain>`
   - parse and validate response format
   - verify MAC/decrypt with embedded metadata
   - store bytes for index if first valid receipt
   - reset no-progress timer when a new slice index is acquired
   - for duplicate index: bytes must match stored bytes exactly
4. Exit failure when retry/round policy is exhausted.
5. Exit failure when no new slice is acquired for `no_progress_timeout_seconds`
   (default `60`).
6. Continue until every index has validated bytes.

Required semantics:
- out-of-order receive accepted
- duplicate responses accepted only if identical
- any parse/verification violation is fatal for that run
- domain-selection policy for v1:
  - initialize `domain_index = 0` at process start
  - use `BASE_DOMAINS[domain_index]` for each request
  - advance on retryable transport events only:
    `domain_index = (domain_index + 1) % len(BASE_DOMAINS)`
  - keep index unchanged on valid DNS responses
  - reset to `0` on process restart

---

## Retry and Timeout Policy

Generation emits explicit defaults for:
- per-request timeout
- no-progress timeout (`60` seconds by default)
- sleep/jitter between requests
- maximum consecutive failures
- maximum rounds over missing indices

Rules:
- no infinite unbounded busy loop
- retries are allowed for transport misses/timeouts
- prolonged no-progress state is terminal
- cryptographic or contract violations are non-retryable fatal errors

Exact defaults are defined in `doc/architecture/CONFIG.md`.

---

## DNS Contract in Generated Client

The generated client must implement exactly the current doc contracts:
- query name mapping: `doc/architecture/QUERY_MAPPING.md`
- CNAME payload parsing: `doc/architecture/CNAME_PAYLOAD_FORMAT.md`
- DNS message construction: include EDNS OPT using embedded `DNS_EDNS_SIZE`
  (default `1232`; OPT omitted only when configured `512`)

No alternate parse modes or fallback wire decoders are allowed in v1.

---

## Reconstruction and Verification

After all slices are present:
1. Reassemble bytes by ascending slice index.
2. Validate reassembled compressed length equals `COMPRESSED_SIZE`.
3. Decompress.
4. Compute plaintext SHA-256.
5. Compare to `PLAINTEXT_SHA256_HEX`.
6. Write plaintext bytes to output path only on success.

On any failure:
- do not emit partially reconstructed final file
- return non-zero exit code

---

## Logging and Exit Codes

Logging should be concise and machine-parseable enough for operator triage.

Minimum events:
- start metadata summary
- per-round progress (received/missing counts)
- fatal failure reason
- success path and output location

Exit code classes:
- `0`: success
- `2`: usage/CLI error
- missing or empty `--psk` is a usage/CLI error
- `3`: DNS/transport exhaustion (timeouts/retries exhausted or no-progress
  timeout reached)
- `4`: parse/format violation
- `5`: crypto verification failure
- `6`: reconstruction/hash/decompress failure
- `7`: output write failure

---

## Generator Failure Conditions

Generation must fail before emitting client artifact when:
- any required metadata field is missing
- `TOTAL_SLICES <= 0`
- token array length mismatch
- duplicate token in `SLICE_TOKENS`
- any token exceeds `DNS_MAX_LABEL_LEN`
- unsupported crypto/wire profile selected
- unsupported `TARGET_OS`
- generation path would require more than one output file for the client
- managed output commit/rollback steps fail at any point

Failure semantics:
- generation is startup-fatal with stable reason codes
  (`generator_invalid_contract`, `generator_write_failed`)
- generation uses run-level transactional commit; on failure, no newly generated
  artifact from that run remains in managed output.
- if rollback restoration itself fails, startup is fatal and backup directory
  material is preserved for operator recovery (never deleted on rollback-failure
  paths).

---

## Security Boundaries

Generated client secrecy properties:
- query names do not expose file path or slice index directly
- embedded metadata makes replay/tamper detection possible

Non-goals:
- hiding destination domain
- traffic shape obfuscation
- anti-analysis hardening of generated code

---

## Versioning and Breaking Changes

Any change to embedded constants schema, wire parser contract, crypto profile,
or exit code semantics is a breaking generator/client contract change.

Policy:
- update server generator and generated client template in one change
- update referenced architecture docs in the same change
- do not add compatibility shims for obsolete generated templates
