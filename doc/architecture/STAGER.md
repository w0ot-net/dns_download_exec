# Stager Pipeline

This document defines the stager generation pipeline that produces one-liner
bootstrap scripts for each payload file.

A stager downloads the universal client via DNS, verifies it, and `exec`s it
with per-payload metadata passed via `sys.argv`.

---

## Overview

Each payload file gets a single stager: a `python3 -c` one-liner that
self-extracts a minified Python script.  The script downloads the universal
client (itself published as a DNS file), reassembles it, verifies integrity,
then executes it with the target payload's metadata so the client downloads
the actual payload.

The pipeline has three modules executed in order:
1. `dnsdle/stager_template.py` -- assemble the full stager source template.
2. `dnsdle/stager_minify.py` -- reduce the source to minimal size.
3. `dnsdle/stager_generator.py` -- substitute placeholders, minify, encode,
   and write one-liner files.

---

## Template Assembly

`build_stager_template()` concatenates five sections into a single Python
source string:

1. **`_STAGER_HEADER`** -- shebang, imports, `@@PLACEHOLDER@@` template
   constants, Python 2/3 type compatibility definitions (`text_type`,
   `binary_type`), and hardcoded crypto/DNS constants (`DnsParseError`,
   `DNS_POINTER_TAG`, `PAYLOAD_*_LABEL`, `PAYLOAD_MAC_TRUNC_LEN`,
   `MAPPING_SLICE_LABEL`).
2. **Extracted functions** -- encoding, crypto, DNS decoding, and resolver
   discovery functions pulled at generation time via `extract_functions()`
   from `compat.py`, `helpers.py`, `cname_payload.py`, `dnswire.py`,
   `resolver_linux.py`, and `resolver_windows.py`.  This is the same
   extraction mechanism the universal client uses (`__EXTRACT__` /
   `__END_EXTRACT__` markers).
3. **`_STAGER_DNS_OPS`** -- stager-specific functions with no canonical
   extractable equivalent: `_encode_name`, `_build_query`, `_parse_cname`,
   `_extract_payload`, `_send_query`, and `_process_slice`.  These call the
   extracted building blocks (e.g. `encode_ascii`, `hmac_sha256`,
   `_keystream_bytes`) rather than maintaining inline copies.
4. **`_STAGER_DISCOVER`** -- the `_discover_resolver()` dispatcher that calls
   the Windows or Linux loader based on `sys.platform` and resolves the first
   working address with IPv4/IPv6 fallback.
5. **`_STAGER_SUFFIX`** -- runtime entry point: CLI argument parsing,
   resolver setup, download loop, reassembly/verification, `sys.argv`
   reconstruction, and `exec()` of the universal client.

---

## Placeholder Substitution

The template contains 19 `@@PLACEHOLDER@@` tokens replaced with `repr()`-encoded
Python literals at generation time.

**Client download params** (universal client's own publish metadata, same for
all stagers):

| Placeholder | Source |
|---|---|
| `@@DOMAIN_LABELS@@` | `config.domain_labels_by_domain[0]` |
| `@@FILE_TAG@@` | client publish item `file_tag` |
| `@@FILE_ID@@` | client publish item `file_id` |
| `@@PUBLISH_VERSION@@` | client publish item `publish_version` |
| `@@TOTAL_SLICES@@` | client publish item `total_slices` |
| `@@COMPRESSED_SIZE@@` | client publish item `compressed_size` |
| `@@PLAINTEXT_SHA256_HEX@@` | client publish item `plaintext_sha256` |
| `@@MAPPING_SEED@@` | `config.mapping_seed` |
| `@@SLICE_TOKEN_LEN@@` | client publish item `slice_token_len` |
| `@@RESPONSE_LABEL@@` | `config.response_label` |
| `@@DNS_EDNS_SIZE@@` | `config.dns_edns_size` |
| `@@PSK@@` | `config.psk` |
| `@@DOMAINS_STR@@` | comma-joined `config.domains` |
| `@@FILE_TAG_LEN@@` | `config.file_tag_len` |

**Payload params** (per-file, passed to universal client via `sys.argv`):

| Placeholder | Source |
|---|---|
| `@@PAYLOAD_PUBLISH_VERSION@@` | payload publish item `publish_version` |
| `@@PAYLOAD_TOTAL_SLICES@@` | payload publish item `total_slices` |
| `@@PAYLOAD_COMPRESSED_SIZE@@` | payload publish item `compressed_size` |
| `@@PAYLOAD_SHA256@@` | payload publish item `plaintext_sha256` |
| `@@PAYLOAD_TOKEN_LEN@@` | payload publish item `slice_token_len` |

