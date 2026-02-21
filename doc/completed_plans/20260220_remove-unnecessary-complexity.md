# Plan: Remove unnecessary complexity

## Summary

Remove dead code, over-engineered transactional file-writing machinery, and a
trivial pass-through wrapper.  Net reduction of ~180 lines with no behavior
change.  Where the current code uses silent fallbacks, replace with hard
invariants.

## Problem

Three areas of the codebase carry more complexity than they justify:

1. **dnswire.py** contains `parse_message`, `_decode_resource_record`,
   `_decode_resource_records`, and `_encode_question` -- none have live
   callers.  `parse_message` was used by a since-removed client-payload
   module.  `_encode_question` only exists as a fallback in `build_response`
   for when `raw_question_bytes` is absent; the only call site that omits it
   is a synthetic dict in `server.py:_validate_runtime_state_for_serving`.
   The fallback silently re-encodes instead of failing, which could mask
   upstream bugs.

2. **client_generator.py** uses a full transactional commit system (staging
   directory, backup directory, rollback on failure) to write a single
   deterministic file during startup.  The machinery spans ~150 lines
   (`_build_run_dir`, `_write_staged_file`, `_collect_backup_targets`,
   `_rollback_commit`, `_transactional_commit`) plus imports (`random`,
   `shutil`, `time`).  On failure the user simply re-runs, so rollback adds
   no practical value and hides whether the write itself succeeded.

3. **dnsdle.py** defines `_emit_record` as a pass-through to
   `emit_structured_record` with an identical signature.

## Goal

- Remove all dead code paths.
- Replace the `build_response` fallback with a hard invariant requiring
  `raw_question_bytes`.
- Replace the transactional commit system with a simple
  write-temp-then-rename.
- Remove `_emit_record` and use `emit_structured_record` directly.
- No behavior change for correct inputs; bugs that previously triggered
  silent fallbacks now fail fast.

## Design

### Phase 1 -- dnswire.py dead code and invariant

1. Delete `_decode_resource_record`, `_decode_resource_records`,
   `parse_message`.
2. Delete `_encode_question`.
3. In `build_response`: remove the `raw_question_bytes is not None` branch
   and the `else` fallback.  Require `raw_question_bytes` as a hard
   invariant -- read it directly from `request["raw_question_bytes"]` and
   fail with a `KeyError` if absent (fast failure on contract violation).
   Derive `qdcount` as `1 if raw_question_bytes else 0`.
4. In `server.py:_validate_runtime_state_for_serving`: the synthetic request
   dict currently provides `"question"` but not `"raw_question_bytes"`.
   Replace the `"question"` key with `"raw_question_bytes"` built via
   `dnswire.encode_name(question_labels) + struct.pack("!HH", DNS_QTYPE_A,
   DNS_QCLASS_IN)` (using the existing module-level `dnswire` import).
   Drop the `"question"` key entirely.

### Phase 2 -- client_generator.py simplification

Replace the transactional commit with a direct write:

1. Delete `_build_run_dir`, `_write_staged_file`, `_collect_backup_targets`,
   `_rollback_commit`, `_transactional_commit`, `_cleanup_tree`,
   `_MANAGED_FILE_RE`.
2. Remove imports: `random`, `re`, `shutil`, `time`.
3. Add a new private helper `_remove_stale_managed_files(managed_dir,
   keep_name)` that lists the directory and removes any `.py` file whose
   name is not `keep_name` and starts with `dnsdl` (the old/current naming
   prefix).  This replaces the backup-targets collector with a simple
   cleanup.  Failure to remove a stale file raises `StartupError` (no
   silent swallowing).
4. Rewrite `generate_client_artifacts` to:
   - Create `base_output_dir` and `managed_dir` (keep `_safe_mkdir`,
     `_norm_abs`, `_is_within_dir` for path-traversal invariant).
   - Build source via `build_client_source()`.
   - Validate the final path is within `managed_dir` (existing
     `_is_within_dir` check).
   - Write to `<final_path>.tmp-<pid>` then remove-before-rename to
     `final_path` (remove existing target first for Windows compatibility,
     matching the pattern in `client_runtime.py:292-294`).
     On write failure raise `StartupError`; attempt temp-file cleanup but
     do not suppress the original error.
   - Call `_remove_stale_managed_files` after the successful rename.

### Phase 3 -- dnsdle.py wrapper removal

1. Delete `_emit_record`.
2. Replace all `_emit_record(...)` calls with `emit_structured_record(...)`.
3. Pass `emit_structured_record` directly as the callback to
   `serve_runtime`.

## Affected Components

- `dnsdle/dnswire.py`: delete `_decode_resource_record`,
  `_decode_resource_records`, `parse_message`, `_encode_question`; harden
  `build_response` to require `raw_question_bytes`.
- `dnsdle/server.py`: update synthetic request dict in
  `_validate_runtime_state_for_serving` to provide `raw_question_bytes`.
- `dnsdle/client_generator.py`: replace transactional commit system with
  simple write-temp-rename; delete six helper functions, `_MANAGED_FILE_RE`,
  and three imports; add `_remove_stale_managed_files`.
- `dnsdle.py`: delete `_emit_record`, use `emit_structured_record` directly.

## Execution Notes

- Phase 1: Added `import struct` to `server.py` for the `struct.pack` call in
  the synthetic `raw_question_bytes` construction.
- Phase 2: Executed as planned.  The remove-before-rename pattern from
  `client_runtime.py:292-294` was used for Windows compatibility.
- Phase 3: Executed as planned.  The `serve_runtime` callback was changed from
  `_emit_record` to `emit_structured_record` directly.
- Review findings fixed before execution: (1) Windows `os.rename` overwrite
  handled via remove-before-rename, (2) `re` import added to removal list,
  (3) `encode_name` referenced via `dnswire.encode_name` using existing
  module import.
