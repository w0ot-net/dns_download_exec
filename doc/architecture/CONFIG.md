# Config

This document defines v1 configuration for server runtime and generated clients.

Configuration is immutable after server startup validation completes.

Processing flow is two-step:
1. parse CLI arguments into raw values
2. normalize/validate raw values into immutable config

---

## Goals

1. Keep the config surface small and explicit.
2. Define hard bounds and fail-fast behavior.
3. Keep generated-client defaults reproducible and reviewable.
4. Avoid hidden fallback behavior.

---

## Server Config Surface

### Required

- `domains` (`--domains`): comma-separated DNS base domains for hosted queries.
- `files` (`--files`): comma-separated file paths to publish.
- `psk` (`--psk`): non-empty shared secret for v1 crypto profile.

### Optional

- `listen_addr` (`--listen-addr`): UDP bind address, default `0.0.0.0:53`.
- `ttl` (`--ttl`): answer TTL seconds, default `30`, valid `1..300`.
- `dns_edns_size` (`--dns-edns-size`): EDNS UDP size advertisement,
  default `1232`, valid `512..4096`. No-OPT mode is non-default and requires
  explicit `--dns-edns-size 512`.
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
  files, default `./generated_clients` (normalized to absolute path at startup).
- `compression_level` (`--compression-level`): compressed payload level,
  default `9`, valid `0..9`.
- `log_level` (`--log-level`): logging threshold
  (`error|warn|info|debug|trace`), default `info`.
- `log_categories` (`--log-categories`): comma-separated logging categories or
  `all`, default `startup,publish,server`.
- `log_sample_rate` (`--log-sample-rate`): diagnostics sampling rate in `0..1`,
  default `1.0`.
- `log_rate_limit_per_sec` (`--log-rate-limit-per-sec`): diagnostics rate limit
  per second, default `200`.
- `log_output` (`--log-output`): `stdout` or `file`, default `stdout`.
- `log_file` (`--log-file`): required when `log_output=file`; invalid
  otherwise.
- `log_focus` (`--log-focus`): optional deterministic request focus key.

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

- `plaintext_sha256`: content identity hash for source plaintext bytes.
- `publish_version`: publish identity hash for compressed bytes.
- `domains`: canonical ordered tuple of normalized base domains.
- `domain_labels_by_domain`: canonical ordered tuple of label-tuples aligned to
  `domains`.
- `longest_domain`: canonical domain string with maximum DNS wire length for the
  launch (ties broken by canonical domain order).
- `longest_domain_labels`: labels for `longest_domain`.
- `longest_domain_wire_len`: DNS wire length for `longest_domain_labels`.
- `file_tag`: deterministic identifier derived from
  `(mapping_seed, publish_version)` with length `file_tag_len`.
- `slice_token_len`: shortest collision-safe token length satisfying:
  - total-slice coverage for the file
  - global `(file_tag, slice_token)` uniqueness for the launch
  - `slice_token_len <= dns_max_label_len`
  - DNS name length constraints with `longest_domain` and `file_tag`
  - digest-encoding capacity from
    `doc/architecture/QUERY_MAPPING.md`
- `max_ciphertext_slice_bytes`: from CNAME payload size budget per
  `doc/architecture/CNAME_PAYLOAD_FORMAT.md`.

Startup fails if any derived value cannot be computed within constraints.

---

## Validation Rules

### Domain and Labels

- `domains` must parse as a comma-separated list with no empty entries.
- each domain in `domains` must normalize to lowercase and strip trailing dot.
- each normalized domain must satisfy DNS label syntax and full-name length
  limits.
- duplicate normalized domains are startup errors (no silent dedupe).
- configured domains must be non-overlapping on label boundaries (for example,
  `example.com` and `sub.example.com` together are invalid).
- canonical stored `domains` order is ascending ASCII lexicographic order of
  normalized domain strings.
- `response_label` must satisfy DNS label syntax.
- `response_label` must contain at least one non-token character so it cannot
  be parsed as a `slice_token` from the `[a-z0-9]` alphabet.
- `mapping_seed` must be non-empty printable ASCII after parsing.

### Files

- `files` list must be non-empty after parsing.
- file paths must be unique after normalization.
- every file must exist and be readable at startup.
- plaintext content identities (`plaintext_sha256`) must be unique across
  `files` for a single launch; duplicate content is a startup error in v1.

### Crypto/Wire

- `psk` must be non-empty.
- only `wire_profile=v1` and `crypto_profile=v1` are accepted in v1.

### Numeric Bounds

- all numeric values must parse and fall within documented ranges.
- any out-of-range value is a startup error.
- `dns_edns_size` controls whether OPT is emitted:
  - `> 512` emits OPT (EDNS enabled)
  - `= 512` omits OPT (DNS classic size)
- `log_sample_rate` must be within `0..1`.
- `log_rate_limit_per_sec` must be a non-negative integer.

### Logging

- `log_level` must be one of:
  `error`, `warn`, `info`, `debug`, `trace`.
- `log_categories` entries must be valid known categories, or the literal
  `all`.
- `log_output` must be `stdout` or `file`.
- `log_file` is required when `log_output=file`.
- `log_file` is invalid when `log_output=stdout`.

### Generation

- every selected `target_os` value must be supported (`windows` or `linux`).
- generator must produce exactly one `.py` file per `(file, target_os)` pair.
- no sidecar files may be emitted.
- generated files are written only under
  `<normalized client_out_dir>/dnsdle_v1/`.

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
- deterministic mapping comes from `(mapping_seed, publish_version)`.
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
