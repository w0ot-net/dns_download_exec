# Plan: Generate preamble constants from constants.py

## Summary

Replace the hardcoded constant literals in `_CLIENT_PREAMBLE` with
programmatic generation from `dnsdle.constants`, eliminating silent-drift
risk between the assembled client and the canonical constant definitions.
Add client exit codes to `constants.py` and rename the
`GENERATED_CLIENT_DEFAULT_*` runtime-tuning constants to the short names
the client already uses, so every preamble constant can use its canonical
name directly -- no rename mapping needed.

`ClientError` / `RetryableTransport` class duplication between the preamble
and the `client_runtime.py` development header is inherently necessary and
left unchanged.

## Problem

Every constant in `_CLIENT_PREAMBLE` (36 values across DNS wire, payload,
mapping, exit code, and runtime tuning groups) is a hand-written literal
copy of a value already defined in `constants.py`.  If someone updates
`constants.py` and forgets the preamble, the development-context and
assembled-client behaviors silently diverge.

The runtime tuning constants are triplicated: canonical values in
`constants.py` (`GENERATED_CLIENT_DEFAULT_*`), hardcoded literals in
`_CLIENT_PREAMBLE`, and aliased copies in the `client_runtime.py`
development header.  The long `GENERATED_CLIENT_DEFAULT_` prefix exists
only for disambiguation in `constants.py`, but nothing else in the
codebase needs that disambiguation -- only `constants.py` defines them and
only `client_runtime.py` consumes them (via manual aliases to the short
names).

The `EXIT_*` constants are client-only values duplicated between the
preamble and the development header with no canonical home in
`constants.py`.

## Goal

1. Every constant value in the assembled client preamble is read from
   `constants.py` at assembly time -- no hand-written literal copies.
2. Every preamble constant uses its canonical `constants.py` name directly
   -- no `(preamble_name, attr_name)` rename mapping.
3. Client exit codes live in `constants.py` alongside all other constants.
4. The `GENERATED_CLIENT_DEFAULT_*` prefix is eliminated; the short names
   (`REQUEST_TIMEOUT_SECONDS`, etc.) become the canonical names.
5. The `client_runtime.py` development header imports exit codes and
   runtime-tuning constants from `constants.py` by their canonical (short)
   names -- no aliasing.
6. The assembled standalone client is functionally equivalent (same names,
   same values, compiles, passes smoke tests).
7. `ClientError` / `RetryableTransport` class definitions remain in both
   the preamble and the development header (inherently necessary; accepted
   duplication).

## Design

### 1. Rename runtime-tuning constants in `constants.py`

Replace the `GENERATED_CLIENT_DEFAULT_` prefix with the short names that
the client already uses.  Update the section comment accordingly.

Before:
```python
# Generated client defaults and output policy
GENERATED_CLIENT_MANAGED_SUBDIR = "dnsdle_v1"
GENERATED_CLIENT_DEFAULT_REQUEST_TIMEOUT_SECONDS = 3.0
GENERATED_CLIENT_DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS = 60
GENERATED_CLIENT_DEFAULT_MAX_ROUNDS = 64
GENERATED_CLIENT_DEFAULT_MAX_CONSECUTIVE_TIMEOUTS = 128
GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_BASE_MS = 100
GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_JITTER_MS = 150
GENERATED_CLIENT_DEFAULT_QUERY_INTERVAL_MS = 50
```

After:
```python
# Generated client defaults and output policy
GENERATED_CLIENT_MANAGED_SUBDIR = "dnsdle_v1"
REQUEST_TIMEOUT_SECONDS = 3.0
NO_PROGRESS_TIMEOUT_SECONDS = 60
MAX_ROUNDS = 64
MAX_CONSECUTIVE_TIMEOUTS = 128
RETRY_SLEEP_BASE_MS = 100
RETRY_SLEEP_JITTER_MS = 150
QUERY_INTERVAL_MS = 50
```

`GENERATED_CLIENT_MANAGED_SUBDIR` keeps its name -- it is server-side only
(used by `client_generator.py`) and never appears in the preamble.

### 2. Add client exit codes to `constants.py`

Add six constants under an `EXIT_` prefix (matching the names the client
already uses), in a new section after the runtime-tuning constants:

```python
# Client exit codes
EXIT_USAGE = 2
EXIT_TRANSPORT = 3
EXIT_PARSE = 4
EXIT_CRYPTO = 5
EXIT_REASSEMBLY = 6
EXIT_WRITE = 7
```

### 3. Split the preamble and generate constants programmatically

Replace the single `_CLIENT_PREAMBLE` string with three parts assembled
in `build_client_source()`:

- `_PREAMBLE_HEADER`: shebang, coding declaration, `from __future__`,
  stdlib imports (static string, ~17 lines).
- Generated constants block: all constant assignments formatted from
  `dnsdle.constants` using `repr()` for values.
- `_PREAMBLE_FOOTER`: PY2/type detection `try`/`except`, `ClientError`,
  `RetryableTransport`, `DnsParseError` (static string, ~17 lines).

Since every constant now has the same name in `constants.py` and the
assembled client, the mapping is a flat tuple of names:

