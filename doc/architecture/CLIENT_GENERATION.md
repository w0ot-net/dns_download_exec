# Client Generation

This document defines how the server generates the universal download client.

v1 generation produces a single universal client that takes all file-specific
parameters via CLI arguments.  The server publishes one client for all
platforms instead of per-file, per-OS templated scripts.

---

## Goals

1. Generate minimal Python client code (2.7/3.x, standard library only).
2. Accept all file-specific metadata via CLI arguments at runtime.
3. Keep download/verify behavior deterministic and fail-fast.
4. Keep generated client independent from server source tree at runtime.
5. Emit exactly one standalone Python file for all published files.

---

## Architecture

The universal client (`dnsdle/client_standalone.py`) is assembled at server
startup from canonical source modules using an extraction system:

1. Canonical modules (`compat.py`, `helpers.py`, `dnswire.py`,
   `cname_payload.py`, `client_runtime.py`) have
   `# __EXTRACT: name__` / `# __END_EXTRACT__` markers around shared
   functions and client-specific logic.
2. `dnsdle/extract.py` parses markers and returns extracted source blocks.
   Canonical function names are used directly in the generated client
   (no rename step).
3. Client-specific logic (CLI parsing, download loop, reassembly, output,
   resolver discovery) is authored as real Python in `client_runtime.py`
   and assembled into the standalone client via marker extraction -- not
   via string literal concatenation.
4. `build_client_source()` assembles the full standalone script by combining
   a static preamble header, a constants section generated programmatically
   from `dnsdle.constants` (via `_PREAMBLE_CONSTANTS`), a static preamble
   footer, and extracted utilities and client-specific code.
5. A thin `DnsParseError(ClientError)` subclass in the client preamble adapts
   the single-arg `DnsParseError` constructor used by extracted `_decode_name`
   to `ClientError`'s `(code, phase, message)` signature.

Extracted blocks:
- **compat.py** (10 functions): `encode_ascii`, `encode_utf8`,
  `decode_ascii`, `base32_lower_no_pad`, `base32_decode_no_pad`,
  `byte_value`, `iter_byte_values`, `constant_time_equals`,
  `encode_ascii_int`, `is_binary`
- **helpers.py** (2 functions): `hmac_sha256`, `dns_name_wire_length`
- **dnswire.py** (1 function): `_decode_name`
- **cname_payload.py** (3 functions): `_derive_file_bound_key`,
  `_keystream_bytes`, `_xor_bytes`
- **client_runtime.py** (1 block): all client-specific logic -- CLI
  parsing, download loop, DNS query/response handling, crypto helpers,
  reassembly, output, and resolver discovery

---

## Inputs

The universal client accepts all parameters via CLI:

**Required per-file (5 values):**
- `--publish-version` -- root identity; `file_id` and `file_tag` derive from it
- `--total-slices` -- needed to know when download is complete
- `--compressed-size` -- needed for MAC verification (part of MAC message)
- `--sha256` -- plaintext SHA-256 hex for final verification
- `--token-len` -- slice token truncation length (per-file)

**Required deployment-wide:**
- `--psk`
- `--domains` (comma-separated base domains)
- `--mapping-seed`

**Optional deployment-wide (with defaults):**
- `--resolver` (default: system resolver discovery)
- `--out` (default: deterministic temp path from derived `file_id`)
- `--file-tag-len` (default: `4`; deployment-wide, set by server config)
- `--response-label` (default: `r-x`)
- `--dns-max-label-len` (default: `63`)
- `--dns-edns-size` (default: `512`)
- `--timeout`, `--no-progress-timeout`, `--max-rounds`,
  `--query-interval` (current defaults)
- `--verbose`

**Derived at runtime (not passed):**
- `file_id` = `sha256("dnsdle:file-id:v1|" + publish_version).hexdigest()[:16]`
- `file_tag` = `base32_lower_no_pad(HMAC-SHA256(mapping_seed, "dnsdle:file:v1|" + publish_version))[:file_tag_len]`
- `source_filename` = `"dnsdle_" + file_id` (used for default output path)
- `TARGET_OS` -- detected via `sys.platform`
- `CRYPTO_PROFILE`, `WIRE_PROFILE` -- hardcoded to `"v1"` in client source

---

## Output Artifacts

A single universal client script is generated per server startup.

Cardinality invariant:
- `artifact_count = 1`
- The universal client is published through the normal pipeline as a single
  additional file.

Required properties:
- standalone executable with only stdlib imports
- ASCII-only source
- no dependency on repository-relative imports
- cross-platform (Linux and Windows resolver discovery at runtime)
- no sidecar files

Output naming:
- `dnsdle_universal_client.py`
- managed output boundary: `client_out_dir/dnsdle_v1/`

---

## Cross-Platform Resolver Discovery

Both resolver implementations live in the same file, branched at runtime:

