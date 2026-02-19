# Plan: Multi-Domain Base-Domain Routing

## Summary
Introduce multi-domain support for request suffixes by replacing single-domain
configuration with `--domains` (comma-separated, non-overlapping domains). Keep
mapping and slice identity domain-agnostic so the server resolves the same
slice for the same `(file_tag, slice_token)` regardless of which configured
base domain is queried. Enforce size safety by clamping all length-sensitive
derivations to the longest configured domain.

## Problem
The current architecture and startup code assume exactly one `domain`, and many
contracts hardcode `<base_domain>` as singular. That blocks the requested
behavior where multiple domains are valid query suffixes for one publish set.
Without explicit contract updates, multi-domain behavior risks inconsistency in
mapping limits, parser classification, and client generation/runtime behavior.

## Goal
After implementation:
- server accepts `--domains d1,d2,...` and rejects invalid sets
  (empty/duplicate/overlapping domains)
- mapping lookup inputs and publish identities are independent of selected base
  domain
- all token-length and payload-capacity constraints are computed against the
  longest configured domain so every configured domain is safe
- architecture docs and startup/core code paths use one aligned multi-domain
  contract
- request-routing equivalence across configured domains is specified as an
  architecture contract and prepared in startup state, with runtime enforcement
  deferred until request-serving implementation exists

## Design
### 1. Config contract: move from `domain` to `domains`
- Replace required `--domain` with required `--domains` CSV.
- Normalize each domain (lowercase, strip trailing dot), reject duplicate
  normalized values (no silent dedupe), and require deterministic canonical
  order for valid unique inputs.
- Canonical order rule: sort normalized unique domain strings by ascending
  ASCII lexicographic order; every derived/output list must use this order.
- Enforce non-overlap invariant at startup:
  no configured domain may be equal to, or DNS-suffix-contained by, another
  configured domain on label boundaries.
- Add derived values:
  `domains` (ordered tuple), `domain_labels_by_domain` (ordered tuple aligned to
  `domains`), `longest_domain_labels`, `longest_domain_wire_len`.
- Internal set-like helpers (for membership/overlap checks) are allowed but must
  not be emitted in logs or serialized startup artifacts.
- Treat legacy `--domain` as removed (clean break) and update all call sites.

### 2. Mapping and publish constraints
- Keep mapping identity inputs unchanged (domain is not part of mapping key).
- Update mapping length-capacity rules to use `longest_domain_labels` when
  validating request QNAME limits.
- Update publish/budget wording so response-capacity calculations clamp to
  longest configured domain suffix.
- Keep fail-fast behavior when a valid mapping/payload cannot be materialized
  under worst-case domain length.

### 3. Request/response runtime semantics (contract-only in this phase)
- Request parse accepts any configured base domain suffix.
- Domain suffix selection must happen before mapping-field parsing to avoid
  ambiguity.
- Mapping lookup key remains `(file_tag, slice_token)`; base domain is a route
  qualifier, not identity input.
- For identical parsed mapping fields, response classification and resolved
  slice identity are identical across configured domains.
- CNAME output suffix policy should be fixed in docs:
  emit response using the queried base domain to preserve resolver chase
  behavior while keeping slice identity identical.
- This plan does not implement a DNS request-serving loop; it defines the
  contract and startup/runtime-state prerequisites for a follow-on serve-path
  implementation plan.

### 4. Client generation/runtime contract
- Generated clients embed `BASE_DOMAINS` (ordered list), not one `BASE_DOMAIN`.
- Runtime query construction uses one fixed policy:
  - `BASE_DOMAINS` order is canonical and deterministic.
  - initialize `domain_index = 0` at client startup.
  - each DNS request uses `BASE_DOMAINS[domain_index]` for QNAME suffix.
  - on transport-level retryable event (timeout/no response/socket retryable
    interruption), advance `domain_index = (domain_index + 1) % len(BASE_DOMAINS)`
    before the next request attempt.
  - on valid DNS response (slice accepted or valid duplicate slice), keep
    `domain_index` unchanged.
  - on non-retryable contract/crypto violation, fail immediately (no domain
    rotation fallback).
  - on process restart, reset `domain_index` to `0`.
- Validation/acceptance rules for payloads remain unchanged; only request suffix
  source changes.

