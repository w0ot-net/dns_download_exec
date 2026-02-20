# Plan: Add dns_max_response_bytes knob

## Summary

Add an optional `--dns-max-response-bytes` server knob that caps the CNAME
response packet size independently of EDNS advertisement size. Operators
who encounter middleboxes that drop DNS responses beyond a certain length can
use this to reduce throughput while keeping the EDNS buffer advertisement
unchanged.

## Problem

The CNAME response packet size is currently bounded by
`max(config.dns_edns_size, CLASSIC_DNS_PACKET_LIMIT)` in `budget.py`. The only
way to reduce response size today is to lower `--dns-edns-size`, but that
simultaneously changes the EDNS OPT record advertisement and OPT-record
inclusion, which may affect resolver behaviour and is semantically wrong.
Operators need a clean, independent cap on the actual response bytes emitted,
without changing the EDNS negotiation surface.

## Goal

After implementation:

- A new optional CLI flag `--dns-max-response-bytes` (default `0` = disabled)
  constrains the response-packet size budget used in `compute_max_ciphertext_slice_bytes()`.
- When set to a positive value, the effective packet-size ceiling becomes
  `min(max(dns_edns_size, 512), dns_max_response_bytes)`.
- When `0` (default), behaviour is identical to today.
- The resulting `max_ciphertext_slice_bytes` is naturally smaller; slice count
  grows proportionally.
- The actual emitted response will be at or below `dns_max_response_bytes` (the
  budget iteration lands on the largest payload that fits, so the response may
  be a few bytes shorter than the cap — acceptable per the ±1-byte framing).
- Startup fails fast with a clear error if the cap is so tight that the budget
  cannot fit even one ciphertext byte (the existing budget invariant handles
  this unchanged).
- The value is logged in the `startup_ok` record.

## Design

### New config field

`dns_max_response_bytes`: integer, `0` (disabled) or `1..65535`. Stored in
`Config` namedtuple alongside `dns_edns_size`. Parsed with `_arg_value_default`
(optional, defaults to `"0"`).

Validation in `build_config()`:
```python
dns_max_response_bytes = _parse_int_in_range(
    "dns_max_response_bytes",
    _arg_value_default(parsed_args, "dns_max_response_bytes", "0"),
    0,
    65535,
)
```

No cross-field constraint is needed beyond the range check — if the cap is
impossibly tight, `compute_max_ciphertext_slice_bytes()` will raise a
`StartupError` with a clear message.

### Budget change

In `compute_max_ciphertext_slice_bytes()` (`budget.py`), after computing
`packet_size_limit`:

```python
packet_size_limit = max(config.dns_edns_size, CLASSIC_DNS_PACKET_LIMIT)
if config.dns_max_response_bytes > 0:
    packet_size_limit = min(packet_size_limit, config.dns_max_response_bytes)
```

`response_size_limit` in `budget_info` already reflects the effective limit
(it is set to `packet_size_limit`), so no additional `budget_info` key is
needed.

### CLI

Add to `_LONG_OPTIONS` and the `dns/wire` argument group in `cli.py`:

```python
dns_wire.add_argument("--dns-max-response-bytes", default="0",
                      help="cap CNAME response bytes, 0=disabled (default: %(default)s)")
```

### Startup log

Add `"dns_max_response_bytes": config.dns_max_response_bytes` to the
`startup_ok` record in `dnsdle.py` alongside `dns_edns_size` and
`dns_max_label_len`.

## Affected Components

- `dnsdle/cli.py`: add `--dns-max-response-bytes` to `_LONG_OPTIONS` and
  the `dns/wire` parser group.
- `dnsdle/config.py`: add `dns_max_response_bytes` field to `Config` namedtuple;
  parse and range-validate in `build_config()` using `_arg_value_default`.
- `dnsdle/budget.py`: apply the cap to `packet_size_limit` in
  `compute_max_ciphertext_slice_bytes()` when `dns_max_response_bytes > 0`.
- `dnsdle.py`: include `dns_max_response_bytes` in the `startup_ok` log record.
- `doc/architecture/CONFIG.md`: document `dns_max_response_bytes` in the
  Optional section and Numeric Bounds validation rules.
- `doc/architecture/SERVER_RUNTIME.md`: add `dns_max_response_bytes` to the
  deterministic-restart "breaking changes" list alongside `dns_max_label_len`,
  since changing it alters `max_ciphertext_slice_bytes` and therefore slice
  count and mapping.

## Execution Notes

Executed 2026-02-20. All plan items implemented as specified with no deviations.

- `dnsdle/cli.py`: added `--dns-max-response-bytes` to `_LONG_OPTIONS` and
  `dns/wire` parser group with default `"0"`.
- `dnsdle/config.py`: added `dns_max_response_bytes` field to `Config`
  namedtuple; parsed via `_arg_value_default` with range `0..65535`.
- `dnsdle/budget.py`: two-line cap applied to `packet_size_limit` when
  `dns_max_response_bytes > 0`.
- `dnsdle.py`: added `dns_max_response_bytes` to `startup_ok` log record.
- `doc/architecture/CONFIG.md`: documented in Optional section and Numeric
  Bounds.
- `doc/architecture/SERVER_RUNTIME.md`: added to breaking-changes list.
