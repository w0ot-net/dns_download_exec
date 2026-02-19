# Plan: Phase 2 -- Two-Phase Startup

## Summary

Restructure `build_startup_state()` so the server auto-publishes generated
client scripts as additional DNS-served files. The startup becomes two
phases: Phase 1 publishes user files and generates clients; Phase 2
auto-publishes client scripts, combines mappings with invariant checks, and
builds the final RuntimeState.

## Prerequisites

- Phase 1 (publish-sources infrastructure) must be complete.
  `build_publish_items_from_sources()` and the `"source"` and
  `"filename"` fields in `generate_client_artifacts()` return are all
  required.

## Goal

After implementation:

- The server publishes each generated client script as an additional
  DNS-served file using the same CNAME/crypto protocol as user files.
- The server's `lookup_by_key` contains entries for both user files and
  client scripts.
- Client scripts generated in Phase 1 embed user-file slice tokens and
  file tags. A structural invariant check at startup confirms these
  mappings are unchanged after combining user + client publish items.
- The combined `realized_max_token_len` fits within the converged
  `query_token_len` from Phase 1.
- `dnsdle.py` no longer calls `generate_client_artifacts()` directly;
  client generation moves inside `build_startup_state()`.

## Design

### 1. Restructure `build_startup_state()` in `dnsdle/__init__.py`

**Phase 1 -- user files:**

1. Run the existing budget convergence loop on user files only (unchanged).
2. Build an intermediate user-file-only `RuntimeState` from the converged
   mapped publish items. This intermediate state is the input to
   `generate_client_artifacts()`, whose interface (`_build_artifacts`
   iterates `runtime_state.publish_items` and reads `runtime_state.config`)
   is unchanged.
3. Call `generate_client_artifacts(intermediate_state)` to produce client
   scripts. The returned artifact dicts include the `"source"` field
   (from Phase 1 infrastructure).
4. Snapshot the user file mappings: for each mapped user-file publish item,
   record `(file_id, file_tag, slice_token_len, slice_tokens)`.
5. Collect `seen_plaintext_sha256` and `seen_file_ids` sets from the user
   file publish items for cross-set uniqueness enforcement.

**Phase 2 -- client scripts as additional files:**

6. Build `sources` list from the generation result: for each artifact,
   `(artifact["filename"], artifact["source"].encode("ascii"))`.
7. Call `build_publish_items_from_sources(sources, config.compression_level,
   max_ciphertext_slice_bytes, seen_plaintext_sha256, seen_file_ids)`.
8. Combine user + client publish items into one list.
9. Apply mapping to the combined set via `apply_mapping()`.
10. **Invariant -- user file mappings unchanged:** for each user-file item
    in the combined mapped output, verify `file_tag`, `slice_token_len`,
    and `slice_tokens` match the Phase 1 snapshot. Fail startup with
    `StartupError("startup", "mapping_stability_violation", ...)` if any
    differ. This is structurally required: client scripts embed Phase 1
    tokens as compiled constants.
11. **Invariant -- token length fits budget:** verify
    `_max_slice_token_len(combined_mapped_items) <= query_token_len` from
    the Phase 1 convergence loop. Fail startup with
    `StartupError("startup", "token_length_overflow", ...)` if violated.
12. Build final `RuntimeState` from all combined mapped items.
13. Return `(runtime_state, generation_result)` instead of just
    `runtime_state`, so `dnsdle.py` can log the generation result.

### 2. Simplify `dnsdle.py`

- Remove the standalone `generate_client_artifacts()` call and its
  surrounding try/except blocks (generation_start, generation_error
  logging).
- `build_startup_state()` now returns `(runtime_state, generation_result)`.
- Keep generation_ok per-artifact logging and generation_summary logging
  using the returned `generation_result`, but move them after the
  `build_startup_state()` call.
- The `generation_start` log event is intentionally removed; generation
  is now an internal detail of `build_startup_state()`.
- The post-startup flow simplifies to:
  `build_startup_state()` -> generation logging -> publish-item logging ->
  `serve_runtime()`.

### 3. Error handling

All new `StartupError` raises use phase `"startup"`. Reason codes:

- `mapping_stability_violation`: user file mapping changed after combining
  with client publish items.
- `token_length_overflow`: combined token length exceeds converged budget.

These are fatal startup errors that prevent the server from starting.

Generation errors (e.g. `generator_invalid_contract`, `generator_write_failed`)
now propagate through `build_startup_state()` instead of being caught by a
dedicated handler in `dnsdle.py`. This changes their log classification from
`"generation_error"` to `"startup_error"` (via `StartupError.to_log_record()`).
The `reason_code` is preserved and remains the primary discriminator; the
classification change is intentional.

## Affected Components

- `dnsdle/__init__.py`:
  - Restructure `build_startup_state()` into two-phase flow.
  - Return `(runtime_state, generation_result)` tuple.
  - Add user-file mapping snapshot and invariant checks.
  - New import: `build_publish_items_from_sources`
    (`generate_client_artifacts` is already imported).
- `dnsdle.py`:
  - Remove standalone `generate_client_artifacts()` call and its error
    handling.
  - Unpack `(runtime_state, generation_result)` from
    `build_startup_state()`.
  - Retain generation logging using the returned result.
  - Remove `generate_client_artifacts` import.
- `unit_tests/test_startup_state.py`:
  - Unpack `(runtime_state, generation_result)` tuple from
    `build_startup_state()` in end-to-end test.
  - Update `len(publish_items)` assertion: the combined set now includes
    client scripts (one per user file per target_os), not just user files.
  - Error-path test is unaffected.
- `unit_tests/test_startup_convergence.py`:
  - Extend `_PATCHABLE` to include `generate_client_artifacts` and
    `build_publish_items_from_sources` so stubs cover the new Phase 2
    calls inside `build_startup_state()`.
  - Update stubs and assertions for the `(runtime_state, generation_result)`
    return tuple.

## Execution Notes

Implemented as designed. All plan items completed.

### Deviations

- `unit_tests/test_startup_state.py`: end-to-end test payload changed from
  `os.urandom(700)` to a deterministic 10000-byte pseudo-random payload
  (`random.Random(42)`). The larger, deterministic payload ensures the
  convergence loop reaches `query_token_len >= 3`, providing sufficient
  headroom for client script slice tokens. With a small 700-byte payload,
  convergence stops at `query_token_len=2` but client scripts (~30KB source,
  ~59 compressed slices) require token length 3, violating the
  `token_length_overflow` invariant. Added `--client-out-dir` pointing at
  a subdirectory of the test tmpdir to avoid side effects.
- `unit_tests/test_startup_convergence.py`: replaced `object()` config stubs
  with a `_FakeConfig` class exposing `compression_level = 9`, required
  because Phase 2 accesses `config.compression_level` when calling
  `build_publish_items_from_sources`. Added `file_id`, `file_tag`,
  `slice_tokens`, and `plaintext_sha256` fields to publish/mapping stubs
  for the snapshot and invariant-check code paths. Default no-op stubs for
  `generate_client_artifacts` and `build_publish_items_from_sources` are
  provided via `_install()` optional kwargs, keeping existing test call
  sites unchanged. Updated `map_lens` assertion in test 1 to include the
  Phase 2 re-mapping call.
