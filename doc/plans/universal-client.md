# Plan: Universal client — eliminate per-file client generation

## Summary

Replace the per-file client generation system (`client_template.py` +
`client_generator.py`) with a single universal client that takes all
file-specific parameters via CLI arguments.  The server publishes one client
for all platforms instead of N * OS_COUNT templated scripts.  Stagers pass
payload metadata to the universal client after downloading it.

## Problem

The current architecture generates one standalone client script per published
file per target OS.  Each script embeds ~22 constants via `@@PLACEHOLDER@@`
substitution into ~800 lines of Python trapped in string literals.  This
causes:

1. **Code duplication**: ~15 functions in the template duplicate logic from
   canonical modules (`compat.py`, `helpers.py`, `dnswire.py`,
   `cname_payload.py`).  Changes to canonical code can silently drift.
2. **Untestable code**: The client logic lives inside string literals —
   no IDE support, no linting, no direct unit testing.
3. **Unnecessary multiplicity**: The only thing that varies between generated
   clients is ~22 small config values.  Everything else — every function,
   every line of logic — is identical.
4. **Bloated machinery**: The template engine (`_TEMPLATE_PREFIX`,
   `_TEMPLATE_SUFFIX`, `@@TOKEN@@` substitution, placeholder validation,
   `_lift_resolver_source`) exists to support what amounts to `repr()`
   substitution on 22 values.

With the completion of `20260219_client-runtime-token-derivation.md`, the
variable data per file shrank further (SLICE_TOKENS tuple replaced by two
small scalars).  The per-file generation model is no longer justified.

## Goal

After implementation:

1. One universal client exists as a real Python file — testable, lintable,
   readable.
2. The client handles both Linux and Windows (resolver discovery branches at
   runtime based on `sys.platform`).
3. All file-specific parameters are CLI arguments; nothing is embedded.
4. The server publishes 1 client file instead of N * OS_COUNT.
5. Stagers download the universal client, set `sys.argv` with payload params,
   and `exec()` it — same two-stage bootstrap, but all stagers reference the
   same client.
6. `client_template.py` and the `@@PLACEHOLDER@@` substitution system are
   eliminated.
7. Code duplication between the client and canonical modules is eliminated.

## Design

### Universal client architecture

The client becomes a real standalone Python file (`dnsdle/client_standalone.py`)
that is assembled at server startup from canonical source modules.  It takes
ALL parameters via CLI.  Several values that the current template embeds
are derived at runtime instead, significantly reducing the parameter count.

**Derived at runtime (not passed):**
- `file_id` = `sha256("dnsdle:file-id:v1|" + publish_version).hexdigest()[:16]`.
  Computed from `--publish-version`.
- `file_tag` = `base32_lower_no_pad(HMAC-SHA256(mapping_seed, "dnsdle:file:v1|" + publish_version))[:file_tag_len]`.
  Computed from `--mapping-seed`, `--publish-version`, and `--file-tag-len`.
  `file_tag_len` is deployment-wide (collision resolution never promotes it;
  only `slice_token_len` is promoted).
- `source_filename` = `"dnsdle_" + file_id`.  Used for the default output
  path when `--out` is not specified.
- `TARGET_OS` — detected via `sys.platform`.
- `CRYPTO_PROFILE`, `WIRE_PROFILE` — hardcoded to `"v1"` in client source.

**Required per-file (5 values):**
- `--publish-version` — root identity; `file_id` and `file_tag` derive from it
- `--total-slices` — needed to know when download is complete
- `--compressed-size` — needed for MAC verification (part of MAC message)
- `--sha256` — plaintext SHA-256 hex for final verification
- `--token-len` — slice token truncation length (per-file, varies with
  collision resolution)

**Required deployment-wide:**
- `--psk`
- `--domains` (comma-separated base domains)
- `--mapping-seed`

**Optional deployment-wide (with defaults):**
- `--resolver` (default: system resolver discovery)
- `--out` (default: deterministic temp path from derived `file_id`)
- `--file-tag-len` (default: `4`; deployment-wide, set by server config)
- `--response-label` (default: `"r"`)
- `--dns-max-label-len` (default: `63`)
- `--dns-edns-size` (default: `512`)
- `--timeout`, `--no-progress-timeout`, `--max-rounds`,
  `--max-consecutive-timeouts`, `--query-interval` (current defaults)
- `--verbose`

### Cross-platform resolver discovery

Both resolver implementations live in the same file, branched at runtime:

