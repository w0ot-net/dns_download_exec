# Plan: Dynamic local resolver detection in the stager

## Summary

Add OS-specific resolver auto-discovery to the stager so it no longer requires
`--resolver` as a mandatory argument. The stager will lift the same resolver
logic from `resolver_linux.py` / `resolver_windows.py` that the client template
already uses, following the identical `# __TEMPLATE_SOURCE__` sentinel pattern.
The one-liner becomes fully zero-argument (PSK is already embedded, resolver is
now auto-discovered).

## Problem

The stager currently requires an explicit `--resolver HOST` argument on every
invocation. The generated client already has OS-specific resolver discovery
(parsing `/etc/resolv.conf` on Linux, running `nslookup` on Windows), but the
stager hard-codes `raise ValueError("--resolver required")` when the argument
is absent. This forces users to know and supply the target machine's DNS
resolver at invocation time, which is unnecessary since every machine already
has a configured resolver.

## Goal

1. The stager discovers the system resolver when `--resolver` is not provided.
2. `--resolver` remains available as an optional override.
3. Resolver source code is lifted from the same modules the client uses -- no
   duplication of discovery logic.
4. The stager template becomes OS-specific (`build_stager_template(target_os)`),
   matching the client template pattern.
5. The generated one-liner is fully standalone: no arguments required.
6. The discovered resolver is forwarded to the exec'd client via `sys.argv`
   to avoid redundant re-discovery.

## Design

### Template restructuring

Split the monolithic `_STAGER_TEMPLATE` string in `stager_template.py` into
three parts, mirroring `client_template.py`'s architecture:

- **`_STAGER_PREFIX`**: shebang, imports (with `@@EXTRA_IMPORTS@@` placeholder),
  embedded constants, and all helper function definitions through `_send_query`.
- **`_STAGER_DISCOVER`**: a small `_discover_resolver()` wrapper function with
  a `@@LOADER_FN@@` build-time placeholder.
- **`_STAGER_SUFFIX`**: the `# __RUNTIME__` section, modified to call
  `_discover_resolver()` when `--resolver` is absent.

`build_stager_template(target_os)` assembles:
`prefix + lifted_resolver_source + discover + suffix`

For `target_os == "linux"`:
- `@@EXTRA_IMPORTS@@` = empty string (no extra imports needed)
- Lifted source from `resolver_linux.py` (after sentinel)
- `@@LOADER_FN@@` = `_load_unix_resolvers`

For `target_os == "windows"`:
- `@@EXTRA_IMPORTS@@` = `import re\nimport subprocess`
- Lifted source from `resolver_windows.py` (after sentinel)
- `@@LOADER_FN@@` = `_load_windows_resolvers`

### Resolver discovery wrapper

Iterate-and-validate function inlined into the stager, matching the client
template's `_discover_system_resolver()` pattern (`client_template.py:644-653`):

```python
def _discover_resolver():
    for _h in @@LOADER_FN@@():
        try:
            _ai = socket.getaddrinfo(_h, 53, socket.AF_INET, socket.SOCK_DGRAM)
            if _ai:
                return _ai[0][4]
        except Exception:
            continue
    raise ValueError("no resolver")
```

Iterates through all resolvers returned by the OS-specific loader and returns
the first `(host, port)` tuple that resolves via `getaddrinfo`. This avoids
committing to a stub listener (e.g. `127.0.0.53` from systemd-resolved) that
may be unreachable, which would otherwise burn the entire retry deadline.
Returns a `(host, port)` sockaddr tuple directly -- the runtime section uses
it as `addr` without further parsing. Fails fast with `ValueError` if no
configured resolver passes `getaddrinfo`.

### Runtime section changes

Replace:
```python
if not resolver:
    raise ValueError("--resolver required")
host = resolver
port = 53
if ":" in resolver:
    host, _port_s = resolver.rsplit(":", 1)
    port = int(_port_s)
addr = (host, port)
```

With:
```python
if not resolver:
    addr = _discover_resolver()
    _sa = ["--resolver", "%s:%d" % addr] + list(_sa)
else:
    host = resolver
    port = 53
    if ":" in resolver:
        host, _port_s = resolver.rsplit(":", 1)
        port = int(_port_s)
    addr = (host, port)
```

When auto-discovered, `_discover_resolver()` returns a validated `(host, port)`
sockaddr tuple used directly as `addr`. The stringified `host:port` is injected
into `_sa` so the exec'd client receives it via `sys.argv` and does not need
to re-discover. When `--resolver` is provided explicitly, the existing
host/port parsing applies unchanged.

### One-liner format

