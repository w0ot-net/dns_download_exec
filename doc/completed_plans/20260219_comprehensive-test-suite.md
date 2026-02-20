# Plan: Comprehensive Test Suite Additions

## Summary

Add dedicated unit tests for every production module that currently lacks
direct test coverage: `compat.py`, `helpers.py`, `stager_minify.py`,
`stager_generator.py`, and `stager_template.py`. This closes all
remaining test gaps in the `dnsdle/` package.

## Problem

Five production modules have no dedicated unit tests:

| Module | Lines | Gap |
|--------|-------|-----|
| `compat.py` | 118 | 13 exported symbols, type-conversion edge cases, base32 validation |
| `helpers.py` | 20 | 3 utility functions used across the codebase |
| `stager_minify.py` | 168 | 5-pass minification with comment stripping, renaming, indent reduction, semicolon joining |
| `stager_generator.py` | 157 | Template substitution, placeholder verification, compile-check, compress/encode round-trip, file I/O |
| `stager_template.py` | 305 | DNS protocol helpers, crypto helpers, slice processing embedded in template string |

These modules are exercised only indirectly through integration tests.
Silent regressions (especially in the minifier rename table or template
protocol logic) would be difficult to diagnose.

`client_template.py` (1,020 lines) is excluded from this plan. Its
internal functions overlap heavily with already-tested modules
(`dnswire.py`, `cname_payload.py`, `client_reassembly.py`), and its
unique logic (argument parsing, download orchestration, resolver
discovery) requires heavyweight subprocess/network faking that warrants
a separate plan.

`constants.py` is excluded because it exports only data (no logic).

## Goal

After implementation every `dnsdle/*.py` module with testable logic has a
corresponding `unit_tests/test_*.py` file. Each new test file covers:

- Happy-path behavior for all public functions.
- Every `StartupError` / `ValueError` / `TypeError` raise path.
- Edge cases: empty inputs, boundary values, type mismatches.
- Determinism where applicable (minifier, generator).

## Design

### 1. `unit_tests/test_compat.py` -- compat module tests

**`CompatTests`** class covering:

- `to_ascii_bytes`: text input, bytes passthrough, non-string TypeError.
- `to_utf8_bytes`: text input, bytes passthrough, non-string TypeError.
- `to_ascii_text`: text input, bytes input, non-string TypeError.
- `base32_lower_no_pad`: round-trip with `base32_decode_no_pad`, known
  vector, output is lowercase with no padding.
- `base32_decode_no_pad`: empty string ValueError, padding chars
  ValueError, uppercase ValueError, valid decode, invalid base32 chars
  ValueError.
- `byte_value`: int in range, int out of range ValueError, single-byte
  binary, multi-byte binary ValueError, wrong type TypeError.
- `iter_byte_values`: iterates bytes correctly, empty input.
- `constant_time_equals`: equal bytes True, different bytes False,
  different lengths False, non-binary TypeError.
- `to_ascii_int_bytes`: valid int, zero, negative ValueError, non-int
  ValueError.
- `is_binary`: bytes True, text False, int False.
- `key_text`: text passthrough, bytes decoded, non-ASCII bytes fallback,
  int coerced.

### 2. `unit_tests/test_helpers.py` -- helpers module tests

**`HelpersTests`** class covering:

- `dns_name_wire_length`: single label, multiple labels, empty label
  list (root only = 1).
- `labels_is_suffix`: exact match, proper suffix, non-suffix, suffix
  longer than full, empty suffix.
- `hmac_sha256`: known test vector (RFC 4231 or hand-computed), output
  is 32 bytes.

### 3. `unit_tests/test_stager_minify.py` -- minifier tests

**`StagerMinifyTests`** class covering:

- **Pass 1 (comment/blank removal):** input with blank lines and
  `# comment` lines stripped; non-comment lines preserved.
- **Pass 2 (variable renaming):** a snippet using a renamed identifier
  (e.g. `DOMAIN_LABELS`) is replaced with its short name; identifiers
  appearing inside string literals are NOT renamed (the rename table
  excludes `psk`, `resolver`, etc. because they appear in strings, but
  the regex `\b` approach means string-interior occurrences of
  *renamed* identifiers will also be replaced -- test that this is the
  intended deterministic behavior).
- **Pass 3 (indent reduction):** 4-space indent becomes 1-space; 8-space
  becomes 2-space; non-4-aligned indent preserved verbatim.
- **Pass 4 (semicolon join):** two consecutive non-block lines at the
  same indent joined with `;`; block starters (`if`, `for`, `def`, etc.)
  prevent joining; different indent levels prevent joining.
- **Determinism:** same input produces identical output across two calls.
- **Full template round-trip:** `minify(build_stager_template())` with
  placeholder values substituted compiles successfully under
  `compile(..., "exec")`.

### 4. `unit_tests/test_stager_generator.py` -- generator pipeline tests

