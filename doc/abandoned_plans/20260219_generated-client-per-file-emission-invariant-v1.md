# Plan: Generated Client Per-File Emission Invariant (v1)

**ABANDONED 2026-02-19**: Roughly half the described work (lifecycle logging,
filename uniqueness, transactional commit) already exists in the codebase.
Design Section 2 duplicates existing `generation_start`/`generation_ok`/
`generation_summary` records in `dnsdle.py`. Design Section 3 describes only
existing transactional commit behavior. Design Section 1 payload constant
parity check lacks a concrete low-complexity approach. Artifact dict is missing
`publish_version` which the plan depends on but does not address. Replaced by
scoped v2 plan: `doc/plans/generated-client-per-file-emission-invariant-v2.md`.

## Summary
Ensure generated-client emission is explicitly and verifiably one client per
published file per selected target OS. Startup must fail fast if emission
cardinality or metadata binding deviates from that contract. The implementation
will tighten generator invariants, add explicit startup accounting, and align
architecture docs with the enforced behavior.

## Problem
The intended behavior is clear (one downloader client per hosted file, expanded
by OS profile), and it is documented in architecture text, but startup
enforcement and observability are still weaker than the contract requires.
Without explicit expected-vs-realized identity-set checks, future changes can
still pass simple count checks while drifting artifact binding semantics.

## Goal
After implementation:
- For publish set size `N` and selected target OS count `M`, startup generates
  exactly `N * M` managed artifacts.
- Every generated artifact is bound to exactly one file identity
  (`file_id`, `publish_version`, `file_tag`) and one `target_os`.
- Any mismatch (missing, duplicate, or over-generated artifacts) is startup
  fatal with stable reason codes.
- Docs state this contract consistently across generation/runtime architecture.

## Design
### 1. Harden generator cardinality and uniqueness invariants
- In `dnsdle/generator.py`, enforce deterministic artifact key uniqueness by
  full identity tuple `(file_id, publish_version, file_tag, target_os)` and
  deterministic filename uniqueness.
- Compute expected count from runtime state (`len(publish_items) * len(target_os)`)
  and fail if rendered artifact count differs.
- Compute expected identity set from publish state cross target OS and fail if
  realized identity set differs (missing, extra, or duplicate identity tuple).
- Fail fast if any artifact payload constants do not match the source publish
  item (for example `TOTAL_SLICES`, `SLICE_TOKENS`, `FILE_ID`).

### 2. Emit explicit startup accounting for per-file/per-OS generation
- In `dnsdle.py`, keep `generation_start` and `generation_summary` records tied
  to expected and realized artifact counts and identity-set coverage.
- Ensure per-artifact lifecycle log (`generation_ok`) carries
  `file_id`, `publish_version`, `file_tag`, and `target_os` so operators can
  verify one-to-one coverage.

### 3. Keep managed output replacement deterministic and scoped
- Keep generator-managed directory ownership unchanged
  (`<client_out_dir>/dnsdle_v1`).
- Preserve stale managed-file pruning only for managed pattern matches so
  reruns with different `target_os` sets correctly converge to the expected
  per-file/per-OS artifact set.

### 4. Align architecture documentation to the enforced invariant
- Update generation architecture docs to explicitly define
  `artifact_count = file_count * target_os_count`.
- Update startup/runtime invariant docs to classify cardinality mismatch as
  startup-fatal and non-recoverable.

### 5. Validation approach
- Run startup with 1 file and `windows,linux`; verify exactly 2 artifacts.
- Run startup with 2 files and `windows,linux`; verify exactly 4 artifacts and
  one artifact per `(file_id, publish_version, file_tag, target_os)`.
- Rerun with reduced target OS (`linux`) and verify stale managed artifacts are
  pruned down to exactly one per file.
- Induce mismatch fixture (tampered publish item metadata) and verify startup
  fails before serve loop with stable reason code.

## Affected Components
- `dnsdle/generator.py`: enforce per-file/per-OS cardinality and uniqueness
  invariants, and fail-fast reason codes.
- `dnsdle.py`: keep explicit generation lifecycle accounting and realized vs
  expected artifact visibility.
- `doc/architecture/CLIENT_GENERATION.md`: codify one-client-per-file-per-OS
  contract and startup-fatal mismatch behavior.
- `doc/architecture/ARCHITECTURE.md`: reflect generation cardinality invariant
  in startup data flow.
- `doc/architecture/SERVER_RUNTIME.md`: include generation count invariant in
  pre-serve startup requirements.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: classify cardinality/identity
  mismatches as startup-fatal invariant breaches.
- `doc/architecture/CONFIG.md`: clarify that `target_os` multiplies per-file
  artifact count deterministically.
- `doc/architecture/LOGGING.md`: document generation accounting fields required
  for expected-vs-realized count and identity visibility.
