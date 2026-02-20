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

### New module: `dnsdle/client_runtime.py`

A real Python module containing all client-specific functions.  Structured in
two sections:

**Development header** (NOT extracted): module-level imports from `dnsdle`
packages plus local definitions of `ClientError`, `RetryableTransport`, and
constants.  These provide name resolution for linters and IDEs.  They are not
included in the assembled standalone client -- the preamble and extracted
utility blocks provide those names instead.

**Extract block** `# __EXTRACT: client_runtime__`: wraps all client functions:
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

The development header imports from `dnsdle.constants` for all DNS/payload/
mapping constants.  `ClientError`, `RetryableTransport`, and the six EXIT_*
codes (`EXIT_USAGE` through `EXIT_WRITE`) are defined locally in the
development header -- they are client-only concepts with no canonical module
home, and three lines of local definitions keeps `constants.py` free of
client exit-code semantics.

### Shrink `_CLIENT_PREAMBLE`

Remove `_VERBOSE`, `_log`, `_TOKEN_RE`, `_LABEL_RE` from the preamble --
they move into the `client_runtime` extract block.  The preamble retains
only pure declarations: shebang, stdlib imports, constants, PY2/type
detection, and the three exception classes (`ClientError`,
`RetryableTransport`, `DnsParseError`).  ~75 lines, no logic.

### Delete `_CLIENT_SUFFIX`

The entire `_CLIENT_SUFFIX` string literal is deleted.  Its content becomes
real Python inside the `client_runtime` extract block.

### Update `build_client_source()`

Add a `_CLIENT_RUNTIME_EXTRACTIONS = ["client_runtime"]` spec.  Extract
from `client_runtime.py` and append the block after the canonical utility
extractions:

```python
runtime_blocks = extract_functions("client_runtime.py", _CLIENT_RUNTIME_EXTRACTIONS)
extracted_parts = (
    compat_blocks + helpers_blocks + dnswire_blocks
    + cname_blocks + runtime_blocks
)
extracted_source = "\n\n".join(extracted_parts)
source = _CLIENT_PREAMBLE + extracted_source + "\n"
```

This preserves the existing assembly order: preamble -> extracted utilities
-> client logic.  The trailing `"\n"` ensures the assembled source ends
with a newline.

### Remove dead imports from `client_standalone.py`

After removing `_CLIENT_SUFFIX`, the `import os` and `import re` statements
in `client_standalone.py` are unused by any real Python code.  Remove them.

### Un-escape backslashes

The `_CLIENT_SUFFIX` string literal double-escapes backslashes (e.g.
`"\\n"` for newline, `b"\\x00"` for null byte, `"\\d"` for regex digit).
When the code moves to real Python in `client_runtime.py`, these become
normal single-backslash escapes (`"\n"`, `b"\x00"`, `r"\d"`).

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