**`StagerGeneratorTests`** class covering:

- **Happy path:** `generate_stager` with a valid config fake, template,
  publish item dict, and target OS returns a dict with expected keys
  (`source_filename`, `target_os`, `oneliner`, `minified_source`).
  The `oneliner` starts with `python3 -c` and contains `RESOLVER PSK`.
  The `minified_source` compiles.
- **Unreplaced placeholder:** template with an extra `@@UNKNOWN@@` marker
  raises `StartupError` with reason code `stager_generation_failed`.
- **Compile failure:** pass a template whose substitution result is
  syntactically invalid after minification; verify `StartupError`.
- **Round-trip integrity:** the happy-path `oneliner` payload can be
  manually base64-decoded and zlib-decompressed to recover
  `minified_source`.

**`GenerateStagersBatchTests`** class covering:

- **Happy path:** `generate_stagers` with matching artifacts and client
  items returns one stager per artifact, with `path` key set.
- **Missing client item:** artifact filename not in client items raises
  `StartupError`.

Both classes use a temp directory (setUp/tearDown with `tempfile.mkdtemp`
/ `shutil.rmtree`) for the managed dir so `_write_stager_file` can
execute.

Config fake is a minimal object with attributes:
`domain_labels_by_domain`, `response_label`, `dns_edns_size`.

### 5. `unit_tests/test_stager_template.py` -- template function tests

The stager template is a string, not importable. Tests `exec()` the
template source (after substituting placeholder values) into a fresh
namespace dict and exercise the resulting functions directly.

**`StagerTemplateFunctionTests`** class with a `setUp` that:
1. Calls `build_stager_template()`.
2. Substitutes all `@@PLACEHOLDER@@` markers with valid test values via
   `repr()`.
3. `exec()`s the result into `self.ns` (a dict).

Test methods exercise the namespace functions:

- `_encode_name` / `_decode_name` round-trip: encode labels, decode the
  result, verify labels match. Edge case: single label, many labels.
- `_decode_name` pointer decompression: hand-craft a message with a DNS
  compression pointer; verify correct label recovery.
- `_decode_name` error paths: truncated message, pointer loop (offset
  points to itself), invalid label type byte.
- `_build_query`: verify header structure (QR=0, OPCODE=0, QDCOUNT=1),
  EDNS OPT record present when `DNS_EDNS_SIZE > 512`, absent otherwise.
- `_parse_cname`: hand-craft a valid CNAME response; verify extracted
  target labels. Error paths: wrong response ID, missing QR flag, TC
  flag set, non-zero RCODE, QDCOUNT != 1, no CNAME answer.
- `_extract_payload`: valid CNAME target with matching suffix returns
  joined payload labels. Error paths: target shorter than suffix, suffix
  mismatch.
- `_b32d`: round-trip with base64.b32encode (stripped/lowered).
- `_secure_compare`: equal bytes True, different bytes False, different
  lengths False.
- `_xor`: known vector, same-length requirement.
- Crypto round-trip (`_enc_key`, `_mac_key`, `_keystream`,
  `_expected_mac`, `_process_slice`): encrypt a slice with known keys,
  build the binary record with MAC, pass to `_process_slice`, verify
  decrypted plaintext matches original. This validates the full
  encrypt-MAC-decrypt chain.

## Affected Components

- `unit_tests/test_compat.py` (NEW): tests for `dnsdle/compat.py`.
- `unit_tests/test_helpers.py` (NEW): tests for `dnsdle/helpers.py`.
- `unit_tests/test_stager_minify.py` (NEW): tests for
  `dnsdle/stager_minify.py`.
- `unit_tests/test_stager_generator.py` (NEW): tests for
  `dnsdle/stager_generator.py`.
- `unit_tests/test_stager_template.py` (NEW): tests for functions
  embedded in the stager template string.

## Execution Notes (2026-02-19)

All five test files implemented as designed. 90 new tests added (39 + 11 +
11 + 6 + 23). Full suite passes: 224 tests, 0 failures.

- `test_compat.py`: 39 tests covering all 13 exported symbols with
  happy-path, type-error, and edge-case coverage.
- `test_helpers.py`: 11 tests covering `dns_name_wire_length`,
  `labels_is_suffix`, and `hmac_sha256` including known vectors.
- `test_stager_minify.py`: 11 tests covering all 4 passes, determinism,
  and full template compile round-trip.
- `test_stager_generator.py`: 6 tests across `StagerGeneratorTests` and
  `GenerateStagersBatchTests` covering happy path, error paths, and
  round-trip integrity.
- `test_stager_template.py`: 23 tests exercising template functions via
  `exec()` into a namespace, including DNS encode/decode round-trips,
  pointer decompression, error paths, query building, CNAME parsing,
  payload extraction, and full encrypt-MAC-decrypt crypto chain.

No deviations from the plan.
