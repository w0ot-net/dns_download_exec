# Plan: CLI/Config Separation Cleanup

## Summary
Refactor startup configuration handling so CLI argument parsing and config
normalization are separate responsibilities. Keep startup behavior and reason
codes deterministic while reducing coupling and parser-specific logic in
`dnsdle/config.py`. Execute as a clean break: remove the legacy
`parse_cli_config(argv)` compatibility path and update all call sites in the
same change.

## Problem
`dnsdle/config.py` currently mixes two concerns:
- CLI transport parsing (`argparse`, raw argv handling, removed-flag detection).
- Config normalization/invariant enforcement (domains/files/numeric bounds).

This makes the file larger than necessary, harder to reason about in isolation,
and pushes parser mechanics into the same module that defines core config
invariants.

## Goal
After implementation:
- CLI parsing lives in a dedicated module with explicit parse behavior.
- `dnsdle/config.py` focuses on normalization/validation and derived values.
- Startup contracts and failure semantics remain fail-fast and deterministic.
- Architecture docs explicitly reflect parse-then-normalize startup flow.

## Design
### 1. Introduce a dedicated CLI parser module
- Add `dnsdle/cli.py` for startup flag parsing only.
- Move `argparse` parser construction from `dnsdle/config.py` into this module.
- Prevent ambiguous long-option prefix matching in a Python 2.7/3.x-safe way:
  - use `allow_abbrev=False` where supported by runtime `argparse`
  - keep deterministic no-abbrev behavior on Python 2.7 via explicit long-option
    token validation before `argparse` parse.
- Keep explicit rejection for removed legacy `--domain` with stable startup
  error semantics.
- Preserve parser failure contract: invalid CLI syntax/options must raise
  `StartupError` (not raw `SystemExit`) with stable startup fields/reason shape.
- Return a parsed args object without applying config invariants in this module.

### 2. Make `dnsdle/config.py` normalization-only
- Remove `argparse` and argv parsing responsibilities from `dnsdle/config.py`.
- Keep existing normalization helpers and fail-fast invariant checks in
  `dnsdle/config.py` (domains, files, numeric bounds, derived longest-domain
  fields, response suffix constraints).
- Add one clear entrypoint that converts parsed args into immutable `Config`
  (for example, `build_config(parsed_args)`).
- Clean break requirement: remove `parse_cli_config(argv)` entirely and update
  all call sites to the explicit parse-then-build flow in the same change.
- Normalize remaining singular-domain wording in config validation messages to
  match the multi-domain contract where applicable.

### 3. Rewire startup build path
- Update startup assembly in `dnsdle/__init__.py` to:
  1) parse argv via `dnsdle/cli.py`
  2) normalize/build config via `dnsdle/config.py`
  3) proceed with unchanged budget/publish/mapping pipeline
- Update direct call sites/imports that currently use `parse_cli_config` (tests
  and any runtime/module code) to the new explicit two-step flow.
- Keep runtime output/log shape unchanged unless a wording fix is required by
  the config cleanup.

### 4. Architecture document alignment
- Update architecture text to represent startup as two explicit steps:
  CLI parse and config normalization/validation.
- Keep external config surface unchanged (`--domains`, etc.); this is a
  structural refactor, not a config-contract expansion.

### 5. Validation approach
- Add/update unit tests for parser/config split behavior:
  - `unit_tests/test_cli.py` (new):
    - removed `--domain` rejected with `StartupError`
    - invalid/unknown flags raise `StartupError` (no `SystemExit`)
    - abbreviated long options are rejected deterministically
    - valid argument set parses successfully and preserves raw values expected by
      config normalization
  - `unit_tests/test_config.py`:
    - validate new `build_config(parsed_args)` entrypoint directly
    - preserve existing domain/normalization invariant checks
  - `unit_tests/test_startup_state.py`:
    - startup path still succeeds with parse-then-build flow
- Run bounded startup sanity checks (no hanging serve-loop dependency):
  - call `dnsdle.build_startup_state(argv)` for:
    - valid multi-domain launch
    - removed `--domain` rejection
    - duplicate/overlap domain failures
- Run targeted module tests:
  - `python -m unittest unit_tests.test_cli`
  - `python -m unittest unit_tests.test_config`
  - `python -m unittest unit_tests.test_startup_state`
- Run syntax checks for touched modules:
  - `python -m py_compile dnsdle/cli.py dnsdle/config.py dnsdle/__init__.py`
- Python compatibility gate:
  - where both interpreters are available, run syntax + targeted tests under
    both Python 2.7 and Python 3.x before completion.

## Affected Components
- `dnsdle/cli.py`: new module containing startup CLI parsing and removed-flag
  handling.
- `dnsdle/config.py`: reduced to config normalization/validation and immutable
  config construction.
- `dnsdle/__init__.py`: startup wiring updated to parse args before config
  normalization.
- `unit_tests/test_cli.py` (new): parser behavior invariants (`StartupError`
  surfaces, removed flag rejection, no-abbrev behavior).
- `unit_tests/test_config.py`: switch to config-build entrypoint and keep
  normalization invariant coverage.
- `unit_tests/test_startup_state.py`: ensure startup integration path remains
  stable after call-site rewiring.
- `doc/architecture/ARCHITECTURE.md`: clarify component boundary between CLI
  parsing and config normalization.
- `doc/architecture/SERVER_RUNTIME.md`: update startup validation sequence to
  reflect parse-then-normalize flow.
- `doc/architecture/CONFIG.md`: clarify config processing flow while preserving
  existing server config surface and invariants.

## Success Criteria
- `dnsdle/config.py` has no `argparse` usage and no direct raw-argv parsing.
- `parse_cli_config(argv)` is removed; no compatibility wrapper remains.
- All call sites are updated to explicit parse-then-build flow in the same
  change.
- CLI parsing behavior is centralized in `dnsdle/cli.py` with deterministic
  fail-fast behavior for removed flags, invalid syntax, and abbreviated long
  options.
- CLI parse errors continue to surface as `StartupError` with stable startup
  logging semantics (no parser-driven process exit path).
- Startup path still produces identical config-derived behavior for equivalent
  valid inputs.
- Architecture docs consistently describe startup config handling as a two-step
  parse + normalization flow.