### 5. Error classes, invariants, and observability
- Add startup errors for:
  invalid `--domains` syntax, duplicate normalized domain, overlapping domain
  set, and unsatisfiable longest-domain capacity constraints.
- Add request-miss classification for unknown/unconfigured base domain.
- Update invariants to state:
  mapping/slice identity are domain-agnostic within one configured domain set,
  and behavior is deterministic for any accepted base domain.
- Update startup and request logs to include domain-set context where relevant.

### 6. Documentation-first rollout
- First change set updates architecture docs to a consistent multi-domain model.
- Second change set updates startup/core code to match docs.
- Merge gate for doc/code alignment:
  - either merge documentation and startup/core code updates atomically in one
    change, or keep documentation changes on a non-default branch until matching
    startup/core code is ready for the same merge.
- Third change set updates generated-client architecture/runtime contracts
  (`BASE_DOMAIN` -> `BASE_DOMAINS`) and the deterministic selection algorithm
  specification only.
- Generated-client implementation changes are explicitly deferred to a follow-on
  execution plan in the same phase as serve-path implementation.
- Runtime request-handler enforcement is explicitly out of scope for this plan
  because a serve-path implementation is not currently present in the codebase.
- No compatibility shims for single-domain contracts.

## Affected Components
- `doc/architecture/CONFIG.md`: replace single `domain` config with `domains`,
  define normalization, non-overlap validation, and derived longest-domain
  values.
- `doc/architecture/ARCHITECTURE.md`: update top-level request model and
  component responsibilities from singular base domain to accepted domain set.
- `doc/architecture/QUERY_MAPPING.md`: define multi-domain QNAME acceptance and
  longest-domain clamping for token-length constraints.
