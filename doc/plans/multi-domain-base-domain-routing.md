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
- mapping lookup and slice identity are independent of selected base domain
- requests to any configured base domain return the same routing outcome and
  same canonical slice identity for identical `(file_tag, slice_token)`
- all token-length and payload-capacity constraints are computed against the
  longest configured domain so every configured domain is safe
- architecture docs and code paths use one aligned multi-domain contract

## Design
### 1. Config contract: move from `domain` to `domains`
- Replace required `--domain` with required `--domains` CSV.
- Normalize each domain (lowercase, strip trailing dot), deduplicate, and
  require deterministic canonical order.
- Enforce non-overlap invariant at startup:
  no configured domain may be equal to, or DNS-suffix-contained by, another
  configured domain on label boundaries.
- Add derived values:
  `domains`, `domain_labels_set`, `longest_domain_labels`,
  `longest_domain_wire_len`.
- Treat legacy `--domain` as removed (clean break) and update all call sites.

### 2. Mapping and publish constraints
- Keep mapping identity inputs unchanged (domain is not part of mapping key).
- Update mapping length-capacity rules to use `longest_domain_labels` when
  validating request QNAME limits.
- Update publish/budget wording so response-capacity calculations clamp to
  longest configured domain suffix.
- Keep fail-fast behavior when a valid mapping/payload cannot be materialized
  under worst-case domain length.

### 3. Request/response runtime semantics
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

### 4. Client generation/runtime contract
- Generated clients embed `BASE_DOMAINS` (ordered list), not one `BASE_DOMAIN`.
- Runtime query construction selects domains by deterministic policy
  (for example fixed-primary with deterministic failover or round-robin).
- Regardless of selection policy, validation/acceptance rules remain unchanged;
  only request suffix source changes.

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
- Third change set updates generated-client contract and implementation.
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