```python
import sys
if sys.platform == "win32":
    import subprocess
    def _load_system_resolvers():
        # nslookup-based discovery (~30 lines)
        ...
else:
    def _load_system_resolvers():
        # /etc/resolv.conf parsing (~30 lines)
        ...
```

~60 lines total, half dead code on either platform.  Eliminates the
per-OS template lifting mechanism (`_lift_resolver_source`,
`_DISCOVER_SYSTEM_RESOLVER`, `@@EXTRA_IMPORTS@@`, `@@LOADER_FN@@`).

### Building the standalone client

The client must be standalone (delivered via DNS, no `dnsdle` imports at
runtime).  The shared utility functions (byte helpers, DNS wire decoding,
crypto primitives) are assembled from canonical modules at server startup
using the extraction approach:

1. Canonical modules (`compat.py`, `helpers.py`, `dnswire.py`,
   `cname_payload.py`) have `# __EXTRACT: name__` / `# __END_EXTRACT__`
   markers around shared functions.
2. A new `dnsdle/extract.py` parses markers and applies whole-word renames
   (e.g. `encode_ascii` -> `_to_ascii_bytes`).
3. `DnsParseError` is aliased as a `ClientError` subclass in the client
   source, so extracted `_decode_name` works without changing `dnswire.py`
   error semantics.
4. The client source file (`dnsdle/client_standalone.py`) contains only the
   client-specific logic (CLI parsing, download loop, reassembly, output).
   A `build_client_source()` function assembles the full standalone script
   by combining extracted utilities + client-specific code.

The extracted functions (16 total):

- **compat.py** (10): `encode_ascii`, `encode_utf8`, `decode_ascii`,
  `encode_ascii_int`, `byte_value`, `iter_byte_values`,
  `base32_decode_no_pad`, `base32_lower_no_pad`, `is_binary`,
  `constant_time_equals`
- **helpers.py** (2): `hmac_sha256`, `dns_name_wire_length`
- **dnswire.py** (1): `_decode_name`
- **cname_payload.py** (3): `_derive_file_bound_key`, `_keystream_bytes`,
  `_xor_bytes`

`base32_lower_no_pad` is needed for runtime `file_tag` and slice token
derivation (new functionality the current template inlines).

**`_decode_name` extraction dependencies:** The canonical `_decode_name`
calls two local helpers not in the extraction list: `_message_length(msg)`
(just `len(msg)`) and `_ord_byte(val)` (just `byte_value(val)`).  The
extraction rename table must map `_message_length` -> `len` and `_ord_byte`
-> the extracted `byte_value` name so these references resolve.

**Foundational type definitions:** Extracted functions (`byte_value`,
`iter_byte_values`, `constant_time_equals`, `is_binary`, `encode_ascii_int`)
depend on module-level `PY2`, `text_type`, `binary_type`, and
`integer_types` from `compat.py`.  `client_standalone.py` must include the
PY2/type-detection preamble (same block the current template uses at lines
86-94).

**Required constants from `dnsdle/constants.py`:** Extracted functions
reference constants via module-level imports that will not be available in
the standalone client.  `client_standalone.py` must define:
- Payload crypto labels: `PAYLOAD_ENC_KEY_LABEL`, `PAYLOAD_ENC_STREAM_LABEL`,
  `PAYLOAD_MAC_KEY_LABEL`, `PAYLOAD_MAC_MESSAGE_LABEL`, `PAYLOAD_MAC_TRUNC_LEN`,
  `PAYLOAD_PROFILE_V1_BYTE`, `PAYLOAD_FLAGS_V1_BYTE`
- DNS wire: `DNS_POINTER_TAG`, `DNS_POINTER_VALUE_MASK`, `DNS_HEADER_BYTES`,
  `DNS_FLAG_QR`, `DNS_FLAG_TC`, `DNS_FLAG_RD`, `DNS_OPCODE_MASK`,
  `DNS_QTYPE_A`, `DNS_QTYPE_CNAME`, `DNS_QTYPE_OPT`, `DNS_QCLASS_IN`,
  `DNS_RCODE_NOERROR`
- Runtime derivation labels: `MAPPING_FILE_LABEL` (`b"dnsdle:file:v1|"`),
  `MAPPING_SLICE_LABEL` (`b"dnsdle:slice:v1|"`),
  `FILE_ID_PREFIX` (`b"dnsdle:file-id:v1|"`)

### Stager changes

The stager template currently downloads a per-file client and exec's it,
passing through `--psk` and `--resolver` via `sys.argv`.  It changes to also
pass the payload file parameters.

Current stager tail:
```python
sys.argv = ["s"] + list(_sa)
exec(client_source)
```

