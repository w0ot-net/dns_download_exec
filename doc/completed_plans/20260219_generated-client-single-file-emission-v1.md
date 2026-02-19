# Plan: Generated Client Single-File Emission (v1)

## Summary
Implement the server-side generated-client emission phase so startup produces exactly one standalone Python file per `(published file, target_os)` pair. This phase focuses on deterministic artifact generation, embedded metadata contract correctness, run-level transactional output behavior, and startup fail-fast guarantees. It emits runnable one-file clients that satisfy the current architecture contract and use the parity crypto/parser behavior defined in the client-parity phase without runtime imports from the server package.

## Problem
The runtime server now publishes encrypted CNAME slices, but there is no implemented client artifact generator in code. Config fields (`target_os`, `client_out_dir`) exist but are not used to emit client files. Without this phase, operators cannot obtain generated one-file clients, and the architecture contract in `CLIENT_GENERATION.md` remains unimplemented.

## Goal
After implementation:
- Startup generates one standalone `.py` artifact per `(file_id, target_os)` in a generator-managed subdirectory under `client_out_dir`.
- Each artifact embeds required immutable constants (`BASE_DOMAINS`, `FILE_TAG`, `FILE_ID`, `PUBLISH_VERSION`, `TOTAL_SLICES`, `COMPRESSED_SIZE`, `PLAINTEXT_SHA256_HEX`, `SLICE_TOKENS`, profile IDs, wire knobs).
- Artifact naming is deterministic and collision-safe for the current publish set.
- Generation is transactional at run level: on failure, no newly generated artifact from that run remains.
- Rerun behavior is deterministic and explicit (managed-file overwrite + stale managed-file pruning policy).
- Startup logs include generation summaries with stable reason codes/events.

## Design
### Scope
In scope:
1. Server-side generation pipeline and file emission.
2. Embedded constants contract and template rendering for one-file scripts.
3. Startup integration and generation logging.
4. Deterministic naming/path behavior and fail-fast filesystem handling.

Out of scope:
- advanced scheduling/optimization of client runtime loop (for example adaptive jitter tuning)
- alternate transport/qtype support
- execution of downloaded files (still non-goal)

### 1. Introduce generation module and deterministic template rendering
Add a dedicated generator module (for example `dnsdle/generator.py`) that:
1. Accepts immutable `RuntimeState` + config generation fields.
2. Iterates publish items and selected target OS list deterministically.
3. Renders one Python source template per `(publish_item, target_os)`.
4. Writes files atomically into the generator-managed output directory under `client_out_dir`.

Deterministic iteration order:
- primary key: canonical publish item order from runtime state
- secondary key: canonical `target_os` order from config

Template contract:
- ASCII-only source
- stdlib-only imports
- one-file structure with sections required by `CLIENT_GENERATION.md`
- emitted script has no runtime dependency on `dnsdle.*` modules
- emitted script is minimally functional end-to-end for one file (query, parse, verify, decrypt, reassemble, hash-check, write), with conservative defaults

Phase dependency:
- this plan assumes the client parity core from `doc/plans/client-crypto-parser-parity-v1.md` is available and can be embedded/inlined into generated scripts without repository-relative runtime imports.

### 2. Artifact naming and path invariants
Define deterministic filename scheme:
- `dnsdl_<file_id>_<file_tag>_<target_os>.py`

Define managed output boundary:
- generator owns only `client_out_dir/dnsdle_v1/`
- stale-file pruning is allowed only inside this managed directory
- generator must not delete or rewrite files outside this managed directory

Fail-fast rules:
- reject path traversal or invalid output path composition
- ensure output dir exists (create if missing) or fail with explicit reason
- transactional generation:
  - render all artifacts into a per-run staging directory under the managed directory
  - if any artifact fails validation/write, delete staging directory and leave destination unchanged
  - commit uses explicit rollback-safe sequence:
    - compute expected managed path set for this run and stale managed path set
    - move all replace/remove targets into a per-run backup directory first
    - place staged outputs into final managed paths using atomic replace semantics
    - if any step fails, restore every moved file from backup, remove newly placed outputs, and fail startup
    - only on full success, remove backup and staging directories
- overwrite/idempotency policy (explicit):
  - for managed target paths in current run, replace destination atomically
  - stale managed files are defined as files inside managed directory matching `dnsdl_<file_id>_<file_tag>_<target_os>.py` not present in current expected set
  - stale managed-file pruning runs only as part of the rollback-safe commit sequence above

### 3. Embedded metadata schema enforcement
Before writing each artifact, validate generation invariants:
- `TOTAL_SLICES > 0`
- `len(SLICE_TOKENS) == TOTAL_SLICES`
- no duplicate tokens in `SLICE_TOKENS`
- `TARGET_OS` is supported
- required profile fields present (`CRYPTO_PROFILE`, `WIRE_PROFILE`)
- required domain/label constants are non-empty and normalized

Generation failure classification:
- startup-fatal with stable reason code (for example `generator_invalid_contract`, `generator_write_failed`)

### 4. Startup integration points
Integrate generator after publish/mapping state build and before serve loop starts:
1. Build startup state.
2. Generate client artifacts.
3. Emit generation summary logs (count, output dir, target_os set, file ids).
4. Only enter serve loop after successful generation.

This preserves fail-fast startup semantics: no listener bind if generation invariants fail.

