# Config

This document defines v1 configuration for server runtime and generated clients.

Configuration is immutable after server startup validation completes.

---

## Goals

1. Keep the config surface small and explicit.
2. Define hard bounds and fail-fast behavior.
3. Keep generated-client defaults reproducible and reviewable.
4. Avoid hidden fallback behavior.

---

## Server Config Surface

### Required

- `domain` (`--domain`): DNS base domain for hosted queries.
- `files` (`--files`): comma-separated file paths to publish.
- `psk` (`--psk`): non-empty shared secret for v1 crypto profile.

### Optional

- `listen_addr` (`--listen-addr`): UDP bind address, default `0.0.0.0:53`.
- `ttl` (`--ttl`): answer TTL seconds, default `30`, valid `1..300`.
- `dns_edns_size` (`--dns-edns-size`): EDNS UDP size advertisement,
  default `1232`, valid `512..4096`.
- `dns_max_label_len` (`--dns-max-label-len`): payload label cap, default `63`,
  valid `16..63`.
- `response_label` (`--response-label`): fixed CNAME response discriminator,
  default `r-x`.
- `mapping_seed` (`--mapping-seed`): deterministic mapping seed, default `0`.
- `file_tag_len` (`--file-tag-len`): deterministic file-tag length, default `6`,
  valid `4..16`.
- `target_os` (`--target-os`): generated client OS profiles, allowed values
  `windows`, `linux`, `windows,linux`, default `windows,linux`.
- `client_out_dir` (`--client-out-dir`): output directory for generated client
  files, default `./generated_clients`.
- `compression_level` (`--compression-level`): compressed payload level,
  default `9`, valid `0..9`.

---

## Fixed v1 Config (Not User-Configurable)

- `query_mapping_alphabet = [a-z0-9]`
- `query_mapping_case = lowercase`
- `wire_profile = v1`
- `crypto_profile = v1`
- `qtype_response = CNAME`
- `generated_client_single_file = true`
- `generated_client_download_only = true`

Changing these values requires architecture/version updates, not runtime flags.

---

## Generated Client Embedded Defaults

These values are emitted into each generated client unless overridden by
runtime CLI flags in the generated client.

- `request_timeout_seconds = 3.0`
- `no_progress_timeout_seconds = 60`
- `max_rounds = 64`
- `max_consecutive_timeouts = 128`
- `retry_sleep_base_ms = 100`
- `retry_sleep_jitter_ms = 150`
- `resolver_mode = system` (unless generator embeds direct resolver)

Generated-client runtime overrides:
- `--resolver host:port`
- `--out path`
- `--timeout seconds`
- `--no-progress-timeout seconds`
- `--max-rounds n`

Generated-client runtime required input:
- `--psk secret` (non-empty shared secret for v1 crypto profile)

No runtime flag for execution is allowed in v1.

---

## Derived Values

These are computed at startup from validated config and file metadata.

- `file_version`: content identity hash for published file bytes.
- `file_tag`: deterministic identifier derived from
  `(mapping_seed, file_version)` with length `file_tag_len`.
- `slice_token_len`: shortest collision-safe token length satisfying:
  - total-slice coverage for the launch
  - `slice_token_len <= dns_max_label_len`
  - DNS name length constraints with `domain` and `file_tag`
- `max_ciphertext_slice_bytes`: from CNAME payload size budget per
  `doc/architecture/CNAME_PAYLOAD_FORMAT.md`.

Startup fails if any derived value cannot be computed within constraints.

---

## Validation Rules

### Domain and Labels

- `domain` must normalize to lowercase and strip trailing dot.
- `domain` must satisfy DNS label syntax and full-name length limits.
- `response_label` must satisfy DNS label syntax.
- `response_label` must contain at least one non-token character so it cannot
  be parsed as a `slice_token` from the `[a-z0-9]` alphabet.
- `mapping_seed` must be non-empty printable ASCII after parsing.

### Files

- `files` list must be non-empty after parsing.
- file paths must be unique after normalization.
- every file must exist and be readable at startup.

### Crypto/Wire

- `psk` must be non-empty.
- only `wire_profile=v1` and `crypto_profile=v1` are accepted in v1.

### Numeric Bounds

- all numeric values must parse and fall within documented ranges.
- any out-of-range value is a startup error.
- `dns_edns_size` controls whether OPT is emitted:
  - `> 512` emits OPT (EDNS enabled)
  - `= 512` omits OPT (DNS classic size)

### Generation

- every selected `target_os` value must be supported (`windows` or `linux`).
- generator must produce exactly one `.py` file per `(file, target_os)` pair.
- no sidecar files may be emitted.

---

## Error Handling Policy

Config errors are fatal startup errors.

Rules:
- report specific field and reason
- do not start DNS listener on invalid config
- do not emit partial generated clients on failure

Detailed runtime error semantics are defined in
`doc/architecture/ERRORS_AND_INVARIANTS.md`.

---

## Caching and TTL Guidance

- `ttl` should stay low to reduce stale cache impact.
- deterministic mapping comes from `(mapping_seed, file_version)`.
- keeping `mapping_seed` stable preserves old-client compatibility.
- rotating `mapping_seed` breaks cross-run mapping continuity and old clients.
- do not depend on resolver honoring very low TTL exactly.

---

## Compatibility and Breaking Changes

Any change to:
- config field names
- default ranges
- embedded client defaults
- fixed v1 constants

is a contract change and requires coordinated updates to:
- server CLI/config parser
- generated client template
- architecture docs referencing the changed field