```python
if sys.platform == "win32":
    # nslookup-based discovery
else:
    # /etc/resolv.conf parsing
```

This eliminates the per-OS template lifting mechanism.

---

## Generated Script Structure

The generated script contains these sections:
1. `CONSTANTS`: DNS wire constants, payload crypto labels, runtime derivation
   labels, exit codes, and default timeouts.
2. `EXTRACTED UTILITIES`: Byte helpers, DNS wire decoding, crypto primitives
   extracted from canonical modules.
3. `RUNTIME DERIVATION`: Functions to derive `file_id`, `file_tag`, and
   `slice_token` from CLI parameters.
4. `DNS ENCODE/DECODE`: Request name construction and CNAME payload parsing.
5. `CRYPTO HELPERS`: Key derivation, decrypt, and MAC verification.
6. `DOWNLOAD ENGINE`: Retry loop and slice store.
7. `REASSEMBLY`: Ordered reassembly, decompress, and final hash verify.
8. `OUTPUT WRITER`: Write reconstructed plaintext to requested output path.
9. `CLI ENTRYPOINT`: Parse CLI args and run.

All helper code is inlined in the generated file.

---

## Stager Integration

Stagers download the universal client and exec it, passing payload metadata
via `sys.argv`:

```python
sys.argv = [
    "c",
    "--psk", psk,
    "--domains", DOMAINS_STR,
    "--mapping-seed", MAPPING_SEED,
    "--publish-version", PAYLOAD_PUBLISH_VERSION,
    "--total-slices", str(PAYLOAD_TOTAL_SLICES),
    "--compressed-size", str(PAYLOAD_COMPRESSED_SIZE),
    "--sha256", PAYLOAD_SHA256,
    "--token-len", str(PAYLOAD_TOKEN_LEN),
    "--file-tag-len", str(FILE_TAG_LEN),
    "--response-label", RESPONSE_LABEL,
    "--dns-edns-size", str(DNS_EDNS_SIZE),
    "--resolver", resolver,
]
exec(client_source)
```

Each stager embeds:
- **Client download params**: universal client publish metadata (same for
  all stagers)
- **Payload params**: 5 per-file values passed to the client via `sys.argv`

---

## Download Algorithm

1. Validate CLI params and derive per-file keys before issuing requests.
2. Initialize missing set: all slice indices.
3. While missing set not empty:
   - derive `slice_token` from `(mapping_seed, publish_version, index)`
   - select domain suffix from `domains` by fixed deterministic policy
   - query `<slice_token>.<file_tag>.<selected_base_domain>`
   - parse and validate response format
   - verify MAC/decrypt with derived metadata
   - store bytes for index if first valid receipt
   - reset no-progress timer on new slice acquisition
   - for duplicate index: bytes must match stored bytes exactly
4. Exit failure when retry/round policy is exhausted.
5. Exit failure when no progress for `no_progress_timeout` seconds.

Domain-selection policy:
- initialize `domain_index = 0`
- use `domains[domain_index]` for each request
- advance on retryable transport events:
  `domain_index = (domain_index + 1) % len(domains)`
- keep index unchanged on valid responses

---

## Retry and Timeout Policy

Built-in defaults:
- per-request timeout: `3.0` seconds
- no-progress timeout: `60` seconds
- max rounds: `64`
- max consecutive timeouts: `128`
- retry sleep: `100` ms base + `150` ms jitter
- query interval: `50` ms

Rules:
- no infinite unbounded busy loop
- retries are allowed for transport misses/timeouts
- prolonged no-progress state is terminal
- cryptographic or contract violations are non-retryable fatal errors

---

## Reconstruction and Verification

After all slices are present:
1. Reassemble bytes by ascending slice index.
2. Validate reassembled compressed length equals `compressed_size`.
3. Decompress.
4. Compute plaintext SHA-256.
5. Compare to `sha256`.
6. Write plaintext bytes to output path only on success.

---

## Logging and Exit Codes

Client is silent by default. Pass `--verbose` to enable diagnostic output on
stderr.

Exit code classes:
- `0`: success
- `2`: usage/CLI error
- `3`: DNS/transport exhaustion
- `4`: parse/format violation
- `5`: crypto verification failure
- `6`: reconstruction/hash/decompress failure
- `7`: output write failure

---

## Generator Failure Conditions

Generation must fail before emitting client artifact when:
- assembled source fails compilation
- assembled source is not ASCII
- extraction markers are missing or malformed in canonical modules

Failure semantics:
- generation is startup-fatal with stable reason codes
  (`generator_invalid_contract`, `generator_write_failed`)
- transactional commit ensures no partial artifacts remain on failure

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

Any change to CLI parameter schema, wire parser contract, crypto profile,
or exit code semantics is a breaking client contract change.

Policy:
- update server generator and client source in one change
- update referenced architecture docs in the same change
- do not add compatibility shims for obsolete templates
