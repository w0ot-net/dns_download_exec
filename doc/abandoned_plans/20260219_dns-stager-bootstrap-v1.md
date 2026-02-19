**ABANDONED 2026-02-19**: Replaced by five sequential phase plans for
incremental implementation and validation. Replacement plans:
- `doc/plans/dns-stager-phase1-publish-sources.md`
- `doc/plans/dns-stager-phase2-two-phase-startup.md`
- `doc/plans/dns-stager-phase3-stager-template.md`
- `doc/plans/dns-stager-phase4-stager-minify.md`
- `doc/plans/dns-stager-phase5-stager-generation.md`

# Plan: DNS Stager Bootstrap

## Summary

Add a two-stage bootstrap mechanism where the server auto-publishes generated
client scripts as DNS-served files and produces minimal Python one-liner
"stagers" per (file, target_os). The stager downloads the full client via DNS,
exec()s it in memory, and the client then downloads the actual payload. This
gives operators a short, pasteable command to distribute instead of the ~900
line generated client script.

## Problem

Generated client scripts are large standalone Python files (~900 lines). An
operator cannot paste them as a single command. There is currently no built-in
mechanism to bootstrap the download chain from a short command, and no way to
retrieve the client script itself via DNS.

## Goal

After implementation:

- The server automatically publishes each generated client script as an
  additional DNS-served file using the same CNAME/crypto protocol as user
  files.
- The server outputs a compact Python one-liner per (file, target_os) that:
  1. Downloads the generated client script via DNS.
  2. Verifies integrity (SHA-256).
  3. `exec()`s it in memory.
  4. The client downloads the actual payload file.
- The stager uses the same PSK and wire protocol as the full client.
- The one-liner is self-contained: no file I/O, no dependencies beyond the
  Python standard library, Python 2.7/3.x compatible.
- The stager accepts resolver address and PSK as positional arguments.
- No functional change to the existing client or server behavior for user
  files.

## Design

### 1. Auto-publish generated client scripts

After generating client scripts for user files, treat each script as an
additional file and publish it through the existing pipeline. The client source
text (ASCII Python code) becomes the "plaintext" that gets compressed, sliced,
encrypted, and served via DNS.

This requires:
- A new function `build_publish_items_from_sources()` in `dnsdle/publish.py`
  with signature `(sources, compression_level, max_ciphertext_slice_bytes,
  seen_plaintext_sha256=None, seen_file_ids=None)` where `sources` is a list
  of `(source_filename, plaintext_bytes)` pairs. It performs the same steps as
  `build_publish_items()`: hash, compress, derive file_id, chunk into slices.
  The optional `seen_plaintext_sha256` and `seen_file_ids` sets enable
  cross-set uniqueness enforcement when called after `build_publish_items()`
  (Phase 2 passes the sets accumulated during Phase 1).
- `generate_client_artifacts()` in `dnsdle/client_generator.py` must include
  the `"source"` field in its returned artifact dicts (currently stripped).

### 2. Two-phase startup

Restructure `build_startup_state()` in `dnsdle/__init__.py`:

**Phase 1 -- user files:**
1. Run the existing budget convergence loop on user files only.
2. Build an intermediate user-file-only `RuntimeState` from the converged
   mapped publish items. This intermediate state is the input to
   `generate_client_artifacts()`, whose interface (`_build_artifacts` iterates
   `runtime_state.publish_items` and reads `runtime_state.config`) is
   unchanged.
3. Call `generate_client_artifacts(intermediate_state)` to produce client
   scripts. The returned artifact dicts now include the `"source"` field.
4. Record the user file mapping snapshot (file_tags and slice_tokens).

**Phase 2 -- client scripts as additional files:**
5. Call `build_publish_items_from_sources()` on the generated client source
   text from each artifact, passing the `seen_plaintext_sha256` and
   `seen_file_ids` sets accumulated during Phase 1 to enforce cross-set
   uniqueness of content hashes and file IDs.
6. Combine user + client publish items into one list.
7. Apply mapping to the combined set.
8. **Invariant:** user file mappings must be unchanged after combining.
   File tags and tokens are HMAC-derived from per-file publish_version, so
   cross-file interference requires an astronomically unlikely hash
   collision. This invariant is structurally required, not merely
   probabilistic: client scripts generated in Phase 1 embed Phase 1 slice
   tokens and file tags as compiled constants. If collision resolution in
   the combined mapping promotes or changes any user-file token, the
   already-generated client scripts contain stale, incorrect `SLICE_TOKENS`
   and would query wrong DNS names at runtime. Fail startup if violated.
