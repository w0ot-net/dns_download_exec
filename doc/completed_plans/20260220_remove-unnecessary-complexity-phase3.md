# Plan: Remove unnecessary complexity

## Summary

Remove dead fields, dead code branches, a custom class that provides no
practical benefit, and over-indirected helpers across six modules. The changes
are pure deletions and inlinings with no behavioral impact. Estimated net
reduction: ~75 lines.

## Problem

Several pieces of infrastructure exist that are never consumed at runtime:

- `config.fixed`, `FIXED_CONFIG`, `PROFILE_V1`, `QTYPE_RESPONSE_CNAME` are
  populated but never read.
- `PublishItem.crypto_profile` and `PublishItem.wire_profile` are set on every
  publish item but never read by any consumer (server, client, stager).
- `FrozenDict` wraps three dicts inside `RuntimeState` to block mutation, but
  nothing ever attempts mutation and the wrapping namedtuple already signals
  immutability.
- `_record_is_required` has a dead `level_name == "error"` branch (error is
  rank 50 -- the highest -- so it always passes the rank threshold anyway).
- `emit_record` infers level/category then round-trips through `emit` which
  re-validates them via `_normalize_name`; the re-validation is redundant.
- `_arg_value` / `_arg_value_default` support both argparse namespace and dict
  lookup, but only namespace is ever passed.
- `_normalize_log_file` is a trivial one-liner wrapper with no validation.
- `_include_opt` is a one-liner called 4 times; each call site is equally
  readable inlined.
- `build_publish_items` accepts `seen_plaintext_sha256` / `seen_file_ids`
  defaulting to None, but no caller ever passes them.

## Goal

After implementation:

1. `PROFILE_V1`, `QTYPE_RESPONSE_CNAME`, `FIXED_CONFIG` no longer exist.
2. `Config` namedtuple has no `fixed` field.
3. `PublishItem` has no `crypto_profile` or `wire_profile` fields.
4. `FrozenDict` class is deleted; `RuntimeState` stores plain dicts.
5. `_record_is_required` has no `level_name == "error"` branch.
6. `emit_record` checks level threshold and writes directly instead of
   round-tripping through `emit` + `_normalize_name`.
7. `_arg_value` / `_arg_value_default` use `getattr` only.
8. `_normalize_log_file` is inlined.
9. `_include_opt` is inlined at all 4 call sites.
10. `build_publish_items` has no `seen_*` default parameters.

## Design

Six groups of changes, each independently safe.

### A. Remove dead profile/fixed fields

Delete from `constants.py`:
- `PROFILE_V1 = "v1"` (line 5)
- `QTYPE_RESPONSE_CNAME = "CNAME"` (line 6)
- `FIXED_CONFIG` dict (lines 92-100)

Delete from `config.py`:
- `from dnsdle.constants import FIXED_CONFIG` import (line 10)
- `"fixed"` from `Config` namedtuple field list (line 49)
- `fixed=FIXED_CONFIG.copy()` from `build_config` return (line 432)

Delete from `publish.py`:
- `from dnsdle.constants import PROFILE_V1` import (line 8)
- `"crypto_profile": PROFILE_V1` dict entry (line 84)
- `"wire_profile": PROFILE_V1` dict entry (line 85)

Delete from `state.py`:
- `"crypto_profile"` and `"wire_profile"` from `PublishItem` field list
  (lines 52-53)
- `crypto_profile=mapped_item["crypto_profile"]` from `to_publish_item`
  (line 83)
- `wire_profile=mapped_item["wire_profile"]` from `to_publish_item` (line 84)

### B. Remove FrozenDict

Delete from `state.py`:
- Entire `FrozenDict` class (lines 27-37)
- Replace three `FrozenDict(...)` calls in `build_runtime_state` with plain
  `dict(...)` (lines 130, 132, 133). `budget_info` is already a dict so just
  pass it directly; `lookup` and `slice_data_by_identity` are already plain
  dicts so just pass them directly.

### C. Simplify logging emit path

In `logging_runtime.py`:

1. Remove dead branch in `_record_is_required` (lines 103-104): delete the
   `if level_name == "error": return True` check.