```python
from dnsdle import constants as _c

_PREAMBLE_CONSTANTS = (
    # DNS wire
    "DNS_FLAG_QR",
    "DNS_FLAG_TC",
    "DNS_FLAG_RD",
    "DNS_OPCODE_QUERY",
    "DNS_OPCODE_MASK",
    "DNS_QTYPE_A",
    "DNS_QTYPE_CNAME",
    "DNS_QTYPE_OPT",
    "DNS_QCLASS_IN",
    "DNS_HEADER_BYTES",
    "DNS_POINTER_TAG",
    "DNS_POINTER_VALUE_MASK",
    "DNS_RCODE_NOERROR",
    # payload
    "PAYLOAD_PROFILE_V1_BYTE",
    "PAYLOAD_FLAGS_V1_BYTE",
    "PAYLOAD_MAC_TRUNC_LEN",
    "PAYLOAD_ENC_KEY_LABEL",
    "PAYLOAD_ENC_STREAM_LABEL",
    "PAYLOAD_MAC_KEY_LABEL",
    "PAYLOAD_MAC_MESSAGE_LABEL",
    # mapping
    "MAPPING_FILE_LABEL",
    "MAPPING_SLICE_LABEL",
    "FILE_ID_PREFIX",
    # exit codes
    "EXIT_USAGE",
    "EXIT_TRANSPORT",
    "EXIT_PARSE",
    "EXIT_CRYPTO",
    "EXIT_REASSEMBLY",
    "EXIT_WRITE",
    # runtime tuning
    "REQUEST_TIMEOUT_SECONDS",
    "NO_PROGRESS_TIMEOUT_SECONDS",
    "MAX_ROUNDS",
    "MAX_CONSECUTIVE_TIMEOUTS",
    "RETRY_SLEEP_BASE_MS",
    "RETRY_SLEEP_JITTER_MS",
    "QUERY_INTERVAL_MS",
)
```

In `build_client_source()`:

```python
constants_lines = "\n".join(
    "%s = %s" % (name, repr(getattr(_c, name)))
    for name in _PREAMBLE_CONSTANTS
)
preamble = _PREAMBLE_HEADER + constants_lines + "\n" + _PREAMBLE_FOOTER
```

The spacing contract: `_PREAMBLE_HEADER` ends with `\n\n` (blank line
after the last import).  The generated constants block has no leading or
trailing blank lines.  `_PREAMBLE_FOOTER` starts with `\n` (blank line
before the `try:` block) and ends with `\n\n` (blank line before the
closing `'''` equivalent, matching the current preamble's trailing spacing
for the extract-block join).

The assembled client output will use `repr()` formatting (e.g., `32768`
instead of `0x8000`, `b'...'` instead of `b"..."`).  This is functionally
identical; no byte-compare against the prior output is needed.

### 4. Update client_runtime.py development header

Replace the aliasing block and hardcoded exit codes:

```python
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
```

With direct imports (no aliasing needed since the names now match):

```python
from dnsdle.constants import (
    EXIT_CRYPTO, EXIT_PARSE, EXIT_REASSEMBLY,
    EXIT_TRANSPORT, EXIT_USAGE, EXIT_WRITE,
    REQUEST_TIMEOUT_SECONDS, NO_PROGRESS_TIMEOUT_SECONDS,
    MAX_ROUNDS, MAX_CONSECUTIVE_TIMEOUTS,
    RETRY_SLEEP_BASE_MS, RETRY_SLEEP_JITTER_MS,
    QUERY_INTERVAL_MS,
)
```

`ClientError` and `RetryableTransport` remain defined locally in the
development header (inherently necessary for importability; accepted
duplication).

### Validation

The assembled client output changes format (repr-style literals) but not
semantics.  Validate with:

```
python -c "import dnsdle.client_standalone as m; src = m.build_client_source(); open('/tmp/preamble_test.py','wb').write(src.encode('ascii'))"
python /tmp/preamble_test.py --help
python /tmp/preamble_test.py --psk x --domains "INVALID DOMAIN" --mapping-seed s --publish-version v --total-slices 1 --compressed-size 1 --sha256 0000000000000000000000000000000000000000000000000000000000000000 --token-len 4 --verbose; echo "exit=$?"
```

## Affected Components

- `dnsdle/constants.py`: rename 7 `GENERATED_CLIENT_DEFAULT_*` constants
  to short names; add 6 `EXIT_*` constants.
- `dnsdle/client_standalone.py`: replace `_CLIENT_PREAMBLE` with
  `_PREAMBLE_HEADER` + `_PREAMBLE_FOOTER` + `_PREAMBLE_CONSTANTS` tuple;
  update `build_client_source()` to generate constants block; add
  `from dnsdle import constants as _c`.
- `dnsdle/client_runtime.py`: replace aliased runtime-tuning imports and
  hardcoded `EXIT_*` lines with direct imports from `constants.py`.
- `doc/architecture/CLIENT_GENERATION.md`: update Architecture bullet 4
  to note that `build_client_source()` generates the constants section
  programmatically from `constants.py` (no longer a static string literal).

## Execution Notes

Executed 2026-02-20.  All plan items implemented as specified with no
deviations.

- `constants.py`: renamed 7 `GENERATED_CLIENT_DEFAULT_*` to short names;
  added 6 `EXIT_*` constants.
- `client_standalone.py`: replaced `_CLIENT_PREAMBLE` monolithic string
  with `_PREAMBLE_HEADER` + `_PREAMBLE_CONSTANTS` flat tuple +
  `_PREAMBLE_FOOTER`; updated `build_client_source()` to generate the
  constants block via `repr(getattr(_c, name))`.
- `client_runtime.py`: collapsed aliased `_c.GENERATED_CLIENT_DEFAULT_*`
  imports and hardcoded `EXIT_*` lines into a single direct import from
  `dnsdle.constants`.
- `CLIENT_GENERATION.md`: updated Architecture bullet 4.

Validation: assembled client compiles (36058 bytes ASCII), `--help` works,
invalid-domain smoke test exits with code 2 as expected.

Commit: 2d5e075
