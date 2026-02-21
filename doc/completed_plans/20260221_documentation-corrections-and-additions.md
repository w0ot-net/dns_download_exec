# Plan: Documentation Corrections and Additions

## Summary

Fix stale module references in two architecture docs, create a new STAGER.md
documenting the stager pipeline, and add a startup convergence section to
SERVER_RUNTIME.md.

## Problem

1. **Stale module references**: ARCHITECTURE.md (lines 100-111) and
   ERRORS_AND_INVARIANTS.md (lines 195-199) reference `dnsdle/client_payload.py`
   and `dnsdle/client_reassembly.py`.  These files do not exist.  The
   functionality was consolidated into `dnsdle/client_runtime.py`.

2. **No stager documentation**: The stager subsystem (`stager_generator.py`,
   `stager_template.py`, `stager_minify.py`) is a complete pipeline with no
   dedicated architecture doc.  CLIENT_GENERATION.md has a brief "Stager
   Integration" section but omits the `@@PLACEHOLDER@@` template substitution
   system, the minification pipeline, the base64+zlib encoding with compilation
   verification, the managed-dir output contract, and the `--verbose`
   passthrough.

3. **Startup convergence undocumented**: The two-phase iterative convergence
   algorithm in `build_startup_state()` (`__init__.py`) is the most
   algorithmically complex piece in the codebase.  SERVER_RUNTIME.md mentions
   startup phases at a high level but does not explain the fixed-point iteration
   on `query_token_len`, why convergence is monotonic, or the 10-iteration
   safety bound.

## Goal

- ARCHITECTURE.md and ERRORS_AND_INVARIANTS.md reference only modules that exist
  and accurately describe current code boundaries.
- A new `doc/architecture/STAGER.md` covers the full stager pipeline: template
  assembly, placeholder substitution, minification, encoding, output contract,
  and invariants.
- SERVER_RUNTIME.md contains a new section explaining the startup convergence
  algorithm with enough detail to understand why it terminates and what
  invariants it enforces.

## Design

### 1. Fix stale module references

**ARCHITECTURE.md** -- Replace the "Client Parity Core Boundaries" section
(lines 100-111).  The three-module split (`dnswire.py`, `client_payload.py`,
`client_reassembly.py`) is replaced by a description reflecting the actual
two-module structure:

- `dnsdle/dnswire.py`: low-level DNS wire parsing primitives (header, name
  decode, RR traversal, pointer safety checks).
- `dnsdle/client_runtime.py`: all client-side protocol logic -- CLI parsing,
  download loop, DNS query/response handling, CNAME payload decode, crypto
  verification, reassembly, decompression, and output writing.

