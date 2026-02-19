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
  that accepts a list of `(source_filename, plaintext_bytes)` pairs instead of
  reading from file paths. It performs the same steps as
  `build_publish_items()`: hash, compress, derive file_id, chunk into slices.
- `generate_client_artifacts()` in `dnsdle/client_generator.py` must include
  the `"source"` field in its returned artifact dicts (currently stripped).

### 2. Two-phase startup

Restructure `build_startup_state()` in `dnsdle/__init__.py`:

**Phase 1 -- user files:**
1. Run the existing budget convergence loop on user files only.
2. Produce mapped publish items and generate client scripts.
3. Record the user file mapping snapshot (file_tags and slice_tokens).

**Phase 2 -- client scripts as additional files:**
4. Call `build_publish_items_from_sources()` on the generated client source
   text from each artifact.
5. Combine user + client publish items into one list.
6. Apply mapping to the combined set.
7. **Invariant:** user file mappings must be unchanged after combining.
   File tags and tokens are HMAC-derived from per-file publish_version, so
   cross-file interference requires an astronomically unlikely hash
   collision. Fail startup if violated.
8. **Invariant:** combined `realized_max_token_len` must fit within the
   converged `query_token_len` from Phase 1. Fail startup if violated.
9. Build final `RuntimeState` from all mapped items (user files + client
   scripts). The server's `lookup_by_key` now contains entries for both.
10. Generate stager one-liners from client publish items.

### 3. Stager template

A new file `dnsdle/stager_template.py` containing a readable Python stager
script with placeholder constants. This is the source-of-truth stager logic
that gets minified and compressed at generation time. It implements the
minimum viable DNS download protocol needed to retrieve one file.

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
- Domain rotation (uses first configured domain only).
- Duplicate-slice handling (fatal on any re-receipt).

**Embedded constants** (filled at generation time via placeholder
substitution, same pattern as the client template):
- `D`: domain label tuple (first configured domain).
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

### 4. Stager generation and compression

A new module `dnsdle/stager_generator.py` that produces the final one-liner
through a multi-stage pipeline:

1. **Substitute** embedded constants into the readable stager template.
2. **Minify** the result: strip comments, collapse whitespace, use
   semicolons for statement separation, shorten variable names where the
   template uses longer ones for readability.
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

### 5. Stager output

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

- `dnsdle/publish.py`: add `build_publish_items_from_sources()` that accepts
  in-memory `(source_filename, plaintext_bytes)` pairs and produces publish
  items through the same compress/hash/slice pipeline as
  `build_publish_items()`.
- `dnsdle/client_generator.py`: include `"source"` field in the artifact
  dicts returned by `generate_client_artifacts()` so auto-publishing can
  access the client source text without re-reading from disk.
- `dnsdle/__init__.py`: restructure `build_startup_state()` into two-phase
  startup; Phase 1 publishes user files and generates clients; Phase 2
  auto-publishes client scripts, combines mappings, checks invariants,
  builds final RuntimeState, and generates stager one-liners.
- `dnsdle/stager_template.py` (NEW): compact Python stager template string
  with placeholder constants implementing the minimum viable DNS download
  protocol.
- `dnsdle/stager_generator.py` (NEW): stager generation logic; constant
  substitution; shell-quoted one-liner formatting; ASCII and compile
  verification.
- `dnsdle/constants.py`: stager placeholder names and any output format
  constants.
- `doc/architecture/STAGER.md` (NEW): document the stager protocol,
  one-liner format, two-stage bootstrap flow, exec handoff, security
  properties, and limitations.
- `doc/architecture/CLIENT_GENERATION.md`: document auto-publishing of
  generated clients and stager generation as part of the generation pipeline.
