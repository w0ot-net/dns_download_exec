# Plan: OS-Specific Resolver Discovery in Generated Clients

## Summary
Replace the runtime `os.name` branch in the generated client template with
build-time OS-specific resolver discovery code. The generator already emits one
client per `(file, target_os)`, so each artifact knows its target OS at
generation time. Emit only the resolver discovery functions for that OS,
eliminating the dead branch, its imports, and the runtime dispatch.

## Problem
The current `CLIENT_TEMPLATE` in `dnsdle/client_template.py` contains both Unix
(`_load_unix_resolvers`, reading `/etc/resolv.conf`) and Windows
(`_load_windows_resolvers` / `_run_nslookup` / `_parse_nslookup_output`, shelling
out to `nslookup`) resolver discovery code paths, plus a runtime `os.name == "nt"`
branch in `_discover_system_resolver()`. Every generated client carries both OS
paths regardless of its `TARGET_OS`. This wastes artifact size and includes
dead code (for example, `import subprocess` and the `_IPV4_RE` regex are
Windows-only but present in Linux clients).

## Goal
After implementation:
- A `target_os=linux` client contains only the Unix resolver discovery path
  and does not import `subprocess` or define `_IPV4_RE`.
- A `target_os=windows` client contains only the Windows resolver discovery
  path and does not define `_load_unix_resolvers`.
- `_discover_system_resolver()` calls the single available loader directly
  with no `os.name` branch.
- Generated clients remain standalone single-file Python 2.7/3.x scripts.
- No functional behavior change: each OS path works identically to today.

## Design

### 1. Split the template into shared core + OS-specific resolver blocks
Replace the single `CLIENT_TEMPLATE` string in `dnsdle/client_template.py` with
three parts:
- `_TEMPLATE_PREFIX`: everything from the shebang through `_parse_resolver_arg`,
  stopping before `_load_unix_resolvers`.
- `_RESOLVER_BLOCK_LINUX`: contains `_load_unix_resolvers` and a
  `_discover_system_resolver` that calls it directly (no branch).
- `_RESOLVER_BLOCK_WINDOWS`: contains `_run_nslookup`,
  `_parse_nslookup_output`, `_load_windows_resolvers`, and a
  `_discover_system_resolver` that calls it directly (no branch).
- `_TEMPLATE_SUFFIX`: everything from `_send_dns_query` through the `__main__`
  block.

Expose a function `build_client_template(target_os)` that returns
`_TEMPLATE_PREFIX + resolver_block + _TEMPLATE_SUFFIX` for the given OS.
This is the only public interface; `CLIENT_TEMPLATE` is removed as a module-level
constant.

### 2. Adjust imports per OS block
- `_TEMPLATE_PREFIX` includes all imports shared by both OS paths
  (`argparse`, `base64`, `hashlib`, `hmac`, `os`, `random`, `re`, `socket`,
  `struct`, `sys`, `tempfile`, `time`, `zlib`).
- `_RESOLVER_BLOCK_LINUX` needs no additional imports.
- `_RESOLVER_BLOCK_WINDOWS` prepends `import subprocess` at the top of its
  block. Move `_IPV4_RE` into this block as well since it is only used by
  `_parse_nslookup_output`.

Since `import subprocess` must appear at module top level in the generated
script, the cleanest approach is:
- Remove `import subprocess` from `_TEMPLATE_PREFIX`.
- Add `import subprocess` to `_TEMPLATE_PREFIX` only when `target_os=windows`.

This is achieved by having `_TEMPLATE_PREFIX` contain a placeholder
`@@EXTRA_IMPORTS@@` after the standard imports. `build_client_template` replaces
it with `import subprocess\n` for Windows or an empty string for Linux.

### 3. Update client_generator.py to use the new interface
In `_render_client_source`, replace the static `CLIENT_TEMPLATE` reference with
a call to `build_client_template(target_os)`. The rest of the placeholder
substitution logic is unchanged.

Update the import: `from dnsdle.client_template import build_client_template`
instead of `from dnsdle.client_template import CLIENT_TEMPLATE`.

### 4. Remove dead code from each OS path
- Linux block: no `_run_nslookup`, `_parse_nslookup_output`,
  `_load_windows_resolvers`, `_IPV4_RE`, `import subprocess`.
- Windows block: no `_load_unix_resolvers`.
- Both blocks: no `os.name` check in `_discover_system_resolver`.

### 5. Update architecture docs
- `doc/architecture/CLIENT_GENERATION.md`: add a statement that generated
  clients contain only the resolver discovery code for their `target_os`,
  with no runtime OS branching.
- `doc/architecture/CLIENT_RUNTIME.md`: update the Resolver Behavior section
  to note that system resolver discovery is OS-specific at generation time,
  not runtime.

## Affected Components
- `dnsdle/client_template.py`: split `CLIENT_TEMPLATE` into prefix/suffix plus
  two OS-specific resolver blocks; expose `build_client_template(target_os)`;
  move `import subprocess` and `_IPV4_RE` into Windows-only block.
- `dnsdle/client_generator.py`: change import from `CLIENT_TEMPLATE` to
  `build_client_template`; call it with `target_os` in `_render_client_source`.
- `doc/architecture/CLIENT_GENERATION.md`: document OS-specific resolver
  discovery emission contract.
- `doc/architecture/CLIENT_RUNTIME.md`: clarify resolver discovery is
  build-time OS-specific.