2. Collapse `emit_record` so it checks the level threshold and writes
   directly, without re-dispatching through `emit` (which re-normalizes
   level/category via `_normalize_name`). The new `emit_record` will:
   - infer `level_name` via `_record_level` (returns a known-valid string)
   - infer `category_name` via `_record_category` (returns a known-valid
     string)
   - check required + rank threshold (same logic as `emit`)
   - redact and write the record directly

### D. Simplify config helpers

In `config.py`:

1. Replace `_arg_value` body with:
   ```python
   def _arg_value(parsed_args, name):
       value = getattr(parsed_args, name, _SENTINEL)
       if value is not _SENTINEL:
           return value
       raise StartupError(...)
   ```
   Add a module-level `_SENTINEL = object()`.

2. Replace `_arg_value_default` body with:
   ```python
   def _arg_value_default(parsed_args, name, default):
       return getattr(parsed_args, name, default)
   ```

3. Inline `_normalize_log_file`: replace the call at line 389 with
   `log_file = (raw_value or "").strip()` where `raw_value` is the
   `_arg_value_default(...)` expression, and delete the function definition.

### E. Inline _include_opt

In `server.py`:

Delete the `_include_opt` function (lines 46-47). At each of the 4 call
sites, replace `_include_opt(config)` with `config.dns_edns_size > 512`.

### F. Remove unused build_publish_items default parameters

In `publish.py`:

Remove `seen_plaintext_sha256=None` and `seen_file_ids=None` from the
`build_publish_items` signature (lines 110-111). The function will pass
`None` directly to `build_publish_items_from_sources` (which already handles
`None` defaults).

### Architecture doc updates

- `doc/architecture/PUBLISH_PIPELINE.md`: remove `crypto_profile` and
  `wire_profile` from the "Required fields per publish object" list and the
  "fixed profile ids" input line.
- `doc/architecture/CONFIG.md`: remove the "Fixed v1 Config" section contents
  that reference `wire_profile`, `crypto_profile`, and `qtype_response`.
  Keep the section header with a note that v1 has no configurable fixed
  fields -- these are implicit in the wire format.
- `doc/architecture/CRYPTO.md`: remove the `crypto_profile = "v1"` bullet
  under "Algorithm Agility".

## Affected Components

- `dnsdle/constants.py`: delete `PROFILE_V1`, `QTYPE_RESPONSE_CNAME`, `FIXED_CONFIG`
- `dnsdle/config.py`: remove `fixed` from Config, simplify `_arg_value`/`_arg_value_default`, inline `_normalize_log_file`
- `dnsdle/state.py`: delete `FrozenDict`, remove `crypto_profile`/`wire_profile` from `PublishItem` and `to_publish_item`
- `dnsdle/publish.py`: remove `PROFILE_V1` import and usage, remove unused default params from `build_publish_items`
- `dnsdle/logging_runtime.py`: remove dead `_record_is_required` branch, collapse `emit_record`
- `dnsdle/server.py`: inline `_include_opt`
- `doc/architecture/PUBLISH_PIPELINE.md`: remove `crypto_profile`/`wire_profile` references
- `doc/architecture/CONFIG.md`: update "Fixed v1 Config" section
- `doc/architecture/CRYPTO.md`: remove `crypto_profile = "v1"` bullet

## Execution Notes

Executed 2026-02-20. All six plan sections implemented. Review findings addressed:

1. **Section C (logging)**: Instead of duplicating the write path in
   `emit_record`, extracted a shared `_do_emit` private method that both
   `emit` and `emit_record` call. This eliminates the redundant
   `_normalize_name` round-trip without code duplication.

2. **CRYPTO.md**: Rewrote the Algorithm Agility section to reference the
   actual wire-level profile byte (`PAYLOAD_PROFILE_V1_BYTE = 0x01`) instead
   of removing the example and leaving a dangling sentence.

3. **CONFIG.md**: Scoped the note to say wire profile, crypto profile, and
   response type are implicit in the v1 wire format. Kept the remaining 4
   fixed config items (`query_mapping_alphabet`, `query_mapping_case`,
   `generated_client_single_file`, `generated_client_download_only`).

4. **Section F**: Omitted `seen_plaintext_sha256` and `seen_file_ids`
   arguments entirely when calling `build_publish_items_from_sources`,
   letting its own defaults apply, instead of passing `None` explicitly.
