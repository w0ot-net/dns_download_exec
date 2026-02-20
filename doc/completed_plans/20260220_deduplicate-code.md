# Plan: Deduplicate Code

## Summary

Four concrete duplications exist across the codebase. This plan eliminates each
one by making the canonical implementation the single source of truth: wiring the
already-defined (but dead) `_read_resolver_source` call into the stager template,
collapsing the repeated `build_publish_items` preamble into a delegation, and
moving `_derive_file_id` / `_derive_file_tag` / `_derive_slice_token` into
`helpers.py` with extraction markers so both server and generated client share
the same code.

## Problem

1. **Resolver functions exist in three copies.** `resolver_linux.py` and
   `resolver_windows.py` are the canonical sources.  `stager_template.py` also
   defines `_read_resolver_source()` to read from those files, but
   `build_stager_template()` never calls it.  Instead, all four resolver
   functions are hardcoded verbatim inside the `_STAGER_PREFIX` string literal.
   Any fix to the canonical files silently diverges from the stager.

2. **`build_publish_items` and `build_publish_items_from_sources` share
   identical boilerplate.** Both functions repeat the same budget guard,
   optional-set initialisation, and loop scaffolding.

3. **`_derive_file_id` is implemented twice.** `publish.py` has its own copy
   (using `_sha256_hex`, which is also used elsewhere in the module);
   `client_runtime.py` has an identical implementation inside the extraction
   block.

4. **`_derive_file_tag` and `_derive_slice_token` are implemented twice with an
   API mismatch.** `mapping.py` takes pre-encoded `seed_bytes` and uses private
   digest helpers. `client_runtime.py` inlines the same HMAC calls and takes a
   `mapping_seed` string, requiring `encode_ascii` at the call site instead of
   the caller.

## Goal

- Each piece of logic lives in exactly one place.
- The stager template is assembled from the canonical resolver files (no divergence risk).
- `build_publish_items` delegates to `build_publish_items_from_sources` after reading files.
- `_derive_file_id`, `_derive_file_tag`, and `_derive_slice_token` are defined
  once in `helpers.py` with `# __EXTRACT__` markers, imported by all server-side
  callers, and automatically embedded in the universal client.

## Design

### 1. Wire `_read_resolver_source` into `build_stager_template`

Rename `_STAGER_PREFIX` to `_STAGER_PRE_RESOLVER` (containing everything up to
but not including the resolver functions).  `build_stager_template()` becomes:

```python
def build_stager_template():
    windows_resolver = _read_resolver_source("resolver_windows.py")
    linux_resolver = _read_resolver_source("resolver_linux.py")
    return _STAGER_PRE_RESOLVER + windows_resolver + linux_resolver + _STAGER_DISCOVER + _STAGER_SUFFIX
```

The hardcoded copies of `_IPV4_RE`, `_run_nslookup`, `_parse_nslookup_output`,
`_load_windows_resolvers`, and `_load_unix_resolvers` are removed from the
prefix string.  `_read_resolver_source` was already correct; it just needed to
be called.

### 2. Collapse `build_publish_items` into a delegating wrapper

`build_publish_items` reads each path from `config.files`, raises
`unreadable_file` on failure (preserving `file_index` in the error context),
then calls `build_publish_items_from_sources` with the resulting
`(basename, bytes)` list and `config.compression_level`.  All shared preamble
(budget guard, set init) lives only in `build_publish_items_from_sources`.

Note: downstream errors and diagnostic logs will use `source_index` /
`source_filename` context (from `build_publish_items_from_sources`) instead of
the previous `file_index`.  This is intentional — filenames are more
informative than opaque indices.

### 3. Move `_derive_file_id` to `helpers.py`

Add to `helpers.py`:
```python
# __EXTRACT: _derive_file_id__
def _derive_file_id(publish_version):
    return hashlib.sha256(FILE_ID_PREFIX + encode_ascii(publish_version)).hexdigest()[:16]
# __END_EXTRACT__
```

Add required imports to `helpers.py`: `encode_ascii` from `dnsdle.compat`,
`FILE_ID_PREFIX` from `dnsdle.constants`.

- `publish.py`: remove local `_derive_file_id`; import from `dnsdle.helpers`.
  Keep `_sha256_hex` (still used by `_build_single_publish_item` lines 41, 72).
- `client_runtime.py`: remove `_derive_file_id` from the extraction block;
  import from `dnsdle.helpers` at the top of the file (already present for other
  helpers).
- `client_standalone.py`: add `"_derive_file_id"` to `_HELPERS_EXTRACTIONS`.

### 4. Move `_derive_file_tag` and `_derive_slice_token` to `helpers.py`

Unified API uses `seed_bytes` (bytes), consistent with the server's pre-encoded
approach.  Add to `helpers.py` as extraction-marked functions:

