# Plan: De-duplicate client_template.py via extraction with error aliasing

## Summary

Eliminate code duplication between `client_template.py`'s string literals and
the canonical modules (`compat.py`, `helpers.py`, `dnswire.py`,
`cname_payload.py`) by extracting marked blocks at generation time.  Error
contract differences are resolved via aliasing in the template rather than
refactoring the canonical modules.

## Problem

`client_template.py` contains ~550 lines of Python inside string literals
(`_TEMPLATE_PREFIX` / `_TEMPLATE_SUFFIX`) that form the generated standalone
client.  Approximately 15 of those functions duplicate logic from canonical
modules.  If the canonical code changes, the template copy silently drifts.

The duplication has three root causes:

1. **Naming**: Template uses `_`-prefixed private names (`_to_ascii_bytes`);
   canonical uses public names (`encode_ascii`).
2. **Error contracts**: Template raises `ClientError(code, phase, msg)`;
   canonical raises `ValueError` or `DnsParseError`.
3. **Signatures**: Some template functions read module globals (`FILE_ID`,
   `PUBLISH_VERSION`); canonical equivalents take them as parameters.

## Goal

After implementation:

1. Each shared function is defined in exactly one canonical source file.
2. The canonical modules contain `# __EXTRACT: name__` / `# __END_EXTRACT__`
   markers around extractable regions.
3. A new `dnsdle/extract.py` module parses markers, extracts blocks, and
   applies whole-word identifier renames.
4. `build_client_template()` splices renamed extractions into the template,
   replacing the duplicated inline code.
5. Error differences are resolved by aliasing `DnsParseError` as a
   `ClientError` subclass in the template header.  Canonical modules are
   **unchanged behaviorally** -- only inert marker comments are added.
6. The old `client_template.py` is preserved as `client_template_legacy.py`.
7. Generated clients are functionally identical to current output.

## Design

### Marker syntax

```python
# __EXTRACT: block_name__
def some_function(...):
    ...
# __END_EXTRACT__
```

Rules:
- Block names match `[a-z][a-z0-9_]*`.
- Content between marker line (exclusive) and end marker (exclusive) is
  captured verbatim, including blank lines.
- Blocks must not nest.  A source file may contain multiple blocks.
- The marker lines are inert comments -- they do not affect module behavior.

### Error aliasing

The template defines `DnsParseError` as a `ClientError` subclass:

```python
class DnsParseError(ClientError):
    def __init__(self, message):
        ClientError.__init__(self, EXIT_PARSE, "parse", message)
```

Extracted code that does `raise DnsParseError("...")` now creates a
`ClientError(EXIT_PARSE, "parse", "...")`.  The existing `except ClientError`
in the template's `main()` catches it.  No changes to `dnswire.py` needed.

For extracted functions that raise `ValueError` (from `compat.py`,
`cname_payload.py`), these are invariant violations (programming errors) that
should fail fast.  The one exception is `base32_decode_no_pad` which can fail
on bad network data -- its call site in `_parse_slice_record` gets a one-line
`try/except ValueError` wrapper.

### Rename map

A single rename map applies to all extracted text via whole-word
(`\bname\b`) substitution:

```python
_RENAMES = {
    # compat.py public names -> template private names
    "encode_ascii": "_to_ascii_bytes",
    "encode_utf8": "_to_utf8_bytes",
    "decode_ascii": "_to_ascii_text",
    "encode_ascii_int": "_to_ascii_int_bytes",
    "byte_value": "_byte_value",
    "iter_byte_values": "_iter_byte_values",
    "base32_decode_no_pad": "_base32_decode_no_pad",
    "is_binary": "_is_binary",
    "constant_time_equals": "_secure_compare",
    # helpers.py
    "hmac_sha256": "_hmac_sha256",
    "dns_name_wire_length": "_dns_name_wire_length",
    # dnswire.py internal helpers (collapsed into extracted functions)
    "_ord_byte": "_byte_value",
    "_message_length": "len",
    # cname_payload.py (names already underscore-prefixed; no rename needed
    # for _derive_file_bound_key, _keystream_bytes, _xor_bytes)
}
```

### Extraction targets

**From `compat.py` (9 functions):**

| Canonical name | Template name | Notes |
|---|---|---|
| `encode_ascii` | `_to_ascii_bytes` | |
| `encode_utf8` | `_to_utf8_bytes` | |
| `decode_ascii` | `_to_ascii_text` | |
| `encode_ascii_int` | `_to_ascii_int_bytes` | References `text_type` (defined in template PY2 block) |
| `byte_value` | `_byte_value` | |
| `iter_byte_values` | `_iter_byte_values` | Needed by extracted `_xor_bytes` and `_secure_compare` |
| `base32_decode_no_pad` | `_base32_decode_no_pad` | Template wraps ValueError at call site |
| `is_binary` | `_is_binary` | Needed by `constant_time_equals` |
| `constant_time_equals` | `_secure_compare` | Canonical updated to try `hmac.compare_digest` first |

