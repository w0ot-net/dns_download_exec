# Architecture Overview

This document defines the v1 architecture for `dns_download_exec`: a DNS server
that publishes selected files as DNS CNAME slice responses.

The architecture is intentionally narrow:
- server publishes operator-selected files only
- transport is DNS with CNAME responses only (v1)
- Python 2.7/3.x, standard library only, Windows and Linux support
- deterministic follow-up A handling for CNAME chase traffic

---

## Request/Response Model

The system uses a simple DNS request/response flow:
- client sends slice requests
- server returns CNAME responses containing the requested slice
- recursive resolvers may issue follow-up A queries for CNAME targets
- server returns a synthetic A response for follow-up queries
- client retries missing slices until reconstruction is complete

---

## Topology

```
Generated Client                                   DNSDL Server
----------------                                   ------------
query: <slice_token>.<file_tag>.<selected_base_domain> --> parse + validate query
query: <slice_token>.<file_tag>.<selected_base_domain> --> read canonical slice bytes
query: <slice_token>.<file_tag>.<selected_base_domain> --> encode CNAME payload
                                                  return CNAME response
```

Client behavior:
- may request slices out of order
- may retry slices many times
- must reconstruct file deterministically after all slices are collected

---

## Layer Stack

```
┌─────────────────────────────────────────────┐
│ CLI / Config                                │
│ (domains, files, bind addr, crypto options) │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────┴──────────────────────┐
│ File Publish Pipeline                        │
│ (read, compress, hash, slice, manifest)      │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────┴──────────────────────┐
│ DNS Response Engine                          │
│ (query parse, lookup, CNAME encode, reply)   │
└──────────────────────┬──────────────────────┘
                       │
```

---

## Components

### 1. CLI and Config

Primary launch form:
- `dnsdle.py --domains example.com,hello.com --files /etc/passwd,/tmp/a.bin --psk <secret>`

Responsibilities:
- parse CLI arguments into raw startup fields
- normalize and validate parsed config fields
- reject empty or duplicate entries
- build immutable runtime config

Fail-fast invariants:
- each configured domain must be valid for DNS label composition
- all input files must exist and be readable before server starts
- PSK must be present and non-empty before server starts
- configuration is immutable after startup

### 2. File Publish Pipeline

Runs once at startup per file:
1. read plaintext bytes
2. compute plaintext SHA-256 (`plaintext_sha256`)
3. enforce unique `plaintext_sha256` across configured files
4. compress with deterministic settings
5. compute `publish_version` from compressed bytes
6. split compressed bytes into fixed-size slices
7. build file manifest and slice table

Output per file:
- `file_id`
- `publish_version`
- `file_tag` (derived from `mapping_seed` and `publish_version`)
- `total_slices`
- `compressed_size`
- `plaintext_sha256`
- slice table indexed by `slice_index`

Design rule:
- the same `(file_id, publish_version, slice_index)` must always map to the
  same
  served bytes for the life of the process

Detailed pipeline contract:
- `doc/architecture/PUBLISH_PIPELINE.md`

### 3. DNS Response Engine (CNAME v1)

Responsibilities:
- parse QNAME into routing fields
- validate configured-domain suffix and mapping tokens
- fetch canonical slice payload
- encode payload into CNAME target
- return standards-compliant DNS response

Current qtype scope:
- request type: one fixed query type for v1 client flow
- response type: CNAME only

Fail-fast behavior:
- invalid names or out-of-range slices are hard misses (no silent remap)
- internal manifest inconsistency is fatal for request processing path

### 4. Generated Client Runtime (Out of Scope for Server Runtime)

Responsibilities:
- request slices (any order)
- retry missing slices
- verify slice-level authenticity/integrity
- deduplicate duplicate slice replies by index
- reassemble and decompress
- verify final plaintext hash
- write output to requested path or default temp directory

Rules:
- accept out-of-order delivery
- retries must be idempotent
- prolonged no-progress state is terminal (default 60 seconds)
- any verification mismatch is fatal
- never execute downloaded bytes in v1

Crypto and integrity requirements are defined in `doc/architecture/CRYPTO.md`.

---

## Data Flow

### Startup Flow

1. Operator starts server with domains, file list, and PSK.
2. Server parses CLI arguments.
3. Server normalizes/validates config invariants.
4. Server builds in-memory publish artifacts for each file.
5. Server validates runtime-serving invariants for DNS response construction.
6. Server binds DNS socket and begins serving queries.

### Download Flow

1. Generated client selects missing slice index `i` and queries its mapped `slice_token`.
2. Server validates query mapping and locates canonical slice `i`.
3. Server replies with CNAME payload for that slice.
4. Client verifies/stores slice and continues until complete.
5. Client reassembles, decompresses, verifies hash, writes file.

---

## Runtime State Model

The server has two main state classes:
- immutable publish state (file manifests and slices)
- immutable lookup indexes (`lookup_by_key`, `slice_bytes_by_identity`)
- network service state (socket, request handling, logging)

The server must not mutate publish bytes while serving.

The generated client maintains:
- immutable expected metadata
- mutable download bitmap/store keyed by `slice_index`

---

## Invariants

1. Deterministic slice serving
For fixed file metadata and slice index, server response payload is stable.

2. Strict bounds
Requested slice index must be within `[0, total_slices - 1]`.

3. No silent fallback
Bad input is rejected explicitly; no alternate file or index substitution.

4. End-to-end verification
Final restored plaintext must match embedded `plaintext_sha256`.

5. One-way compatibility policy
When wire format or crypto profile changes, update all call sites and generated
client templates together; do not add compatibility shims.

---

## Extensibility Boundaries

Planned future extension:
- support response types beyond CNAME

Boundary rule:
- keep file publish pipeline and client reconstruction independent of DNS
  record type details
- isolate DNS record encoding/decoding behind a transport format interface

This allows adding TXT or other qtypes without redefining file slicing,
integrity rules, or client-side reconstruction semantics.

---

## Non-Goals (v1)

- multi-hop transport abstraction
- runtime file mutation/hot reload
- compatibility layer for legacy wire formats
- stealth/timing obfuscation features
- execution of downloaded files

The v1 goal is a small, deterministic, auditable file download path over DNS.
