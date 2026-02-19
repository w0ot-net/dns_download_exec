# Plan: Runtime CNAME Serving (v1)

## Summary
Implement the first end-to-end server runtime path for `dns_download_exec` after startup state build: bind UDP, parse DNS queries, resolve deterministic mapping keys, and emit CNAME answers. This plan also closes entrypoint/runtime wiring gaps so the executable transitions cleanly from startup validation to serve loop without stale single-domain assumptions. The result is a deterministic, fail-fast DNS responder for the existing publish/mapping state.

## Problem
Current code builds startup state and exits, so there is no live DNS serving path. The entrypoint/runtime boundary is incomplete for operational use: startup logs exist, but there is no request loop, no DNS parser/encoder, and no mapping-to-wire response implementation. Without this, the publish/mapping core cannot be exercised by clients.

## Goal
After implementation:
- `dnsdle.py` performs startup validation/build once, logs startup summaries, then enters a UDP serve loop.
- Query routing uses `<slice_token>.<file_tag>.<selected_base_domain>` and resolves via immutable runtime lookup.
- Valid mapped requests return deterministic `NOERROR` + one IN CNAME answer.
- Deterministic misses return `NXDOMAIN`; internal runtime faults return `SERVFAIL`.
- EDNS behavior honors existing config contract: default `dns_edns_size=1232` emits OPT, `dns_edns_size=512` is explicit no-OPT mode.
- Runtime behavior aligns with architecture docs and stable reason-code logging semantics.

