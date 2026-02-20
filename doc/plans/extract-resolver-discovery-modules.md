# Plan: Extract Resolver Discovery into Standalone Modules

## Summary

Extract OS-specific resolver discovery logic from string literals in
`client_template.py` into real Python modules (`resolver_linux.py`,
`resolver_windows.py`). The client template reads source from these modules at
generation time via a sentinel-delimited lift pattern. The resolver logic becomes
independently importable and testable while generated client output remains
unchanged.

## Problem

The resolver discovery functions (`_load_unix_resolvers`, `_run_nslookup`,
`_parse_nslookup_output`, `_load_windows_resolvers`) exist only as string
literals (`_RESOLVER_BLOCK_LINUX`, `_RESOLVER_BLOCK_WINDOWS`) inside
`client_template.py`. They cannot be imported, syntax-checked, linted, or
unit-tested without generating and executing a complete client artifact.

## Goal

After implementation:
- `_load_unix_resolvers` lives in `dnsdle/resolver_linux.py` as importable code.
- `_IPV4_RE`, `_run_nslookup`, `_parse_nslookup_output`, and
  `_load_windows_resolvers` live in `dnsdle/resolver_windows.py` as importable
  code.
- `client_template.py` reads source from these modules at generation time to
  build the resolver blocks for generated clients.
- Generated client output is functionally identical (same inlined resolver code).
- The stager template can lift resolver discovery from these same modules in a
  future change.

## Design

### 1. Sentinel-delimited source lift pattern

Each resolver module uses a `# __TEMPLATE_SOURCE__` sentinel (matching the
existing `# __RUNTIME__` sentinel pattern in `stager_template.py`). Everything
above the sentinel is module boilerplate (`from __future__`, `import` lines)
needed for direct import. Everything below the sentinel is the function source
that gets lifted verbatim into the generated client template.

### 2. Create `dnsdle/resolver_linux.py`

Module structure:
```
from __future__ import absolute_import

# __TEMPLATE_SOURCE__

def _load_unix_resolvers():
    # (exact current logic from _RESOLVER_BLOCK_LINUX)
```

Importable for testing: `from dnsdle.resolver_linux import _load_unix_resolvers`

### 3. Create `dnsdle/resolver_windows.py`

Module structure:
```
from __future__ import absolute_import

import re
import subprocess

# __TEMPLATE_SOURCE__

_IPV4_RE = re.compile(...)

def _run_nslookup():
    ...

def _parse_nslookup_output(output):
    ...

def _load_windows_resolvers():
    ...
```

Importable for testing:
`from dnsdle.resolver_windows import _parse_nslookup_output` etc.

### 4. Update `client_template.py`

- Add `import os` at module level (currently only imports `absolute_import` and
  `StartupError`; the `import os` on line 14 of the file is inside the
  `_TEMPLATE_PREFIX` string literal, not a real module-level import).
- Remove `_RESOLVER_BLOCK_LINUX` and `_RESOLVER_BLOCK_WINDOWS` string literals.
- Add `_lift_resolver_source(filename)`: resolves sibling `.py` file via
  `os.path.dirname(os.path.abspath(__file__))`, reads its contents, finds the
  `# __TEMPLATE_SOURCE__` sentinel, returns everything after it (including the
  newline immediately following the sentinel line). Raises `StartupError` with
  `reason_code="generator_invalid_contract"` and the filename in context if
  the file cannot be read or the sentinel is missing (fail-fast invariant).
- Add `_DISCOVER_SYSTEM_RESOLVER` string literal containing the 7-line
  `_discover_system_resolver()` wrapper with a `@@LOADER_FN@@` placeholder.
  This wrapper bridges the platform loader to the template-internal
  `_resolve_udp_address` and `ClientError` and is identical across platforms
  except for the loader function name.
- Newline boundary contract: `_lift_resolver_source` returns text that begins
  with a newline (the line after the sentinel) so no extra padding is needed at
  the prefix join. `_DISCOVER_SYSTEM_RESOLVER` must include its own leading
  `\n\n` separator and trailing `\n\n\n` to match the current resolver block
  endings. The three-section concatenation `_TEMPLATE_PREFIX + lifted_source +
  discover_wrapper + _TEMPLATE_SUFFIX` must produce output identical to the
  current two-section concatenation.
- Update `build_client_template(target_os)`:
  - Call `_lift_resolver_source("resolver_linux.py")` or
    `_lift_resolver_source("resolver_windows.py")`.
  - Substitute `@@LOADER_FN@@` in the discover wrapper with
    `_load_unix_resolvers` or `_load_windows_resolvers`.
  - Assemble: `_TEMPLATE_PREFIX + lifted_source + discover_wrapper +
    _TEMPLATE_SUFFIX`.
  - Handle `@@EXTRA_IMPORTS@@` as before (`import subprocess` for Windows,
    empty for Linux).

### 5. Validate output identity

Generate client templates for both `linux` and `windows` targets before and
after the change using identical inputs. Diff the outputs to confirm they are
byte-identical. This is the primary verification that the refactoring preserves
behavior.

### 6. Update architecture docs

Update `doc/architecture/CLIENT_GENERATION.md` to note that OS-specific resolver
discovery source is maintained in standalone modules under `dnsdle/` and lifted
into generated client templates at generation time via the `__TEMPLATE_SOURCE__`
sentinel pattern.

## Affected Components

- `dnsdle/resolver_linux.py` (new): standalone `_load_unix_resolvers()`, lifted
  into Linux client templates.
- `dnsdle/resolver_windows.py` (new): standalone `_IPV4_RE`,
  `_run_nslookup()`, `_parse_nslookup_output()`, `_load_windows_resolvers()`,
  lifted into Windows client templates.
- `dnsdle/client_template.py`: replace `_RESOLVER_BLOCK_LINUX` and
  `_RESOLVER_BLOCK_WINDOWS` string literals with `_lift_resolver_source()`
  file-read mechanism; add `_DISCOVER_SYSTEM_RESOLVER` wrapper template.
- `doc/architecture/CLIENT_GENERATION.md`: document resolver source lift-in
  pattern.
