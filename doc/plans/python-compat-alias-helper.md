# Plan: Python 2/3 Compat Alias Helper

## Summary
Introduce a shared `dnsdle/compat.py` module that centralizes Python 2.7/3.x compatibility behavior and prefers import-time aliasing over repeated runtime branching. The implementation will replace duplicated byte/text/int compatibility helpers currently spread across modules with a single, explicit compat API. This keeps behavior deterministic, reduces repeated compatibility code, and makes cross-version invariants auditable in one place.

## Problem
Compatibility handling is currently duplicated and inconsistent across modules (`dnsdle/mapping.py`, `dnsdle/publish.py`, `dnsdle/cname_payload.py`, `dnsdle/dnswire.py`, `dnsdle/logging_runtime.py`). Most modules perform per-call `isinstance(..., bytes)` or `isinstance(..., str)` branching inline, and several reimplement equivalent byte/text coercion logic independently. This increases maintenance risk and makes it harder to guarantee identical Python 2/3 behavior.

## Goal
After implementation:
- One canonical compat layer (`dnsdle/compat.py`) provides shared Python 2/3 aliases and coercion helpers.
- Compatibility decisions that can be fixed at import time are implemented as aliases/function bindings, not repeated per-call version branches.
- Existing wire, crypto, mapping, and logging behavior remains unchanged for valid inputs.
- Invalid type expectations fail fast through explicit compat helper errors.

## Design
### 1. Add a dedicated compat module
Create `dnsdle/compat.py` with:
- import-time aliases for runtime type families (`text_type`, `binary_type`, `string_types`, `integer_types`)
- byte/text helpers with strict contracts (for example, ASCII bytes conversion and safe byte-value iteration)
- utility helpers for operations currently duplicated (for example, base32 text normalization result type handling)

Rule: branch on interpreter differences inside `compat.py` only; downstream call sites consume aliases/helpers directly.

### 2. Alias-first, fail-fast helper contracts
Define helpers so call sites do not guess coercion behavior:
- explicit accepted input types per helper
- explicit output type invariants
- deterministic exceptions on unsupported input type or encoding violations

Avoid compatibility fallbacks that silently coerce unexpected data.

### 3. Migrate current call sites
Refactor modules that currently contain compatibility branching to use `dnsdle.compat`:
- replace local `_ascii_bytes` / `_to_bytes` duplicates
- replace local `bytes`/`str` conditional decoding for base32/text normalization
- replace byte-iteration compatibility branches in payload and DNS parsing helpers
- keep module logic and protocol semantics unchanged while removing duplicated compat logic

### 3.1 Logging-runtime migration invariants (indirect validation allowed)
For `dnsdle/logging_runtime.py`, preserve these behaviors while adopting `compat` helpers:
- sensitive-key redaction semantics stay unchanged (`psk`, key/payload-derived names)
- bytes values remain JSON-safe and deterministic in emitted records
- nested map/list/tuple redaction and coercion remains deterministic
- no new logging of raw payload/psk/derived-key bytes

### 4. Scope boundaries
This change is an internal refactor only:
- no CLI/config surface changes
- no wire/protocol/schema changes
- no compatibility shim for old protocol versions

Breaking API cleanup is allowed for private module-local helper names as long as all in-repo call sites are updated in the same change.

### 5. Architecture documentation updates
Update architecture docs to codify compatibility implementation policy:
- Python 2.7/3.x support remains required
- compatibility logic must be centralized in `dnsdle/compat.py`
- alias-at-import policy is preferred to repeated per-call branching where behavior can be pre-bound

## Affected Components
- `dnsdle/compat.py` (new): canonical Python 2/3 alias and coercion helper module.
- `dnsdle/mapping.py`: replace duplicated ASCII/base32 compatibility helpers with `compat` calls.
- `dnsdle/publish.py`: replace duplicated ASCII-byte coercion helper with `compat` call.
- `dnsdle/cname_payload.py`: replace duplicated bytes/text/int and byte-iteration compatibility helpers with `compat` helpers.
- `dnsdle/dnswire.py`: replace inline byte/str decode and ord-compat branching with `compat` helpers.
- `dnsdle/logging_runtime.py`: replace inline bytes-redaction type checks and key/text coercion branches with `compat` helpers where applicable.
- `doc/architecture/ARCHITECTURE.md`: document centralized compat-layer responsibility in the architecture overview.
- `doc/architecture/CRYPTO.md`: align Python compatibility constraints with shared compat helper policy for crypto/payload byte handling.
- `unit_tests/test_mapping.py`: verify mapping determinism/error behavior remains stable after compat refactor.
- `unit_tests/test_publish.py`: verify publish pipeline byte-path behavior remains stable after compat refactor.
- `unit_tests/test_cname_payload.py`: verify payload-record deterministic MAC/layout behavior remains stable.
- `unit_tests/test_cname_payload_encryption.py`: verify encryption determinism and metadata binding remain stable.
- `unit_tests/test_dnswire.py`: verify DNS parse/encode behavior and parse safety remain stable.
- `unit_tests/test_server_runtime.py`: indirectly validate logging/runtime integration remains stable while request behavior stays deterministic.
- `unit_tests/test_server_request_envelope_validation.py`: verify envelope validation ordering/invariants remain stable.
- `unit_tests/test_server_request_envelope_integration.py`: verify envelope behavior remains stable end-to-end.

## Phased Execution
1. Add `dnsdle/compat.py` with import-time alias bindings and strict helper contracts.
2. Migrate each module to use `compat` helpers and remove duplicated local compatibility helpers.
3. Perform a focused pass to ensure no remaining ad hoc Python 2/3 branching outside `compat.py` where aliasing is appropriate.
4. Update architecture docs to capture the compatibility policy and invariants.

## Validation
- Module behavior remains unchanged for valid inputs (mapping tokens, payload record encoding, DNS parse/encode, logging serialization).
- No module outside `dnsdle/compat.py` keeps duplicated bytes/text compatibility helper implementations where aliasing applies.
- Invalid input types still fail fast with explicit errors.
- Python 2.7/3.x compatibility requirement remains satisfied with stdlib-only code.
- Execute focused runtime test suites:
  - `python -m unittest unit_tests.test_mapping`
  - `python -m unittest unit_tests.test_publish`
  - `python -m unittest unit_tests.test_cname_payload`
  - `python -m unittest unit_tests.test_cname_payload_encryption`
  - `python -m unittest unit_tests.test_dnswire`
  - `python -m unittest unit_tests.test_server_runtime`
  - `python -m unittest unit_tests.test_server_request_envelope_validation`
  - `python -m unittest unit_tests.test_server_request_envelope_integration`
- Syntax check touched runtime modules:
  - `python -m py_compile dnsdle/compat.py dnsdle/mapping.py dnsdle/publish.py dnsdle/cname_payload.py dnsdle/dnswire.py dnsdle/logging_runtime.py`

## Success Criteria
- Compatibility aliases/helpers live in one module and are used by all affected runtime modules.
- Repeated per-call compatibility branching is removed at migrated call sites when aliasing can pre-bind behavior.
- Architecture docs explicitly define the centralized compat policy.
- Protocol/runtime behavior remains deterministic and unchanged for existing valid flows.