## Design
### Scope
This phase covers only:
1. startup/entrypoint runtime wiring cleanup (#1)
2. DNS UDP request handling + CNAME response engine (#2)

Out of scope for this phase:
- generated client implementation details
- new qtype transports beyond current v1 contract
- changing publish/mapping derivation semantics
- compatibility shims for legacy CLI/domain behavior

### 1. Entrypoint and lifecycle wiring (#1)
1. Keep startup-state build as a strict prerequisite to binding socket.
2. Ensure there are no stale single-domain runtime assumptions (only `domains`/matched selected domain).
3. Convert `main()` lifecycle to:
   - build startup state
   - emit startup summaries
   - start server loop
   - return non-zero on fatal startup or bind failure
4. Preserve machine-readable JSON logging fields and stable startup error structure.

### 2. Introduce explicit DNS wire codec module
Add a dedicated stdlib-only DNS codec module (single responsibility):
- parse request header/question safely
- decode QNAME with compression-pointer loop/bounds protection
- encode response headers/questions/answers deterministically
- encode optional OPT additional record based on `dns_edns_size`
- support only required v1 record types/classes (A query + CNAME answer; A answer for follow-up)

Fail-fast invariants:
- malformed envelope/name parse => non-parseable datagram (drop)
- parseable but unsupported shape => deterministic miss classification
- no fallback remap on parse ambiguity

### 3. Implement runtime request classifier and responder
Add server runtime module that:
1. Receives UDP datagrams.
2. Parses DNS request (single-question contract).
3. Classifies request in deterministic order:
   - CNAME-chase follow-up (`<payload_labels>.<response_label>.<selected_base_domain>`, qtype A)
   - v1 slice query (`<slice_token>.<file_tag>.<selected_base_domain>`, qtype A)
   - deterministic miss
4. For slice path:
   - select matched configured domain suffix
   - resolve `(file_tag, slice_token)` from `lookup_by_key`
   - retrieve canonical slice bytes from runtime publish state
   - build CNAME target using selected domain + configured `response_label`
   - emit `NOERROR` with one CNAME answer and configured TTL
5. For follow-up A path:
   - emit deterministic synthetic A answer (`0.0.0.0`) with configured TTL
6. For misses/faults:
   - emit `NXDOMAIN` or `SERVFAIL` per error matrix

### 4. Runtime-state lookup ergonomics
Minimize per-request complexity by extending immutable runtime state with direct lookup indexes needed by server path, avoiding linear scans over `publish_items` for each request. Keep lookup structures immutable (`FrozenDict`) and built once at startup.

### 5. EDNS and packet policy
1. Default mode (`dns_edns_size > 512`): include OPT RR in responses.
2. Classic mode (`dns_edns_size == 512`): omit OPT RR (non-default no-OPT behavior).
3. Do not emit truncated (`TC=1`) slice responses in v1.
4. Treat configuration-driven response-construction infeasibility (including required compression-pointer layout constraints) as a fatal startup invariant violation; do not start serving in that state.
5. Reserve runtime `SERVFAIL` for true internal faults after startup (for example, unexpected encode/state inconsistency), with stable reason codes.

### 6. Logging and stable error taxonomy
Add runtime request log classes with stable reason codes aligned to architecture:
- `served` (slice response)
- `followup` (A follow-up response)
- `miss` (deterministic miss)
- `runtime_fault` (internal processing fault)

Required log context where available:
- selected base domain
- `file_tag`, `slice_token`
- classification/reason

No sensitive-value logs (PSK, raw file bytes).

### 7. Documentation alignment in same change
Update architecture docs to match implemented behavior exactly (clean break, no shim wording). Document deterministic classification order, follow-up handling, and EDNS response policy as implemented.

## Affected Components
- `dnsdle.py`: transition from startup-only run to startup + UDP serve lifecycle; keep startup/fatal logging contract.
- `dnsdle/__init__.py`: expose/import runtime entry helpers as needed for clean CLI wiring.
- `dnsdle/state.py`: extend immutable runtime lookup structures for efficient request-path slice resolution.
- `dnsdle/constants.py`: add DNS wire constants (qtype/class/rcode/flags/opcodes, fixed RR constants) used by parser/encoder.
- `dnsdle/config.py`: only if needed for runtime-serving invariants or explicit no-OPT UX surface; preserve current EDNS contract and fail-fast validation.
- `dnsdle/budget.py`: only if runtime response-construction constraints require clarified shared constants/logic reuse.
- `dnsdle/dnswire.py` (new): DNS request parser and deterministic response encoder utilities.
- `dnsdle/server.py` (new): UDP socket loop, request classification, mapping resolution, and response dispatch.
- `dnsdle/cname_payload.py` (new or equivalent): deterministic CNAME target materialization from canonical slice bytes + response/domain suffix.
- `doc/architecture/ARCHITECTURE.md`: align component and startup/download flow wording with actual runtime implementation boundary.
- `doc/architecture/SERVER_RUNTIME.md`: align serve loop, classifier order, and response-path behavior to code.
- `doc/architecture/DNS_MESSAGE_FORMAT.md`: align packet/header/count semantics, compression use, and EDNS/no-OPT behavior to implementation.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: align request-classification matrix and runtime fault boundaries to implementation.
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`: align server CNAME materialization details if code clarifies/limits encoding behavior.

## Phased Execution
1. Add DNS wire codec module and constants.
2. Add runtime server loop and request classifier with explicit response matrix.
3. Extend runtime state for O(1) slice retrieval in request path.
4. Wire `dnsdle.py` to run serve loop after successful startup build.
5. Align architecture docs with final implemented behavior and invariants.

## Validation
- Startup success path: valid config binds UDP and enters loop.
- Startup invariant failure path: config that cannot satisfy required response-construction constraints fails before bind (no serve loop).
- Mapped query path: returns `NOERROR` + one CNAME answer.
- Deterministic miss path: returns `NXDOMAIN` with empty answer section.
- Follow-up chase path: returns `NOERROR` + one A answer (`0.0.0.0`).
- Runtime internal fault simulation (post-startup): returns `SERVFAIL` with stable reason code logging.
- EDNS policy: default includes OPT at `1232`; explicit `512` omits OPT.
- Determinism check: repeated identical queries for same slice produce identical answer payload and TTL.

## Success Criteria
- `dnsdle.py` runs as an actual UDP DNS server after startup instead of exiting immediately.
- Request behavior matches documented response matrix (`NOERROR` CNAME, follow-up A, `NXDOMAIN`, `SERVFAIL`).
- Query routing uses configured multi-domain suffix matching with no single-domain fallback logic.
- Runtime response building remains deterministic for fixed runtime state.
- Architecture docs listed above are updated to reflect exact implemented runtime behavior.
