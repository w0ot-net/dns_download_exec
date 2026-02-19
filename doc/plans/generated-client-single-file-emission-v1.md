# Plan: Generated Client Single-File Emission (v1)

## Summary
Implement the server-side generated-client emission phase so startup produces exactly one standalone Python file per `(published file, target_os)` pair. This phase focuses on deterministic artifact generation, embedded metadata contract correctness, and fail-fast output behavior. It does not implement full client download orchestration logic; it wires generation around the parity/runtime contracts and prepares the artifact shape for the next phase.

## Problem
The runtime server now publishes encrypted CNAME slices, but there is no implemented client artifact generator in code. Config fields (`target_os`, `client_out_dir`) exist but are not used to emit client files. Without this phase, operators cannot obtain generated one-file clients, and the architecture contract in `CLIENT_GENERATION.md` remains unimplemented.

## Goal
After implementation:
- Startup generates one standalone `.py` artifact per `(file_id, target_os)` in `client_out_dir`.
- Each artifact embeds required immutable constants (`BASE_DOMAINS`, `FILE_TAG`, `FILE_ID`, `PUBLISH_VERSION`, `TOTAL_SLICES`, `COMPRESSED_SIZE`, `PLAINTEXT_SHA256_HEX`, `SLICE_TOKENS`, profile IDs, wire knobs).
- Artifact naming is deterministic and collision-safe for the current publish set.
- Generation fails fast on invariant violations and does not leave partial artifacts.
- Startup logs include generation summaries with stable reason codes/events.

## Design
### Scope
In scope:
1. Server-side generation pipeline and file emission.
2. Embedded constants contract and template rendering for one-file scripts.
3. Startup integration and generation logging.
4. Deterministic naming/path behavior and fail-fast filesystem handling.

Out of scope:
- full generated-client download loop runtime behavior (separate phase)
- generated-client crypto/parser implementation details beyond wiring/import-safe placeholders to parity core contract
- execution of downloaded files (still non-goal)

### 1. Introduce generation module and deterministic template rendering
Add a dedicated generator module (for example `dnsdle/generator.py`) that:
1. Accepts immutable `RuntimeState` + config generation fields.
2. Iterates publish items and selected target OS list deterministically.
3. Renders one Python source template per `(publish_item, target_os)`.
4. Writes files atomically into `client_out_dir`.

Deterministic iteration order:
- primary key: canonical publish item order from runtime state
- secondary key: canonical `target_os` order from config

Template contract:
- ASCII-only source
- stdlib-only imports
- one-file structure with sections required by `CLIENT_GENERATION.md`
- includes explicit TODO/runtime placeholders only where downstream phases are intentionally deferred

### 2. Artifact naming and path invariants
Define deterministic filename scheme:
- `dnsdl_<file_id>_<file_tag>_<target_os>.py`

Fail-fast rules:
- reject path traversal or invalid output path composition
- ensure output dir exists (create if missing) or fail with explicit reason
- prevent silent overwrite ambiguity; choose explicit policy and enforce it deterministically
- on any generation failure, remove temp file and do not leave partial artifact

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

### 6. Architecture-doc synchronization
Update architecture docs to reflect actual generation behavior and startup placement:
- generation now implemented as startup phase before serve loop
- deterministic filename/embedded constants contract
- explicit failure semantics for generation phase

### 7. Validation approach
Validation for this phase:
1. run startup with 1 file, 2 target OS values and verify 2 generated scripts.
2. verify filenames and embedded constant blocks are deterministic across reruns with unchanged inputs.
3. verify generation fails fast on an induced invariant violation (for example token/count mismatch fixture) with no partial output.
4. verify startup does not enter serve loop when generation fails.

## Affected Components
- `dnsdle/generator.py` (new): deterministic one-file client artifact rendering/writing and invariant checks.
- `dnsdle/__init__.py`: integrate generation call in startup flow after runtime state build and before serving.
- `dnsdle.py`: include generation lifecycle logging and startup failure handling integration.
- `dnsdle/constants.py`: add generation-related constants (template/version identifiers, filename prefix policy, logging category if needed).
- `dnsdle/config.py`: enforce/clarify generation-specific config invariants for `target_os` and `client_out_dir` as used by emitter.
- `dnsdle/logging_runtime.py`: add/align generation event categories/required lifecycle events as needed.
- `dnsdle/state.py`: extend runtime structures only if generator requires stable derived metadata view (avoid mutation).
- `doc/architecture/CLIENT_GENERATION.md`: align implemented template/filename/startup behavior and failure semantics.
- `doc/architecture/CONFIG.md`: align generation-related config behavior and defaults with implementation.
- `doc/architecture/ARCHITECTURE.md`: reflect startup data flow now including concrete generation step.
- `doc/architecture/SERVER_RUNTIME.md`: reflect generation as enforced pre-serve startup phase.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: add/align startup generation failure classes and invariants.
- `doc/architecture/LOGGING.md`: include generation event classes/required fields if new categories are introduced.
