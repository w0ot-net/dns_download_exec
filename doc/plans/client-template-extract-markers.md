# Plan: Marker-based extraction for client_template.py

## Summary

Replace duplicated code in `client_template.py`'s template strings with a
marker-based extraction system that lifts function source from canonical modules
at generation time.  This eliminates ~20 copy-pasted functions that can silently
drift from their canonical counterparts in `compat.py`, `helpers.py`,
`dnswire.py`, and `cname_payload.py`.

## Problem

`client_template.py` contains `_TEMPLATE_PREFIX` / `_TEMPLATE_SUFFIX` —
large string literals that become a standalone generated client script.  Because
the generated client has zero imports from `dnsdle`, every shared utility
(byte helpers, DNS wire encoding/decoding, crypto routines) is duplicated
inline.  If the canonical version changes, the template copy silently drifts.

There is already a precedent: `_lift_resolver_source()` reads
`resolver_linux.py` / `resolver_windows.py` at generation time and splices
the portion after a `# __TEMPLATE_SOURCE__` sentinel into the template output.
This plan generalises that pattern.

## Goal

After implementation:

1. Each shared function exists in exactly one place (its canonical module).
2. The canonical modules contain `# __EXTRACT: block_name__` /
   `# __END_EXTRACT__` markers around liftable regions.
3. A new `extract.py` module reads marked blocks from source files and
   returns them as named text fragments.
4. `build_client_template()` splices extracted fragments into the template,
   eliminating inline duplication.
5. The old `client_template.py` is preserved as `client_template_legacy.py`
   (not imported anywhere; kept for reference/rollback).
6. Generated client output is byte-identical to the current output for the
   same inputs (verified by running the generator before and after).

## Design

### Marker syntax

```python
# __EXTRACT: block_name__
def some_function(...):
    ...
# __END_EXTRACT__
```

Rules:
- Block names are `[a-z][a-z0-9_]*`.
- Everything between the marker line (exclusive) and the end marker line
  (exclusive) is captured verbatim, including blank lines.
- A source file may contain multiple extract blocks; they must not nest.
- The marker lines themselves are inert comments — they do not affect the
  module's normal operation or imports.

### Naming reconciliation

The canonical modules use public names (`byte_value`, `encode_ascii`, etc.)
while the template uses underscore-prefixed private names (`_byte_value`,
`_to_ascii_bytes`, etc.).  Two of the functions also differ in more than
prefix (`encode_ascii` vs `_to_ascii_bytes`).

Strategy: **rename at the extraction site, not in the canonical module.**
`build_client_template()` applies a rename map after extraction:

```python
_RENAMES = {
    "encode_ascii": "_to_ascii_bytes",
    "encode_utf8": "_to_utf8_bytes",
    "decode_ascii": "_to_ascii_text",
    "encode_ascii_int": "_to_ascii_int_bytes",
    "byte_value": "_byte_value",
    "constant_time_equals": "_secure_compare",
    "hmac_sha256": "_hmac_sha256",
    "dns_name_wire_length": "_dns_name_wire_length",
    ...
}
```

Each rename is a whole-word replacement applied to the extracted text.  This
also catches call-sites within the extracted block (e.g. `_keystream_bytes`
calling `encode_ascii_int` — the rename turns it into `_to_ascii_int_bytes`).

### Error-style reconciliation

The canonical modules raise `ValueError` / `DnsParseError`; the template
raises `ClientError(EXIT_CODE, "phase", "message")`.  These functions are
*not* the same code — they have different error-handling contracts.

For functions where the implementations actually diverge (different error
types, different validation, different signatures), we do **not** extract.
Those stay in the template as-is.  The extraction targets only the functions
whose logic is identical or near-identical modulo naming.

After careful comparison, the functions that are truly extractable (identical
logic, only names differ) are the **low-level utilities** that have no
error-raising paths or whose error paths already match:

| Template function | Canonical source | Module |
|---|---|---|
| `_to_ascii_bytes` | `encode_ascii` | `compat.py` |
| `_to_utf8_bytes` | `encode_utf8` | `compat.py` |
| `_to_ascii_text` | `decode_ascii` | `compat.py` |
| `_to_ascii_int_bytes` | `encode_ascii_int` | `compat.py` |
| `_byte_value` | `byte_value` | `compat.py` |
| `_bytes_from_bytearray` | (new extract block) | `compat.py` |
| `_hmac_sha256` | `hmac_sha256` | `helpers.py` |
| `_dns_name_wire_length` | `dns_name_wire_length` | `helpers.py` |

The following **cannot** be cleanly extracted because their template versions
have meaningfully different implementations:

