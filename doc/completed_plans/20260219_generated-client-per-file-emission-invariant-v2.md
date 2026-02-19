# Plan: Generated Client Per-File Emission Invariant (v2)

## Summary
Tighten generator invariants to the delta not already covered by existing code.
Existing lifecycle logging (`generation_start`, `generation_ok`,
`generation_summary`), filename uniqueness checks, and transactional commit
semantics are already implemented and are not modified by this plan. This plan
adds: unreplaced-placeholder detection in rendered source, identity-tuple and
cardinality post-conditions in artifact building, `publish_version` threading
through the artifact dict, and targeted doc updates.

## Problem
Three gaps remain after the existing implementation:
1. `_render_client_source` performs `@@KEY@@` substitution but never verifies
   all placeholders were replaced. A typo in the replacements dict or template
   would silently emit broken client source.
2. `_build_artifacts` checks filename uniqueness but does not assert expected
   cardinality (`len(publish_items) * len(target_os)`) or full identity-tuple
   uniqueness as a post-condition.
3. The artifact dict omits `publish_version`, so `dnsdle.py` cannot include it
   in `generation_ok` records and the `generation_summary` identity set is
   incomplete.

## Goal
After implementation:
- `_render_client_source` fails fast if any `@@...@@` placeholder survives
  substitution.
- `_build_artifacts` fails fast if realized artifact count differs from
  `len(publish_items) * len(target_os)`, or if the realized identity set
  `{(file_id, publish_version, file_tag, target_os)}` contains duplicates.
- `publish_version` is available in every artifact dict from build through
  return to caller.
- `generation_ok` log records include `publish_version`.
- Architecture docs state the cardinality formula and classify mismatches as
  startup-fatal.

## Design

### 1. Unreplaced-placeholder invariant in `_render_client_source`
After the substitution loop and before the ASCII check
(`generator.py:1164`), search the rendered source for the pattern
`@@[A-Z_]+@@`. If any match is found, raise `StartupError` with reason code
`generator_invalid_contract` and include the first unreplaced placeholder name
in context.

One `re.search` call; no source parsing required.

### 2. Identity-tuple and cardinality post-conditions in `_build_artifacts`
Add a `seen_identities` set alongside the existing `seen_names` set. On each
iteration, insert `(file_id, publish_version, file_tag, target_os)` and fail
on collision with reason code `generator_invalid_contract`.

After the loop, assert:
```
len(artifacts) == len(runtime_state.publish_items) * len(config.target_os)
```
Fail with `generator_invalid_contract` if violated.

Both checks are defense-in-depth: the filename uniqueness check and the
deterministic loop structure make violations unlikely, but explicit
post-conditions catch future regressions.

### 3. Thread `publish_version` through artifact dict
In `_build_artifacts` (`generator.py:1221-1228`), add
`"publish_version": publish_item.publish_version` to each artifact dict.

In `generate_client_artifacts` (`generator.py:1422-1431`), propagate
`"publish_version": artifact["publish_version"]` into the returned
`generated` list.

### 4. Add `publish_version` to `generation_ok` log record
In `dnsdle.py:94-107`, add `"publish_version": artifact["publish_version"]`
to the `generation_ok` record dict. No other lifecycle records need changes.

### 5. Architecture doc updates

**`CLIENT_GENERATION.md` -- Output Artifacts section (line 50-70):**
Add after "For each published file and `target_os`, generate exactly one
Python script artifact":
- `artifact_count = file_count * target_os_count`
- cardinality or identity-tuple mismatch is startup-fatal

**`CLIENT_GENERATION.md` -- Generator Failure Conditions (line 249-270):**
Add to failure list:
- unreplaced template placeholder after substitution
- artifact count mismatch (`realized != file_count * target_os_count`)
- duplicate identity tuple `(file_id, publish_version, file_tag, target_os)`

**`ERRORS_AND_INVARIANTS.md` -- Generator Contract (line 214-222):**
Add invariant:
- `artifact_count == file_count * target_os_count`; mismatch is startup-fatal

**`LOGGING.md` -- Event Schema (line 10-22):**
Add a generation-events subsection documenting:
- `generation_ok` required fields: `file_id`, `publish_version`, `file_tag`,
  `target_os`, `path`
- `generation_summary` required fields: `managed_dir`, `artifact_count`,
  `target_os`, `file_ids`

## Execution Notes

Executed 2026-02-19. All five design items implemented as specified:

1. **Unreplaced-placeholder invariant**: Added `re.search(r"@@[A-Z_]+@@", source)`
   after substitution loop in `_render_client_source`, raising `StartupError`
   with `generator_invalid_contract` and placeholder name in context.
2. **Identity-tuple and cardinality post-conditions**: Added `seen_identities`
   set in `_build_artifacts` checking `(file_id, publish_version, file_tag,
   target_os)` uniqueness per iteration, plus cardinality assertion
   `len(artifacts) == len(publish_items) * len(target_os)` after the loop.
3. **`publish_version` threading**: Added to artifact dict in `_build_artifacts`
   and propagated into returned `generated` list in `generate_client_artifacts`.
4. **`generation_ok` logging**: Added `publish_version` field to the
   `generation_ok` record in `dnsdle.py`.
5. **Architecture docs**: Updated `CLIENT_GENERATION.md` (Output Artifacts and
   Generator Failure Conditions sections), `ERRORS_AND_INVARIANTS.md` (Generator
   Contract invariant #6), and `LOGGING.md` (Generation Events subsection).

No deviations from plan.

## Affected Components
- `dnsdle/generator.py`: unreplaced-placeholder check in
  `_render_client_source`; identity-tuple set and cardinality assertion in
  `_build_artifacts`; `publish_version` added to artifact dicts in
  `_build_artifacts` and `generate_client_artifacts`.
- `dnsdle.py`: add `publish_version` to `generation_ok` log record.
- `doc/architecture/CLIENT_GENERATION.md`: cardinality formula and new
  failure conditions.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: cardinality invariant under
  Generator Contract.
- `doc/architecture/LOGGING.md`: generation event field requirements.