New stager tail:
```python
sys.argv = [
    "c",
    "--psk", psk,
    "--domains", DOMAINS_STR,
    "--mapping-seed", MAPPING_SEED,
    "--publish-version", PAYLOAD_PUBLISH_VERSION,
    "--total-slices", str(PAYLOAD_TOTAL_SLICES),
    "--compressed-size", str(PAYLOAD_COMPRESSED_SIZE),
    "--sha256", PAYLOAD_SHA256,
    "--token-len", str(PAYLOAD_TOKEN_LEN),
    "--file-tag-len", str(FILE_TAG_LEN),
    "--response-label", RESPONSE_LABEL,
    "--dns-edns-size", str(DNS_EDNS_SIZE),
    "--resolver", resolver,
]
exec(client_source)
```

The client derives `file_id`, `file_tag`, and the output filename at
runtime — the stager does not need to pass them.

The stager now embeds two sets of metadata:
- **Client download params** (from universal client publish item): used by
  the stager's own download logic — `FILE_TAG`, `FILE_ID`,
  `PUBLISH_VERSION`, `TOTAL_SLICES`, `COMPRESSED_SIZE`,
  `PLAINTEXT_SHA256_HEX`, `SLICE_TOKEN_LEN`.  Same for all stagers.
- **Payload params** (5 per-file values + deployment config): passed to the
  client via `sys.argv`.  Only the 5 per-file values
  (`PAYLOAD_PUBLISH_VERSION`, `PAYLOAD_TOTAL_SLICES`,
  `PAYLOAD_COMPRESSED_SIZE`, `PAYLOAD_SHA256`, `PAYLOAD_TOKEN_LEN`) vary
  per stager; the deployment values (`DOMAINS_STR`, `MAPPING_SEED`,
  `RESPONSE_LABEL`, `DNS_EDNS_SIZE`, `FILE_TAG_LEN`) are shared.

The stager grows by 5 per-file values.  These are short strings and
integers; after minification and zlib compression the size increase is
modest.

### Generator changes

`client_generator.py` changes from "generate N * OS_COUNT scripts with
placeholder substitution" to "build one universal client via extraction".
Its public interface changes:

- `generate_client_artifacts()` no longer iterates over files and target OSes.
  It calls `build_client_source()` once and returns a single artifact.
- The `@@PLACEHOLDER@@` substitution loop, unreplaced-placeholder check, and
  per-OS template lifting are eliminated.
- The artifact count invariant changes from
  `file_count * os_count` to `1`.

### Stager generator changes

`stager_generator.py` changes:

