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
def generate_stager(config, client_publish_item, client_artifact):
    """Generate a stager one-liner for a single (file, target_os) pair.

    Returns a dict with keys:
        "source_filename": str (user payload filename)
        "target_os": str
        "oneliner": str (the pasteable command)
        "minified_source": str (for verification)
    """
```

**Pipeline (applied in order):**

1. **Substitute** embedded constants into the stager template from
   `build_stager_template()`. The constants come from `config` (domains,
   response_label, dns_max_label_len, dns_edns_size) and
   `client_publish_item` (file_tag, file_id, publish_version,
   total_slices, compressed_size, plaintext_sha256, slice_tokens).
   Domain is `config.domains[0]` (lexicographically first, since
   `config.domains` is sorted during normalization).
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

    Returns a list of stager dicts (one per artifact).
    """
```

This iterates the generation result artifacts, matches each to its
corresponding client publish item (by file_id + publish_version), and
calls `generate_stager()` for each.

### 3. Integration into `build_startup_state()`

After Phase 2's combined RuntimeState is built:

1. Call `generate_stagers()` with the config, generation result, and the
   client publish items from the combined mapped set.
2. Return `(runtime_state, generation_result, stagers)` from
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
- Works on both Linux and Windows.

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