### 5. Logging alignment
Add generation logging events through centralized logging runtime:
- `generation_start`
- per-artifact `generation_ok`
- `generation_error` with stable reason code/context
- `generation_summary`

Ensure sensitive values are not logged (no PSK or raw slice bytes).
Generation events use existing visible categories under default config:
- category `startup` for lifecycle events (`generation_start`, `generation_summary`, `generation_error`)
- category `publish` for per-artifact success details (`generation_ok`)
Logging severity contract:
- `generation_error` must emit at `ERROR` with `required=True` on fatal generation failure paths
- `generation_start` and `generation_summary` emit at `INFO`
No new logging category is introduced in this phase.

### 6. Architecture-doc synchronization
Update architecture docs to reflect actual generation behavior and startup placement:
- generation now implemented as startup phase before serve loop
- deterministic filename/embedded constants contract
- explicit failure semantics for generation phase

### 7. Validation approach
Validation for this phase:
1. run startup with 1 file, 2 target OS values and verify 2 generated scripts.
2. verify filenames and embedded constant blocks are deterministic across reruns with unchanged inputs.
3. verify rerun replaces managed files deterministically and prunes stale managed files not in current expected set.
4. verify generation fails fast on an induced invariant violation (for example token/count mismatch fixture) with no newly emitted files left behind.
5. verify startup does not enter serve loop when generation fails.
6. execute one generated script against live server and confirm successful download/verify/write path for a hosted file.

## Affected Components
- `dnsdle/generator.py` (new): deterministic one-file client artifact rendering/writing and invariant checks.
- `dnsdle/__init__.py`: integrate generation call in startup flow after runtime state build and before serving.
- `dnsdle.py`: include generation lifecycle logging and startup failure handling integration.
- `dnsdle/constants.py`: add generation-related constants (template/version identifiers, managed subdirectory name, filename policy).
- `dnsdle/config.py`: enforce/clarify generation-specific config invariants for `target_os` and `client_out_dir` as used by emitter.
- `dnsdle/state.py`: extend runtime structures only if generator requires stable derived metadata view (avoid mutation).
- `dnsdle/client_payload.py`: parity crypto/parser helpers used as generation-time source for inlined emitted client logic (no runtime `dnsdle` import from generated artifact).
- `dnsdle/client_reassembly.py`: parity reassembly/hash verification helpers used as generation-time source for inlined emitted client logic (no runtime `dnsdle` import from generated artifact).
- `doc/architecture/CLIENT_GENERATION.md`: align implemented template/filename/startup behavior and failure semantics.
- `doc/architecture/CONFIG.md`: align generation-related config behavior and defaults with implementation.
- `doc/architecture/ARCHITECTURE.md`: reflect startup data flow now including concrete generation step.
- `doc/architecture/SERVER_RUNTIME.md`: reflect generation as enforced pre-serve startup phase.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: add/align startup generation failure classes and invariants.

## Execution Notes
- Implemented `dnsdle/generator.py` with deterministic one-file emission per
  `(publish_item, target_os)`, embedded runtime constants, and managed output
  ownership under `<client_out_dir>/dnsdle_v1/`.
- Implemented rollback-safe transactional generation:
  - per-run staging and backup directories
  - atomic managed-file replacement
  - stale managed-file pruning restricted to managed filename pattern
  - rollback restoration on commit failure
- Integrated generation into startup flow before serving in `dnsdle.py` with
  fail-fast `generation_error` handling and no server loop entry on generation
  failure.
- Added generation lifecycle logging (`generation_start`, `generation_ok`,
  `generation_summary`, `generation_error`) via existing startup/publish
  categories and level mapping.
- Added generation constants/defaults in `dnsdle/constants.py` and normalized
  `client_out_dir` to absolute form in `dnsdle/config.py`.
- Updated architecture docs to align startup placement, managed output
  semantics, and generator failure/invariant contract.

Validation executed:
- `python -m py_compile dnsdle/generator.py dnsdle.py dnsdle/__init__.py dnsdle/config.py dnsdle/constants.py dnsdle/logging_runtime.py`
- `python2 -m py_compile dnsdle/generator.py dnsdle.py dnsdle/__init__.py dnsdle/config.py dnsdle/constants.py dnsdle/logging_runtime.py`
- Startup generation validation (1 file, 2 target OS, deterministic rerun,
  stale pruning, induced invariant failure, and fail-before-serve checks):
  - `timeout 3 python dnsdle.py --domains example.com --files /tmp/dnsdle_plan_validate/input1.txt --psk test-psk --listen-addr 127.0.0.1:55353 --client-out-dir /tmp/dnsdle_plan_validate/out --target-os windows,linux --log-level info --log-categories startup,publish,server`
  - rerun with same args and diff of generated file hashes
  - rerun with `--target-os linux` and verify stale pruning to one artifact
  - induced mismatch fixture via `generate_client_artifacts(...)` with tampered
    `slice_tokens` and verify `generator_invalid_contract` + unchanged managed
    directory
  - failure-path run with `--client-out-dir` pointing to an existing file and
    verify `generation_error` with no `server_start`
- Live generated-client transfer check (local UDP loopback server using
  `handle_request_message` + generated client subprocess):
  - result: `live_generated_client_download_ok`
  - note: this check required elevated execution in the sandbox to permit UDP
    sockets.

Implementation commit: `261e092`
Deviations from plan: none.
