# Plan: Deduplicate Client Resolver Functions

## Summary

`client_runtime.py` contains verbatim copies of all four resolver functions
from `resolver_linux.py` and `resolver_windows.py`. This plan eliminates the
duplication by importing the canonical functions and extracting them into the
standalone client via `client_standalone.py`, mirroring the approach already
used for the stager.

## Problem

`client_runtime.py` reimplements the resolver logic inline inside the
`# __EXTRACT: client_runtime__` block:

- `_run_nslookup` (lines 372-391) is identical to
  `resolver_windows.py:_run_nslookup`
- `_parse_nslookup_output` (lines 394-417) is identical to
  `resolver_windows.py:_parse_nslookup_output`
- `_load_system_resolvers` (lines 420-451) inlines the body of
  `resolver_linux.py:_load_unix_resolvers` in its `else` branch and wraps
  `_run_nslookup`/`_parse_nslookup_output` in its `win32` branch -- identical
  to `resolver_windows.py:_load_windows_resolvers`

Any fix to the canonical resolver files silently diverges from the client.
The stager was already fixed by the deduplicate-code plan; the client was not.

## Goal

- Each resolver function is defined once, in its canonical module.
- `client_runtime.py` imports and calls them instead of reimplementing them.
- The standalone client extracts the resolver functions from the canonical
  modules ahead of the client_runtime block.
- No behavioral change to client or stager.

## Design

### 1. Add `# __EXTRACT__` markers to resolver modules

Both resolver files already have `# __TEMPLATE_SOURCE__` for the stager.
Add `# __EXTRACT__` markers around each function for the extract system.
The two marker systems are independent; coexistence is safe because the
stager minifier strips comment lines.

**`resolver_linux.py`**: wrap `_load_unix_resolvers` in a single extract block.

**`resolver_windows.py`**: wrap each of the three functions
(`_run_nslookup`, `_parse_nslookup_output`, `_load_windows_resolvers`) in
their own extract blocks.

### 2. Simplify `client_runtime.py`

- Add imports at the top:
  ```python
  from dnsdle.resolver_linux import _load_unix_resolvers
  from dnsdle.resolver_windows import (
      _run_nslookup, _parse_nslookup_output, _load_windows_resolvers,
  )
  ```
- Remove the three duplicated function bodies (`_run_nslookup`,
  `_parse_nslookup_output`, and `_load_system_resolvers`) from the extraction
  block.
- Replace with a minimal `_load_system_resolvers` that delegates:
  ```python
  def _load_system_resolvers():
      if sys.platform == "win32":
          return _load_windows_resolvers()
      return _load_unix_resolvers()
  ```

### 3. Wire resolver extractions into `client_standalone.py`

Add extraction specs and include resolver blocks in `build_client_source()`
before the runtime block:

```python
_RESOLVER_LINUX_EXTRACTIONS = ["_load_unix_resolvers"]
_RESOLVER_WINDOWS_EXTRACTIONS = [
    "_run_nslookup",
    "_parse_nslookup_output",
    "_load_windows_resolvers",
]
```

In `build_client_source()`, extract from both resolver modules and
concatenate them ahead of the `client_runtime` block so that names are
defined before use.

### 4. Update stager minifier rename table

The minifier in `stager_minify.py` already has rename entries for
`_load_windows_resolvers`, `_parse_nslookup_output`, `_load_unix_resolvers`,
and `_run_nslookup` (used by the stager). No changes needed -- these names
are not referenced by the universal client's minification pipeline (only the
stager is minified).

## Affected Components

- `dnsdle/resolver_linux.py`: add `# __EXTRACT__` markers around
  `_load_unix_resolvers`
- `dnsdle/resolver_windows.py`: add `# __EXTRACT__` markers around
  `_run_nslookup`, `_parse_nslookup_output`, `_load_windows_resolvers`
- `dnsdle/client_runtime.py`: remove three duplicated functions, add imports
  from resolver modules, replace with delegating `_load_system_resolvers`
- `dnsdle/client_standalone.py`: add resolver extraction specs and include
  them in `build_client_source()`

## Execution Notes

Executed 2026-02-20.

All three tasks implemented as designed with no deviations:

1. **Resolver module markers**: added `# __EXTRACT__` / `# __END_EXTRACT__`
   markers to `resolver_linux.py` (1 block) and `resolver_windows.py`
   (3 blocks), coexisting with existing `# __TEMPLATE_SOURCE__` sentinels.

2. **client_runtime.py cleanup**: removed ~80 lines of duplicated resolver
   code (`_run_nslookup`, `_parse_nslookup_output`, inline resolv.conf
   parsing). Added imports from canonical resolver modules. Replaced with
   3-line delegating `_load_system_resolvers`.

3. **client_standalone.py wiring**: added `_RESOLVER_LINUX_EXTRACTIONS` and
   `_RESOLVER_WINDOWS_EXTRACTIONS` specs. Inserted extraction calls and
   concatenation in `build_client_source()` so resolver functions appear
   ahead of the `client_runtime` block.

Validation:
- All four changed modules pass `py_compile`.
- All module imports succeed (no circular dependency).
- `extract_functions` successfully extracts all 4 resolver blocks.
- `build_client_source()` produces valid universal client (35618 chars)
  containing all resolver functions.
- `build_stager_template()` still produces valid stager template (13436
  chars) with resolver functions -- no regression.
