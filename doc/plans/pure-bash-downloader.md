# Plan: Generate a Pure Bash Downloader

## Summary

Add a generated, per-payload Bash downloader that retrieves and verifies the
payload directly, without invoking Python or PowerShell on the download host.
Keep the existing universal Python client and Python one-line stagers, but
replace the startup return/log/display plumbing with a language-tagged artifact
contract so both output types are reported consistently and another language
can be added without another orchestration redesign. The current repository
does not contain a PowerShell generator, template, call site, or architecture
contract, so this plan does not claim to preserve or modify PowerShell support.

## Problem

The current startup path emits one universal Python client and one Python 3
stager per payload. A target host must therefore have Python even when the
operator wants to use Bash, and the current `stagers`-only return shape cannot
represent a direct downloader cleanly.

The existing publish stream is an RFC 1950 zlib stream. Bash has no builtin DNS,
HMAC-SHA256, binary-safe string, or zlib APIs, and common `gzip` implementations
cannot decode that stream. A useful generated Bash script therefore needs a
precise external-command contract and a compression format that a normal Bash
host can decode without falling back to Python. In this plan, "pure Bash"
means that the generated program and its control flow are Bash and never invoke
another programming-language runtime; the explicitly validated system commands
`dig`, `openssl`, `base32`, `od`, `dd`, `xxd`, `gzip`, `sha256sum`, `mktemp`,
`cat`, `rm`, `sleep`, `wc`, and `mv` remain runtime prerequisites.

The current `stager_ready` structured event also includes the complete Python
one-liner. That encoded program contains embedded transfer configuration,
including the PSK, so the generic artifact logging change must stop logging
generated command/source content and retain only non-secret metadata. The
cleaner invariant is to remove the PSK from generated payload artifacts
entirely: both the Python stager and Bash downloader must require `--psk` at
runtime.

## Goal

After implementation, every configured payload has:

- its existing Python one-line stager;
- one standalone ASCII Bash downloader under the managed output directory;
- deterministic, collision-safe artifact metadata identifying language, kind,
  source payload, and output path;
- equivalent mapping, DNS payload, authentication, decryption, reconstruction,
  final-hash, bounded-retry, and atomic-output invariants across the Python and
  Bash download paths.

The Bash downloader must run without Python or PowerShell, require a non-empty
runtime `--psk`, fail before its first DNS request when a required command or
embedded invariant is unavailable, remain silent unless `--verbose` is
supplied, and never write unverified plaintext. Server-side generation must
continue to work under Python 2.7 and 3.x on Windows and Linux using only the
Python standard library.

PowerShell generation is not part of the current codebase or this change. The
new language-tagged artifact boundary is the integration point for a separate
PowerShell implementation if three-language generation is required.

## Design

### 1. Make the compressed publish representation shell-decodable

Change the canonical publish representation from an RFC 1950 zlib stream to a
deterministic RFC 1952 gzip stream. Build the gzip envelope explicitly around
raw DEFLATE output with a fixed header plus CRC32/ISIZE trailer so timestamps,
host OS, filenames, and Python-version-specific gzip defaults cannot enter the
publish identity. Continue to bind `publish_version`, mapping, crypto, slicing,
and `compressed_size` to the exact emitted bytes.

This is a coordinated clean break: the universal Python client and Python
stager must decode gzip only, with no legacy zlib fallback. Existing published
identities change automatically because `publish_version` hashes the compressed
stream. Keep the wire record and crypto profile unchanged because their fields
and algorithms do not change. Keep the Python one-liner's outer
base64/zlib-packed stager representation unchanged; only bytes entering the
file publish pipeline move to gzip.

### 2. Render a direct per-payload Bash program

Add one generator/template module that accepts validated config plus the final
mapped payload item. Render only ASCII source and embed:

- `file_id`, `publish_version`, `file_tag`, `total_slices`, compressed size,
  plaintext SHA-256, response label, DNS payload-label cap, EDNS size, ordered
  domains, and the final collision-resolved `slice_tokens` from
  `apply_mapping()`;
- crypto/profile labels and bounded retry/timing defaults from canonical Python
  constants rather than independently chosen values;