| Template function | Why it diverges |
|---|---|
| `_secure_compare` | Uses `hmac.compare_digest` with try/except fallback; canonical version (`constant_time_equals`) does type-check + `is_binary` gate instead |
| `_encode_name` | Raises `ClientError` on validation failure; canonical raises `ValueError` |
| `_decode_name` | Raises `ClientError`; canonical raises `DnsParseError`; canonical uses `_ord_byte` wrapper |
| `_base32_decode_no_pad` | Raises `ClientError`; canonical raises `ValueError`; slightly different validation |
| `_keystream_bytes` | Template version reads `FILE_ID`/`PUBLISH_VERSION` from module globals; canonical takes them as parameters |
| `_xor_bytes` | Template uses `_byte_at` + `_bytes_from_bytearray`; canonical uses `iter_byte_values` + `bytes(bytearray)` |
| `_enc_key` / `_mac_key` | Template reads globals; canonical takes parameters |
| `_expected_mac` | Template reads globals; canonical takes parameters |
| `_parse_slice_record` | Template-specific error handling |
| `_decrypt_and_verify_slice` | Template-specific composition |
| `_extract_payload_text` | Template-specific error handling |

### What we actually extract

Given the divergence analysis, the extractable set is the **8 low-level
utility functions** listed above.  This is still a meaningful win: these are
the trickiest to keep in sync (Python 2/3 byte-handling) and the most likely
to need a coordinated change.

The remaining ~14 template-specific functions stay in the template strings.
They are client-specific logic (different error model, global-reading
signatures) and were never truly "duplicated" — they were re-implementations
with different contracts.

### Module-level constants extraction

The PY2/text_type/binary_type/integer_types block at the top of the template
is also extractable.  We add an extract block in `compat.py` covering lines
7-16 and splice it into the template prefix.

### New file: `dnsdle/extract.py`

```python
def extract_blocks(source_text):
    """Parse # __EXTRACT: name__ / # __END_EXTRACT__ pairs from source text.
    Returns dict mapping block names to source fragments."""

def extract_blocks_from_file(filepath):
    """Read file, return extract_blocks(content)."""

def rename_identifiers(source_text, rename_map):
    """Apply whole-word renames to extracted source text."""
```

This is a small, focused module (~40-60 lines).  It generalises the existing
`_lift_resolver_source()` sentinel approach.

### Changes to `build_client_template()`

The existing `_lift_resolver_source()` continues to work as-is for the
resolver files (which use a different marker convention).

`build_client_template()` gains a new step between prefix and suffix
assembly:

1. Call `extract_blocks_from_file()` on `compat.py` and `helpers.py`.
2. Apply `rename_identifiers()` with the rename map.
3. Concatenate the renamed blocks in dependency order.
4. Insert the result into the template between the constants/imports section
   and the first template-only function.

The template strings (`_TEMPLATE_PREFIX` / `_TEMPLATE_SUFFIX`) shrink: the
8 extracted functions and the PY2 compat block are removed from the string
literals, replaced by a single `@@EXTRACTED_UTILS@@` placeholder.

### Preserving the legacy template

`client_template.py` is copied to `client_template_legacy.py` with no
modifications.  The legacy file is not imported by anything and serves as
a reference.  The new `client_template.py` has the extracted functions removed
from its string literals and the new `@@EXTRACTED_UTILS@@` placeholder added.

## Phases

### Phase 1: Add extract markers to canonical modules

Add `# __EXTRACT__` markers to `compat.py` and `helpers.py` around the 8
target functions plus the PY2/type-detection block.

Files touched: `dnsdle/compat.py`, `dnsdle/helpers.py`.

### Phase 2: Build `dnsdle/extract.py`

Implement `extract_blocks()`, `extract_blocks_from_file()`, and
`rename_identifiers()`.  Keep it minimal.

Files touched: `dnsdle/extract.py` (new).

### Phase 3: Preserve legacy and restructure template

1. Copy `client_template.py` to `client_template_legacy.py`.
2. In `client_template.py`:
   - Remove the 8 duplicated function definitions and the PY2 compat block
     from `_TEMPLATE_PREFIX`.
   - Insert `@@EXTRACTED_UTILS@@` placeholder where they were.
3. Update `build_client_template()` to:
   - Call the extraction engine on `compat.py` and `helpers.py`.
   - Apply renames.
   - Replace `@@EXTRACTED_UTILS@@` in the assembled template.

Files touched: `dnsdle/client_template.py`, `dnsdle/client_template_legacy.py`
(new, copy of original).

### Phase 4: Verify byte-identical output

Run the client generator before and after, diff the generated output to
confirm identical results.  This is the acceptance gate.

## Affected Components

- `dnsdle/compat.py`: Add `# __EXTRACT__` markers around PY2 detection block,
  `encode_ascii`, `encode_utf8`, `decode_ascii`, `encode_ascii_int`,
  `byte_value`.
- `dnsdle/helpers.py`: Add `# __EXTRACT__` markers around `hmac_sha256` and
  `dns_name_wire_length`.
- `dnsdle/extract.py` (new): Marker parser, file reader, identifier renamer.
- `dnsdle/client_template.py`: Remove duplicated functions from template
  strings; add `@@EXTRACTED_UTILS@@` placeholder; update
  `build_client_template()` to use extraction engine.
- `dnsdle/client_template_legacy.py` (new): Verbatim copy of original
  `client_template.py`.
- `dnsdle/client_generator.py`: No changes required — it calls
  `build_client_template()` which has the same signature and return type.
