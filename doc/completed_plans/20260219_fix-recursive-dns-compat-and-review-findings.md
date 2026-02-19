# Plan: Fix Recursive DNS Compatibility and Review Findings

## Summary
Resolve all issues from the review of commits 66802bd and 261e092. The primary
fix removes authoritative-only DNS flag and section-count checks from both the
generated client template and the server-side parity module so that generated
clients work through recursive resolvers (the default and primary deployment
path). Secondary fixes improve error diagnostic ordering and remove dead code.

## Problem
Commit 66802bd added response-envelope checks to `_parse_response_for_cname()`
in the generated client template and to `extract_response_cname_labels()` in
`dnsdle/client_payload.py` that assume responses arrive directly from the
authoritative server. When queries traverse recursive resolvers (the default
operational mode), recursive resolvers typically clear `AA` and set `RA`,
and may modify `ARCOUNT`. These checks reject every valid response through
the standard recursive resolver path, making every generated client non-functional
in the default configuration.

Additionally, the generated client template checks section counts before rcode,
producing a less informative error message ("response section counts invalid")
instead of the diagnostic rcode-based message for SERVFAIL/NXDOMAIN responses.

## Goal
After implementation:
- Generated clients work through recursive DNS resolvers (default mode) and
  direct authoritative queries (`--resolver` mode).
- Parser still fails fast on true contract violations: bad transaction ID,
  non-query opcode, truncated responses (`TC`), non-NOERROR rcode, missing or
  ambiguous required CNAME answer, malformed wire content, MAC mismatch.
- Rcode-based errors produce diagnostic rcode messages before section-count
  validation fires.
- Architecture docs explicitly state recursive DNS compatibility as mandatory.
- No compatibility shims; one clear response-validation contract.

## Design

### 1. Remove authoritative-only checks from `dnsdle/client_payload.py`

In `extract_response_cname_labels()`:
- Remove `AA=1` check (line 146-147) and `RA=0` check (line 150-151).
- Remove strict section-count check `qdcount != 1 or ancount != 1 or nscount != 0`
  (line 158-159). Replace with `qdcount != 1` (keep) and `ancount < 1` (require
  at least one answer). Remove nscount constraint entirely.
- Remove EDNS-driven `arcount` check (lines 161-163).
- Remove `dns_edns_size` parameter from `extract_response_cname_labels()` and
  `decode_response_slice()` signatures since its only consumer was the arcount
  check. Update the internal call from `decode_response_slice` to
  `extract_response_cname_labels` accordingly.
- Remove dead imports: `DNS_FLAG_AA`, `DNS_FLAG_RA`.
- Keep: `QR=1`, `TC=0`, opcode `QUERY`, matching ID, `RCODE=NOERROR`,
  question echo, exactly one matching IN CNAME answer.

### 2. Update generated client template in `dnsdle/generator.py`

In `_parse_response_for_cname()` inside `_CLIENT_TEMPLATE`:
- Remove `AA=1` check (line 309-310).
- Remove `RA=0` check (line 313-314).
- Move rcode check (`rcode != DNS_RCODE_NOERROR`) to immediately after flags
  extraction, before section count checks. This ensures SERVFAIL/NXDOMAIN
  produce the diagnostic "unexpected DNS rcode=N" message.
- Replace strict section-count check `qdcount != 1 or ancount != 1 or nscount != 0`
  with `qdcount != 1` and `ancount < 1`. Remove nscount constraint.
- Remove EDNS-driven `arcount` check (`expected_arcount` / `arcount != expected_arcount`).
- Remove dead template constants: `DNS_FLAG_AA`, `DNS_FLAG_RA`.
- Keep `DNS_FLAG_TC`, `DNS_OPCODE_QUERY`, `DNS_OPCODE_MASK` (still used).
- Keep `_consume_rrs()` for all sections (handles any count). Keep trailing
  bytes check `offset != len(message)`.

### 3. Update architecture docs

**`doc/architecture/DNS_MESSAGE_FORMAT.md`** (lines 64-78, 172-188):
- In "Response Header Contract" section: clarify that `AA=1` and `RA=0` describe
  server emission behavior only and that client-side parsing must not gate on
  these flags since recursive resolvers may modify them.
- In "Parsing Rules (Client Side)" section: replace authoritative-only validation
  bullets with recursive-compatible invariants. Remove `AA=1`, `RA=0`, exact
  section-count shape, and EDNS-driven `ARCOUNT` from client-side requirements.
  State that recursive DNS traversal is the primary operating mode.

