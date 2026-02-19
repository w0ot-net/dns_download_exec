# Plan: Recursive DNS Compatibility for Client Payload Validation (v1)

## Summary
Fix the client parity response-envelope validation so generated clients work through recursive DNS, which is the primary deployment path. The current parser is overly strict on authoritative-only header/count assumptions and rejects legitimate recursive-resolver responses. This plan narrows validation to protocol invariants that must hold end-to-end while removing resolver-topology-specific checks. It also updates architecture documentation to make recursive DNS support an explicit MUST-level contract.

## Problem
`dnsdle/client_payload.py` and the generated client template in `dnsdle/generator.py` (`_parse_response_for_cname()`) currently enforce authoritative-response constraints (`AA=1`, `RA=0`, exact section-count shape, EDNS-driven `ARCOUNT`) that are not guaranteed when queries traverse recursive resolvers. As a result, valid recursive DNS responses can be rejected as parse failures before CNAME payload validation/MAC verification runs. This conflicts with documented runtime defaults (`resolver_mode = system`) and with intended real-world usage where recursive DNS is expected for most client traffic.

## Goal
After implementation:
- Client payload decode/verify succeeds for valid recursive-resolver responses carrying the expected slice CNAME.
- Parser still fails fast on true contract violations (bad transaction association, non-query response, non-`NOERROR`, missing/ambiguous required CNAME, malformed payload, MAC mismatch).
- Architecture docs explicitly state recursive DNS compatibility is mandatory in v1 and is the default/primary operating mode.
- No compatibility shim layer is added; one clear response-validation contract is enforced.

## Design
### 0. Transport-asymmetry reference for this change
`doc/architecture/ASYMMETRY.md` is currently not present in this repository.
For this DNS-only compatibility change, treat the following as the normative
transport contract set during execution/review:
- `doc/architecture/DNS_MESSAGE_FORMAT.md`
- `doc/architecture/CLIENT_RUNTIME.md`
- `doc/architecture/ERRORS_AND_INVARIANTS.md`

This plan does not change tunnel/multi-transport behavior; it only adjusts
client acceptance rules for recursive DNS response envelopes.

### 1. Replace authoritative-only envelope checks with recursive-compatible invariants
Update `extract_response_cname_labels()` in `dnsdle/client_payload.py`:
1. Keep strict checks for invariants that must hold regardless of resolver topology:
- `QR=1`
- opcode `QUERY`
- matching transaction ID
- `RCODE=NOERROR` for slice-success path
- `TC=0`
- exactly one echoed question matching request qname/qtype/qclass
- exactly one matching `IN CNAME` answer for the requested owner name
2. Remove checks that are resolver-topology-specific and break recursive compatibility:
- `AA=1` requirement
- `RA=0` requirement
- exact section-count requirement (`QDCOUNT=1, ANCOUNT=1, NSCOUNT=0`)
- exact EDNS-mode `ARCOUNT` requirement tied to request-side `dns_edns_size`
3. Retain fail-fast behavior for ambiguity and malformed wire content:
- no matching CNAME or multiple matching CNAME answers remains fatal parse error
- compressed-name decode failures and payload suffix mismatch remain fatal parse errors
4. Remove dead imports of `DNS_FLAG_AA` and `DNS_FLAG_RA` from `dnsdle/client_payload.py` since their only consumers are the checks being removed.

### 2. Update generated client template with the same recursive-compatible invariants
Update `_parse_response_for_cname()` in `_CLIENT_TEMPLATE` within `dnsdle/generator.py`:
1. Remove the same authoritative-only checks removed from `client_payload.py`:
- `AA=1` requirement (template line `if (flags & DNS_FLAG_AA) == 0`)
- `RA=0` requirement (template line `if flags & DNS_FLAG_RA`)
- exact section-count requirement `qdcount != 1 or ancount != 1 or nscount != 0`
- EDNS-driven ARCOUNT requirement (`expected_arcount` / `arcount != expected_arcount`)
2. Keep the same recursive-compatible invariants: `QR=1`, opcode `QUERY`, matching ID, `RCODE=NOERROR`, `TC=0`, question echo, exactly one matching `IN CNAME` answer.
3. Remove dead constant definitions `DNS_FLAG_AA` and `DNS_FLAG_RA` from the template since their only consumers are the checks being removed. `DNS_EDNS_SIZE` stays because `_build_dns_query()` still uses it for request-side OPT construction.

