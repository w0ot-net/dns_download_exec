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
`_log`, `_TOKEN_RE`, and `_LABEL_RE`, written as correct real Python.

"Correct real Python" means writing the functions as they would appear in any
normal `.py` file -- not copying raw source text from inside the
`_CLIENT_PREAMBLE` triple-quoted string.  Concretely: `_CLIENT_PREAMBLE`'s
raw source has `"\\n"` (two chars: `\` + `n`) because it sits inside a string
literal; as real Python in `client_runtime.py` the same newline is written
`"\n"`.  No stage-2 escape conversion is needed for `_log`.

**Development header** (NOT extracted): all imports needed so that
`client_runtime.py` is importable and lint-clean as standalone Python.
These provide name resolution for linters and IDEs; they are not included in
the assembled standalone client (the preamble and extracted utility blocks
supply those names instead).

The complete import block:

```python
# stdlib -- mirrors what _CLIENT_PREAMBLE injects into the assembled client
import sys, os, re, struct, socket, subprocess, time, random
import hashlib, zlib, argparse, base64, hmac, tempfile

# dnsdle utility functions extracted into the assembled client ahead of this block
from dnsdle.compat import (
    encode_ascii, encode_ascii_int,
    base32_lower_no_pad, base32_decode_no_pad,
    byte_value, constant_time_equals,
)
from dnsdle.helpers import hmac_sha256, dns_name_wire_length
from dnsdle.dnswire import _decode_name
from dnsdle.cname_payload import _derive_file_bound_key, _keystream_bytes, _xor_bytes

# dnsdle constants -- DNS/payload/mapping values used by the extract block
from dnsdle.constants import (
    DNS_FLAG_QR, DNS_FLAG_TC, DNS_FLAG_RD,
    DNS_HEADER_BYTES, DNS_OPCODE_MASK, DNS_OPCODE_QUERY,
    DNS_QCLASS_IN, DNS_QTYPE_A, DNS_QTYPE_CNAME, DNS_QTYPE_OPT,
    DNS_RCODE_NOERROR,
    FILE_ID_PREFIX, MAPPING_FILE_LABEL, MAPPING_SLICE_LABEL,
    PAYLOAD_ENC_KEY_LABEL,
    PAYLOAD_FLAGS_V1_BYTE, PAYLOAD_MAC_KEY_LABEL,
    PAYLOAD_MAC_MESSAGE_LABEL, PAYLOAD_MAC_TRUNC_LEN,
    PAYLOAD_PROFILE_V1_BYTE,
)

# client runtime-tuning constants: canonical home is constants.py under the
# GENERATED_CLIENT_DEFAULT_ prefix; aliased here to match the plain names
# used in _CLIENT_PREAMBLE and referenced directly by _build_parser et al.
from dnsdle import constants as _c
REQUEST_TIMEOUT_SECONDS     = _c.GENERATED_CLIENT_DEFAULT_REQUEST_TIMEOUT_SECONDS
NO_PROGRESS_TIMEOUT_SECONDS = _c.GENERATED_CLIENT_DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS
MAX_ROUNDS                  = _c.GENERATED_CLIENT_DEFAULT_MAX_ROUNDS
MAX_CONSECUTIVE_TIMEOUTS    = _c.GENERATED_CLIENT_DEFAULT_MAX_CONSECUTIVE_TIMEOUTS
RETRY_SLEEP_BASE_MS         = _c.GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_BASE_MS
RETRY_SLEEP_JITTER_MS       = _c.GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_JITTER_MS
QUERY_INTERVAL_MS           = _c.GENERATED_CLIENT_DEFAULT_QUERY_INTERVAL_MS

# client-only: no canonical module home; defined locally like EXIT_*
EXIT_USAGE = 2; EXIT_TRANSPORT = 3; EXIT_PARSE = 4
EXIT_CRYPTO = 5; EXIT_REASSEMBLY = 6; EXIT_WRITE = 7

class ClientError(Exception):
    def __init__(self, code, phase, message):
        Exception.__init__(self, message)
        self.code = int(code)
        self.phase = phase
        self.message = message
class RetryableTransport(Exception): pass
```

The exact set of `dnsdle.constants` names imported should be verified against
the extract block's actual usages during execution; the list above covers
the known references but may need adjustment if additional constants are
found.

The extract block boundaries are exact: the first non-marker line is
`_VERBOSE = False` (no blank line between the opening marker and `_VERBOSE`),
and the last non-marker line is `    sys.exit(main(sys.argv[1:]))` (no
trailing blank line before the closing marker).  This is required because
`"\n\n".join()` in `build_client_source()` controls inter-block spacing;
any extra blank line inside the block shifts the assembled output and breaks
the stage-1/stage-2 byte-compare.

Remove `_VERBOSE`, `_log`, `_TOKEN_RE`, `_LABEL_RE` from `_CLIENT_PREAMBLE`.
The trimmed preamble must end with `\n\n` (a blank line before the closing
`'''`), preserving the two-blank-line separation between `DnsParseError` and
the first extracted utility function in the assembled output.  The preamble
retains only pure declarations: shebang, stdlib imports, constants, PY2/type
detection, and the three exception classes (`ClientError`,
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
source = _CLIENT_PREAMBLE + extracted_source + _CLIENT_SUFFIX
```

`_CLIENT_SUFFIX` is unchanged and still concatenated.  The assembled output
changes structurally (the four functions move from end-of-preamble to after
the utility extractions, before the suffix) but no escape conversions occur,
so correctness is verifiable by inspection.

**Stage-1 smoke test**: capture the assembled output and confirm it is
parseable and executable:

```
python -c "import dnsdle.client_standalone as m; open('/tmp/stage1.py','wb').write(m.build_client_source().encode('ascii'))"
python /tmp/stage1.py --help
python /tmp/stage1.py --psk x --domains "INVALID DOMAIN" --mapping-seed s --publish-version v --total-slices 1 --compressed-size 1 --sha256 0000000000000000000000000000000000000000000000000000000000000000 --token-len 4 --verbose; echo "exit=$?"
```

`--help` exercises the CLI parser.  The second invocation exercises early
validation paths: `_validate_cli_params` applies `_LABEL_RE` to the domain,
`--verbose` sets `_VERBOSE` and exercises `_log`, and the invalid domain
triggers a `ClientError` exit.  Both confirm the reordering did not break
name resolution.

### Stage 2: Move `_CLIENT_SUFFIX` to real Python (escape-only changes)

Before beginning stage 2, capture the stage-1 assembled output as a baseline
(reuses the `/tmp/stage1.py` already written by the smoke test):

```
python -c "import dnsdle.client_standalone as m; open('/tmp/stage1.py','wb').write(m.build_client_source().encode('ascii'))"
```

**Pre-flight seam check**: before writing any stage-2 code, verify the
stage-1 baseline has the expected spacing at the `_LABEL_RE` /
`_derive_file_id` boundary:

```
grep -A2 '_LABEL_RE' /tmp/stage1.py | head -4
```

The output must show `_LABEL_RE = ...`, one blank line, then
`def _derive_file_id`.  If two blank lines appear, the preamble or extract
block boundaries are wrong -- fix before proceeding.

Append all `_CLIENT_SUFFIX` content into the `client_runtime` extract block
as real Python.

**Spacing rule (required for byte-compare):** `_CLIENT_SUFFIX` starts with
`\n\ndef _derive_file_id` (one blank line before the first function).
Within the suffix, all subsequent function pairs use two blank lines (PEP 8).
In `client_runtime.py` the transition from `_LABEL_RE` (the last stage-1
item) to `def _derive_file_id` (the first stage-2 item) must use exactly
**one blank line** -- matching what `_CLIENT_SUFFIX`'s leading `\n\n`
produces when concatenated after the extract block in stage 1.  All
subsequent function pairs use two blank lines, matching the suffix's
internal spacing.  Using PEP 8 standard two blank lines at the `_LABEL_RE`
/ `_derive_file_id` boundary would add an extra `\n` and break `cmp`.

Convert the 3 double-escaped sites in `_CLIENT_SUFFIX` to their real-Python
forms:
- `b"\\x00"` → `b"\x00"` in `_encode_name` (line 182 of `_CLIENT_SUFFIX`)
- `b"\\x00"` → `b"\x00"` in `_build_dns_query` (line 202 of `_CLIENT_SUFFIX`)
- `r"(\\d{1,3}(?:\\.\\d{1,3}){3})"` → `r"(\d{1,3}(?:\.\d{1,3}){3})"` in
  `_IPV4_RE` (line 504 of `_CLIENT_SUFFIX`)

(`_log`'s `"\n"` was already written correctly as real Python in stage 1;
there is nothing to convert in stage 2 for that function.)  The full extract block now contains all client functions:
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

Delete `_CLIENT_SUFFIX` from `client_standalone.py`.  Update the assembly
formula to drop the suffix concatenation:

```python
source = _CLIENT_PREAMBLE + extracted_source + "\n"
```

Remove the now-dead `import os` and `import re` statements.

**Validation**: capture the stage-2 assembled output and byte-compare against
the stage-1 baseline:

```
python -c "import dnsdle.client_standalone as m; open('/tmp/stage2.py','wb').write(m.build_client_source().encode('ascii'))"
cmp /tmp/stage1.py /tmp/stage2.py
```

Stage 1 and stage 2 must produce identical bytes.  In stage 1 the seam
between the extract block and `_CLIENT_SUFFIX` is: `[last extracted
line]` + `\n\n` (suffix opens with `\n\n`).  In stage 2 the same position
is inside the extract block.  To match, `client_runtime.py` must use exactly
one blank line between `_LABEL_RE` and `_derive_file_id` (matching the
suffix's leading `\n\n`), and two blank lines between all subsequent
top-level functions (matching the suffix's internal spacing).  A
byte-identical `cmp` result confirms all escape sites were converted
correctly with no semantic regression.

## Affected Components

- `dnsdle/client_runtime.py` (new): all client-specific functions as real
  Python with development imports and a single `client_runtime` extract
  block.
- `dnsdle/client_standalone.py`: delete `_CLIENT_SUFFIX` (~790 lines);
  shrink `_CLIENT_PREAMBLE` (remove `_VERBOSE`, `_log`, `_TOKEN_RE`,
  `_LABEL_RE`); add `_CLIENT_RUNTIME_EXTRACTIONS`; update
  `build_client_source()` to extract from `client_runtime.py`; remove dead
  `import os` and `import re`.
- `doc/architecture/CLIENT_GENERATION.md`: three sections require revision:
  - Architecture bullet 1 (canonical modules list): add `client_runtime.py`
    as the 5th canonical module alongside compat, helpers, dnswire, and
    cname_payload.
  - Architecture bullet 3 ("The client source file contains only
    client-specific logic"): rewrite to clarify that `client_runtime.py`
    is where client logic is authored (as real Python), and that
    `build_client_source()` assembles it via marker extraction -- not via
    string literal concatenation.
  - "Extracted functions (16 total)": the count and per-function listing
    become incorrect; update to reflect that `client_runtime.py` contributes
    1 extraction block (not individual named functions) in addition to the
    existing 16 utility functions from the four canonical modules.
- `doc/architecture/CLIENT_RUNTIME.md`: no change -- documents runtime
  behavior only; unaffected by where the source is authored.
- `dnsdle/client_generator.py`: no change -- imports `build_client_source`
  and `_UNIVERSAL_CLIENT_FILENAME` from `client_standalone`; both are
  unchanged (same name, same signature, same return type).

## Execution Notes

Executed 2026-02-20.

**Stage 1** -- Created `dnsdle/client_runtime.py` with development header
and extract block containing `_VERBOSE`, `_log`, `_TOKEN_RE`, `_LABEL_RE`.
Removed these four items from `_CLIENT_PREAMBLE`.  Added
`_CLIENT_RUNTIME_EXTRACTIONS` and updated `build_client_source()`.  Smoke
tests passed: `--help` exercised CLI parser; invalid-domain invocation with
`--verbose` exercised `_VERBOSE`, `_log`, `_LABEL_RE`.  Seam check confirmed
one blank line between `_LABEL_RE` and `_derive_file_id`.

**Stage 2** -- Appended all `_CLIENT_SUFFIX` functions into the
`client_runtime` extract block as real Python.  Converted 3 escape sites:
`b"\\x00"` to `b"\x00"` in `_encode_name` and `_build_dns_query`;
`r"(\\d{1,3}(?:\\.\\d{1,3}){3})"` to `r"(\d{1,3}(?:\.\d{1,3}){3})"` in
`_IPV4_RE`.  Deleted `_CLIENT_SUFFIX` (~790 lines) from
`client_standalone.py`.  Updated assembly formula to
`_CLIENT_PREAMBLE + extracted_source + "\n"`.  Removed dead `import os` and
`import re`.  Byte-compare (`cmp`) confirmed stage-1 and stage-2 assembled
output are identical.

**Architecture doc** -- Updated `CLIENT_GENERATION.md`: added
`client_runtime.py` to canonical modules list, rewrote bullet 3 to describe
real-Python authoring model, updated extracted blocks listing.

**Final sizes** -- `client_standalone.py`: 165 lines (down from ~960).
`client_runtime.py`: ~560 lines (development header + full extract block).

No deviations from plan.  All validation steps passed.