9. **Invariant:** combined `realized_max_token_len` must fit within the
   converged `query_token_len` from Phase 1. Fail startup if violated.
10. Build final `RuntimeState` from all mapped items (user files + client
    scripts). The server's `lookup_by_key` now contains entries for both.
    The intermediate user-file-only RuntimeState from Phase 1 is discarded.
11. Generate stager one-liners from client publish items.

### 3. Stager template

A new file `dnsdle/stager_template.py` containing a readable Python stager
script with placeholder constants. This is the source-of-truth stager logic.
It is written as normal, readable Python and minified at generation time. It
implements the minimum viable DNS download protocol needed to retrieve one
file.

**Included in the stager:**
- Raw UDP DNS query construction (A record, RD flag, EDNS OPT when
  dns_edns_size > 512).
- CNAME response parsing (header skip, question skip, name decompression
  with pointer support, payload label extraction).
- Base32 decode (lowercase, no padding).
- HMAC-SHA256 key derivation (enc_key, mac_key from PSK + file identity).
- XOR stream keystream generation and decryption.
- MAC verification (truncated 8-byte HMAC-SHA256).
- Slice reassembly, zlib decompression, SHA-256 final verification.
- `exec()` of downloaded client with argument forwarding.

**Excluded from the stager (to minimize size):**
- Retry logic (each slice attempted once; any failure is fatal).
- CLI argument parsing (positional `sys.argv` only).
- Logging or progress output.
- Descriptive error messages (raw exceptions propagate).
- System resolver discovery (resolver is a required positional argument).
- Domain rotation (uses lexicographically first configured domain only;
  `config.domains` is sorted alphabetically during normalization).
- Duplicate-slice handling (fatal on any re-receipt).

**Embedded constants** (filled at generation time via placeholder
substitution, same pattern as the client template):
- `D`: domain label tuple (lexicographically first configured domain).
- `T`: file_tag of the client publish item.
- `F`: file_id.
- `V`: publish_version.
- `N`: total_slices.
- `Z`: compressed_size.
- `H`: plaintext_sha256_hex.
- `K`: ordered slice_tokens tuple.
- `R`: response_label.
- `L`: dns_max_label_len.
- `E`: dns_edns_size.

**Runtime arguments:** `<resolver_ip> <psk> [extra_client_args...]`

**Exec handoff:** After downloading and verifying the client source, the
stager sets `sys.argv = ['s', '--psk', psk, '--resolver', resolver] +
extra_args` and calls `exec(client_source)`. The client's `if __name__ ==
"__main__"` block fires, calling `sys.exit(main(sys.argv[1:]))`, which
terminates the process with the client's exit code. The PSK and resolver
used by the stager are forwarded to the client so it uses the same
credentials and network path.

**Python 2.7/3.x compatibility:** The stager avoids `print` (no output),
uses `b"..."` byte literals for all wire and crypto operations, and handles
the str/bytes split for `sys.argv` values with a compact `encode` guard.

**Template coding discipline:** The template is written in a constrained
style that makes mechanical minification trivial:
- Every statement on its own line (no multi-line expressions).
- Comments always on their own line (never inline after code).
- Consistent 4-space indentation.
- No multi-line string literals containing `#`.
- No nested functions or closures.
- No decorators, `with` statements, or comprehensions spanning lines.

### 4. Custom minifier

A new module `dnsdle/stager_minify.py` containing a simple, deterministic
minifier tailored to the template's disciplined coding style. No AST, no
tokenizer, no external dependencies -- just mechanical text passes that are
correct by construction given the template constraints above.

**Minification passes (applied in order):**

1. **Strip comment lines.** Drop any line whose `.strip()` starts with `#`.
2. **Strip blank lines.** Drop lines that are empty after stripping.
3. **Rename variables.** Apply a fixed rename table using word-boundary
   regex: `re.sub(r'\benc_key\b', 'e', src)`. Process longest names first
   to prevent substring interference. The rename table maps every
   template-local variable to a single-character name and lives in the
   minifier module, doubling as documentation of the mapping.
