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
Add `publish_item.slice_token_len > 0` invariant to `_validate_publish_item`
so the generator continues to validate every value it embeds.

### Security note

`mapping_seed` is not a secret. Its purpose is namespace control (see
QUERY_MAPPING.md: "To reduce cross-run linkability, rotate mapping_seed").
Clients already expose all tokens in their embedded tuple; embedding the seed
instead does not expand an attacker's capability. Both `mapping_seed` and
`publish_version` are embedded in every generated client, so the change does
not reveal any new derivation inputs.

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
  validation from `_validate_publish_item`; add
  `publish_item.slice_token_len > 0` invariant check.
- `dnsdle/stager_generator.py`: replace `SLICE_TOKENS` replacement with
  `MAPPING_SEED` (from `config.mapping_seed`) + `SLICE_TOKEN_LEN` (from
  `client_publish_item["slice_token_len"]`).
- `dnsdle/stager_minify.py`: update rename table -- remove `SLICE_TOKENS`
  entry, add entries for `MAPPING_SEED`, `SLICE_TOKEN_LEN`, and
  `_derive_slice_token`.
- `doc/architecture/QUERY_MAPPING.md`: update "Generated Client Mapping"
  section to describe runtime derivation instead of embedded token list.
- `doc/architecture/CLIENT_GENERATION.md`: update "Inputs" to replace
  `slice_tokens` array with `slice_token_len`; update "Embedded Constants
  Contract" to list `MAPPING_SEED` + `SLICE_TOKEN_LEN` instead of
  `SLICE_TOKENS`; update "Download Algorithm" to describe runtime derivation;
  update "Generator Failure Conditions" to replace `SLICE_TOKENS`-specific
  conditions (token array length mismatch, duplicate token, token exceeds
  label len) with `MAPPING_SEED`/`SLICE_TOKEN_LEN` validation conditions.
- `doc/architecture/CLIENT_RUNTIME.md`: update "Runtime Initialization"
  steps 1-2 to reference `MAPPING_SEED`/`SLICE_TOKEN_LEN` instead of
  `SLICE_TOKENS`; update "Runtime Invariants" item 1 to describe derivation
  inputs rather than `SLICE_TOKENS` cardinality.

## Test Breakage

The following tests reference `SLICE_TOKENS` constants or validation being
removed and will need updates after execution:

- `unit_tests/test_client_generator.py`:
  `test_rejects_slice_token_count_mismatch` and
  `test_rejects_duplicate_slice_tokens` test `_validate_publish_item` checks
  that are being removed. Replace with a test for the new
  `slice_token_len > 0` invariant.
- `unit_tests/test_stager_template.py`: `_build_ns()` substitutes
  `SLICE_TOKENS` into the stager template. Update to substitute
  `MAPPING_SEED` + `SLICE_TOKEN_LEN` instead and add a test for the new
  `_derive_slice_token` helper.
- `unit_tests/test_stager_minify.py`: `test_full_template_compiles_after_minify`
  uses stale `SLICE_TOKENS` substitution. Update to use `MAPPING_SEED` +
  `SLICE_TOKEN_LEN`.

## Execution Notes

Executed 2026-02-19.

All planned items implemented:

- `dnsdle/client_template.py`: replaced `SLICE_TOKENS` constant with
  `MAPPING_SEED` + `SLICE_TOKEN_LEN`; added `_derive_slice_token()` helper
  using `_to_ascii_bytes`/`_to_ascii_int_bytes`; updated
  `_validate_embedded_constants()` to validate new constants; updated download
  loop to call `_derive_slice_token(slice_index)`.
- `dnsdle/stager_template.py`: same constant/function/loop changes using
  `_ab`/`_ib` helpers. Stager derivation function uses intermediate `msg`
  variable to avoid multi-line function arguments that break the minifier's
  semicolon-join pass.
- `dnsdle/client_generator.py`: replaced `SLICE_TOKENS` substitution with
  `MAPPING_SEED` (from `config.mapping_seed`) + `SLICE_TOKEN_LEN` (from
  `publish_item.slice_token_len`); removed `SLICE_TOKENS` length/uniqueness
  checks from `_validate_publish_item`; added `slice_token_len > 0` invariant.
- `dnsdle/stager_generator.py`: replaced `SLICE_TOKENS` substitution with
  `MAPPING_SEED` + `SLICE_TOKEN_LEN`.
- `dnsdle/stager_minify.py`: replaced `SLICE_TOKENS` rename entry with
  `MAPPING_SEED`; added `_derive_slice_token` and `SLICE_TOKEN_LEN` entries
  at proper length-ordered positions (short names `cj`, `ck`).
- `doc/architecture/QUERY_MAPPING.md`: updated "Generated Client Mapping" to
  describe runtime derivation.
- `doc/architecture/CLIENT_GENERATION.md`: updated "Inputs",
  "Embedded Constants Contract", "Download Algorithm", and
  "Generator Failure Conditions".
- `doc/architecture/CLIENT_RUNTIME.md`: updated "Runtime Initialization" and
  "Runtime Invariants".

Deviations:
- Stager `_derive_slice_token` uses a local `msg` variable instead of inline
  multi-line `hmac.new(...)` args, because the minifier's semicolon-join pass
  incorrectly merges continuation lines inside multi-line function calls.

Test breakage items listed above are not addressed in this execution (tests
not modified per repository policy).