- `generate_stager()` takes both `universal_client_publish_item` (the one
  universal client's mapped publish item) and `payload_publish_item` (the
  specific payload file's mapped publish item).
- The replacements dict gains payload metadata entries (prefixed to
  distinguish from client metadata, e.g. `PAYLOAD_PUBLISH_VERSION`).
- `generate_stagers()` iterates over payload files (not client artifacts),
  producing one stager per payload file (not per file * OS).

### Server startup changes (`__init__.py`)

The convergence loop simplifies:

1. Phase 1: converge user file mappings (unchanged).
2. Phase 2: build one universal client source via `build_client_source()`.
   Publish it through the normal pipeline as a single additional file.
3. Check combined mapping stability (same invariant, simpler because only
   1 client file instead of N * OS_COUNT).
4. Generate stagers: for each user file, generate one stager that references
   the universal client and carries the payload params.

The `_GeneratorInput` class, per-artifact filename matching, and
`client_mapped_items` partitioning simplify or disappear since there is
only one client artifact.

### Stager minify changes

`stager_minify.py` rename table needs entries for the new payload metadata
constants (`PAYLOAD_PUBLISH_VERSION`, `PAYLOAD_TOTAL_SLICES`,
`PAYLOAD_COMPRESSED_SIZE`, `PAYLOAD_SHA256`, `PAYLOAD_TOKEN_LEN`,
`FILE_TAG_LEN`, `DOMAINS_STR`) and the new `sys.argv` construction.

## Phases

### Phase 1: Extract markers and extraction engine

Add `# __EXTRACT__` markers to `compat.py`, `helpers.py`, `dnswire.py`,
`cname_payload.py`.  Build `dnsdle/extract.py`.  Update
`constant_time_equals` to use `hmac.compare_digest` fast path.

No behavioral changes to any module.

### Phase 2: Create universal client

Write `dnsdle/client_standalone.py` containing:
- PY2/type-detection preamble (`PY2`, `text_type`, `binary_type`,
  `integer_types`)
- All required constants from `dnsdle/constants.py` (payload crypto labels,
  DNS wire constants, runtime derivation labels) defined inline
- Client-specific logic (download loop, CLI, reassembly, output, validation)
- Cross-platform resolver discovery (both implementations, runtime branch)
- `build_client_source()` function that assembles the full standalone script
  via extraction

Verify the assembled standalone client compiles and is ASCII-clean.

### Phase 3: Update client generator

Rewrite `client_generator.py` to use `build_client_source()`.  Single
artifact output.  Remove template substitution machinery.

### Phase 4: Update stager template and generator

1. Add payload metadata placeholders to `stager_template.py`.
2. Update the stager's `exec()` tail to build `sys.argv` with payload params.
3. Update `stager_generator.py` to accept universal client + payload items.
4. Update `stager_minify.py` rename table for new constants.

### Phase 5: Update server startup flow

Update `__init__.py` convergence loop:
- Build one universal client instead of N * OS_COUNT.
- Publish as single file.
- Generate one stager per user file.
- Simplify mapping stability checks.

### Phase 6: Update architecture docs

Update `CLIENT_GENERATION.md`, `CLIENT_RUNTIME.md`, and any other docs that
reference per-file client generation, embedded constants, or per-OS template
lifting.

### Phase 7: Remove dead code

Delete `client_template.py`.  Remove `build_client_template()`,
`_lift_resolver_source()`, `_TEMPLATE_PREFIX`, `_TEMPLATE_SUFFIX`,
`_DISCOVER_SYSTEM_RESOLVER`.  Git history preserves the original file for
reference — no legacy copy needed.

Remove vestigial `target_os` config surface:
- Remove `target_os` and `target_os_csv` from `Config` namedtuple in
  `config.py` and its `_normalize_target_os` validator.
- Remove `--target-os` from `cli.py`.
- Remove `ALLOWED_TARGET_OS` from `constants.py`.
- Remove `GENERATED_CLIENT_FILENAME_TEMPLATE` from `constants.py` if no
  longer used.
- Update all call sites that reference these removed fields.

## Affected Components

- `dnsdle/compat.py`: Add extract markers around 10 functions (including
  `base32_lower_no_pad`); improve `constant_time_equals` to try
  `hmac.compare_digest` first.
- `dnsdle/helpers.py`: Add extract markers around 2 functions.
- `dnsdle/dnswire.py`: Add extract markers around `_decode_name`.  Extraction
  rename table must map `_message_length` -> `len` and `_ord_byte` ->
  extracted `byte_value` name.
- `dnsdle/cname_payload.py`: Add extract markers around 3 functions.
- `dnsdle/extract.py` (new): Marker parser, file reader, identifier renamer.
  Rename table must include local-helper mappings for `_decode_name`
  dependencies.
- `dnsdle/client_standalone.py` (new): Universal client logic +
  `build_client_source()` assembler.  PY2/type-detection preamble.  All
  required constants from `dnsdle/constants.py` defined inline.
  Cross-platform resolver discovery.  Full CLI argument parser for all
  file/deployment/runtime parameters.
- `dnsdle/client_template.py`: Deleted (git history preserves original).
- `dnsdle/client_generator.py`: Major rewrite — single universal client
  output via `build_client_source()`.  Template substitution removed.
- `dnsdle/stager_template.py`: Add payload metadata placeholders; update
  `exec()` tail to construct `sys.argv` with payload params.
- `dnsdle/stager_generator.py`: Accept universal client item + payload item;
  embed both metadata sets; generate one stager per payload file.
- `dnsdle/stager_minify.py`: Update rename table for new payload constants.
- `dnsdle/__init__.py`: Simplify convergence loop — build 1 client, publish
  1 file, generate N stagers (not N * OS_COUNT).
- `dnsdle/cli.py`: Remove `--target-os` argument.
- `dnsdle/config.py`: Remove `target_os`, `target_os_csv` from `Config`;
  remove `_normalize_target_os`.
- `dnsdle/constants.py`: Remove `ALLOWED_TARGET_OS`,
  `GENERATED_CLIENT_FILENAME_TEMPLATE` if unused.
- `dnsdle/resolver_linux.py`: No changes (still used by server); its source
  is inlined into `client_standalone.py`.
- `dnsdle/resolver_windows.py`: Same as above.
- `doc/architecture/CLIENT_GENERATION.md`: Rewrite for universal client
  architecture.
- `doc/architecture/CLIENT_RUNTIME.md`: Update CLI contract, remove embedded
  constants references, document runtime parameter validation.