### 3. Remove dead `dns_edns_size` parameter (clean break)
- Remove the `dns_edns_size` parameter from `extract_response_cname_labels()` and `decode_response_slice()` in `dnsdle/client_payload.py`. With the ARCOUNT check removed, this parameter has no remaining use.
- Update all call sites: `decode_response_slice()` internal call to `extract_response_cname_labels()`, and test call sites in `unit_tests/test_client_payload_parity.py`.
- Per project policy: prefer clean breaks over dead compatibility parameters.
- Keep parse vs crypto failure boundaries intact:
- envelope/CNAME/payload shape issues -> `ClientParseError`
- MAC verification mismatch -> `ClientCryptoError`
- Remove now-obsolete reason branches generated only by authoritative-only checks (`response_not_aa`, `response_ra_set`, `response_counts_invalid`, `response_arcount_invalid`).

### 4. Make recursive DNS support explicit in architecture docs
Update docs so contracts are unambiguous and consistent with implementation:
1. `doc/architecture/ARCHITECTURE.md`
- add explicit normative statement: generated client runtime MUST support recursive DNS responses; this is the primary operating path.
2. `doc/architecture/CONFIG.md`
- keep `resolver_mode = system` default and clarify it is the expected default deployment mode.
3. `doc/architecture/DNS_MESSAGE_FORMAT.md`
- replace authoritative-only client response-validation bullets with recursive-compatible invariants.
- explicitly forbid dependence on `AA`/`RA`/exact section-count shape for success-path acceptance.
4. `doc/architecture/CLIENT_RUNTIME.md`
- align response-validation narrative with recursive-compatible parser rules.
5. `doc/architecture/CLIENT_GENERATION.md`
- clarify generated client parser logic must accept recursive-resolver responses under the same invariants.
6. `doc/architecture/ERRORS_AND_INVARIANTS.md`
- align parse-failure semantics so recursive-compatible envelopes are accepted and only true contract violations map to parse failure.

### 5. Validation approach
Use deterministic validation commands during execution:
1. run parity/regression suites already covering parser/crypto paths:
- `python -m unittest unit_tests.test_client_payload_parity`
- `python -m unittest unit_tests.test_dnswire`
- `python -m unittest unit_tests.test_cname_payload`
2. run explicit recursive-response acceptance tests (new deterministic unit tests):
- `python -m unittest unit_tests.test_client_payload_parity.ClientPayloadParityTests.test_accepts_recursive_resolver_style_response`
- `python -m unittest unit_tests.test_client_payload_parity.ClientPayloadParityTests.test_accepts_non_authoritative_response_with_extra_sections`
3. run explicit negative parsing tests to confirm malformed/ambiguous answers remain fatal:
- `python -m unittest unit_tests.test_client_payload_parity.ClientPayloadParityTests.test_rejects_ambiguous_matching_cname_answers`
- `python -m unittest unit_tests.test_client_payload_parity.ClientPayloadParityTests.test_rejects_missing_matching_cname_answer`
- `python -m unittest unit_tests.test_client_payload_parity.ClientPayloadParityTests.test_rejects_tc_set_even_with_valid_cname`

## Affected Components
- `dnsdle/client_payload.py`: remove authoritative-only response gating, enforce recursive-compatible response invariants, remove dead `dns_edns_size` parameter from public entrypoints, remove dead `DNS_FLAG_AA`/`DNS_FLAG_RA` imports.
- `dnsdle/generator.py`: update `_parse_response_for_cname()` in `_CLIENT_TEMPLATE` with the same recursive-compatible invariants; remove dead `DNS_FLAG_AA`/`DNS_FLAG_RA` constant definitions from template.
- `unit_tests/test_client_payload_parity.py`: add deterministic recursive-envelope acceptance tests and ambiguity/missing-CNAME/TC negative tests; update call sites to remove `dns_edns_size` argument.
- `doc/architecture/ARCHITECTURE.md`: declare recursive DNS support as a MUST-level client runtime invariant and primary usage mode.
- `doc/architecture/CONFIG.md`: clarify recursive/system resolver mode is the default expected deployment path.
- `doc/architecture/DNS_MESSAGE_FORMAT.md`: redefine client response-acceptance rules to be recursive-compatible.
- `doc/architecture/CLIENT_RUNTIME.md`: align runtime validation flow with recursive-compatible envelope parsing.
- `doc/architecture/CLIENT_GENERATION.md`: align generated-client parser contract with recursive-compatible acceptance rules.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: align parse/crypto failure boundaries with recursive-compatible response handling.
