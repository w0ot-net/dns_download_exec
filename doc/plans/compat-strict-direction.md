# Plan: Strict Directional Compat Helpers

## Summary

Rename the polymorphic `to_ascii_bytes` / `to_ascii_text` / `to_utf8_bytes` /
`to_ascii_int_bytes` helpers to `encode_ascii` / `decode_ascii` / `encode_utf8` /
`encode_ascii_int`, signalling one-way conversion. Drop the explicit `TypeError`
branches (let `AttributeError` propagate for truly wrong types). Remove ~5
passthrough calls in `cname_payload.py` and `client_payload.py` where the input is
provably already bytes. Make `FILE_ID_PREFIX` a bytes literal so its only call site
needs no conversion.

## Problem

The current helpers accept *both* text and bytes, which:

1. **Hides caller intent.** A call like `to_ascii_bytes(ciphertext_bytes)` looks
   like a conversion but is actually a no-op passthrough because the value is
   already bytes. The reader cannot tell at a glance whether real work happens.
2. **Encourages dead calls.** Five call sites in `cname_payload.py` and
   `client_payload.py` pass known-bytes values through `to_ascii_bytes` for no
   effect.
3. **Uses ambiguous naming.** `to_ascii_bytes` does not indicate *from what* — the
   name reads the same whether you are encoding text or passing through bytes.
4. **Carries unnecessary branches.** Each helper has three branches (binary
   passthrough, text conversion, TypeError). The TypeError can never fire in
   practice and can be replaced by a natural `AttributeError` from `.encode()` /
   `.decode()`, dropping one branch per function.
5. **`FILE_ID_PREFIX`** is declared as a text literal but only ever used in byte
   context (HMAC input concatenated with other bytes). It requires a conversion
   call that would disappear if the constant were `b"..."`.

## Goal

- Helpers renamed to signal direction: `encode_ascii`, `decode_ascii`,
  `encode_utf8`, `encode_ascii_int`.
- Each function body drops the explicit `text_type` / `binary_type` + `TypeError`
  three-branch pattern to a two-branch pattern (isinstance check for the Py2 `str`
  edge case, then direct `.encode()` / `.decode()`).
- No passthrough calls on values whose type is provably bytes at the call site.
- `FILE_ID_PREFIX` is a bytes literal; its call site concatenates directly.
- All call sites and imports updated in the same commit.
- No test files modified.

## Design

### `dnsdle/compat.py` — function renames and body simplification

Rename and simplify each conversion helper. The isinstance check for `binary_type`
(Py2 `str` passthrough) or `text_type` (Py3 `str` passthrough) stays — it is the
minimal Py2/3 `str`-means-different-things bridge and cannot be removed without
`unicode_literals`. The explicit `text_type` / `binary_type` guard and `TypeError`
raise are dropped; an invalid type hits `.encode()` / `.decode()` and raises
`AttributeError` naturally.

```python
# before
def to_ascii_bytes(value):
    if isinstance(value, binary_type):
        return value
    if isinstance(value, text_type):
        return value.encode("ascii")
    raise TypeError("value must be text or bytes")

# after
def encode_ascii(value):
    if isinstance(value, binary_type):
        return value
    return value.encode("ascii")
```

Same pattern for `decode_ascii` (drop `binary_type` guard + TypeError),
`encode_utf8` (drop `text_type` guard + TypeError), and `encode_ascii_int`
(internal call rename only).

Internal callers (`base32_lower_no_pad`, `base32_decode_no_pad`, `key_text`,
`encode_ascii_int`) update their calls to the new names.

### `dnsdle/constants.py` — `FILE_ID_PREFIX` to bytes

```python
# before
FILE_ID_PREFIX = "dnsdle:file-id:v1|"

# after
FILE_ID_PREFIX = b"dnsdle:file-id:v1|"
```

### `dnsdle/publish.py` — remove `FILE_ID_PREFIX` conversion

```python
# before
file_id_input = to_ascii_bytes(FILE_ID_PREFIX) + to_ascii_bytes(publish_version)

# after
file_id_input = FILE_ID_PREFIX + encode_ascii(publish_version)
```

