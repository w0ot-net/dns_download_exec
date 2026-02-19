# Server Runtime

This document defines v1 runtime behavior for the DNS download server process.

It specifies process lifecycle, socket behavior, request handling, and restart
semantics for deterministic mappings.

---

## Goals

1. Keep runtime behavior deterministic and auditable.
2. Keep request handling simple and bounded.
3. Ensure restart compatibility without mandatory state files.
4. Enforce fail-fast startup and strict request invariants.

---

## Process Lifecycle

Server runtime has four phases:
1. startup validation
2. publish preparation
3. serving loop
4. graceful shutdown

The listener must not accept requests before phases 1 and 2 complete.

---

## Startup Validation

At startup, server must:
1. Parse CLI config.
2. Validate all config constraints from `doc/architecture/CONFIG.md`.
3. Validate every input file exists and is readable.
4. Validate deterministic mapping inputs (`mapping_seed`, tag/token bounds).
5. Validate CNAME payload budget can support at least one ciphertext byte.

Any failure is fatal startup error and process exits non-zero before binding
the socket.

---

## Publish Preparation

For each configured file:
1. Read plaintext bytes in binary mode.
2. Compute `plaintext_sha256`.
3. Enforce unique `plaintext_sha256` across configured files.
4. Compress deterministically.
5. Compute `publish_version` and `file_id` from compressed bytes.
6. Split compressed bytes into slices.
7. Derive deterministic `file_tag` and `slice_token` mapping.
8. Build immutable manifest/slice tables.

All publish tables become read-only before entering serve mode.
Publish pipeline details are defined in
`doc/architecture/PUBLISH_PIPELINE.md`.

Client generation integration is intentionally out of scope for this document.
Generation behavior is specified in `doc/architecture/CLIENT_GENERATION.md`.

---

## Deterministic Restart Semantics

Runtime determinism rule:
- If `(mapping_seed, file content, relevant config, implementation profile)`
  are unchanged, then `file_tag`, `slice_token`, and served CNAME payloads
  remain unchanged across process restarts.

No persisted mapping state file is required for compatibility.

Changing any of the following may break old clients:
- `mapping_seed`
- file content
- `compression_level`
- implementation profile (python implementation/version and zlib runtime
  version; see `doc/architecture/PUBLISH_PIPELINE.md`)
- relevant mapping/wire config (`file_tag_len`, `dns_max_label_len`,
  `domain`, `response_label`, profile values)

---

## Socket Model

v1 transport is UDP/IPv4 DNS.

Rules:
- bind exactly one UDP socket at `listen_addr`
- process one datagram per receive iteration
- reply to source address/port of received request
- ignore/skip malformed datagrams that cannot be safely parsed

Socket bind failure is fatal startup error.

---

## Concurrency Model

v1 runtime is single-process and single request loop.

Implications:
- no mutable shared publish state across worker threads
- deterministic request ordering by socket receive order
- simpler shutdown and invariant enforcement

Future concurrency changes are allowed only with preserved functional
equivalence for wire behavior and invariants.

---

## Request Handling Pipeline

For each parseable request:
1. Parse DNS envelope and question.
2. Validate qtype/qclass/qname shape for v1 contract.
3. Extract `slice_token`, `file_tag`, `base_domain`.
4. Resolve mapping key to canonical slice identity.
5. Build binary slice record.
6. Encode CNAME target and write response.

Response behavior must follow `doc/architecture/ERRORS_AND_INVARIANTS.md`:
- valid mapped request -> `NOERROR` + one CNAME answer
- deterministic miss -> `NXDOMAIN`
- internal runtime fault after mapping -> `SERVFAIL`

No fallback remap is allowed.

---

## Runtime State

State classes:
- immutable publish state:
  - per-file metadata
  - slice byte tables
  - token lookup maps
- mutable service state:
  - socket handle
  - counters/metrics
  - stop flag/signal state

Runtime must not mutate published slice bytes.

---

## Observability

Minimum runtime logs:
- startup summary (domain, file count, listen address)
- per-file publish summary (`file_id`, `file_tag`, `total_slices`,
  ciphertext slice budget)
- request outcomes (`served`, `miss`, `runtime_fault`)
- shutdown summary (uptime and counters)

Sensitive values must not be logged:
- plaintext file paths in network-facing error context
- raw PSK
- derived encryption keys

---

## Shutdown

Graceful shutdown sequence:
1. Stop accepting new work (set stop flag).
2. Close UDP socket.
3. Flush pending logs.
4. Exit process.

No runtime state is persisted for mapping compatibility.

Forced shutdown may drop in-flight responses; this is acceptable for UDP.

---

## Runtime Invariants

1. Listener starts only after successful validation and publish preparation.
2. Publish state is immutable while serving.
3. Same mapped identity returns same payload for process lifetime.
4. Deterministic mapping contract holds across restarts for unchanged inputs.
5. Request failures follow the defined response matrix exactly.
6. Runtime internal inconsistencies are surfaced as faults, never silently
   remapped.

---

## Related Docs

- `doc/architecture/ARCHITECTURE.md`
- `doc/architecture/CONFIG.md`
- `doc/architecture/PUBLISH_PIPELINE.md`
- `doc/architecture/QUERY_MAPPING.md`
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`
- `doc/architecture/CLIENT_GENERATION.md`
- `doc/architecture/ERRORS_AND_INVARIANTS.md`