**From `helpers.py` (2 functions):**

| Canonical name | Template name | Notes |
|---|---|---|
| `hmac_sha256` | `_hmac_sha256` | |
| `dns_name_wire_length` | `_dns_name_wire_length` | |

**From `dnswire.py` (1 function):**

| Canonical name | Template name | Notes |
|---|---|---|
| `_decode_name` | `_decode_name` | `DnsParseError` aliased; `_ord_byte`/`_message_length` collapsed via rename |

**From `cname_payload.py` (3 functions):**

| Canonical name | Template name | Notes |
|---|---|---|
| `_derive_file_bound_key` | `_derive_file_bound_key` | Template call sites pass globals explicitly |
| `_keystream_bytes` | `_keystream_bytes` | Template call sites pass `FILE_ID`, `PUBLISH_VERSION` |
| `_xor_bytes` | `_xor_bytes` | Uses `iter_byte_values`; template drops `_bytes_from_bytearray` |

**Total: 15 functions extracted.**

### What stays in the template

These functions are template-specific (client-only orchestration, global-binding
wrappers, or different enough that extraction is not worth it):

- **PY2 / type detection block** -- 10-line boilerplate, uses `try/except
  NameError` which is better for a standalone script than canonical's
  `sys.version_info` approach.  Will not drift.
- **Error classes**: `ClientError`, `RetryableTransport`, `DnsParseError`
  alias.
- **`_log`** -- template-specific logging.
- **`_encode_name`** -- 8 lines; raises `ClientError` directly on validation
  failure vs canonical's `ValueError`.  Too small to justify boundary wrapping.
- **`_enc_key(psk)`** -- thin 2-line wrapper; calls extracted
  `_derive_file_bound_key(psk, FILE_ID, PUBLISH_VERSION, PAYLOAD_ENC_KEY_LABEL)`.
- **`_mac_key(psk)`** -- thin 2-line wrapper; calls extracted
  `_derive_file_bound_key(psk, FILE_ID, PUBLISH_VERSION, PAYLOAD_MAC_KEY_LABEL)`.
- **`_expected_mac`** -- uses pre-derived key + reads globals; different
  structure from canonical `_mac_bytes`.
- All remaining client-specific functions: `_build_dns_query`,
  `_parse_response_for_cname`, `_extract_payload_text`, `_parse_slice_record`,
  `_decrypt_and_verify_slice`, `_reassemble_plaintext`,
  `_deterministic_output_path`, `_write_output_atomic`, argument parsers,
  `_send_dns_query`, `_retry_sleep`, `_validate_embedded_constants`,
  `_download_slices`, `_build_parser`, `_parse_runtime_args`, `main`.

### What gets dropped from the template

- **`_byte_at(raw, index)`** -- trivial helper (`_byte_value(raw[index])`).
  Inlined at remaining call sites (`_parse_slice_record`,
  `_expected_mac`).
- **`_bytes_from_bytearray`** -- not needed; canonical `_xor_bytes` uses
  `bytes(out)` directly.

### Template call-site changes

These template-specific functions update their calls to match extracted
function signatures:

- **`_enc_key(psk)`**: body becomes
  `return _derive_file_bound_key(psk, FILE_ID, PUBLISH_VERSION, PAYLOAD_ENC_KEY_LABEL)`.
- **`_mac_key(psk)`**: body becomes
  `return _derive_file_bound_key(psk, FILE_ID, PUBLISH_VERSION, PAYLOAD_MAC_KEY_LABEL)`.
- **`_decrypt_and_verify_slice`**: passes `FILE_ID, PUBLISH_VERSION` to
  `_keystream_bytes`.
- **`_parse_slice_record`**: wraps `_base32_decode_no_pad` call in
  `try/except ValueError` -> `ClientError(EXIT_PARSE, ...)`.
- **`_expected_mac`**: replaces `_byte_at(x, i)` with `_byte_value(x[i])`.
- **`_parse_slice_record`**: replaces `_byte_at(x, i)` with `_byte_value(x[i])`.

### Canonical module changes

Only inert additions -- no behavioral changes except one improvement:

- **`compat.py`**: Add extract markers around 9 functions.  Update
  `constant_time_equals` to try `hmac.compare_digest` first (strictly better;
  uses C implementation when available).