```python
# __EXTRACT: _derive_file_tag__
def _derive_file_tag(seed_bytes, publish_version, file_tag_len):
    digest = hmac_sha256(seed_bytes, MAPPING_FILE_LABEL + encode_ascii(publish_version))
    return base32_lower_no_pad(digest)[:file_tag_len]
# __END_EXTRACT__

# __EXTRACT: _derive_slice_token__
def _derive_slice_token(seed_bytes, publish_version, slice_index, token_len):
    msg = MAPPING_SLICE_LABEL + encode_ascii(publish_version) + b"|" + encode_ascii_int(slice_index, "slice_index")
    return base32_lower_no_pad(hmac_sha256(seed_bytes, msg))[:token_len]
# __END_EXTRACT__
```

Add `encode_ascii_int`, `base32_lower_no_pad`, `MAPPING_FILE_LABEL`,
`MAPPING_SLICE_LABEL` to `helpers.py` imports.

- `mapping.py`: remove `_derive_file_digest`, `_derive_slice_digest`,
  `_derive_file_tag`, `_derive_slice_token`; import `_derive_file_tag` and
  `_derive_slice_token` from `dnsdle.helpers` (the digest helpers are inlined
  into the new implementations and eliminated). `_compute_tokens` and
  `apply_mapping` call sites need no change — signatures are compatible (both
  already use `seed_bytes`).
- `client_runtime.py`: remove `_derive_file_tag` and `_derive_slice_token` from
  the extraction block; update the two call sites to pass
  `encode_ascii(mapping_seed)` instead of `mapping_seed` (i.e. in
  `_parse_runtime_args` and `_download_slices`).
- `client_standalone.py`: add `"_derive_file_tag"` and `"_derive_slice_token"`
  to `_HELPERS_EXTRACTIONS`. `MAPPING_FILE_LABEL`, `MAPPING_SLICE_LABEL`,
  `FILE_ID_PREFIX` are already in `_PREAMBLE_CONSTANTS`; `encode_ascii`,
  `encode_ascii_int`, `base32_lower_no_pad` are already in `_COMPAT_EXTRACTIONS`.

## Affected Components

- `dnsdle/stager_template.py`: rename `_STAGER_PREFIX`, strip hardcoded resolver
  functions, wire `_read_resolver_source` into `build_stager_template()`
- `dnsdle/resolver_windows.py`: unchanged (already canonical)
- `dnsdle/resolver_linux.py`: unchanged (already canonical)
- `dnsdle/publish.py`: remove local `_derive_file_id`, import from
  `dnsdle.helpers`, collapse `build_publish_items` into delegation
- `dnsdle/helpers.py`: add imports; add `_derive_file_id`, `_derive_file_tag`,
  `_derive_slice_token` with extraction markers
- `dnsdle/mapping.py`: remove `_derive_file_digest`, `_derive_slice_digest`,
  `_derive_file_tag`, `_derive_slice_token`; import `_derive_file_tag` and
  `_derive_slice_token` from `dnsdle.helpers`
- `dnsdle/client_runtime.py`: remove `_derive_file_id`, `_derive_file_tag`,
  `_derive_slice_token` from extraction block; import from `dnsdle.helpers`;
  update two call sites to pass `encode_ascii(mapping_seed)` as `seed_bytes`
- `dnsdle/client_standalone.py`: add `_derive_file_id`, `_derive_file_tag`,
  `_derive_slice_token` to `_HELPERS_EXTRACTIONS`

## Execution Notes

Executed 2026-02-20.

All four deduplication tasks implemented as designed:

1. **Stager resolver wiring**: renamed `_STAGER_PREFIX` to `_STAGER_PRE_RESOLVER`,
   stripped 80+ lines of hardcoded resolver functions, wired `_read_resolver_source`
   into `build_stager_template()`.  Template assembly verified -- all resolver
   functions present from canonical files.

2. **Publish delegation**: collapsed `build_publish_items` from 45 lines to 20 lines;
   reads files then delegates to `build_publish_items_from_sources`.  Budget guard
   and set init now live only in `build_publish_items_from_sources`.

3. **`_derive_file_id` to helpers**: added to `helpers.py` with `__EXTRACT__`
   markers.  Removed from `publish.py` (kept `_sha256_hex` which is still used)
   and from `client_runtime.py` extraction block.  Added to `_HELPERS_EXTRACTIONS`.

4. **`_derive_file_tag` / `_derive_slice_token` to helpers**: unified API on
   `seed_bytes` (bytes).  Removed four functions from `mapping.py`
   (`_derive_file_digest`, `_derive_slice_digest`, `_derive_file_tag`,
   `_derive_slice_token`), importing the last two from helpers.  Removed from
   `client_runtime.py` extraction block.  Updated `_download_slices` to
   pre-compute `seed_bytes = encode_ascii(mapping_seed)` once (review finding:
   avoids repeated encoding in per-slice loop).  Updated `_parse_runtime_args`
   call site with inline `encode_ascii(mapping_seed)`.

Deviations from plan:
- `_download_slices` pre-computes `seed_bytes` at function top rather than
  encoding inline at the call site, per review finding about avoiding
  redundant per-iteration encoding.

Validation:
- All six changed modules pass `py_compile`.
- All module imports succeed (no circular dependency).
- `build_stager_template()` produces template with all resolver functions.
- `build_client_source()` produces valid universal client with all three
  extracted functions.

Commit: 19e476c
