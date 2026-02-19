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
`publish_version`/`file_id` derivation, no compression/slicing, no mapping token
materialization, and no global lookup namespace for request resolution. This
blocks all runtime behavior because DNS serving and generated clients depend on
these artifacts.

## Goal
After implementation:
- startup enforces full startup-time config/input validation for the core
  publish/mapping phase and builds publish artifacts for all files
- duplicate-content files are rejected (`plaintext_sha256` uniqueness invariant)
- deterministic mapping outputs are generated from canonical inputs
- global `(file_tag, slice_token)` uniqueness is enforced before serving
- immutable publish state is returned for downstream DNS/client components
- failures are explicit startup errors with stable classifications/reason codes

## Design
### 1. Introduce a minimal module layout
Add a small, stdlib-only Python module set for v1 core behavior:
- config normalization/validation
- CNAME payload budget math (`max_ciphertext_slice_bytes`)
- publish pipeline (read, hash, compress, slice, manifest build)
- mapping derivation/materialization
- immutable runtime state assembly

Keep code Python 2.7/3.x compatible and ASCII-only.

### 2. Enforce startup-validation scope in this phase
This phase validates all startup-time config invariants from
`doc/architecture/CONFIG.md` and `doc/architecture/SERVER_RUNTIME.md`:
- domain normalization/syntax and label/full-name bounds
- `response_label` syntax and non-token-character rule
- `mapping_seed` printable ASCII
- files list non-empty, path uniqueness, existence/readability
- duplicate-content rejection (`plaintext_sha256` uniqueness)
- `psk` non-empty
- numeric bounds (`ttl`, `dns_edns_size`, `dns_max_label_len`,
  `file_tag_len`, `compression_level`, retry/timeout defaults as applicable)
- `target_os` value-set validity and `client_out_dir` argument validity
- CNAME payload budget viability (`max_ciphertext_slice_bytes > 0`)
- mapping feasibility within DNS and digest-capacity bounds

Generation execution invariants remain out of scope until the generator is
implemented, but generator-related config field validation is in scope now.

Non-goals for this phase (explicitly deferred):
- generator output invariant: exactly one `.py` artifact per `(file, target_os)`
- generator output invariant: no sidecar output artifacts
- generator output/content invariant checks tied to client template emission

### 3. Implement deterministic identity + publish pipeline
For each input file:
1. read plaintext bytes
2. compute `plaintext_sha256`
3. enforce unique `plaintext_sha256` across configured files
4. compress with configured zlib level
5. set `publish_version = sha256(compressed_bytes).hexdigest().lower()`
6. derive deterministic `file_id` from `publish_version`
7. compute slice geometry from `max_ciphertext_slice_bytes`
8. split compressed bytes into canonical ordered slices
9. derive `file_tag` and `slice_tokens`
10. build per-file immutable publish object

Cross-file startup checks:
- unique normalized paths
- unique `plaintext_sha256`
- unique `file_id`

### 4. Implement exact mapping derivation + collision resolution contract
Implement canonical derivation from `doc/architecture/QUERY_MAPPING.md`:
- canonical ASCII input encoding for `mapping_seed`, `publish_version`,
  `slice_index`
- HMAC-SHA256 with domain-separated labels
- RFC4648 base32 lowercase no-padding materialization
- deterministic truncation by computed lengths
- fail if requested token length exceeds digest text capacity

Deterministic collision-resolution algorithm:
1. Canonicalize file processing order by ascending
   `(file_tag, file_id, publish_version)`.
2. For each file in that order, compute the minimal local `slice_token_len`
   that resolves intra-file token collisions.
3. Build global key set over `(file_tag, slice_token)`.
4. If a global collision exists, promote exactly one file: the earliest file
   in canonical order participating in a collision; increment only that file's
   `slice_token_len` by 1.
5. Recompute tokens for that file and rebuild collision checks.
6. Repeat until no collisions remain or promoted length exceeds limits
   (`dns_max_label_len`, digest text capacity, DNS constraints).
7. On any limit breach, fail startup with explicit collision reason code.

Termination and determinism:
- algorithm is monotonic (`slice_token_len` only increases)
- tie-breaks are deterministic by canonical order
- same inputs always produce the same final token lengths/tables or the same
  startup failure

### 5. Build startup-facing API in `dnsdle.py`
Add a single startup flow callable from CLI:
- parse args
- run full startup validation
- compute `max_ciphertext_slice_bytes` via dedicated budget module
- build immutable publish state
- print concise startup summary/log output