Remove the trailing ` --resolver RESOLVER` from the generated one-liner in
`stager_generator.py`. The one-liner becomes:
```
python3 -c "import base64,zlib;exec(zlib.decompress(base64.b64decode('...')))"
```

### Resolver source lifting

Import `_lift_resolver_source` from `dnsdle.client_template` rather than
duplicating it. Both template modules live in the same package and share the
same resolver source files; the coupling is justified and avoids code
duplication. No circular import exists (`client_template` does not import
`stager_template`).

### Minifier updates

Add rename entries to `_RENAME_TABLE` in `stager_minify.py` for all new
identifiers introduced by the lifted resolver code. Entries must be inserted
at length-sorted positions to maintain the longest-first ordering invariant.

All new identifiers >3 characters from both platform resolver modules, the
discovery wrapper, and any existing stager locals that now appear in more
scopes. Both platforms' entries are included since the rename table is shared
and unmatched entries are harmless no-ops. All candidates verified safe from
`\b` word-boundary collisions with string literals in both resolver modules,
the stager template, and the discovery wrapper (`"nameserver"`, `"run"`,
`"server:"`, `"no resolver"` -- none contain a candidate as a delimited word).

Function names and module-level constants:

- `_load_windows_resolvers` (23 chars)
- `_parse_nslookup_output` (22 chars)
- `_load_unix_resolvers` (20 chars)
- `_discover_resolver` (18 chars)
- `_run_nslookup` (13 chars)
- `server_index` (12 chars)
- `resolvers` (9 chars)
- `stripped` (8 chars)
- `_IPV4_RE` (8 chars)
- `raw_line` (8 chars)

Local variables (all verified safe, worthwhile compression):

- `handle` (6 chars) -- Linux resolver, local in `_load_unix_resolvers`
- `result` (6 chars) -- Windows resolver, local in `_run_nslookup`
- `output` (6 chars) -- Windows resolver, local in `_parse_nslookup_output`
  and `_load_windows_resolvers`
- `run_fn` (6 chars) -- Windows resolver, local in `_run_nslookup`
- `lines` (5 chars) -- Windows resolver, local in `_parse_nslookup_output`
- `match` (5 chars) -- Windows resolver, local in `_parse_nslookup_output`
- `index` (5 chars) -- Windows resolver, local in `_parse_nslookup_output`
- `line` (4 chars) -- both resolvers, local in loader functions
- `args` (4 chars) -- Windows resolver, local in `_run_nslookup`
- `proc` (4 chars) -- Windows resolver, local in `_run_nslookup`

Skipped (already <=3 characters, negligible gain):

- `_h` (2 chars), `_ai` (3 chars) -- discovery wrapper
- `ip` (2 chars) -- Windows resolver

### Generator changes

In `generate_stagers()`, build the template per `target_os` instead of once:

```python
template_by_os = {}
...
for artifact in generation_result["artifacts"]:
    target_os = artifact["target_os"]
    if target_os not in template_by_os:
        template_by_os[target_os] = build_stager_template(target_os)
    template = template_by_os[target_os]
    stager = generate_stager(config, template, client_item, target_os)
```

## Affected Components

- `dnsdle/stager_template.py`: split monolithic template into prefix/discover/
  suffix; import `_lift_resolver_source` from `client_template`; change
  `build_stager_template()` to `build_stager_template(target_os)`; add
  `_discover_resolver` wrapper and `@@EXTRA_IMPORTS@@`/`@@LOADER_FN@@`
  build-time placeholders; modify runtime section to auto-discover.
- `dnsdle/stager_generator.py`: build template per `target_os` in
  `generate_stagers()`; remove ` --resolver RESOLVER` from one-liner format.
- `dnsdle/stager_minify.py`: add rename entries for resolver-related
  identifiers from both platform resolver modules.
- `dnsdle/resolver_linux.py`: no changes (source of truth, lifted via sentinel).
- `dnsdle/resolver_windows.py`: no changes (source of truth, lifted via
  sentinel).
- `dnsdle/client_template.py`: no changes (`_lift_resolver_source` imported
  from here).

## Test Breakage

The following tests call `build_stager_template()` with no arguments and will
fail after the signature changes to `build_stager_template(target_os)`:

- `unit_tests/test_stager_template.py`: `_build_ns()` helper on line 26.
- `unit_tests/test_stager_minify.py`: `test_full_template_compiles_after_minify`
  on line 98. This test also uses stale `SLICE_TOKENS` substitution (pre-
  existing breakage).
- `unit_tests/test_stager_generator.py`: three tests on lines 44, 57, 76.

Tests are not modified per repository policy.
