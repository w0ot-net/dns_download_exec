# Plan: Move client logic from string literals to real Python

## Summary

Move the ~790 lines of client-specific logic from the `_CLIENT_SUFFIX` string
literal in `client_standalone.py` to a new `dnsdle/client_runtime.py` module.
All client functions become real, lintable, IDE-navigable Python source.
`build_client_source()` extracts the client logic via the existing extract
marker mechanism, identical to how it already handles `compat.py`,
`helpers.py`, `dnswire.py`, and `cname_payload.py`.

## Problem

The universal client plan identified "client logic lives inside string
literals -- no IDE support, no linting, no direct unit testing" as one of four
core problems with the old template system, and stated "one universal client
exists as a real Python file -- testable, lintable, readable" as goal #1.

The execution recreated the exact same problem.  `_CLIENT_SUFFIX` is ~790
lines of functions inside a Python string literal -- the same
`_TEMPLATE_PREFIX`/`_TEMPLATE_SUFFIX` pattern from the deleted
`client_template.py`, just without `@@PLACEHOLDER@@` substitution.  Only the
16 utility functions extracted from canonical modules benefit from being real
Python.  The client-specific logic (download loop, CLI parsing, reassembly,
resolver discovery, validation) -- which is the bulk of the file -- remains
untouchable by linters, IDEs, and static analysis.

## Goal

1. All client-specific functions exist as real Python source in a `.py` module.
2. Linters, IDEs, and static analysis tools work on the client logic.
3. `_CLIENT_PREAMBLE` shrinks to ~75 lines of pure declarations (imports,
   constants, PY2 detection, exception classes) -- no logic.
4. The assembled standalone client is functionally equivalent.
5. `client_standalone.py` shrinks from ~960 lines to ~110 lines.

## Design

The change is executed in two stages so that the byte-compare validation is
structurally valid.

### Stage 1: Relocate four preamble functions (no escape changes)

Create `dnsdle/client_runtime.py` with a development header and a single
extract block `# __EXTRACT: client_runtime__` containing only `_VERBOSE`,
`_log`, `_TOKEN_RE`, and `_LABEL_RE`, copied verbatim from `_CLIENT_PREAMBLE`
with no escape changes.

**Development header** (NOT extracted): imports from `dnsdle` packages for
DNS/payload/mapping constants.  `ClientError`, `RetryableTransport`, and the
six EXIT_* codes (`EXIT_USAGE` through `EXIT_WRITE`) are defined locally --
they are client-only concepts with no canonical module home.

The seven runtime-tuning constants used in `_CLIENT_PREAMBLE` under plain
names (`REQUEST_TIMEOUT_SECONDS`, `NO_PROGRESS_TIMEOUT_SECONDS`, `MAX_ROUNDS`,
`MAX_CONSECUTIVE_TIMEOUTS`, `RETRY_SLEEP_BASE_MS`, `RETRY_SLEEP_JITTER_MS`,
`QUERY_INTERVAL_MS`) have canonical counterparts in `constants.py` under the
`GENERATED_CLIENT_DEFAULT_` prefix.  The development header aliases them:

```python
from dnsdle import constants as _c
REQUEST_TIMEOUT_SECONDS          = _c.GENERATED_CLIENT_DEFAULT_REQUEST_TIMEOUT_SECONDS
NO_PROGRESS_TIMEOUT_SECONDS      = _c.GENERATED_CLIENT_DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS
MAX_ROUNDS                       = _c.GENERATED_CLIENT_DEFAULT_MAX_ROUNDS
MAX_CONSECUTIVE_TIMEOUTS         = _c.GENERATED_CLIENT_DEFAULT_MAX_CONSECUTIVE_TIMEOUTS
RETRY_SLEEP_BASE_MS              = _c.GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_BASE_MS
RETRY_SLEEP_JITTER_MS            = _c.GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_JITTER_MS
QUERY_INTERVAL_MS                = _c.GENERATED_CLIENT_DEFAULT_QUERY_INTERVAL_MS
```

This avoids duplicating the magic numbers (EXIT_* have no canonical home;
these seven do) while giving linters the plain names that `_build_parser` and
other extract-block functions reference.  These provide name resolution for
linters and IDEs and are not included in the assembled standalone client.

