# Plan: Replace stager inline implementations with extracted functions

## Summary

Replace ~15 hand-written crypto/encoding/DNS functions in `stager_template.py`
with functions pulled via the existing `extract_functions()` system, and retire
the `__TEMPLATE_SOURCE__` sentinel extraction mechanism. This eliminates a
parallel maintenance path for crypto code and substantially shrinks the
minifier rename table.

## Problem

`stager_template.py` contains ~300 lines of inline Python (as string
constants) that re-implement functions already available via `__EXTRACT__`
markers in `compat.py`, `helpers.py`, `cname_payload.py`, and `dnswire.py`.
These inline versions are simplified copies (shorter names, fewer validations)
that must be kept in sync with the canonical implementations manually. A crypto
or encoding bug fix in the main modules does not propagate to the stager.

Additionally, the codebase has two separate source extraction mechanisms:

1. `extract.py` with `__EXTRACT__`/`__END_EXTRACT__` markers (universal client)
2. `_read_resolver_source()` with `# __TEMPLATE_SOURCE__` sentinel (stager)

The 157-entry rename table in `stager_minify.py` is tightly coupled to every
variable name in the stager template, including those inside the inline
functions that would be eliminated.

## Goal

After implementation:

- The stager assembles its encoding, crypto, base32, and DNS-decode functions
  via `extract_functions()`, the same mechanism the universal client uses.
- The `__TEMPLATE_SOURCE__` sentinel and `_read_resolver_source()` are gone;
  resolver functions are also pulled via `extract_functions()`.
- Stager-specific DNS/protocol functions (query builder, CNAME parser, payload
  extractor, UDP sender) remain as inline string constants since they have no
  canonical equivalent.
- The rename table covers only the stager-specific code and the extracted
  function/variable names, and is smaller than today.
- The stager one-liner still compiles, runs, and produces equivalent behavior.

## Design

### What gets extracted (via `extract_functions`)

These are functions the stager currently re-implements inline that have
identical-purpose extractable equivalents:

| Stager inline | Extracted from | Extract name |
|---|---|---|
| `_ab(v)` | `compat.py` | `encode_ascii` |
| `_ub(v)` | `compat.py` | `encode_utf8` |
| `_ib(v)` | `compat.py` | `encode_ascii_int` |
| (implicit) | `compat.py` | `decode_ascii` |
| (implicit) | `compat.py` | `is_binary` |
| `_b32d(text)` | `compat.py` | `base32_decode_no_pad` |
| `_secure_compare` | `compat.py` | `constant_time_equals` |
| (implicit) | `compat.py` | `base32_lower_no_pad` |
| (via `hmac.new`) | `helpers.py` | `hmac_sha256` |
| `_derive_slice_token` | `helpers.py` | `_derive_slice_token` |
| `_enc_key` / `_mac_key` | `cname_payload.py` | `_derive_file_bound_key` |
| `_keystream` | `cname_payload.py` | `_keystream_bytes` |
| `_xor` | `cname_payload.py` | `_xor_bytes` |
| `_decode_name` | `dnswire.py` | `_decode_name` |
| (via sentinel) | `resolver_linux.py` | `_load_unix_resolvers` |
| (via sentinel) | `resolver_windows.py` | `_run_nslookup`, `_parse_nslookup_output`, `_load_windows_resolvers` |

Entries marked "(implicit)" are dependencies of other extracted functions that
the stager currently does not need standalone equivalents for.

### What remains as stager-specific string constants

These functions have no canonical extractable equivalent -- the universal
client's versions are inside a monolithic extract block with much more
complexity (argparse, domain rotation, etc.):

- `_encode_name(labels)` -- trivial DNS name encoder (7 lines); rewrite
  `_ab(label)` calls to `encode_ascii(label)`
- `_build_query(qid, labels)` -- builds a DNS A query (10 lines)
- `_parse_cname(msg, qid, qname_labels)` -- extracts CNAME from response
  (30 lines); already uses `_decode_name` by name so extraction is transparent
- `_extract_payload(cname_labels)` -- strips CNAME suffix (7 lines)
- `_send_query(addr, pkt)` -- UDP send/recv (11 lines); rewrite `_ab` if used
- `_process_slice(ek, mk, si, payload_text)` -- rewrite internals to call
  extracted building blocks instead of inline copies (see below)

### Constants and type definitions

Extracted functions reference constants and types not currently in the stager.
Add a static preamble section (not template-filled):

```python
# Type compatibility
try:
    text_type = unicode
    binary_type = str
except NameError:
    text_type = str
    binary_type = bytes

# Extracted function dependencies
DnsParseError = ValueError
DNS_POINTER_TAG = 0xC0
PAYLOAD_ENC_KEY_LABEL = b"dnsdle-enc-v1|"
PAYLOAD_ENC_STREAM_LABEL = b"dnsdle-enc-stream-v1|"
PAYLOAD_MAC_KEY_LABEL = b"dnsdle-mac-v1|"
PAYLOAD_MAC_MESSAGE_LABEL = b"dnsdle-mac-msg-v1|"
PAYLOAD_MAC_TRUNC_LEN = 8
PAYLOAD_PROFILE_V1_BYTE = 0x01
PAYLOAD_FLAGS_V1_BYTE = 0x00
MAPPING_SLICE_LABEL = b"dnsdle:slice:v1|"
```