Keep the compressed-name decoding invariant ("No module may maintain a second
compressed-name decoding implementation").

**ERRORS_AND_INVARIANTS.md** -- Replace the "Parity-core boundary" lines
(195-199).  Update to reference `client_runtime.py` as the single authority for
parse/format (`4`), crypto verification (`5`), and reconstruction/decompress
(`6`) failures.

### 2. Create STAGER.md

New file: `doc/architecture/STAGER.md`

Sections:

- **Overview**: Purpose (one-liner bootstrap that downloads the universal client
  via DNS, then `exec`s it with payload metadata via `sys.argv`).
- **Pipeline**: Three-module pipeline flow: template assembly
  (`stager_template.py`) -> minification (`stager_minify.py`) -> generation
  (`stager_generator.py`).
- **Template Assembly**: `build_stager_template()` concatenates
  `_STAGER_PRE_RESOLVER` + extracted resolver sources (Windows and Linux via
  `# __TEMPLATE_SOURCE__` sentinel) + `_STAGER_DISCOVER` + `_STAGER_SUFFIX`.
- **Placeholder Substitution**: The 19 `@@PLACEHOLDER@@` tokens (13
  deployment-wide from the universal client's publish metadata, plus 5 per-file
  payload params, plus 1 domain labels tuple).  `repr()` encoding.
  Fail-fast on unreplaced placeholders.
- **Minification**: The 5-pass pipeline (strip comments, strip blanks, protect
  string literals + rename identifiers, reduce indentation, semicolon-join).
  Deterministic: same input -> same output.
- **Encoding and Verification**: Compile minified source (syntax check),
  encode ASCII, zlib compress level 9, base64 encode, wrap in
  `python3 -c "import base64,zlib;exec(...)"` one-liner.
- **Output Contract**: One `.1-liner.txt` per payload file written
  atomically (`.tmp` + rename) to the managed output directory
  (`<client_out_dir>/dnsdle_v1/`).  Filename: `<payload_basename>.1-liner.txt`.
  Write failure triggers transactional cleanup.
- **Runtime Behavior**: Stager downloads universal client slices via DNS, verifies
  (compressed size + SHA-256), decompresses, then `exec()`s the client source
  after reconstructing `sys.argv` with payload metadata.  `--verbose` detected
  without consuming it (forwarded to universal client).  `--psk` and `--resolver`
  parsed and forwarded.  60-second no-progress deadline per slice.
- **Invariants**: No unreplaced placeholders; minified source compiles;
  one-liner is ASCII-clean; write is atomic.

Add cross-references in ARCHITECTURE.md (Related Docs) and SERVER_RUNTIME.md
(Related Docs).

### 3. Document startup convergence in SERVER_RUNTIME.md

Add a new section "Startup Convergence" between "Publish Preparation" and
"Deterministic Restart Semantics".  Content:

- **Problem**: Token length and slice budget are mutually dependent.  Longer
  query tokens consume QNAME bytes, reducing CNAME payload budget, producing
  more slices, which may require longer tokens to avoid collisions.
- **Algorithm**: Fixed-point iteration starting at `query_token_len = 4`.
  Each iteration: compute budget, build all publish items (user files + universal
  client), apply deterministic mapping, find maximum realized
  `slice_token_len`.  If `realized <= query_token_len`, converged.  Otherwise
  set `query_token_len = realized` and repeat.
- **Monotonicity**: `query_token_len` only increases, and the realized token
  length is bounded by a finite alphabet, so convergence is guaranteed within
  a small number of iterations.
- **Safety bound**: 10 iterations.  Non-convergence raises
  `StartupError("token_convergence_failed")`.
- **Post-convergence invariant**: After convergence, the combined mapping is
  used to build `RuntimeState`.  The universal client publish item must be
  present in the combined mapping (`mapping_stability_violation` if not).

Also fix the "four phases" / "five items" numbering inconsistency in the
Process Lifecycle section (line 21 says "four phases" but lists 5 items).

## Affected Components

- `doc/architecture/ARCHITECTURE.md`: Fix "Client Parity Core Boundaries"
  section; add STAGER.md to Related Docs.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: Fix "Parity-core boundary"
  lines.
- `doc/architecture/SERVER_RUNTIME.md`: Add "Startup Convergence" section;
  fix phase count text; add STAGER.md to Related Docs.
- `doc/architecture/STAGER.md`: New file documenting the full stager pipeline.

## Execution Notes

Executed 2026-02-21.  All plan items implemented as designed with no deviations.

1. **ARCHITECTURE.md**: Renamed section "Client Parity Core Boundaries" to
   "Client Protocol Modules"; replaced `client_payload.py`/`client_reassembly.py`
   references with `client_runtime.py`; added STAGER.md to Related Docs.
2. **ERRORS_AND_INVARIANTS.md**: Replaced "Parity-core boundary" with
   "Client module boundary" referencing `client_runtime.py` as single authority
   for exit codes 4/5/6.
3. **STAGER.md**: Created with sections: Overview, Template Assembly,
   Placeholder Substitution (19-token table), Minification (5-pass pipeline),
   Encoding and Verification, Output Contract, Runtime Behavior, Invariants,
   Related Docs.
4. **SERVER_RUNTIME.md**: Fixed "four phases" to "five phases"; added
   "Startup Convergence" section (Algorithm, Monotonicity, Post-Convergence);
   added STAGER.md to Related Docs.

Commit: 8830063