Remove `_VERBOSE`, `_log`, `_TOKEN_RE`, `_LABEL_RE` from `_CLIENT_PREAMBLE`.
The preamble retains only pure declarations: shebang, stdlib imports,
constants, PY2/type detection, and the three exception classes (`ClientError`,
`RetryableTransport`, `DnsParseError`).  ~75 lines, no logic.

Add `_CLIENT_RUNTIME_EXTRACTIONS = ["client_runtime"]` and update
`build_client_source()` to extract from `client_runtime.py`, appending after
the cname block:

```python
runtime_blocks = extract_functions("client_runtime.py", _CLIENT_RUNTIME_EXTRACTIONS)
extracted_parts = (
    compat_blocks + helpers_blocks + dnswire_blocks
    + cname_blocks + runtime_blocks
)
extracted_source = "\n\n".join(extracted_parts)
source = _CLIENT_PREAMBLE + extracted_source + "\n"
```

`_CLIENT_SUFFIX` remains unchanged at the end of this stage.  The assembled
output changes structurally (the four functions move to after the utility
extractions) but no escape conversions occur, so correctness is verifiable by
inspection.

### Stage 2: Move `_CLIENT_SUFFIX` to real Python (escape-only changes)

Before beginning stage 2, capture the stage-1 assembled output as a baseline:

```
python -c "import dnsdle.client_standalone as m; open('/tmp/stage1.py','wb').write(m.build_client_source())"
```

Append all `_CLIENT_SUFFIX` content into the `client_runtime` extract block
as real Python, converting double-escapes to single-escapes (`"\\n"` →
`"\n"`, `b"\\x00"` → `b"\x00"`, `"\\d"` → `r"\d"`, etc.) at all ~8-10
escape sites.  The full extract block now contains all client functions:
`_VERBOSE`, `_log`, `_TOKEN_RE`, `_LABEL_RE`, `_derive_file_id`,
`_derive_file_tag`, `_derive_slice_token`, `_encode_name`,
`_build_dns_query`, `_parse_response_for_cname`, `_extract_payload_text`,
`_enc_key`, `_mac_key`, `_parse_slice_record`, `_expected_mac`,
`_decrypt_and_verify_slice`, `_reassemble_plaintext`,
`_deterministic_output_path`, `_write_output_atomic`,
`_parse_positive_float`, `_parse_positive_int`, `_parse_non_negative_int`,
`_resolve_udp_address`, `_parse_resolver_arg`, `_IPV4_RE`,
`_run_nslookup`, `_parse_nslookup_output`, `_load_system_resolvers`,
`_discover_system_resolver`, `_send_dns_query`, `_retry_sleep`,
`_validate_cli_params`, `_download_slices`, `_build_parser`,
`_parse_runtime_args`, `main`, and the `if __name__` block.

Delete `_CLIENT_SUFFIX` from `client_standalone.py`.  Remove the now-dead
`import os` and `import re` statements.

**Validation**: capture the stage-2 assembled output and byte-compare against
the stage-1 baseline:

```
python -c "import dnsdle.client_standalone as m; open('/tmp/stage2.py','wb').write(m.build_client_source())"
cmp /tmp/stage1.py /tmp/stage2.py
```

Stage 1 and stage 2 have identical assembly structure (same preamble, same
utility blocks, same `client_runtime` block in the same position), so `cmp`
is a valid comprehensive check.  A byte-identical result confirms all escape
sites were converted correctly with no semantic regression.

## Affected Components

- `dnsdle/client_runtime.py` (new): all client-specific functions as real
  Python with development imports and a single `client_runtime` extract
  block.
- `dnsdle/client_standalone.py`: delete `_CLIENT_SUFFIX` (~790 lines);
  shrink `_CLIENT_PREAMBLE` (remove `_VERBOSE`, `_log`, `_TOKEN_RE`,
  `_LABEL_RE`); add `_CLIENT_RUNTIME_EXTRACTIONS`; update
  `build_client_source()` to extract from `client_runtime.py`; remove dead
  `import os` and `import re`.
- `doc/architecture/CLIENT_GENERATION.md`: update to note client logic lives
  in `client_runtime.py` as real Python, extracted via markers.
- `doc/architecture/CLIENT_RUNTIME.md`: no change -- documents runtime
  behavior only; unaffected by where the source is authored.
