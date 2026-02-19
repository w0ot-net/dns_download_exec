# Plan: Python Logging Strategy

## Summary
Define and implement a Python-native logging strategy that preserves current machine-readable JSON records while adding explicit levels, categories, and diagnostics controls. The strategy will keep production defaults safe and low-overhead, while allowing deep request-path introspection when explicitly enabled. This change will centralize log emission and redaction so startup, publish, and runtime paths follow one contract.

## Problem
Current logging is ad hoc and split across direct JSON `print` calls (`dnsdle.py`) and per-module record builders (`dnsdle/server.py`, `dnsdle/state.py`). There is no unified level/category model, no centralized diagnostics controls (sampling, rate limiting, focused filtering), and no shared disabled-path pattern that guarantees expensive log context is skipped. Architecture docs require specific log behavior, but there is no dedicated logging architecture contract.

## Goal
After implementation:
- All logs flow through one stdlib-only logging facade with stable JSON schema.
- Levels are explicit: `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE`.
- Categories are explicit and module-aligned: `startup`, `config`, `budget`, `publish`, `mapping`, `dnswire`, `server`.
- Disabled diagnostic paths do not evaluate expensive log context (single fast branch only).
- Sensitive values (PSK, key material, payload bytes, network-facing path data) are consistently redacted.

## Design
### 1. Canonical logging contract
Define one event schema with required fields:
- `ts_unix_ms`
- `level`
- `category`
- existing semantic fields preserved where applicable: `phase`, `classification`, `reason_code`
- optional context keys for event-specific metadata

All emitters write single-line JSON objects with deterministic key ordering.

### 2. Python-specific low-overhead disabled-path model
Python cannot literally compile log calls out like build-tagged Go code, so the invariant for this codebase is:
- disabled path is one inlined branch on precomputed booleans
- no formatting/string building on disabled path
- no context dict construction on disabled path
- no locks/atomics on disabled path (single-threaded runtime model)

Implementation pattern:
- add a new logger module with precomputed `enabled_<level>_<category>` booleans
- gate call sites before building context objects
- for expensive context, use a `context_fn` callable evaluated only when enabled

### 3. Logging configuration surface
Add explicit server CLI/config controls:
- `--log-level` (`error|warn|info|debug|trace`, default `info`)
- `--log-categories` (comma-separated, default `startup,publish,server`; `all` allowed)
- `--log-sample-rate` (`0..1`, default `1.0`) for high-volume `debug/trace`
- `--log-rate-limit-per-sec` (non-negative integer, default bounded value)
- `--log-output` (`stdout` or `file`)
- `--log-file` (required when `--log-output file`)

Validation is fail-fast in config parsing; invalid combinations are startup errors.

### 4. Runtime and startup integration
Replace direct `_emit_record` usage with the centralized logger:
- startup success/failure and publish summaries remain `INFO`/`ERROR`
- request outcomes remain stable (`served`, `followup`, `miss`, `runtime_fault`)
- add opt-in `DEBUG/TRACE` events for packet parsing, mapping resolution, payload encoding, and loop pacing
- keep shutdown summary as a required `INFO` event

### 5. Diagnostics controls
Support deep introspection without always-on noise:
- per-session focus filter (`sid`-style filter mapped to deterministic request identity keys in this codebase)
- probabilistic sampling for `debug/trace` request events
- rate limiting for repetitive high-frequency events

### 6. Redaction and safety
Centralize redaction policy in the logger module:
- never log PSK, derived keys, or raw payload bytes
- never include source file paths in network-facing request logs
- permit explicit payload logging only in `TRACE` with dedicated opt-in flag

### 7. Documentation alignment
Add a dedicated architecture logging document and update existing architecture docs so logging behavior, defaults, and invariants are specified in one place and cross-referenced consistently.

## Affected Components
- `dnsdle.py`: replace direct JSON printing with centralized logger initialization and emission.
- `dnsdle/cli.py`: add logging CLI options and strict argument parsing for logging controls.
- `dnsdle/config.py`: validate logging config fields and store them in immutable runtime config.
- `dnsdle/constants.py`: define canonical logging level/category constants and defaults.
- `dnsdle/state.py`: route startup error records through the unified schema helpers.
- `dnsdle/__init__.py`: plumb logger/config usage through startup-state build flow where needed.
- `dnsdle/budget.py`: add gated diagnostics for budget computation decisions.
- `dnsdle/publish.py`: add gated diagnostics for file read/compress/slice metadata flow.
- `dnsdle/mapping.py`: add gated diagnostics for token derivation/collision-promotion decisions.
- `dnsdle/dnswire.py`: add gated diagnostics for parse/encode and response-shape decisions.
- `dnsdle/server.py`: migrate request/runtime/shutdown logging to centralized level/category-aware API.
- `dnsdle/logging_runtime.py` (new): stdlib-only logging facade, gating, sampling, rate limiting, sinks, and redaction helpers.
- `doc/architecture/LOGGING.md` (new): canonical logging architecture, schema, levels, categories, redaction, and performance model.
- `doc/architecture/ARCHITECTURE.md`: reference logging subsystem as first-class runtime component.
- `doc/architecture/CONFIG.md`: document logging CLI/config fields and validation constraints.
- `doc/architecture/SERVER_RUNTIME.md`: align runtime observability behavior to logger contract and diagnostics controls.
- `doc/architecture/PUBLISH_PIPELINE.md`: align startup publish logging fields and redaction rules to logger contract.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: align required error/runtime log invariants to level/category schema.

## Phased Execution
1. Add centralized logger module and schema/redaction helpers.
2. Wire CLI/config logging controls and fail-fast validation.
3. Migrate entrypoint/startup/publish/mapping/budget logs to the new API.
4. Migrate DNS wire/server runtime logs and add gated `debug/trace` diagnostics.
5. Add sampling/rate-limit/session-focus controls for high-volume runtime events.
6. Update architecture docs to match implemented behavior and defaults.

## Validation
- Startup error paths still emit machine-readable JSON with stable `phase/classification/reason_code`.
- Default run (`--log-level info`) emits current operational summaries without debug noise.
- `debug/trace` disabled paths do not build expensive log context objects.
- Redaction checks confirm no PSK/key/payload leakage in emitted logs.
- Sampling and rate-limit controls deterministically bound high-frequency diagnostics.

## Success Criteria
- One unified logging API is used across startup, publish, mapping, dnswire, and server runtime.
- Level/category controls are operator-configurable and fail-fast validated.
- Existing required operational logs remain present and machine-parseable.
- Diagnostics mode provides deep introspection without changing runtime correctness behavior.
- Architecture docs clearly define logging behavior, invariants, and safety policy.
