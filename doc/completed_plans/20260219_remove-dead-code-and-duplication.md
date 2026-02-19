# Plan: Remove Dead Code and Duplication

## Summary

`constants.py` exports twelve named intermediaries (`LOG_LEVEL_*`, `LOG_CATEGORY_*`) that
exist only to populate two tuples and are never imported individually — they can be inlined.
Three private functions are duplicated across modules (`_dns_name_wire_length` ×3,
`_labels_is_suffix` ×2) and one crypto helper is inconsistently extracted only in some of
its callers (`_hmac_sha256`). The shared functions move to `constants.py` — the one module
already imported by every affected caller — eliminating all duplication without adding a new
module. `DNS_QTYPE_AAAA` and `DNS_FLAG_RA` appear unused in production code but are
imported by test files; they are left in place.

## Problem

**Dead named intermediaries in `constants.py`:**
- `LOG_LEVEL_ERROR/WARN/INFO/DEBUG/TRACE` (5 names): used only within `constants.py` to
  populate `LOG_LEVELS`; no module imports them individually.
- `LOG_CATEGORY_STARTUP/CONFIG/BUDGET/PUBLISH/MAPPING/DNSWIRE/SERVER` (7 names): same
  pattern, used only to populate `LOG_CATEGORIES`.
- `DEFAULT_LOG_LEVEL = LOG_LEVEL_INFO` references one of these intermediaries.

**Duplicated private functions:**
- `_dns_name_wire_length`: identical one-liner (`1 + sum(1 + len(l) for l in labels)`)
  independently defined in `config.py`, `mapping.py`, and `budget.py`.
- `_labels_is_suffix`: identical 5-line function independently defined in `config.py` and
  `server.py`.

**Inconsistent `hmac.new()` usage:**
- `mapping.py` wraps `hmac.new(..., hashlib.sha256).digest()` in a local `_hmac_sha256`
  helper and calls it consistently.
- `cname_payload.py` calls `hmac.new(..., hashlib.sha256).digest()` inline three separate
  times with no local helper.

## Goal

- `LOG_LEVELS` and `LOG_CATEGORIES` are tuple literals; the twelve intermediate names are
  gone.
- `dns_name_wire_length` and `labels_is_suffix` are defined exactly once in `constants.py`
  and imported from there by all callers.
- `cname_payload.py` uses a local `_hmac_sha256` helper consistently, matching
  `mapping.py`'s pattern.
- No new module is introduced; `constants.py` gains two pure, import-free utility functions
  that are tightly coupled to its DNS wire-format constants.
- No behaviour change anywhere.

## Design

### New module decision

Both `dns_name_wire_length` and `labels_is_suffix` are pure functions with no imports.
Every module that needs them (`config.py`, `mapping.py`, `budget.py`, `server.py`) already
imports from `constants.py`, so moving there adds zero new import edges. A dedicated
`util.py` would add a file and new imports for two tiny functions — not worth it at this
scale.

### `dnsdle/constants.py`

**Inline the log-level and log-category names:**

```python
# remove all LOG_LEVEL_* = "..." lines
# remove all LOG_CATEGORY_* = "..." lines

LOG_LEVELS = ("error", "warn", "info", "debug", "trace")
LOG_CATEGORIES = ("startup", "config", "budget", "publish", "mapping", "dnswire", "server")
DEFAULT_LOG_LEVEL = "info"
```

**Add the two shared utility functions** (after all constant definitions, at the bottom of
the file — no imports required):

```python
def dns_name_wire_length(labels):
    return 1 + sum(1 + len(label) for label in labels)


def labels_is_suffix(suffix_labels, full_labels):
    suffix_len = len(suffix_labels)
    full_len = len(full_labels)
    if suffix_len > full_len:
        return False
    return full_labels[full_len - suffix_len:] == suffix_labels
```

### `dnsdle/config.py`

- Remove local `_dns_name_wire_length` definition.
- Remove local `_labels_is_suffix` definition.
- Add `dns_name_wire_length` and `labels_is_suffix` to the `from dnsdle.constants import`
  block.
- Replace all six call sites (`_dns_name_wire_length(...)` → `dns_name_wire_length(...)`,
  `_labels_is_suffix(...)` → `labels_is_suffix(...)`).

### `dnsdle/mapping.py`

- Remove local `_dns_name_wire_length` definition.
- Add `dns_name_wire_length` to the `from dnsdle.constants import` block.
- Replace the one call site.

### `dnsdle/budget.py`

- Remove local `_dns_name_wire_length` definition.
- Add `dns_name_wire_length` to the `from dnsdle.constants import` block.
- Replace the three call sites.

### `dnsdle/server.py`

- Remove local `_labels_is_suffix` definition.
- Add `labels_is_suffix` to the `from dnsdle.constants import` block.
- Replace the one call site (`_labels_is_suffix(...)` → `labels_is_suffix(...)`).

### `dnsdle/cname_payload.py`

Add a local `_hmac_sha256` helper immediately after the imports (consistent with
`mapping.py`) and replace the three inline `hmac.new()` calls:

```python
def _hmac_sha256(key_bytes, message_bytes):
    return hmac.new(key_bytes, message_bytes, hashlib.sha256).digest()
```

Replace:
```python
# before:
return hmac.new(psk_bytes, key_label + ..., hashlib.sha256).digest()
hmac.new(enc_key, block_input, hashlib.sha256).digest()
hmac.new(mac_key, message, hashlib.sha256).digest()[:PAYLOAD_MAC_TRUNC_LEN]

# after:
return _hmac_sha256(psk_bytes, key_label + ...)
_hmac_sha256(enc_key, block_input)
_hmac_sha256(mac_key, message)[:PAYLOAD_MAC_TRUNC_LEN]
```

### Out of scope

`DNS_QTYPE_AAAA` and `DNS_FLAG_RA` are not used in production code but are imported by
`unit_tests/test_server_runtime.py` and `unit_tests/test_client_payload_parity.py`
respectively. Removing them requires test changes outside this plan's scope.

## Affected Components

- `dnsdle/constants.py`: inline `LOG_LEVEL_*` and `LOG_CATEGORY_*` names; add
  `dns_name_wire_length` and `labels_is_suffix` functions.
- `dnsdle/config.py`: remove two local private functions; add two imports from constants;
  update six call sites.
- `dnsdle/mapping.py`: remove one local private function; add one import from constants;
  update one call site.
- `dnsdle/budget.py`: remove one local private function; add one import from constants;
  update three call sites.
- `dnsdle/server.py`: remove one local private function; add one import from constants;
  update one call site.
- `dnsdle/cname_payload.py`: add local `_hmac_sha256` helper; replace three inline
  `hmac.new()` call expressions.

## Execution Notes

Executed 2026-02-19. All changes applied as specified. 134 unit tests pass.
