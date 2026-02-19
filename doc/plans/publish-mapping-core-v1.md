# Plan: Publish and Mapping Core (v1)

## Summary
Implement the first runnable core for `dns_download_exec`: deterministic
startup publish processing and deterministic query mapping generation. This
phase focuses on building immutable per-file publish artifacts and a global
lookup map keyed by `(file_tag, slice_token)`, with strict fail-fast
validation. The outcome is a startup-ready core that can be consumed by the DNS
request handler and client generator in later phases.

## Problem
The repository currently has architecture contracts but no code implementing the
publish/mapping pipeline. Without this layer, there is no deterministic
`file_version`/`file_id` derivation, no compression/slicing, no mapping token
materialization, and no global lookup namespace for request resolution. This
blocks all runtime behavior because DNS serving and generated clients depend on
these artifacts.

## Goal
After implementation:
- startup can parse required config and build publish artifacts for all files
- duplicate-content files are rejected (`file_version` uniqueness invariant)
- deterministic mapping outputs are generated from canonical inputs
- global `(file_tag, slice_token)` uniqueness is enforced before serving
- immutable publish state is returned for downstream DNS/client components
- failures are explicit startup errors with stable reasoned logs/messages

## Design
### 1. Introduce a minimal module layout
Add a small, stdlib-only Python module set for v1 core behavior:
- config normalization/validation
- publish pipeline (read, hash, compress, slice, manifest build)
- mapping derivation/materialization
- immutable runtime state assembly

Keep code Python 2.7/3.x compatible and ASCII-only.

### 2. Implement deterministic identity + publish pipeline
For each input file:
1. read plaintext bytes
2. compute `plaintext_sha256`
3. set `file_version = plaintext_sha256`
4. derive deterministic `file_id`
5. compress with configured zlib level
6. compute slice geometry from `max_ciphertext_slice_bytes`
7. split compressed bytes into canonical ordered slices
8. derive `file_tag` and `slice_tokens`
9. build per-file immutable publish object

Cross-file startup checks:
- unique normalized paths
- unique `file_version`
- unique `file_id`

### 3. Implement exact mapping derivation contract
Implement canonical derivation from `doc/architecture/QUERY_MAPPING.md`:
- canonical ASCII input encoding for `mapping_seed`, `file_version`,
  `slice_index`
- HMAC-SHA256 with domain-separated labels
- RFC4648 base32 lowercase no-padding materialization
- deterministic truncation by computed lengths
- fail if requested token length exceeds digest text capacity

Collision handling:
- determine shortest collision-safe `slice_token_len` per file under limits
- enforce global uniqueness of `(file_tag, slice_token)` across all files
- fail startup on any unresolved collision within configured limits

### 4. Build startup-facing API in `dnsdle.py`
Add a single startup flow callable from CLI:
- parse args
- validate config
- compute `max_ciphertext_slice_bytes` inputs needed by publish layer
- build immutable publish state
- print concise startup summary/log output

No DNS socket serve loop or client generation logic in this phase; only
construct and expose ready state.

### 5. Error and logging semantics
Use fail-fast explicit exceptions/error codes for startup-only phase:
- config errors
- file read/compression/slicing errors
- mapping derivation/materialization errors
- uniqueness/collision violations

Log stable context fields where available (`file_id`, `file_version`,
`file_tag`, counts) without leaking sensitive data.

### 6. Documentation alignment during implementation
If implementation reveals ambiguity, update architecture docs in the same
change (clean break, no shim behavior), especially:
- `doc/architecture/CONFIG.md`
- `doc/architecture/PUBLISH_PIPELINE.md`
- `doc/architecture/QUERY_MAPPING.md`
- `doc/architecture/ERRORS_AND_INVARIANTS.md`
- `doc/architecture/SERVER_RUNTIME.md`

## Affected Components
- `dnsdle.py`: add startup CLI entrypoint and wire publish/mapping core build.
- `dnsdle/__init__.py`: package marker and exported startup-core interfaces.
- `dnsdle/config.py`: config schema parsing, normalization, and invariant checks.
- `dnsdle/publish.py`: deterministic publish pipeline and manifest assembly.
- `dnsdle/mapping.py`: deterministic `file_tag`/`slice_token` derivation and collision handling.
- `dnsdle/state.py`: immutable runtime publish-state structures and lookup map build.
- `doc/architecture/CONFIG.md`: update only if implementation forces clarified field semantics.
- `doc/architecture/PUBLISH_PIPELINE.md`: update only if implementation forces clarified algorithm details.
- `doc/architecture/QUERY_MAPPING.md`: update only if implementation forces clarified canonicalization/length behavior.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: align startup failure classification wording if needed.
- `doc/architecture/SERVER_RUNTIME.md`: align publish preparation wording if needed.

## Phased Execution
1. Scaffold module layout and shared constants/types.
2. Implement config parse/validation and deterministic helper primitives.
3. Implement publish pipeline for one file, then extend to multi-file.
4. Implement mapping length selection and collision resolution.
5. Build global lookup map and startup invariants.
6. Integrate startup build path into `dnsdle.py`.
7. Perform manual smoke verification with small local files and expected
   deterministic outputs.
8. Apply doc clarifications required by actual implementation behavior.

## Success Criteria
- Running `dnsdle.py --domain <d> --files <f1,f2> --psk <p>` builds publish
  state without runtime exceptions for valid inputs.
- Same inputs produce identical derived publish/mapping outputs across restarts.
- Duplicate-content file inputs fail startup with explicit reason.
- Global `(file_tag, slice_token)` collision is detected and fails startup.
- No fallback remap behavior exists for invalid or ambiguous mapping state.