- arrays through a generator-owned shell-quoting function that accepts every
  validated value or fails generation.

Never pass `config.psk` to either payload-artifact renderer. Remove the
`@@PSK@@` placeholder and embedded fallback from the Python stager template as
the same coordinated clean break. Both generated payload artifacts must reject
a missing or empty runtime `--psk` with usage exit code `2` before resolver
selection, DNS, key derivation, or output work.

Embedding final tokens is intentional: it makes the Bash artifact consume the
actual globally promoted mapping instead of reimplementing mapping HMAC and
base32 truncation in shell. The renderer must reject an empty token list,
metadata/token cardinality mismatches, unreplaced placeholders, non-ASCII
output, duplicate output paths, and values outside the documented shell
contract before writing anything.

Use file-ID-only ASCII names, `dnsdle_<file_id>.bash.sh` and
`dnsdle_<file_id>.python.1-liner.txt`. This avoids basename collisions and
removes platform-dependent filename sanitization entirely. Render and validate
the complete payload-artifact set, including global path uniqueness, before
opening any output file. Write each artifact through a PID-qualified temporary
file in the managed directory and rename only after complete output. Do not
depend on executable-bit support on a Windows generation host. Documentation
and console output should instruct the operator to invoke a Bash artifact as
`bash <path> --psk <secret>` without constructing or storing that shell command
string in artifact metadata.

### 3. Implement the Bash runtime as a binary-safe file pipeline

The generated script must require Bash 4.0 or newer, use `set -u` and explicit
checked status handling, and avoid `set -e` behavior that can accidentally
convert classified retry paths into process exits. Set `umask 077`, allocate one
private temporary directory, and install its cleanup trap before deriving keys.
Before networking, validate Bash version, required commands, embedded array
cardinalities, numeric bounds, runtime PSK presence, output directory, and the
optional `--resolver` syntax.
Run fixed local capability vectors for the exact `openssl` HMAC-with-hex-key,
base32 decode, and gzip decode forms used later, so an incompatible command
version fails before DNS or output work rather than during a transfer.

Support the common operator-facing flags `--psk`, `--resolver`, `--out`, and
`--verbose`. Require `--psk`; default only the resolver to the system resolver
used by `dig` and output to `${TMPDIR:-/tmp}/dnsdle_<file_id>`. Preserve binary
stdout behavior for `--out -`; otherwise stage plaintext beside the requested
destination and use `mv` only after all verification succeeds. Install a trap
that removes all temporary files on every exit path.

For each embedded `(slice_index, slice_token)` pair:

1. rotate deterministically through configured domains on retryable DNS
   failures and invoke `dig` for the protocol's required A query, with UDP-only
   transport, the configured EDNS buffer size, a bounded timeout, and an
   optional explicit resolver;
2. require one matching CNAME result, normalize only the presentation trailing
   dot/case, validate its response-label/domain suffix, validate every payload
   label against the embedded `dns_max_label_len`, and join the base32 text;
3. uppercase the canonical unpadded payload text, reject impossible base32
   length residues, and restore the exact RFC 4648 `=` padding needed to reach a
   multiple of eight characters before invoking `base32 -d`; decode into a file
   and use `od`/`dd` to enforce profile, flags, big-endian ciphertext length,
   record size, and eight-byte MAC invariants;
4. use `openssl` HMAC-SHA256 over exact byte files to derive the file keys,
   authenticate the metadata/ciphertext message, and generate deterministic
   keystream blocks; compare the received and expected MAC hex with a
   fixed-length XOR accumulator rather than an early-exit string comparison;
5. XOR hex text in bounded Bash arithmetic and convert it back to a binary
   slice file with `xxd`, never placing ciphertext, plaintext, or NUL-bearing
   data in a shell variable;
6. append verified slices strictly by index, verify compressed length, decode
   with `gzip`, and compare `sha256sum` output to the embedded plaintext hash.

DNS command failures, empty/missing CNAME output, and timeouts are retryable and
consume the same bounded round/no-progress budgets as the Python client.
Malformed payload text/records, MAC mismatches, decompression failures, and hash
mismatches are terminal and use the existing exit-code classes `3` through `7`.
Do not add compatibility fallbacks or accept multiple CNAME candidates.