Import changes from `to_ascii_bytes` to `encode_ascii`.

### `dnsdle/client_payload.py` — remove passthrough, drop import

`parse_payload_record` receives `record_bytes` from `base32_decode_no_pad()`, which
always returns `bytes`. The `to_ascii_bytes(record_bytes)` call is a no-op.

```python
# before
def parse_payload_record(record_bytes):
    raw = to_ascii_bytes(record_bytes)
    ...raw...

# after — use record_bytes directly, drop `raw` alias
def parse_payload_record(record_bytes):
    ...record_bytes...
```

`to_ascii_bytes` / `encode_ascii` import removed entirely (no remaining callers in
this file).

### `dnsdle/cname_payload.py` — remove 3 passthrough calls, rename rest

Three `to_ascii_bytes` calls on values that are provably already bytes:

| Line | Expression | Why it is bytes |
|------|-----------|-----------------|
| 97   | `to_ascii_bytes(slice_bytes)` in `_encrypt_slice_bytes` | callers pass `bytes` from `_xor_bytes` / `build_slice_record` |
| 127  | `to_ascii_bytes(ciphertext_bytes)` in `_mac_bytes` | callers pass `bytes` ciphertext |
| 184  | `to_ascii_bytes(slice_bytes)` in `build_slice_record` | callers pass `bytes` from `_chunk_bytes` |

Remove these three calls and use the parameter directly. Remaining
`to_ascii_bytes` calls (on `file_id`, `publish_version` — text identifiers) rename
to `encode_ascii`. Other renames: `to_utf8_bytes` → `encode_utf8`,
`to_ascii_int_bytes` → `encode_ascii_int`.

### `dnsdle/client_generator.py` — rename imports and calls

```python
# imports
from dnsdle.compat import encode_ascii
from dnsdle.compat import decode_ascii

# line 188 (validation guard)
decode_ascii(source)

# line 204 (binary write)
handle.write(encode_ascii(source_text))
```

### `dnsdle/__init__.py` — rename import and call

```python
from dnsdle.compat import encode_ascii
...
(artifact["filename"], encode_ascii(artifact["source"]))
```

### `dnsdle/dnswire.py` — rename imports and calls

```python
from dnsdle.compat import encode_ascii
from dnsdle.compat import decode_ascii

# _to_label_bytes
raw = encode_ascii(label)

# _decode_name
label = decode_ascii(raw)
```

### `dnsdle/mapping.py` — rename imports and calls

```python
from dnsdle.compat import encode_ascii
from dnsdle.compat import encode_ascii_int

# _derive_slice_digest
slice_index_bytes = encode_ascii_int(slice_index, "slice_index")

# _derive_file_tag, _derive_slice_token
publish_version_bytes = encode_ascii(publish_version)

# apply_mapping
seed_bytes = encode_ascii(config.mapping_seed)
```

### `dnsdle/client_reassembly.py` — rename import and call

```python
from dnsdle.compat import decode_ascii
...
expected_hash = decode_ascii(plaintext_sha256).lower()
```

## Affected Components

- `dnsdle/compat.py`: rename four functions, simplify bodies (drop TypeError
  branch), update internal callers.
- `dnsdle/constants.py`: change `FILE_ID_PREFIX` from text to bytes literal.
- `dnsdle/client_payload.py`: remove `to_ascii_bytes` passthrough call and import.
- `dnsdle/cname_payload.py`: remove 3 passthrough calls on known-bytes values;
  rename remaining 5 conversion imports/calls.
- `dnsdle/client_generator.py`: rename 2 imports and 2 call sites.
- `dnsdle/__init__.py`: rename 1 import and 1 call site.
- `dnsdle/dnswire.py`: rename 3 imports and 2 call sites.
- `dnsdle/mapping.py`: rename 3 imports and 4 call sites.
- `dnsdle/publish.py`: rename 1 import, remove 1 conversion call
  (`FILE_ID_PREFIX`).
- `dnsdle/client_reassembly.py`: rename 1 import and 1 call site.