No DNS socket serve loop or client generation logic in this phase; only
construct and expose ready state.

### 6. Error and logging semantics
Deliver explicit startup taxonomy aligned to
`doc/architecture/ERRORS_AND_INVARIANTS.md`:
- classification: `startup_error`
- phase: `startup`, `config`, `publish`, `mapping`, `budget`
- stable reason codes (for example `invalid_config`, `unreadable_file`,
  `duplicate_plaintext_sha256`, `budget_unusable`, `mapping_collision`)

Required startup log fields:
- `phase`
- `classification`
- `reason_code`
- request-independent key context when available (`file_id`, `publish_version`,
  `plaintext_sha256`, `file_tag`, counts)

No sensitive logging (PSK, derived keys, raw plaintext bytes).

### 7. Documentation alignment during implementation
If implementation reveals ambiguity, update architecture docs in the same
change (clean break, no shim behavior), especially:
- `doc/architecture/CONFIG.md`
- `doc/architecture/PUBLISH_PIPELINE.md`
- `doc/architecture/QUERY_MAPPING.md`
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`
- `doc/architecture/ERRORS_AND_INVARIANTS.md`
- `doc/architecture/SERVER_RUNTIME.md`

## Affected Components
- `dnsdle.py`: add startup CLI entrypoint and wire publish/mapping core build.
- `dnsdle/__init__.py`: package marker and exported startup-core interfaces.
- `dnsdle/config.py`: config schema parsing, normalization, and invariant checks.
- `dnsdle/budget.py`: strict CNAME payload-budget calculation (`max_ciphertext_slice_bytes`).
- `dnsdle/publish.py`: deterministic publish pipeline and manifest assembly.
- `dnsdle/mapping.py`: deterministic `file_tag`/`slice_token` derivation and collision handling.
- `dnsdle/state.py`: immutable runtime publish-state structures and lookup map build.
- `doc/architecture/CONFIG.md`: update only if implementation forces clarified field semantics.
- `doc/architecture/PUBLISH_PIPELINE.md`: update only if implementation forces clarified algorithm details.
- `doc/architecture/QUERY_MAPPING.md`: update only if implementation forces clarified canonicalization/length behavior.
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`: align payload-budget contract wording if code clarifies strict math.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: align startup failure classification wording if needed.
- `doc/architecture/SERVER_RUNTIME.md`: align publish preparation wording if needed.

## Phased Execution
1. Scaffold module layout and shared constants/types.
2. Implement full startup config/bounds validation and canonical normalization.
3. Implement strict CNAME payload-budget math in dedicated module.
4. Implement publish pipeline for one file, then extend to multi-file.
5. Implement mapping derivation and deterministic collision-resolution procedure.
6. Build global lookup map and startup invariants.
7. Integrate startup build path into `dnsdle.py`.
8. Run reproducible validation matrix (below) and verify expected outcomes.
9. Apply doc clarifications required by actual implementation behavior.

## Validation Matrix
- Case 1 (deterministic baseline): same inputs/run twice -> identical
  `file_id`, `publish_version`, `file_tag`, token lengths, and lookup
  cardinality.
- Case 2 (duplicate content): two different paths with identical file content ->
  startup fails with `duplicate_plaintext_sha256`.
- Case 3 (collision pressure): constrained label limits that force token
  promotion -> deterministic promoted lengths and stable outputs across runs.
- Case 4 (unsatisfiable collisions): constraints too tight to resolve collisions
  -> startup fails with deterministic collision reason code.
- Case 5 (budget failure): settings/domain that make
  `max_ciphertext_slice_bytes <= 0` -> startup fails with `budget_unusable`.
- Case 6 (config validation): invalid representative fields (`domain`,
  `dns_max_label_len`, `file_tag_len`, `target_os`) -> startup fails with
  specific `invalid_config` reason.

## Success Criteria
- Running `dnsdle.py --domain <d> --files <f1,f2> --psk <p>` builds publish
  state without runtime exceptions for valid inputs.
- Same inputs produce identical derived publish/mapping outputs across restarts.
- Duplicate-content file inputs fail startup with explicit reason.
- Unresolved global `(file_tag, slice_token)` collisions after deterministic
  promotion to configured limits fail startup with stable reason codes.
- collision-resolution promotion behavior is deterministic for identical inputs.
- startup failures emit stable classification/phase/reason_code fields.
- No fallback remap behavior exists for invalid or ambiguous mapping state.
