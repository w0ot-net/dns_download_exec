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
4. Handle recursive-resolver CNAME follow-up queries explicitly.

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
- `ARCOUNT = 1` by default (`dns_edns_size=1232`, OPT present)
- `ARCOUNT = 0` only when `dns_edns_size=512` (EDNS disabled)
- `QCLASS = IN`
- `QTYPE = A` (fixed in v1)
- `QNAME = <slice_token>.<file_tag>.<selected_base_domain>`
  where selected base domain is one configured domain from `domains`

Query flags:
- `RD = 1`
- `QR = 0`
- other flags cleared

The server must reject malformed requests and apply the response matrix in
`doc/architecture/ERRORS_AND_INVARIANTS.md`.

For parseable request envelopes, v1 validation requires:
- `QR = 0`
- opcode = `QUERY`
- `QDCOUNT = 1`
- `ANCOUNT = 0`
- `NSCOUNT = 0`
- `ARCOUNT` policy:
  - when `dns_edns_size = 512`, require `ARCOUNT = 0`
  - when `dns_edns_size > 512`, accept `ARCOUNT` in `{0,1}` and reject
    `ARCOUNT > 1`

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
- `ARCOUNT = 1` by default (`dns_edns_size=1232`, OPT present)
- `ARCOUNT = 0` only when `dns_edns_size=512` (EDNS disabled)

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
- CNAME target suffix uses pointer to selected-base-domain location in question
  qname
  when constructing compressed RDATA form

Reason:
- this is required to maximize payload bytes and satisfy MTU constraints.

Startup invariant:
- if required compression-pointer layout cannot be constructed safely for the
  longest configured domain/suffix rules, startup fails before bind.

Parser safety:
- name decoder must detect invalid pointers and pointer loops.

---

## CNAME-Chase Follow-Up Handling

Recursive resolvers may issue follow-up A queries for CNAME targets.

Detection:
- request qname matches `<payload_labels>.<response_label>.<selected_base_domain>`
  where selected base domain is configured
- request qtype is `A`

Follow-up response behavior:
- `RCODE = NOERROR`
- one `A` answer for the queried name
- `TTL = configured ttl`
- address value is fixed synthetic `0.0.0.0` in v1

This response is protocol plumbing only. Clients ignore follow-up A data.

Follow-up queries must never be interpreted as slice-token requests.

---

## Miss and Fault Responses

For deterministic misses:
- `RCODE = NXDOMAIN`
- no answer RRs
- `QDCOUNT = 1` when one parseable question is present; otherwise `QDCOUNT = 0`

For internal runtime faults:
- `RCODE = SERVFAIL`
- no answer RRs
- `QDCOUNT = 1` when one parseable question is present; otherwise `QDCOUNT = 0`

Response code selection and classification are governed by
`doc/architecture/ERRORS_AND_INVARIANTS.md`.

---

## EDNS and Size Handling

EDNS policy:
- default configuration includes one OPT RR in additional section
  (`dns_edns_size=1232`)
- advertised UDP size follows configured EDNS size
- `dns_edns_size=512` disables OPT emission for classic DNS behavior

Packet size policy:
- responses must fit configured UDP/EDNS bounds
- oversized construction attempt is a runtime fault path, not silent truncation

v1 does not emit truncated (`TC=1`) slice responses.

---

## Parsing Rules (Client Side)

Client validation requirements for recursive-compatible acceptance:
1. verify `QR=1` and matching transaction ID
2. verify `TC=0` and opcode `QUERY`
3. verify `QDCOUNT=1` (generated template positional parser requires this;
   `client_payload.py` gets equivalent coverage from downstream question-count
   check)
4. verify response question matches request qname/qtype/qclass
5. require `RCODE=NOERROR` for slice success path
6. locate exactly one matching `IN CNAME` answer for requested name
7. decode compressed names safely
8. pass CNAME target to payload decoder in
   `doc/architecture/CNAME_PAYLOAD_FORMAT.md`

Clients MUST NOT depend on `AA`, `RA`, exact `ANCOUNT`/`NSCOUNT`, or
EDNS-driven `ARCOUNT` for success-path acceptance. Recursive resolvers may
clear `AA`, set `RA`, inject additional answer/authority/additional RRs, and
modify or remove OPT records. The invariants above hold end-to-end regardless
of resolver topology.

Any parse or format violation is fatal per client error policy.

---

## Determinism Requirements

For fixed runtime state and identical request packet:
- selected response class (`slice`, `followup`, `miss`, `fault`) is deterministic
- response header counts/flags are deterministic
- answer content and TTL are deterministic

---

## Versioning

Any change to:
- fixed request qtype behavior
- compression-pointer requirements
- follow-up chase response rules
- response header/count semantics

is a breaking wire change and requires synchronized updates to:
- server runtime
- generated client parser
- affected architecture docs