After substitution, a regex scan for any remaining `@@[A-Z0-9_]+@@` pattern
triggers a fatal `StartupError("stager_generation_failed")`.

---

## Minification

`minify(source)` applies a deterministic 5-pass transformation pipeline:

1. **Strip comment lines** -- remove all lines whose stripped content starts
   with `#`.
2. **Strip blank lines** -- remove empty lines.
3. **Protect strings and rename identifiers** -- extract all string literals
   (single/double quoted, optional `b` prefix) into numbered placeholders
   (`__S0__`, `__S1__`, ...) to prevent corruption, then auto-generate a
   longest-first rename table from identifiers found in the source.  The
   generator collects all identifiers, subtracts Python keywords, builtins,
   stdlib module names, attribute names (after `.`), and placeholder names,
   selects candidates with `len > 2`, sorts by `(-len, name)`, and assigns
   deterministic short names (`a`..`z`, `A`..`Z`, `aa`..`az`, ...).  Each
   rename is applied via compiled `\b`-bounded regex.  String literals are
   restored after renaming.
4. **Reduce indentation** -- convert 4-space indentation to 1 space per
   nesting level.
5. **Semicolon-join** -- join consecutive same-indent non-block lines with `;`.
   Block starters (`if`, `for`, `while`, `try`, `except`, `else`, `elif`,
   `finally`, `def`, `return`, `with`, `break`, `continue`) are never joined.
   Lines ending with `,` or `(` are not joined.  Lines starting with an
   operator (`+`, `-`, `*`, `/`, `|`, `&`, `^`, `%`, `~`) or `)` are not
   joined, preserving multiline parenthesized expressions.

Same input always produces same output.

---

## Encoding and Verification

After minification, `generate_stager()` performs:

1. **Compile check** -- `compile(minified, "<stager>", "exec")`.  Syntax
   errors raise `StartupError("stager_generation_failed")`.
2. **ASCII encode** -- `minified.encode("ascii")`.
3. **Compress** -- `zlib.compress(bytes, 9)`.
4. **Base64 encode** -- `base64.b64encode(compressed)`.
5. **Wrap** -- produce the final one-liner:
   ```
   python3 -c "import base64,zlib;exec(zlib.decompress(base64.b64decode('...')))"
   ```

---

## Output Contract

One `.1-liner.txt` file per payload, written to the managed output directory
(`<client_out_dir>/dnsdle_v1/`).

Filename derivation: `<payload_basename>.1-liner.txt` (extension stripped from
source filename, `.1-liner.txt` appended).

Write is atomic: content is written to a `.tmp` file first, then renamed.
On write failure, the `.tmp` file is cleaned up and a
`StartupError("stager_generation_failed")` is raised.

---

## Runtime Behavior

When a user executes a stager one-liner, the extracted script:

1. Parses `--psk` and `--resolver` from `sys.argv` if present; falls back to
   the embedded `PSK` constant and system resolver discovery.
2. Detects `--verbose` in `sys.argv` without consuming it, so it is forwarded
   unchanged to the universal client.  When active, the stager emits to stderr:
   - `resolver <addr>` -- resolved DNS address
   - `[N/T]` -- per-slice progress after each successful fetch
   - `retry N` -- slice index being retried on exception
3. Downloads the universal client by iterating over all slice indices
   sequentially, querying `<slice_token>.<file_tag>.<domain_labels>` for each.
4. Enforces a 60-second no-progress deadline per slice (resets after each
   successful acquisition).  Retries on any exception with 1-second sleep.
5. After all slices are collected, verifies compressed size matches
   `COMPRESSED_SIZE`, decompresses, and verifies plaintext SHA-256 matches
   `PLAINTEXT_SHA256_HEX`.
6. Reconstructs `sys.argv` with per-payload metadata and calls
   `exec(client_source)` to invoke the universal client.

---

## Invariants

1. No unreplaced `@@PLACEHOLDER@@` tokens may remain after substitution.
2. Minified stager source must pass `compile()` before encoding.
3. Final one-liner is ASCII-clean.
4. File write is atomic (`.tmp` + rename); partial files are never left behind.
5. Minification is deterministic: same template + same inputs = same output.

---

## Related Docs

- `doc/architecture/CLIENT_GENERATION.md`
- `doc/architecture/SERVER_RUNTIME.md`
- `doc/architecture/CRYPTO.md`