These are fixed values from `constants.py`. They are hardcoded in the stager
template (not pulled dynamically) because the stager template is a string that
gets placeholder-filled and minified -- it cannot import modules.

### Key call site changes in `_STAGER_SUFFIX`

The download-loop code currently calls the inline functions with
module-constant shortcuts. These change to call extracted functions with
explicit parameters:

```python
# Before                              # After
pk = _ub(psk)                         # (removed -- _derive_file_bound_key encodes internally)
ek = _enc_key(pk)                     ek = _derive_file_bound_key(psk, FILE_ID, PUBLISH_VERSION, PAYLOAD_ENC_KEY_LABEL)
mk = _mac_key(pk)                     mk = _derive_file_bound_key(psk, FILE_ID, PUBLISH_VERSION, PAYLOAD_MAC_KEY_LABEL)
_derive_slice_token(si)               _derive_slice_token(encode_ascii(MAPPING_SEED), PUBLISH_VERSION, si, SLICE_TOKEN_LEN)
```

### Rewritten `_process_slice`

Replace inline crypto with extracted building blocks. The function signature
stays the same (`ek, mk, si, payload_text`), but internals change:

- `_b32d(payload_text)` --> `base32_decode_no_pad(payload_text)`
- `_expected_mac(mk, si, ciphertext)` --> inline MAC message assembly using
  `hmac_sha256`, `encode_ascii`, `encode_ascii_int`, and
  `PAYLOAD_MAC_MESSAGE_LABEL` / `PAYLOAD_MAC_TRUNC_LEN`
- `_secure_compare(em, mac)` --> `constant_time_equals(em, mac)`
- `_keystream(ek, si, clen)` --> `_keystream_bytes(ek, FILE_ID, PUBLISH_VERSION, si, clen)`
- `_xor(ciphertext, stream)` --> `_xor_bytes(ciphertext, stream)`

This eliminates `_expected_mac`, `_secure_compare`, `_keystream`, `_xor`, and
`_b32d` as standalone stager functions.

### `build_stager_template()` rewrite

The assembly function changes from string concatenation + sentinel reads to
extract calls + string concatenation:

```python
def build_stager_template():
    encoding = extract_functions("compat.py", [
        "encode_ascii", "encode_utf8", "decode_ascii", "encode_ascii_int",
        "is_binary", "base32_lower_no_pad", "base32_decode_no_pad",
        "constant_time_equals",
    ])
    crypto = extract_functions("helpers.py", [
        "hmac_sha256", "_derive_slice_token",
    ])
    crypto += extract_functions("cname_payload.py", [
        "_derive_file_bound_key", "_keystream_bytes", "_xor_bytes",
    ])
    dns = extract_functions("dnswire.py", ["_decode_name"])
    resolvers = extract_functions("resolver_linux.py", [
        "_load_unix_resolvers",
    ])
    resolvers += extract_functions("resolver_windows.py", [
        "_run_nslookup", "_parse_nslookup_output", "_load_windows_resolvers",
    ])
    extracted = "\n\n".join(encoding + crypto + dns + resolvers)
    return (
        _STAGER_HEADER
        + extracted + "\n"
        + _STAGER_DNS_OPS
        + _STAGER_DISCOVER
        + _STAGER_SUFFIX
    )
```

### Rename table refresh

The rename table must be regenerated from the new stager source. Many entries
for removed inline functions and their internal variables can be dropped; new
entries are needed for extracted function names and their internal variables.
Build the new table by:

1. Assembling the template with no renames
2. Scanning for all identifier tokens longer than 2 characters
3. Assigning short replacements in longest-first order
4. Validating that no replacement collides with string literals or builtins

The table will be smaller because:
- ~15 removed function names and ~10 function-local variables disappear
- Extracted functions share variable names with the stager-specific code
  (some names like `blocks`, `counter` already needed renaming)

### Sentinel elimination

Remove `_read_resolver_source()` from `stager_template.py` and the
`# __TEMPLATE_SOURCE__` sentinel from `resolver_linux.py` and
`resolver_windows.py`. The resolver files already have `__EXTRACT__` markers
that fully cover the same code regions; the sentinel is now redundant.

### Risk: stager one-liner size

Extracted functions have slightly more validation code (e.g.,
`base32_decode_no_pad` validates non-empty / no-padding / lowercase). After
minification and zlib compression, the size increase should be small (estimated
50--100 bytes of base64, ~3% of a typical stager). This is an acceptable
tradeoff for crypto correctness guarantees.

## Affected Components

- `dnsdle/stager_template.py`: Major rewrite. Split `_STAGER_PRE_RESOLVER`
  into `_STAGER_HEADER` (imports + template constants + type defs + crypto
  constants) and `_STAGER_DNS_OPS` (stager-specific functions). Remove
  `_read_resolver_source()`. Rewrite `build_stager_template()` to use
  `extract_functions()`. Rewrite `_process_slice` and `_encode_name` to use
  extracted function names. Update call sites in `_STAGER_SUFFIX`.
- `dnsdle/stager_minify.py`: Regenerate `_RENAME_TABLE` from the new stager
  source. Remove entries for eliminated names, add entries for new names.
- `dnsdle/resolver_linux.py`: Remove `# __TEMPLATE_SOURCE__` sentinel line
  (line 4). No functional change.
- `dnsdle/resolver_windows.py`: Remove `# __TEMPLATE_SOURCE__` sentinel line
  (line 6). No functional change.
