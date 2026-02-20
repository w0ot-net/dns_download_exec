# Plan: Client-Side Runtime Token Derivation

## Summary

Move slice token derivation from a pre-embedded tuple (`SLICE_TOKENS`) to
runtime computation in generated clients and stagers. The client computes
`base32(HMAC-SHA256(mapping_seed, "dnsdle:slice:v1|" + publish_version + "|" +
index))[:token_len]` on the fly -- the same derivation the server already uses.
This makes generated client/stager file size independent of payload size.

## Problem

Generated clients and stagers embed a `SLICE_TOKENS` tuple containing one
opaque token string per slice. Because slice count scales with payload size
(payload bytes / ~100 bytes per CNAME slice), the embedded tuple -- and
therefore the generated script -- grows proportionally. For large payloads
this produces large clients and especially large stager 1-liners, since the
semi-random token strings compress poorly under zlib.

## Goal

After implementation:
1. Generated clients embed `MAPPING_SEED` (small string) and
   `SLICE_TOKEN_LEN` (small integer) instead of the full `SLICE_TOKENS` tuple.
2. Client/stager file size is bounded by code size alone, independent of slice
   count.
3. Wire behavior is identical -- the same tokens appear in DNS queries.
4. Server-side mapping, lookup, and response logic is unchanged.
5. No protocol version bump required (same derivation algorithm, same wire
   format).

## Design

### Core Idea

The server already derives slice tokens via:

```
slice_token[i] = base32_lower_no_pad(
    HMAC-SHA256(seed_bytes, b"dnsdle:slice:v1|" + publish_version_bytes + b"|" + index_bytes)
)[:token_len]
```

Currently, the server pre-computes all tokens and embeds them as a literal
tuple in the generated client. Instead, embed the derivation inputs
(`mapping_seed`, `slice_token_len`) and let the client compute each token at
query time.

### What stays the same

- Server-side `mapping.py`: still pre-computes all tokens for its
  `lookup_by_key` table. No change.
- Server-side `state.py`: `PublishItem` still carries `slice_tokens` for the
  server lookup. No change.
- Wire format: identical tokens, identical QNAMEs.
- Startup convergence loop in `__init__.py`: still runs, still validates
  mapping stability. No change to convergence logic.

### What changes

**Templates** -- replace the `SLICE_TOKENS` constant with `MAPPING_SEED` +
`SLICE_TOKEN_LEN`, and add a small derivation function:

Stager template (uses short helpers `_ab`/`_ib` already defined in template):

```python
def _derive_slice_token(index):
    d = hmac.new(
        _ab(MAPPING_SEED),
        b"dnsdle:slice:v1|" + _ab(PUBLISH_VERSION) + b"|" + _ib(index),
        hashlib.sha256,
    ).digest()
    t = base64.b32encode(d).decode("ascii").lower().rstrip("=")
    return t[:SLICE_TOKEN_LEN]
```

Client template uses its own existing helpers (`_to_ascii_bytes`,
`_to_ascii_int_bytes`) for the same derivation.

Download loops call `_derive_slice_token(i)` instead of `SLICE_TOKENS[i]`.

**Generators** -- substitute `MAPPING_SEED` (from `config.mapping_seed`) and
`SLICE_TOKEN_LEN` (from `publish_item.slice_token_len`) instead of
`SLICE_TOKENS`. Remove `SLICE_TOKENS`-specific validation (length, uniqueness)
from the generator; those checks remain in `mapping.py` where they belong.

### Security note

`mapping_seed` is not a secret. Its purpose is namespace control (see
QUERY_MAPPING.md: "To reduce cross-run linkability, rotate mapping_seed").
Clients already expose all tokens in their embedded tuple; embedding the seed
instead does not expand an attacker's capability. An attacker with the seed
still needs `publish_version` (file-specific) to compute tokens for other
files.

## Affected Components

- `dnsdle/client_template.py`: remove `SLICE_TOKENS` constant, add
  `MAPPING_SEED` + `SLICE_TOKEN_LEN` constants, add `_derive_slice_token()`
  helper (using `_to_ascii_bytes`/`_to_ascii_int_bytes`), update download loop
  to compute tokens; update `_validate_embedded_constants()` to validate
  `MAPPING_SEED` (non-empty) and `SLICE_TOKEN_LEN` (positive, within
  `DNS_MAX_LABEL_LEN`) instead of `SLICE_TOKENS` length/uniqueness/format
  checks.
- `dnsdle/stager_template.py`: same constant/function/loop changes as
  client_template (using `_ab`/`_ib` helpers). No validation function exists
  in the stager template.
- `dnsdle/client_generator.py`: replace `SLICE_TOKENS` replacement with
  `MAPPING_SEED` (from `config.mapping_seed`) + `SLICE_TOKEN_LEN` (from
  `publish_item.slice_token_len`); remove `SLICE_TOKENS` length/uniqueness
  validation from `_validate_publish_item`.
- `dnsdle/stager_generator.py`: replace `SLICE_TOKENS` replacement with
  `MAPPING_SEED` (from `config.mapping_seed`) + `SLICE_TOKEN_LEN` (from
  `client_publish_item["slice_token_len"]`).
- `dnsdle/stager_minify.py`: update rename table -- remove `SLICE_TOKENS`
  entry, add entries for `MAPPING_SEED`, `SLICE_TOKEN_LEN`, and
  `_derive_slice_token`.
- `doc/architecture/QUERY_MAPPING.md`: update "Generated Client Mapping"
  section to describe runtime derivation instead of embedded token list.
- `doc/architecture/CLIENT_GENERATION.md`: update "Embedded Constants
  Contract" to list `MAPPING_SEED` + `SLICE_TOKEN_LEN` instead of
  `SLICE_TOKENS`; update "Download Algorithm" to describe runtime derivation;
  update "Generator Failure Conditions" to replace `SLICE_TOKENS`-specific
  conditions (token array length mismatch, duplicate token, token exceeds
  label len) with `MAPPING_SEED`/`SLICE_TOKEN_LEN` validation conditions.
