# Plan: Eliminate extracted-function renames from universal client assembly

## Summary

Remove all four rename tables (`_COMPAT_RENAMES`, `_HELPERS_RENAMES`,
`_DNSWIRE_RENAMES`, `_CNAME_PAYLOAD_RENAMES`) and the `apply_renames` mechanism
from the universal client build pipeline.  The generated client will use the
canonical function names from the codebase modules directly.  Three
cross-module references in the extracted `_decode_name` block (`_message_length`,
`_ord_byte`, `DnsParseError`) that have no canonical equivalent in the client
context are resolved by modifying the source block and adding a thin subclass.

## Problem

`build_client_source()` extracts 16 functions from 4 canonical modules and then
applies 21 whole-word renames via `apply_renames()`.  Most renames are purely
cosmetic (e.g. `encode_ascii` -> `_to_ascii_bytes`).  This adds complexity, a
separate rename table per module, and an `apply_renames` pass for every
extraction group.  The `_CLIENT_SUFFIX` template then uses the renamed names
throughout, diverging from the rest of the codebase for no functional benefit.

Additionally, the `DnsParseError` -> `ClientError` rename is **broken at
runtime**: the extracted `_decode_name` raises `DnsParseError("message")` with
1 arg, but `ClientError.__init__` requires 3 positional args `(code, phase,
message)`.  After rename the generated code contains
`raise ClientError("name extends past message")` which would crash with
`TypeError` whenever a DNS parse error occurs during client execution.

## Goal

1. Generated client uses the same function names as the canonical modules.
2. `apply_renames` mechanism is removed entirely.
3. The `DnsParseError` constructor mismatch bug is fixed.
4. No behavioral change to the generated client beyond the bug fix.

## Design

### Eliminate cosmetic renames

Delete all four rename lists and their corresponding `apply_renames()` calls.
Update every reference in `_CLIENT_SUFFIX` to use the canonical name:

| Renamed name (current)    | Canonical name (after)     |
|---------------------------|----------------------------|
| `_to_ascii_bytes`         | `encode_ascii`             |
| `_to_utf8_bytes`          | `encode_utf8`              |
| `_to_ascii_text`          | `decode_ascii`             |
| `_base32_lower_no_pad`    | `base32_lower_no_pad`      |
| `_base32_decode_no_pad`   | `base32_decode_no_pad`     |
| `_byte_value`             | `byte_value`               |
| `_iter_byte_values`       | `iter_byte_values`         |
| `_secure_compare`         | `constant_time_equals`     |
| `_to_ascii_int_bytes`     | `encode_ascii_int`         |
| `_is_binary`              | `is_binary`                |
| `_hmac_sha256`            | `hmac_sha256`              |
| `_dns_name_wire_length`   | `dns_name_wire_length`     |

Names that are already canonical (no rename applied): `_derive_file_bound_key`,
`_keystream_bytes`, `_xor_bytes`.

### Handle `_decode_name` cross-module references

The extracted `_decode_name` block calls three names defined in `dnswire.py`
that are NOT extracted and have no equivalent in the client context:

1. **`_message_length(message)`** -- trivial wrapper for `len(message)`.
   **Fix:** change the two calls inside the extract markers in `dnswire.py`
   to `len(message)` directly.  The `_message_length` function and its
   other call sites outside the markers remain unchanged.

2. **`_ord_byte(value)`** -- trivial wrapper for `byte_value(value)`.
   **Fix:** change the two calls inside the extract markers in `dnswire.py`
   to `byte_value(value)` directly (which is imported at the top of
   `dnswire.py`).  The `_ord_byte` function and its other call sites remain
   unchanged.

3. **`DnsParseError`** -- different exception class with a 1-arg constructor
   vs `ClientError`'s 3-arg constructor.
   **Fix:** add a `DnsParseError` subclass to `_CLIENT_PREAMBLE` that adapts
   the single-string constructor to `ClientError(EXIT_PARSE, "parse", msg)`:

   ```python
   class DnsParseError(ClientError):
       def __init__(self, message):
           ClientError.__init__(self, EXIT_PARSE, "parse", message)
   ```

   This is caught by `except ClientError` in the download loop and produces
   correct `code`/`phase`/`message` attributes -- fixing the current bug.

### Remove `apply_renames` from `extract.py`

Delete the `apply_renames` function.  The `re` import stays (used by the
extract marker regexes).

### Remove `_CNAME_PAYLOAD_RENAMES` defensive entries

All 5 entries in `_CNAME_PAYLOAD_RENAMES` are duplicates of entries in the
other tables, applied defensively so that cross-module references in extracted
`cname_payload` functions resolve correctly.  With no renames at all, the
canonical names are already consistent across all extracted blocks.

## Affected Components

- `dnsdle/client_standalone.py`: delete 4 rename lists and comment; delete
  `from dnsdle.extract import apply_renames`; remove `apply_renames()` calls
  from `build_client_source()`; add `DnsParseError` subclass to
  `_CLIENT_PREAMBLE`; update all renamed references in `_CLIENT_SUFFIX` to
  canonical names (approx 25 call sites).
- `dnsdle/dnswire.py`: inside the `_decode_name` extract markers only, change
  `_message_length(message)` -> `len(message)` (1 call) and
  `_ord_byte(message[...])` -> `byte_value(message[...])` (2 calls).
- `dnsdle/extract.py`: delete `apply_renames` function.
- `doc/architecture/CLIENT_GENERATION.md`: update the "Extracted functions"
  section to note that canonical names are used directly (no rename step).

## Execution Notes (2026-02-20)

All plan items implemented as specified, no deviations.

- Deleted 4 rename tables and `apply_renames` import from `client_standalone.py`.
- Replaced 8 renamed function references across `_CLIENT_SUFFIX` with canonical
  names (`encode_ascii`, `encode_ascii_int`, `hmac_sha256`,
  `base32_lower_no_pad`, `base32_decode_no_pad`, `byte_value`,
  `constant_time_equals`, `dns_name_wire_length`).
- Added `DnsParseError(ClientError)` subclass to `_CLIENT_PREAMBLE` -- fixes
  the runtime `TypeError` bug on DNS parse errors.
- Simplified `build_client_source()` to concatenate block lists directly.
- Inlined `_message_length` and `_ord_byte` calls inside extract markers in
  `dnswire.py` (3 call sites).
- Deleted `apply_renames` function from `extract.py`.
- Updated stale extraction-spec comment in `client_standalone.py`.
- Updated `CLIENT_GENERATION.md` architecture section.