- **`helpers.py`**: Add extract markers around 2 functions.
- **`dnswire.py`**: Add extract markers around `_decode_name`.
- **`cname_payload.py`**: Add extract markers around 3 functions.

### New file: `dnsdle/extract.py`

```python
def extract_blocks(source_text):
    """Parse # __EXTRACT: name__ / # __END_EXTRACT__ markers.
    Returns dict mapping block names to source text fragments."""

def extract_blocks_from_file(filepath):
    """Read a file and return extract_blocks(content)."""

def rename_identifiers(source_text, rename_map):
    """Apply whole-word renames to source text using \\b boundaries."""
```

Small, focused module (~40-60 lines).

### Template structure after extraction

The assembled template (output of `build_client_template()`) has this order:

```
1. Shebang, encoding, imports
2. @@PLACEHOLDER@@ constants (BASE_DOMAINS, FILE_TAG, ...)
3. DNS wire constants
4. Payload constants, exit codes
5. PY2 / type detection block (stays in template)
6. ClientError, RetryableTransport, DnsParseError alias
7. _log
8. @@EXTRACTED_UTILS@@ -- all 15 extracted functions, dependency-ordered:
   a. compat utilities (encode/decode, byte helpers, base32, is_binary, secure_compare)
   b. helpers (hmac_sha256, dns_name_wire_length)
   c. _decode_name (from dnswire)
   d. cname_payload functions (_derive_file_bound_key, _keystream_bytes, _xor_bytes)
9. Template-specific functions (_encode_name, _enc_key, _mac_key, _expected_mac,
   _build_dns_query, _parse_response_for_cname, _extract_payload_text,
   _parse_slice_record, _decrypt_and_verify_slice, _reassemble_plaintext,
   output/arg-parsing helpers)
10. Lifted resolver source (existing _lift_resolver_source mechanism)
11. _discover_system_resolver (existing)
12. _TEMPLATE_SUFFIX (network I/O, download loop, CLI, main)
```

## Phases

### Phase 1: Add extract markers to canonical modules

Add `# __EXTRACT: name__` / `# __END_EXTRACT__` markers to `compat.py`,
`helpers.py`, `dnswire.py`, and `cname_payload.py`.  Update
`constant_time_equals` in `compat.py` to use `hmac.compare_digest` fast path.

No behavioral changes to any module.

### Phase 2: Build `dnsdle/extract.py`

Implement `extract_blocks()`, `extract_blocks_from_file()`, and
`rename_identifiers()`.

### Phase 3: Preserve legacy and restructure template

1. Copy `client_template.py` to `client_template_legacy.py`.
2. In `client_template.py`:
   - Remove the 15 extracted function definitions and the 2 dropped helpers
     from `_TEMPLATE_PREFIX`.
   - Add `DnsParseError` alias after `ClientError`/`RetryableTransport`.
   - Add `@@EXTRACTED_UTILS@@` placeholder.
   - Update template call sites (`_enc_key`, `_mac_key`,
     `_decrypt_and_verify_slice`, `_parse_slice_record`, `_expected_mac`).
   - Inline `_byte_at` at remaining call sites.
3. Update `build_client_template()` to:
   - Call extraction engine on `compat.py`, `helpers.py`, `dnswire.py`,
     `cname_payload.py`.
   - Apply rename map.
   - Concatenate in dependency order.
   - Replace `@@EXTRACTED_UTILS@@`.

### Phase 4: Verify functional equivalence

Generate clients before and after.  Confirm the generated output is
functionally equivalent (same behavior, same constants, same control flow).
Source text will differ slightly due to canonical vs template style differences
(e.g. `(first & 0x3F) << 8` vs `& DNS_POINTER_VALUE_MASK`).

## Affected Components

- `dnsdle/compat.py`: Add extract markers around 9 functions; update
  `constant_time_equals` to try `hmac.compare_digest` first.
- `dnsdle/helpers.py`: Add extract markers around 2 functions.
- `dnsdle/dnswire.py`: Add extract markers around `_decode_name`.
- `dnsdle/cname_payload.py`: Add extract markers around 3 functions.
- `dnsdle/extract.py` (new): Marker parser, file reader, identifier renamer.
- `dnsdle/client_template.py`: Remove 15 duplicated + 2 dropped function
  definitions from template strings; add DnsParseError alias and
  `@@EXTRACTED_UTILS@@` placeholder; update `build_client_template()` to use
  extraction engine; update template call sites for signature changes.
- `dnsdle/client_template_legacy.py` (new): Verbatim copy of original
  `client_template.py`.
- `dnsdle/client_generator.py`: No changes -- `build_client_template()` has
  same signature and return type.