### 4. Introduce one generated-artifact contract

Add a small orchestration module that invokes the existing Python stager
generator and the new Bash generator, then returns one deterministic sequence
of dictionaries. Every dictionary must have exactly these non-secret common
fields: `language`, `kind`, `source_filename`, and `path`. Use
`language=python, kind=stager` for current one-liners and
`language=bash, kind=downloader` for the new scripts. Generated source and
invocation strings remain inside their language-specific renderer/writer and
are not returned by the common orchestration boundary.

Artifact order is payload input order, with the Python stager immediately
followed by the Bash downloader for each payload. The orchestrator must assert
that it produced exactly two artifacts per payload and that every common field
is present before returning the immutable sequence.

Update the Python stager generator to produce that schema directly. Update all
call sites in the same change: `build_startup_state()`, the process entry point,
and human console rendering. Emit a single `download_artifact_ready` structured
event per artifact with language, kind, source basename, and path only. Never
log an invocation string, rendered source, PSK material, or other embedded
secrets.
Keep `generation_result["artifact_count"] == 1` scoped to the universal Python
client; report the separately generated payload-artifact count explicitly so
the two cardinalities cannot be confused.

### 5. Update architecture and user documentation as one contract change

Document gzip as the one accepted compression representation, the Bash runtime
dependency/preflight contract, embedded-token mapping behavior, `dig` as the
Bash DNS presentation boundary, the generic artifact schema/cardinality, Bash
exit/error behavior, and secret-free artifact logging. Keep the Python runtime
documents explicit about the universal Python client rather than silently
generalizing Python-specific behavior to Bash.

Document the actual feature matrix: Python generation exists, Bash is added by
this plan, and PowerShell is absent. Do not advertise PowerShell until a real
generator and runtime contract are implemented.

## Implementation Phases

1. Replace zlib publish output with deterministic gzip bytes and update both
   current Python decompression call sites in the same commit-sized change.
2. Add the Bash renderer, invariant checks, transactional writer, direct
   runtime, and deterministic per-file filenames.
3. Add the common artifact orchestrator, migrate the Python stager dictionaries
   and all startup/log/console call sites, and remove generated command content
   from logs.
4. Update every affected architecture and user document to describe the final
   compression, generation, runtime, security, and support contracts.
5. Validate generation under Python 2.7 and 3.x and run generated artifacts
   against a live local server without creating or modifying files under
   `tests/`.

## Validation

- Generate artifacts for two payloads with the same basename but different
  contents and confirm file-ID-only unique Python/Bash paths plus stable
  payload-order/Python-then-Bash ordering across repeated launches.
- Decode the Python one-liner and inspect both rendered sources to confirm the
  configured PSK is absent. Confirm missing/empty runtime `--psk` exits with
  code `2` before resolver, DNS, key, temporary-output, or final-output work.
- Confirm every generated Bash file is ASCII, contains no placeholders, passes
  `bash -n`, and produces its dependency/config failure before issuing DNS or
  creating the final output.
- Download empty, text, NUL-containing binary, and multi-slice payloads through
  the Python client/stager and Bash script; compare exact SHA-256 values and
  exercise both file output and `--out -`.
- Exercise system and explicit resolvers, multiple-domain rotation, verbose and
  silent operation, bounded timeout/no-progress termination, wrong PSK,
  malformed CNAME payload, MAC failure, gzip failure, final hash failure, and
  unwritable output. Confirm no failed path leaves final or temporary data.
- Inspect structured logs to confirm artifact language/kind/path are present and
  no launch command, generated source, encoded PSK, raw PSK, or key material is
  emitted.
- Generate from Windows and Linux server hosts to confirm output writing and
  path reporting are host-safe; execute the Bash artifact on its documented
  Linux/Bash runtime only.

## Success Criteria

- Startup emits exactly one Bash downloader and one Python stager for every
  configured payload, plus the existing single universal Python client.
- A generated Bash downloader retrieves, authenticates, decrypts,
  gzip-decompresses, hashes, and atomically writes the selected payload without
  invoking Python or PowerShell.
