# Logging

This document defines the v1 server logging contract.

All server logs are single-line JSON records emitted through the centralized
runtime logger (`dnsdle/logging_runtime.py`).

---

## Event Schema

Every emitted record includes:
- `ts_unix_ms`
- `level` (`ERROR|WARN|INFO|DEBUG|TRACE`)
- `category` (`startup|config|budget|publish|mapping|dnswire|server`)

Existing semantic fields remain required where applicable:
- `phase`
- `classification`
- `reason_code`

Additional event-specific keys may be included as context.

### Generation Events

`generation_ok` required fields: `file_id`, `publish_version`, `file_tag`,
`target_os`, `path`.

`generation_summary` required fields: `managed_dir`, `artifact_count`,
`target_os`, `file_ids`.

---

## Configuration

Runtime logging controls:
- `--log-level` (`error|warn|info|debug|trace`, default `info`)
- `--log-categories` (CSV or `all`, default `startup,publish,server`)
- `--log-sample-rate` (`0..1`, default `1.0`)
- `--log-rate-limit-per-sec` (non-negative integer, default `200`)
- `--log-output` (`stdout|file`, default `stdout`)
- `--log-file` (required when `--log-output file`)
- `--log-focus` (optional deterministic request focus key)

Invalid combinations are startup errors.

---

## Suppression Rules

Filtering and suppression apply to diagnostics only:
- category filters apply to non-required `INFO/DEBUG/TRACE` diagnostics.
- sampling and rate limiting apply only to `DEBUG/TRACE`.

Required events are unsuppressed:
- all `ERROR` events always emit.
- lifecycle events `server_start` and `shutdown` always emit.

---

## Disabled-Path Cost Model

For disabled diagnostics:
- one fast branch on precomputed logger state
- no message formatting work
- no expensive context construction
- no `context_fn` evaluation

---

## Redaction Rules

Logging must never include:
- PSK material
- derived key material
- raw payload bytes

Network-facing request logs must not include source file paths.

Deep diagnostics must use safe surrogates only (lengths, hashes, counters),
not raw payload content.

---

## Related Docs

- `doc/architecture/ARCHITECTURE.md`
- `doc/architecture/CONFIG.md`
- `doc/architecture/SERVER_RUNTIME.md`
- `doc/architecture/ERRORS_AND_INVARIANTS.md`
