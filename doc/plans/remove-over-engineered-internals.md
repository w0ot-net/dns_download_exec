# Plan: Remove over-engineered internals (issues 3-5)

## Summary

Three internal subsystems carry dead code and unnecessary complexity: CLI option
pre-validation duplicates argparse, stale-file removal guards against a scenario
that cannot occur, and log redaction handles data shapes that never appear. This
plan removes all three, replacing them with minimal equivalents.

## Problem

1. `_validate_long_option_tokens` in `cli.py` manually mirrors every argparse
   option in a separate `_KNOWN_LONG_OPTIONS` frozenset, then pre-scans argv.
   This duplicates argparse rejection of unknown arguments and forces every new
   option to be added in two places. The only unique behavior is a
   `--domain -> --domains` migration hint.

2. `_remove_stale_managed_files` in `client_generator.py` lists a managed
   directory looking for `dnsdl*.py` files that are not the current client file.
   The client filename is the constant `"dnsdle_universal_client.py"` -- it
   never changes between runs, so no stale files can exist. The function is
   ~20 lines of dead code.

3. Log redaction in `logging_runtime.py` (`_safe_json_value`, `_is_sensitive_key`,
   `_redact_map`) handles recursive dicts, lists, and Python 2 bytes-to-ASCII
   decoding. Audit of every call site shows: all log records are flat dicts with
   string/int/bool values, except two fields (`domains` as tuple/list in
   `budget.py` and `dnsdle.py`). No bytes values are ever logged. The exact keys
   in `_SENSITIVE_EXACT_KEYS` (`"slice_bytes"`, `"plaintext_bytes"`) never appear
   as log-record keys. A flat key scan with a unified sensitive-key set suffices.

## Goal

- `cli.py`: no `_KNOWN_LONG_OPTIONS`; `--domain` migration hint preserved via
  argparse itself; argparse handles all unknown-option rejection.
- `client_generator.py`: `_remove_stale_managed_files` deleted; its call site
  removed.
- `logging_runtime.py`: `_safe_json_value` removed; `_is_sensitive_key` and
  `_redact_map` collapsed into a single flat-dict redaction pass; Python 2
  bytes handling and recursive traversal removed; `_SENSITIVE_EXACT_KEYS`
  merged into `_SENSITIVE_KEY_PARTS` as a single set; tuple/list values
  converted to list via a simple isinstance check (the one non-scalar shape
  that does occur).

## Design

### Issue 3: `_validate_long_option_tokens` removal (cli.py)

Delete `_KNOWN_LONG_OPTIONS` and `_validate_long_option_tokens`. To preserve
the `--domain` migration message, add `--domain` as a recognized argument in
`_build_parser` that triggers a clear error. Specifically, add a mutually
exclusive dummy argument `--domain` whose presence raises `StartupError` in
`parse_cli_args` after parsing. This avoids argparse's generic "unrecognized
arguments" error and gives the user the migration hint.

Implementation: add `--domain` to the parser with `dest="domain_deprecated"` and
`default=None`. After `parser.parse_args(...)`, check if `domain_deprecated` is
not None and raise `StartupError`. Remove the `_validate_long_option_tokens`
call from `parse_cli_args`.

### Issue 4: `_remove_stale_managed_files` removal (client_generator.py)

Delete the function entirely. Remove the call at line 105 of
`generate_client_artifacts`. No other call sites exist.

### Issue 5: Simplify log redaction (logging_runtime.py)

Replace the three-function redaction system with a single `_redact_map` that:
- Iterates top-level keys only (no recursion).
- Checks each key against a single `_SENSITIVE_KEY_PARTS` tuple of substrings
  (merging the former exact-keys into substring matches -- `"slice_bytes"`
  contains `"key"` wait, no. Let me reconsider.).

Actually, `"slice_bytes"` does NOT contain any of `("psk", "key", "payload")`.
Nor does `"plaintext_bytes"`. So the exact-keys set is genuinely separate from
the substring set and both are needed. However, since neither key ever appears
in log records, the exact-keys set is pure dead code. Remove
`_SENSITIVE_EXACT_KEYS` entirely.

For values: replace `_safe_json_value` with inline handling in `_redact_map`.
The only non-scalar type that actually appears is tuple/list (`domains` field).
Convert tuple/list to list in-place. Drop bytes handling (never occurs) and
recursive dict handling (never occurs). Remove `is_binary`, `key_text`, `PY2`
imports that become unused.

Updated `_redact_map`:

```python
def _redact_map(record):
    output = {}
    for key, value in record.items():
        k = key if isinstance(key, str) else str(key)
        if _is_sensitive_key(k):
            output[k] = "[redacted]"
        elif isinstance(value, (tuple, list)):
            output[k] = list(value)
        else:
            output[k] = value
    return output
```

`_is_sensitive_key` stays but drops the exact-key check:

```python
def _is_sensitive_key(key):
    lower = key.lower()
    for part in _SENSITIVE_KEY_PARTS:
        if part in lower:
            return True
    return False
```

Remove: `_safe_json_value`, `_SENSITIVE_EXACT_KEYS`, imports of `is_binary`,
`key_text`, `PY2` from `dnsdle.compat`.

## Affected Components

- `dnsdle/cli.py`: delete `_KNOWN_LONG_OPTIONS`, `_validate_long_option_tokens`;
  add `--domain` deprecated argument to `_build_parser`; update `parse_cli_args`
  to check for deprecated `--domain` after parsing.
- `dnsdle/client_generator.py`: delete `_remove_stale_managed_files` function
  and its call site on line 105.
- `dnsdle/logging_runtime.py`: delete `_safe_json_value`, `_SENSITIVE_EXACT_KEYS`;
  simplify `_is_sensitive_key` and `_redact_map`; remove unused `is_binary`,
  `key_text`, `PY2` imports.