**`doc/architecture/CLIENT_RUNTIME.md`** (line 151):
- Change `QR/AA/TC/RA` to `QR/TC` in the parity helper description for
  `dnsdle/client_payload.py` response-envelope rules.
- Remove "section counts" from the enumerated checks since strict section-count
  gating is removed.

**`doc/architecture/CLIENT_GENERATION.md`** (lines 204-213):
- In "DNS Contract in Generated Client" section: add explicit statement that
  the generated client parser must accept responses traversing recursive
  resolvers. Note that `AA`, `RA`, and exact section-count/ARCOUNT checks
  are not enforced on the client parse path.

**`doc/architecture/ERRORS_AND_INVARIANTS.md`** (lines 193-212):
- In "Client Assembly" section: add invariant stating generated clients must
  accept valid responses regardless of resolver topology (recursive or direct).

**`doc/architecture/ARCHITECTURE.md`**:
- In "Generated Client Runtime" section (lines 158-175): add note that
  recursive DNS is the primary transport path and client-side parsing must
  not depend on authoritative-only response properties.

## Execution Notes

Executed 2026-02-19.

### Prior plan overlap

The majority of this plan's scope was already implemented by
`20260219_recursive-dns-client-payload-compat-v1.md`, which was executed
immediately before this plan. That plan covered:
- Design 1: all client_payload.py changes (AA/RA removal, section-count
  removal, dns_edns_size parameter removal, dead import cleanup)
- Design 2: most template changes (AA/RA removal, section-count replacement,
  dead constant removal)
- Design 3: most architecture doc updates (5 of 6 planned docs)

### Deviation: generator.py split

The plan references `dnsdle/generator.py`. This file was split into
`dnsdle/client_template.py` and `dnsdle/client_generator.py` by commit
`57ae1adf`. Template changes were applied to `dnsdle/client_template.py`.

### Deviation: ancount < 1 check not added

The plan called for adding `ancount < 1` as a header-level check. The prior
plan took a different approach: no header-level ancount check at all,
relying on the downstream "exactly one matching IN CNAME" check which
provides equivalent coverage with a cleaner error path.

### Residual items implemented

Three items from this plan were not covered by the prior plan:

1. **Rcode check reordering in template** (Design 2): Moved
   `rcode != DNS_RCODE_NOERROR` check to immediately after rcode extraction
   and before qdcount check. This ensures SERVFAIL/NXDOMAIN responses produce
   the diagnostic "unexpected DNS rcode=N" message instead of being caught
   by qdcount or question validation first.

2. **Response Header Contract clarification** (Design 3): Added note to
   DNS_MESSAGE_FORMAT.md clarifying that `AA=1` and `RA=0` describe server
   emission behavior only and that client-side parsing must not gate on them.

3. **Client Assembly invariant** (Design 3): Added invariant 8 to
   ERRORS_AND_INVARIANTS.md stating clients must accept valid responses
   regardless of resolver topology.

### Validation

- All modules import cleanly
- `python -m unittest unit_tests.test_client_payload_parity`: 9 tests OK

## Affected Components
- `dnsdle/client_payload.py`: remove AA/RA checks, remove strict section-count and arcount checks, remove `dns_edns_size` parameter from `extract_response_cname_labels()` and `decode_response_slice()`, remove dead `DNS_FLAG_AA`/`DNS_FLAG_RA` imports.
- `dnsdle/generator.py`: update `_parse_response_for_cname()` in `_CLIENT_TEMPLATE` with recursive-compatible invariants, remove dead `DNS_FLAG_AA`/`DNS_FLAG_RA` template constants, reorder rcode check before section counts.
- `doc/architecture/DNS_MESSAGE_FORMAT.md`: redefine client-side parsing rules as recursive-compatible; clarify AA/RA describe server emission only.
- `doc/architecture/CLIENT_RUNTIME.md`: align parity helper description with recursive-compatible envelope checks.
- `doc/architecture/CLIENT_GENERATION.md`: add recursive DNS acceptance requirement to generated client DNS contract.
- `doc/architecture/ERRORS_AND_INVARIANTS.md`: add recursive-topology acceptance invariant to client assembly section.
- `doc/architecture/ARCHITECTURE.md`: add recursive DNS as primary transport note to generated client runtime section.
