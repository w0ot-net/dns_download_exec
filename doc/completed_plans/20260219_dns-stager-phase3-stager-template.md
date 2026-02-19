# Plan: Phase 3 -- Stager Template

## Summary

Create the stager template: a readable Python script implementing the
minimum viable DNS download protocol needed to retrieve one file via CNAME
records. The template is self-contained and can be validated independently
(compile-check, ASCII-only) before minification or integration.

## Prerequisites

- None for code changes (new module only).
- Conceptually depends on the protocol defined in the existing codebase
  (`dnsdle/cname_payload.py`, `dnsdle/client_reassembly.py`,
  `dnsdle/dnswire.py`).

## Goal

After implementation:

- `dnsdle/stager_template.py` contains a readable Python stager script
  with placeholder constants.
- The template implements the full download-verify-exec chain using the
  same crypto and wire protocol as the server.
- The template is written in a disciplined coding style that enables
  mechanical minification in Phase 4.
- The template `compile()`s successfully after placeholder substitution
  with representative values.

## Design

### 1. Stager template (`dnsdle/stager_template.py`)

A module exporting a single function:

```python
def build_stager_template():
    """Return the stager template source as a string."""
```

The template is a complete Python script stored as a string constant. It
uses `@@PLACEHOLDER@@` substitution markers (same pattern as the client
template in `dnsdle/client_template.py`).

**Included protocol operations:**

- Raw UDP DNS query construction (QTYPE A, RD flag, EDNS OPT record when
  `dns_edns_size > 512`).
- CNAME response parsing: header validation, question section skip, answer
  section CNAME RDATA extraction with DNS name decompression (pointer
  support). Walk answer RRs only; ignore remaining message bytes after the
  last answer RR (authority/additional sections and trailing data are not
  consumed -- recursive resolvers routinely append extra sections).
- Payload label extraction from CNAME target (strip response_label and
  domain suffix).
- Base32 decode (lowercase alphabet, no padding).
- HMAC-SHA256 key derivation: `enc_key` and `mac_key` from PSK + file
  identity, using the same label constants as `dnsdle/cname_payload.py`.
- XOR stream keystream generation and decryption.
- MAC verification (truncated 8-byte HMAC-SHA256).
- Binary record parsing (profile byte, flags byte, payload, MAC).
- Slice reassembly into compressed stream, zlib decompression, SHA-256
  final verification.
- `exec()` handoff: sets `sys.argv` and calls `exec(client_source)`.

**Excluded (to minimize size):**

- Retry logic (each slice attempted once; any failure is fatal).
- CLI argument parsing (positional `sys.argv` only).
- Logging or progress output.
- Descriptive error messages (raw exceptions propagate).
- System resolver discovery (resolver is a required positional argument).
- Domain rotation (uses first configured domain only).

**Embedded constants** (filled at generation time):

- `@@DOMAIN_LABELS@@`: domain label tuple for `config.domains[0]`
  (lexicographically first configured domain).
- `@@FILE_TAG@@`: file_tag of the client publish item.
- `@@FILE_ID@@`: file_id.
- `@@PUBLISH_VERSION@@`: publish_version.
- `@@TOTAL_SLICES@@`: total_slices.
- `@@COMPRESSED_SIZE@@`: compressed_size.
- `@@PLAINTEXT_SHA256_HEX@@`: plaintext_sha256.
- `@@SLICE_TOKENS@@`: ordered slice_tokens tuple.
- `@@RESPONSE_LABEL@@`: response_label.
- `@@DNS_MAX_LABEL_LEN@@`: dns_max_label_len.
- `@@DNS_EDNS_SIZE@@`: dns_edns_size.

**Runtime arguments:** `<resolver_ip> <psk> [extra_client_args...]`

**Exec handoff:** After downloading and verifying the client source, the
stager sets `sys.argv = ['s', '--psk', psk, '--resolver', resolver] +
extra_args` and calls `exec(client_source)`. The client's
`if __name__ == "__main__"` block fires and runs to completion.

**Python 2.7/3.x compatibility:** The stager uses `b"..."` byte literals
for all wire and crypto operations and handles the str/bytes split for
`sys.argv` values with a compact `encode` guard. No `print` calls (no
output).

**Template coding discipline** (enables mechanical minification in Phase 4):

- Every statement on its own line (no multi-line expressions).
- Comments always on their own line (never inline after code).
- Consistent 4-space indentation.
- No multi-line string literals containing `#`.
- No nested functions or closures.
- No decorators, `with` statements, or comprehensions spanning lines.

## Affected Components

- `dnsdle/stager_template.py` (NEW): stager template source string with
  placeholder constants. Exports `build_stager_template()`.

## Execution Notes

Executed 2026-02-19.

All plan items implemented as specified:

- Created `dnsdle/stager_template.py` with `build_stager_template()`
  returning the complete stager template as a string constant.
- Template implements all included protocol operations: DNS query
  construction (QTYPE A, RD, EDNS OPT), CNAME response parsing with
  pointer decompression (answer RRs only, trailing sections ignored),
  payload label extraction, base32 decode, HMAC-SHA256 key derivation
  (enc_key/mac_key using identical label constants as cname_payload.py),
  XOR keystream decryption, truncated MAC verification, binary record
  parsing, slice reassembly with zlib decompression and SHA-256
  verification, and exec() handoff with sys.argv setup.
- All excluded items confirmed absent: no retry logic, no argparse, no
  logging/print, no descriptive error messages, no system resolver
  discovery, no domain rotation.
- All 11 `@@PLACEHOLDER@@` markers present and substitutable.
- Coding discipline verified: one statement per line, comments on own
  lines only, 4-space indentation, no nested functions, no `with`
  statements, no decorators, no multi-line comprehensions.
- Python 2.7/3.x compatibility via bytearray for byte access, `_ab()`
  / `_ub()` encode guards, `chr()` label reconstruction, and
  `isinstance(x, str)` exec decode guard.
- Template compiles successfully after representative placeholder
  substitution (297 lines, 8128 chars).

No deviations from plan.
