# Plan: Strict Directional Compat Helpers

## Summary

Add `from __future__ import unicode_literals` to all 18 modules under `dnsdle/`,
then rename and simplify the compat conversion helpers. With `unicode_literals`,
every string literal produces `text_type` on Py2, so `encode_ascii` and
`encode_utf8` collapse to one-liners with zero isinstance checks. `decode_ascii`
retains one isinstance check because `str.decode()` does not exist on Py3. Remove
~5 passthrough calls on known-bytes values. Make `FILE_ID_PREFIX` a bytes literal.

Branch count before: 9 (3 per encode/decode/encode_utf8).
Branch count after: 2 (decode_ascii + config PSK normalization).

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
   passthrough, text conversion, TypeError). Without `unicode_literals`, the binary
   passthrough is genuinely exercised on Py2 (string literals are `str`/bytes). With
   `unicode_literals`, string literals produce `text_type` on both Py2 and Py3, and
   stdlib functions that still return Py2 `str` (like `hexdigest()`) survive the
   bare `.encode("ascii")` via the implicit decode-encode roundtrip for ASCII
   content.
5. **`FILE_ID_PREFIX`** is declared as a text literal but only ever used in byte
   context (HMAC input concatenated with other bytes). It requires a conversion
   call that would disappear if the constant were `b"..."`.

## Goal

- `from __future__ import unicode_literals` in every module under `dnsdle/`.
- Helpers renamed to signal direction: `encode_ascii`, `decode_ascii`,
  `encode_utf8`, `encode_ascii_int`.
- `encode_ascii` and `encode_utf8` become one-liners: bare
  `return value.encode(...)` with zero isinstance checks.
- `decode_ascii` drops to one isinstance check (text_type passthrough, required
  because `str.decode()` does not exist on Py3).
- No passthrough calls on values whose type is provably bytes at the call site.
- `FILE_ID_PREFIX` is a bytes literal; its call site concatenates directly.
- All call sites and imports updated in the same commit.
- No test files modified.

## Design

### All 18 modules under `dnsdle/` — add `unicode_literals`

Every file already has `from __future__ import absolute_import`. Add
`unicode_literals` on the same line or as a second import:

```python
from __future__ import absolute_import, unicode_literals
```

The audit found zero bare string literals in bytes context across the entire
`dnsdle/` tree — every byte-context literal already uses `b"..."`. No literal
changes are needed beyond the import.

### `dnsdle/compat.py` — function renames and body simplification

With `unicode_literals`, callers pass `text_type` for all string-literal arguments.
Stdlib functions like `hexdigest()` still return Py2 `str` (bytes), but
`str.encode("ascii")` on Py2 performs an implicit decode-then-re-encode roundtrip
that works correctly for ASCII content. This means `encode_ascii` no longer needs
its `isinstance(value, binary_type)` passthrough:

```python
# before (3 branches)
def to_ascii_bytes(value):
    if isinstance(value, binary_type):
        return value
    if isinstance(value, text_type):
        return value.encode("ascii")
    raise TypeError("value must be text or bytes")

# after (0 branches)
def encode_ascii(value):
    return value.encode("ascii")
```

Same for `encode_utf8`:

```python
# after (0 branches)
def encode_utf8(value):
    return value.encode("utf-8")
```

`decode_ascii` cannot drop its isinstance check because `str` on Py3 has no
`.decode()` method. Callers like `client_reassembly.py` and `base32_decode_no_pad`
receive values that are `text_type` on Py3 (from `hexdigest()` or `.join()`), so
the passthrough is exercised:

```python
# after (1 branch)
def decode_ascii(value):
    if isinstance(value, text_type):
        return value
    return value.decode("ascii")
```

`encode_ascii_int` — internal call rename only. Change `str(int_value)` to
`text_type(int_value)` so the value is `text_type` on Py2 (avoids the Py2 `str()`
→ bytes path):

```python
def encode_ascii_int(value, field_name):
    try:
        int_value = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer" % field_name)
    if int_value < 0:
        raise ValueError("%s must be non-negative" % field_name)
    return encode_ascii(text_type(int_value))
```

`key_text` — same `str()` → `text_type()` fix (two occurrences) so the function
consistently returns `text_type`:

```python
def key_text(value):
    if isinstance(value, text_type):
        return value
    if is_binary(value):
        try:
            return decode_ascii(value)
        except Exception:
            return text_type(value)
    return text_type(value)
```

Internal callers (`base32_lower_no_pad`, `base32_decode_no_pad`) update their calls
to the new names.

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

### `dnsdle/config.py` — normalize PSK to `text_type`

The PSK arrives from `sys.argv` as Py2 `str` (bytes). Without normalization,
`encode_utf8(psk)` would call `str.encode("utf-8")` on Py2, which performs an
implicit ASCII decode first — crashing on any PSK containing bytes > 127. Fix
by decoding the PSK to `text_type` at the config boundary so `encode_utf8`
always receives text:

```python
from dnsdle.compat import binary_type

# after the existing non-empty check
if isinstance(psk, binary_type):
    try:
        psk = psk.decode("utf-8")
    except UnicodeDecodeError:
        raise StartupError("config", "invalid_config", "psk must be valid UTF-8")
```

This is 1 isinstance check at the system boundary where type coercion belongs.
`encode_utf8` stays a zero-branch one-liner because `config.psk` is guaranteed
`text_type` by the time it reaches any compat helper.

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

### `dnsdle/client_generator.py` — replace `to_ascii_text` validation, rename

The validation guard at line 188 currently calls `to_ascii_text(source)`. With
`unicode_literals`, `source` is `text_type` on both Py2 and Py3, so the renamed
`decode_ascii` would be a no-op passthrough — no actual ASCII validation. Replace
with `encode_ascii(source)` which validates by encoding text to ASCII bytes (raises
`UnicodeEncodeError` on non-ASCII). This drops the `to_ascii_text` import entirely:

```python
# imports — only encode_ascii needed (to_ascii_text import removed)
from dnsdle.compat import encode_ascii

# line 188 (validation guard — encode validates ASCII, result discarded)
encode_ascii(source)

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
from dnsdle.compat import base32_lower_no_pad  # unchanged
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

- `dnsdle/*.py` (all 18 modules): add `from __future__ import unicode_literals`.
- `dnsdle/compat.py`: rename four functions; `encode_ascii` and `encode_utf8`
  become one-liners (zero isinstance); `decode_ascii` drops to one isinstance;
  `str()` → `text_type()` in `encode_ascii_int` and `key_text`.
- `dnsdle/constants.py`: change `FILE_ID_PREFIX` from text to bytes literal.
- `dnsdle/client_payload.py`: remove `to_ascii_bytes` passthrough call and import.
- `dnsdle/config.py`: normalize PSK to `text_type` at the system boundary (1
  isinstance check); import `binary_type` from compat.
- `dnsdle/cname_payload.py`: remove 3 passthrough calls on known-bytes values;
  rename 3 imports and 12 call sites.
- `dnsdle/client_generator.py`: replace `to_ascii_text` import with `encode_ascii`
  (drop `to_ascii_text`); validation guard changes from `to_ascii_text(source)` to
  `encode_ascii(source)`; binary-write call renamed.
- `dnsdle/__init__.py`: rename 1 import and 1 call site.
- `dnsdle/dnswire.py`: rename 2 imports and 2 call sites.
- `dnsdle/mapping.py`: rename 3 imports and 4 call sites.
- `dnsdle/publish.py`: rename 1 import, remove 1 conversion call
  (`FILE_ID_PREFIX`).
- `dnsdle/client_reassembly.py`: rename 1 import and 1 call site.
