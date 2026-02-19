# Plan: Organize and Colorize CLI Help Output

## Summary
Restructure the `--help` output of `dnsdle.py` so arguments are grouped by
purpose, every argument has a short help description with its default/range,
and required arguments appear first. Add ANSI color to group headings and the
required marker when stdout is a TTY.

## Problem
The current `--help` output is a flat, ungrouped list of 20 arguments with no
descriptions. Required and optional arguments are visually indistinguishable.
Operators cannot tell at a glance which flags matter, what the defaults are, or
which values are valid without reading architecture docs.

## Goal
After implementation, `python dnsdle.py --help` produces output shaped like:

```
usage: dnsdle.py [options]

required:
  --domains DOMAINS       comma-separated base domains (required)
  --files FILES           comma-separated file paths to publish (required)
  --psk PSK               shared secret for v1 crypto (required)

server:
  --listen-addr ADDR      UDP bind address (default: 0.0.0.0:53)
  --ttl N                 answer TTL in seconds, 1..300 (default: 30)

dns/wire:
  --dns-edns-size N       EDNS UDP size, 512..4096 (default: 1232)
  --dns-max-label-len N   payload label cap, 16..63 (default: 63)
  --response-label LABEL  CNAME response discriminator (default: r-x)

mapping:
  --mapping-seed SEED     deterministic mapping seed (default: 0)
  --file-tag-len N        file-tag length, 4..16 (default: 6)

generation:
  --target-os OS          windows,linux or subset (default: windows,linux)
  --client-out-dir DIR    output dir for generated clients (default: ./generated_clients)
  --compression-level N   zlib level, 0..9 (default: 9)

logging:
  --log-level LEVEL       error|warn|info|debug|trace (default: info)
  --log-categories CATS   category filter or "all" (default: startup,publish,server)
  --log-sample-rate RATE  sampling rate, 0..1 (default: 1.0)
  --log-rate-limit-per-sec N  rate limit per second (default: 200)
  --log-output MODE       stdout|file (default: stdout)
  --log-file PATH         log file path (required when --log-output=file)
  --log-focus KEY         focus key for debug filtering
```

When stdout is a TTY, group headings are bold/colored. When piped or on
non-TTY, output is plain text (no escape codes). The check is on stdout
because argparse `print_help()` writes to `sys.stdout`.

## Design

### 1. Use argparse argument groups
Replace the flat `add_argument` calls in `_build_parser()` with
`parser.add_argument_group(title)` groups in this order:

1. **required** -- `--domains`, `--files`, `--psk`
2. **server** -- `--listen-addr`, `--ttl`
3. **dns/wire** -- `--dns-edns-size`, `--dns-max-label-len`, `--response-label`
4. **mapping** -- `--mapping-seed`, `--file-tag-len`
5. **generation** -- `--target-os`, `--client-out-dir`, `--compression-level`
6. **logging** -- `--log-level`, `--log-categories`, `--log-sample-rate`,
   `--log-rate-limit-per-sec`, `--log-output`, `--log-file`, `--log-focus`

### 2. Add help text to every argument
Each `add_argument` call gets a `help=` string showing a brief description
and the default or valid range where applicable. Use argparse's built-in
`%(default)s` interpolation to avoid hardcoding defaults in two places.
Format: `"<description>, <range> (default: %(default)s)"`.

Required arguments use `help="<description> (required)"`.

### 3. Colorize group titles on TTY
Subclass `argparse.HelpFormatter` to wrap group titles in ANSI bold
(`\033[1m...\033[0m`) when `sys.stdout.isatty()` is true. The subclass
overrides `start_section` to inject the escape codes around the title.
This is the minimal intervention point; no other formatter behavior changes.

Pass the custom formatter via `formatter_class=` to `_RaisingArgumentParser`.

### 4. Suppress default argparse options group
Use `add_help=False` to prevent argparse from creating the default `options:`
group containing only `-h, --help`. Without this, argparse emits an extra
group header above the custom groups that doesn't match the goal output.
Add `-h`/`--help` explicitly as the last entry in the **required** group
with `help="show this help message and exit"`. This keeps `-h` discoverable
while producing clean, group-only output.

### 5. Update `_LONG_OPTIONS` and `_KNOWN_LONG_OPTIONS`
No change needed -- the set of accepted option names is unchanged. The grouping
is purely a display concern.

## Validation
- Run `python dnsdle.py --help` in a terminal to confirm grouped output with
  ANSI-colored headings.
- Run `python dnsdle.py --help | cat` to confirm plain text output with no
  escape codes (stdout is not a TTY when piped).
- Verify that `%(default)s` values in help text match the actual `default=`
  parameter on each argument.
- Run existing test suite (`python -m pytest unit_tests/`) to confirm no
  regressions in argument parsing behavior.

## Affected Components
- `dnsdle/cli.py`: restructure `_build_parser()` to use argument groups, add
  `help=` strings, add custom `HelpFormatter` subclass for TTY colorization.

## Execution Notes

Implemented as planned with no deviations:

- Added `_ColorHelpFormatter` subclass overriding `start_section` to wrap
  headings in ANSI bold when `sys.stdout.isatty()`.
- Changed `add_help=False`, added explicit `-h`/`--help` with `action="help"`
  and `default=argparse.SUPPRESS` as last entry in the required group.
- All 20 arguments assigned to 6 named groups with `help=` strings using
  `%(default)s` interpolation for optional args.
- All existing defaults and `_LONG_OPTIONS`/`_KNOWN_LONG_OPTIONS` unchanged.

Validation results:
- Help output shows 6 clean groups with correct defaults, no stray `options:`
  section.
- ANSI bold codes present in all 6 headings when `isatty()` is true; absent
  when false.
- 126/126 existing unit tests pass with no regressions.
