# DNS Message Format

This document defines the v1 DNS packet-level contract for request and response
messages used by the download protocol.

It complements:
- `doc/architecture/QUERY_MAPPING.md` (name mapping)
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md` (slice payload envelope)
- `doc/architecture/ERRORS_AND_INVARIANTS.md` (error matrix)

---

## Goals

1. Define exact DNS header/question/answer behavior.
2. Require deterministic packet construction for identical inputs.
3. Maximize CNAME payload capacity with safe compression pointers.
4. Define deterministic miss behavior for non-slice DNS queries.

---

## Supported Transport

- UDP/IPv4 DNS only in v1.
- One request message maps to one response message.
- No TCP fallback handling in v1 runtime contract.

---

## Request Message Contract

Generated client emits requests with:
- `QDCOUNT = 1`
- `ANCOUNT = 0`
- `NSCOUNT = 0`
- `ARCOUNT = 0`
- `QCLASS = IN`
- `QTYPE = A` (fixed in v1)
- `QNAME = <slice_token>.<file_tag>.<base_domain>`

Query flags:
- `RD = 1`
- `QR = 0`
- other flags cleared

The server must reject malformed requests and apply the response matrix in
`doc/architecture/ERRORS_AND_INVARIANTS.md`.

---

## Response Header Contract

All parseable responses emitted by the server set:
- `QR = 1`
- `AA = 1`
- `RA = 0`
- `TC = 0`

ID handling:
- response `ID` must equal request `ID`.

Question handling:
- response echoes exactly one question matching request qname/qtype/qclass.

---

## Slice Response (Primary Path)

For a valid mapped slice request:
- `RCODE = NOERROR`
- `QDCOUNT = 1`
- `ANCOUNT = 1`
- `NSCOUNT = 0`
- `ARCOUNT = 0`

Answer RR:
- `NAME`: compression pointer to question name start (offset 12)
- `TYPE`: `CNAME`
- `CLASS`: `IN`
- `TTL`: configured `ttl`
- `RDATA`: CNAME target from `doc/architecture/CNAME_PAYLOAD_FORMAT.md`

---

## Compression Policy

v1 requires DNS name compression for served CNAME responses:
- answer owner name uses pointer to question qname
- CNAME target suffix uses pointer to base-domain location in question qname
  when constructing compressed RDATA form

Reason:
- this is required to maximize payload bytes and satisfy MTU constraints.

Startup invariant:
- if required compression-pointer layout cannot be constructed safely for the
  configured domain/suffix rules, startup fails.

Parser safety:
- name decoder must detect invalid pointers and pointer loops.

---

## Non-Slice Query Handling

Recursive resolvers may issue follow-up A queries for CNAME targets.

Detection:
- request qname matches `<payload_labels>.<response_label>.<base_domain>`
- request qtype is `A`

Follow-up response behavior:
- classify as deterministic miss
- `RCODE = NXDOMAIN`
- no answer RRs

This behavior keeps server handling aligned with the global miss matrix.

Follow-up queries must never be interpreted as slice-token requests.

---

## Miss and Fault Responses

For deterministic misses:
- `RCODE = NXDOMAIN`
- no answer RRs

For internal runtime faults:
- `RCODE = SERVFAIL`
- no answer RRs

Response code selection and classification are governed by
`doc/architecture/ERRORS_AND_INVARIANTS.md`.

---

## Size Handling

v1 message policy:
- do not require or emit EDNS OPT records
- responses must fit classic UDP DNS message bounds
- oversized construction attempt is a runtime fault path, not silent truncation

v1 does not emit truncated (`TC=1`) slice responses.

---

## Parsing Rules (Client Side)

Client validation requirements:
1. verify `QR=1` and matching transaction ID
2. verify response question matches request
3. require `RCODE=NOERROR` for slice success path
4. locate matching `CNAME` answer for requested name
5. decode compressed names safely
6. pass CNAME target to payload decoder in
   `doc/architecture/CNAME_PAYLOAD_FORMAT.md`

Any parse or format violation is fatal per client error policy.

---

## Determinism Requirements

For fixed runtime state and identical request packet:
- selected response class (`slice`, `miss`, `fault`) is deterministic
- response header counts/flags are deterministic
- answer content and TTL are deterministic

---

## Versioning

Any change to:
- fixed request qtype behavior
- compression-pointer requirements
- non-slice query handling rules
- response header/count semantics

is a breaking wire change and requires synchronized updates to:
- server runtime
- generated client parser
- affected architecture docs