4. **Reduce indentation.** Replace 4 spaces per indent level with 1 space.
5. **Semicolon-join.** Consecutive lines at the same indent level that are
   not control-flow openers (`if`, `for`, `while`, `try`, `except`, `else`,
   `elif`, `finally`, `def`, `return`, `with`, `break`, `continue`) get
   joined with `;`.

The minifier exposes a single function: `minify(source) -> str`. It is
deterministic: same input always produces same output.

**Verification:** The generation pipeline `compile()`-checks the minified
output. The minifier is also tested by round-tripping the stager template
through minification and checking that it compiles and retains all expected
function names and constants.

### 5. Stager generation and compression

A new module `dnsdle/stager_generator.py` that produces the final one-liner
through a multi-stage pipeline:

1. **Substitute** embedded constants into the readable stager template.
2. **Minify** using `stager_minify.minify()`.
3. **Compress** the minified Python source with `zlib.compress()`.
4. **Encode** the compressed bytes with `base64.b64encode()`.
5. **Wrap** in a self-extracting bootstrap:
   ```
   python3 -c "import base64,zlib;exec(zlib.decompress(base64.b64decode('...')))" RESOLVER PSK
   ```

The bootstrap wrapper is fixed overhead (~65 chars). The payload is opaque
base64, which completely sidesteps shell quoting -- no quotes from the inner
stager code can leak through. The same wrapper works on both Linux and
Windows (double-quote wrapping is safe because the payload contains no
double quotes).

The generation must verify:
- The one-liner is valid ASCII.
- The decompressed inner code compiles (`compile()` check).
- The one-liner round-trips: decompress(decode(payload)) equals the
  minified source.

### 6. Stager output

During startup, after building the final runtime state and stagers, output
the stager one-liners. One entry per (source file, target_os):

```
<source_filename> (<target_os>):
  python3 -c "import base64,zlib;exec(zlib.decompress(base64.b64decode('...')))" RESOLVER PSK
```

`RESOLVER` and `PSK` are literal placeholder tokens. The operator fills in
actual values before distributing.

Output goes through the existing logging system at `info` level, category
`startup`.

## Affected Components

- `dnsdle.py`: remove the standalone `generate_client_artifacts()` call and
  its surrounding generation logging (generation_start, generation_ok,
  generation_summary). Client generation now happens inside
  `build_startup_state()` during Phase 1. Stager output logging replaces
  generation-summary logging. The main function's post-startup flow
  simplifies to: `build_startup_state()` -> publish-item logging ->
  `serve_runtime()`.
- `dnsdle/publish.py`: add `build_publish_items_from_sources()` that accepts
  in-memory `(source_filename, plaintext_bytes)` pairs plus
  `compression_level`, `max_ciphertext_slice_bytes`, and optional cross-set
  uniqueness sets, producing publish items through the same
  compress/hash/slice pipeline as `build_publish_items()`.
- `dnsdle/client_generator.py`: include `"source"` field in the artifact
  dicts returned by `generate_client_artifacts()` so auto-publishing can
  access the client source text without re-reading from disk.
- `dnsdle/__init__.py`: restructure `build_startup_state()` into two-phase
  startup; Phase 1 publishes user files and generates clients; Phase 2
  auto-publishes client scripts, combines mappings, checks invariants,
  builds final RuntimeState, and generates stager one-liners.
- `dnsdle/stager_template.py` (NEW): readable Python stager template string
  with placeholder constants implementing the minimum viable DNS download
  protocol. Written in disciplined style amenable to mechanical minification.
- `dnsdle/stager_minify.py` (NEW): custom deterministic minifier for the
  stager template. Five passes: strip comments, strip blanks, rename
  variables (fixed table, word-boundary regex), reduce indentation (4-space
  to 1-space), semicolon-join same-level statements. No AST, no tokenizer,
  no external dependencies.
- `dnsdle/stager_generator.py` (NEW): stager generation pipeline; constant
  substitution, minification, zlib compression, base64 encoding,
  self-extracting wrapper; ASCII and compile verification.
- `dnsdle/constants.py`: stager placeholder names and any output format
  constants.
- `doc/architecture/STAGER.md` (NEW): document the stager protocol,
  one-liner format, two-stage bootstrap flow, exec handoff, security
  properties, and limitations.
- `doc/architecture/CLIENT_GENERATION.md`: document auto-publishing of
  generated clients and stager generation as part of the generation pipeline.