- `doc/architecture/PUBLISH_PIPELINE.md`: update wire-input assumptions and
  worst-case domain-length sizing contract.
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`: update suffix and capacity
  calculations for multiple configured domains and longest-domain clamp.
- `doc/architecture/DNS_MESSAGE_FORMAT.md`: update qname/follow-up parsing and
  compression policy to support multiple valid base domains.
- `doc/architecture/SERVER_RUNTIME.md`: update startup validation and request
  parse flow for domain-set matching and deterministic cross-domain behavior.
- `doc/architecture/CLIENT_GENERATION.md`: switch embedded domain metadata from
  singular to list and define runtime domain-selection contract.
- `doc/architecture/CLIENT_RUNTIME.md`: update query-construction/runtime rules
  for selecting among embedded domains.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: add domain-set-specific startup
  and request invariants/reason classes.
- `dnsdle/config.py`: parse/validate `--domains`, enforce non-overlap, expose
  longest-domain derived fields, and remove `--domain`.
- `dnsdle/budget.py`: compute payload capacity from longest configured domain.
- `dnsdle/mapping.py`: apply request-QNAME token-length limits using longest
  configured domain labels.
- `dnsdle/state.py`: carry updated config/runtime fields for domain-set-aware
  request handling.
- `dnsdle.py`: update startup logs/output fields from single `domain` to
  domain-set representation.
- `dnsdle/__init__.py`: wire updated config/budget/mapping contracts in startup
  state build path.

## Phased Execution
1. Update architecture docs to replace singular base-domain contracts with
   multi-domain contracts and explicit longest-domain clamping rules.
2. Update startup config parsing/validation for `--domains`, including
   normalize-then-reject duplicate handling and non-overlap checks.
3. Update budget/mapping derivation logic to consume longest-domain derived
   fields and enforce worst-case length constraints.
4. Update startup/runtime state and startup logging to carry domain-set fields.
5. Update generated-client architecture/runtime contracts from `BASE_DOMAIN` to
   `BASE_DOMAINS` with deterministic domain-selection policy.
6. Verify validation and acceptance gates below before considering the plan
   complete.

## Validation Matrix
- Case 1 (valid multi-domain baseline):
  `--domains example.com,hello.com` with valid inputs starts successfully and
  produces deterministic startup artifacts.
- Case 2 (duplicate normalized domain rejection):
  `--domains EXAMPLE.com,example.com.` fails startup with explicit duplicate
  domain reason code (no silent dedupe).
- Case 3 (overlap rejection):
  `--domains example.com,sub.example.com` fails startup with explicit overlap
  reason code.
- Case 4 (unknown request suffix classification, contract level):
  architecture docs and error matrix explicitly classify non-configured base
  domain requests as deterministic miss behavior.
- Case 5 (longest-domain clamp correctness):
  with mixed domain lengths, derived token-length/payload-capacity limits match
  the longest configured domain, and startup fails if worst-case domain makes
  constraints unsatisfiable.
- Case 6 (cross-domain mapping identity contract):
  docs and startup state define mapping lookup as domain-agnostic so identical
  `(file_tag, slice_token)` resolve to the same canonical slice identity across
  accepted domains.
- Case 7 (deterministic ordering of valid inputs):
  same unique domain set provided in different CSV orders yields identical
  canonical stored order and identical derived startup outputs.
- Case 8 (client domain-selection determinism):
  contract-only check in this plan:
  `doc/architecture/CLIENT_GENERATION.md` and
  `doc/architecture/CLIENT_RUNTIME.md` specify that with
  `BASE_DOMAINS=[d0,d1,d2]`, retryable transport failures rotate suffix strictly
  `d0 -> d1 -> d2 -> d0`, valid responses keep current index, and process
  restart resets selection to `d0`.

## Success Criteria
- `--domain` is removed from the contract and replaced by `--domains` with
  explicit normalize-then-reject duplicate semantics.
- Domain overlap and duplicate normalized domains are hard startup failures with
  stable reason codes.
- Length/capacity derivations are explicitly defined and implemented against the
  longest configured domain.
- Architecture docs consistently define that base domain is a route qualifier,
  not part of mapping identity.
- `doc/architecture/CLIENT_GENERATION.md` and
  `doc/architecture/CLIENT_RUNTIME.md` define exactly one shared domain-selection
  algorithm (the fixed rotation-on-retry policy above), with no alternate modes.
- Startup/core code paths expose sufficient domain-set state for follow-on
  request-handler implementation to enforce cross-domain routing contract.
- Plan scope remains executable in current codebase by limiting runtime-routing
  outcomes to documented contract + startup-state prerequisites.

## Execution Notes
- Implemented multi-domain startup/config contract in `dnsdle/config.py`:
  added required `--domains`, normalize-then-reject duplicate semantics,
  non-overlap checks, canonical domain ordering, longest-domain derivations,
  and explicit failure for removed legacy `--domain`.
- Updated startup/core derivations to clamp against longest configured domain:
  `dnsdle/budget.py` and `dnsdle/mapping.py` now use
  `longest_domain_labels`; startup summary output in `dnsdle.py` now reports
  `domains` and `longest_domain`.
- Updated architecture contracts to the multi-domain model across affected docs
  (`CONFIG`, `ARCHITECTURE`, `QUERY_MAPPING`, `PUBLISH_PIPELINE`,
  `CNAME_PAYLOAD_FORMAT`, `DNS_MESSAGE_FORMAT`, `SERVER_RUNTIME`,
  `CLIENT_GENERATION`, `CLIENT_RUNTIME`, `ERRORS_AND_INVARIANTS`).
- Validation matrix execution:
  - Case 1: baseline `--domains example.com,hello.com` startup succeeded.
  - Case 2: duplicate normalized domain rejected with `duplicate_domain`.
  - Case 3: overlapping domains rejected with `overlapping_domains`.
  - Case 4: docs classify unknown/unconfigured base domain as deterministic miss.
  - Case 5: longest-domain clamp enforced; 220-char domain case failed startup
    with `budget_unusable` (`max_ciphertext_slice_bytes is not positive`).
  - Case 6: docs/state contract defines base domain as route qualifier, not
    mapping identity.
  - Case 7: canonical ordering deterministic; reversed CSV order produced
    byte-identical startup logs.
  - Case 8: contract-only deterministic `BASE_DOMAINS` rotation/reset policy
    defined in client architecture/runtime docs.
- Deviation: `dnsdle/state.py` and `dnsdle/__init__.py` required no code
  changes because existing startup-state wiring already carries updated config
  and budget structures after config/budget/module updates.
- Execution commit hash: `84924441f200b8d8c9ea3afe271528b24dfbb2af`.