- Python and Bash accept the same canonical publish bytes and enforce the same
  mapping, crypto, reconstruction, retry-bound, and final-output invariants.
- Generation remains Python 2.7/3.x compatible, standard-library-only, ASCII in
  code/scripts, and valid on Windows and Linux server hosts.
- Logs and console output identify every generated artifact without exposing
  embedded command/source content or secret material.
- Neither generated payload artifact contains the configured PSK; both require
  it explicitly at runtime and never log it.
- Documentation describes only implemented language support and does not claim
  PowerShell generation exists.

## Affected Components

- `dnsdle/publish.py`: emit deterministic RFC 1952 gzip publish bytes instead
  of RFC 1950 zlib bytes while preserving publish identity and slicing
  invariants.
- `dnsdle/client_runtime.py`: decode the new gzip-only representation in the
  universal Python downloader without a legacy fallback.
- `dnsdle/stager_template.py`: decode the gzip-published universal client before
  executing it, remove the embedded PSK placeholder/fallback, and require
  runtime `--psk`.
- `dnsdle/bash_downloader.py`: new ASCII Bash template, safe renderer,
  invariant validation, file-ID-only naming, no embedded PSK, and transactional
  artifact writer.
- `dnsdle/downloader_generator.py`: new common orchestrator and exact
  language-tagged generated-artifact contract.
- `dnsdle/stager_generator.py`: migrate Python stager output to the common
  artifact schema, stop substituting the PSK, replace basename naming with
  file-ID-only naming, and retain atomic ASCII one-liner emission.
- `dnsdle/__init__.py`: generate Bash and Python payload artifacts after mapping
  convergence and return the common artifact collection.
- `dnsdle.py`: consume the new startup result, report separate artifact
  cardinalities, and emit secret-free `download_artifact_ready` events.
- `dnsdle/console.py`: display generated artifacts by language and kind through
  the common schema.
- `README.md`: advertise the implemented Python and Bash output choices without
  claiming absent PowerShell support.
- `doc/FAQ.md`: explain when to choose Bash, its runtime command prerequisites,
  and the difference between the direct Bash downloader and Python stager.
- `doc/architecture/ARCHITECTURE.md`: add the multi-artifact generation layer,
  Bash runtime boundary, and updated startup/data flows.
- `doc/architecture/CONFIG.md`: define generated artifact cardinality, managed
  output policy, Bash embedded defaults, and lack of a new server CLI flag.
- `doc/architecture/PUBLISH_PIPELINE.md`: replace the zlib compression contract
  with deterministic gzip envelope construction and its identity consequences.
- `doc/architecture/CLIENT_GENERATION.md`: scope the existing document to the
  universal Python client and describe its relationship to direct Bash
  artifacts and the common output schema.
- `doc/architecture/CLIENT_RUNTIME.md`: update Python reconstruction to the
  gzip-only representation and keep Python-specific CLI/DNS behavior explicit.
- `doc/architecture/STAGER.md`: update gzip bootstrap decoding, the migrated
  artifact schema, and its coexistence with direct Bash downloaders.
- `doc/architecture/BASH_DOWNLOADER.md`: new authoritative Bash generation,
  dependency, rendering, runtime, DNS, crypto, retry, output, and failure
  contract.
- `doc/architecture/SERVER_RUNTIME.md`: add Bash generation to the post-mapping
  startup phase and listener-start invariant.
- `doc/architecture/QUERY_MAPPING.md`: distinguish runtime-derived Python tokens
  from the Bash downloader's embedded final reverse mapping.
- `doc/architecture/DNS_MESSAGE_FORMAT.md`: define which DNS invariants the
  Bash runtime delegates to `dig` and which presentation-level invariants it
  must validate itself.
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`: require both generated decoders to
  enforce the same record/label invariants and be updated together.
- `doc/architecture/CRYPTO.md`: add Bash HMAC/keystream/file-pipeline parity and
  remove Python-only wording from generated-client acceptance requirements.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: add Bash generator/runtime failure
  classes, common artifact invariants, and gzip-only reconstruction failures.
- `doc/architecture/LOGGING.md`: define `download_artifact_ready`, separate
  client/artifact counts, and prohibit logging rendered commands or sources.
