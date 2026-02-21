# Plan: Human-Friendly Console Output

## Summary

Replace the default verbose JSON log output with concise, colorized
human-readable messages on stderr.  Add a `--verbose` flag that preserves
the existing JSON-to-stdout behavior for programmatic consumers.  The
default experience becomes a short startup banner, per-stager paths, a
"listening" line, one-line download-activity notices, and a shutdown
summary -- all optionally colorized when stderr is a TTY.

## Problem

Running `dnsdle.py` today dumps every event as a full JSON record to
stdout.  This is useful for log ingestion but overwhelming for interactive
use.  There is no quick way to see "what files are published, where are
the stagers, is anything downloading" without parsing JSON.

## Goal

- Default run (`python dnsdle.py ...`) prints a handful of readable,
  color-highlighted lines to stderr and suppresses JSON on stdout.
- `--verbose` restores full JSON logging to stdout (current behavior)
  and suppresses the human-friendly stderr output.
- Colors auto-disable when stderr is not a TTY or on platforms without
  ANSI support.

## Design

### New CLI flag

Add `--verbose` as a boolean flag in `cli.py`.  Propagate it through
`Config` so every component can branch on it.

### Two output modes

| condition              | stderr (human)       | stdout (JSON)       |
|------------------------|----------------------|---------------------|
| default (no --verbose) | concise lines        | suppressed          |
| --verbose              | suppressed           | JSON (current)      |

*Suppressed* means the RuntimeLogger is created with a no-op stream that
silently discards writes, so all existing `log_event` / `emit_record`
call sites keep working unchanged.

### Human-friendly output module -- `dnsdle/console.py`

A new, small module that owns stderr writing.  Key helpers:

- `_color(code, text)` -- wraps text in `\033[<code>m ... \033[0m` when
  `_USE_COLOR` is True.
- Module-level `_USE_COLOR` set once at import time: True only when
  `sys.stderr.isatty()` is True and the platform is not Windows (unless
  Windows 10+ with VT support, detected via `os.name != 'nt'` for
  simplicity).
- `console_startup(config, generation_result, stagers)` -- prints the
  startup banner.
- `console_server_start(host, port)` -- prints the listening line.
- `console_activity(file_tag, file_id, source_filename)` -- prints a
  one-line notice when a new file_tag is first served (download started).
- `console_error(message)` -- prints an error line.
- `console_shutdown(counters)` -- prints the summary line.
- All functions are no-ops when the module is disabled (verbose mode).
  A module-level `_ENABLED` flag gates every function.  It defaults to
  `True` so that `console_error` works for errors that occur before
  `configure_console` is called (e.g. config-parsing failures).
  `configure_console(enabled)` overrides the flag explicitly.

### Startup banner content (example)

```
dnsdle serving 2 files via [example.com, cdn.example.com]
  stagers:  ./generated_clients/dnsdle_v1/
    payload.bin  -> payload.1-liner.txt
    tool.py      -> tool.1-liner.txt
  client:   ./generated_clients/dnsdle_v1/dnsdle_v1.py
listening on 0.0.0.0:53 (ctrl-c to stop)
```

### Runtime activity (example)

```
<< download started: payload.bin (tag=a3bf2k)
```

Triggered the first time a `served` classification is emitted for a
previously-unseen `file_tag`.  The set of seen tags lives in
`serve_runtime`.  We call `console_activity` from the request loop
right after incrementing the `served` counter.

To map `file_tag -> source_filename` we build a dict from
`runtime_state.publish_items` at the start of `serve_runtime` and pass
it through.  The universal-client file_tag is labeled "(universal client)"
instead of a source filename.

### Shutdown summary (example)

```
shutdown: served=142 miss=3 faults=0
```

### Error output

Errors (startup failures, runtime faults) are printed to stderr in red
in default mode.  In `--verbose` mode they go to JSON on stdout (current
behavior) and the console module is disabled, so no duplicate stderr
line.

Startup errors are surfaced by `console_error(...)` in the except blocks
of `main()`.

Runtime faults inside `serve_runtime` (recv_error, send_error,
unhandled_request_exception) are surfaced by calling `console_error(...)`
alongside the existing `emit_record` call at each fault site.  This
keeps the user informed of operational errors in real time; the shutdown
summary still reports aggregate counts.

### Suppressing JSON in default mode

`_NullStream` is a trivial class in `logging_runtime.py` whose `write`
and `flush` methods silently discard all data.

**Bootstrap logger.**  `_bootstrap_logger()` changes from
`stream=sys.stdout` to `stream=_NullStream()`.  Combined with
`_ENABLED = True` in `console.py`, this means errors that occur before
`configure_active_logger` (e.g. invalid CLI args) are routed to stderr
via `console_error` and the JSON record is silently discarded.  This is
the correct default-mode behavior -- the user sees a human error, not
raw JSON.

**Configured logger.**  `build_logger_from_config` reads `config.verbose`
and `config.log_file` to choose the stream:

| verbose | log_file set | stream passed to RuntimeLogger |
|---------|--------------|-------------------------------|
| False   | no           | `_NullStream()`               |
| False   | yes          | `None` (opens log_file)       |
| True    | no           | `None` (falls through to stdout) |
| True    | yes          | `None` (opens log_file)       |

This ensures every existing `log_event` / `emit_structured_record` call
site works without modification; in default mode they just write into
the void.

When `--log-file` *is* set, JSON always goes to that file regardless of
`--verbose`, so the user can get both human stderr and JSON file output
simultaneously.

### Integration in `dnsdle.py`

After `build_startup_state` returns, `main()` calls the `console_*`
functions instead of (or in addition to) `_emit_record`.  The
`_emit_record` calls remain -- they just go to the null stream in
default mode.  The sequence:

1. `configure_console(enabled=not config.verbose)` -- early, right
   after config is built (inside `build_startup_state`, after
   `configure_active_logger`).
2. `console_startup(...)` -- after startup completes.
3. `serve_runtime` internally calls `console_server_start` and
   `console_activity`.
4. `console_shutdown(...)` -- called from `serve_runtime` before return.
5. Errors: `console_error(...)` in the except blocks of `main()`.

### Color palette

| element         | ANSI code | appearance   |
|-----------------|-----------|--------------|
| banner header   | 1;36      | bold cyan    |
| file paths      | 0;33      | yellow       |
| listen address  | 1;32      | bold green   |
| download notice | 0;36      | cyan         |
| shutdown stats  | 0;37      | white/normal |
| errors          | 1;31      | bold red     |

### `--verbose` interaction with `--log-level` and `--log-file`

- `--verbose` alone: JSON to stdout at configured log-level (current
  behavior), no human output.
- `--log-file` alone (no --verbose): JSON to file, human output to
  stderr.
- `--log-file` + `--verbose`: JSON to file, no terminal output.
  (The `--verbose` help text should note this.)
- Neither: human output to stderr, JSON suppressed.

## Affected Components

- `dnsdle/cli.py`: add `--verbose` flag to parser and known-options set.
- `dnsdle/constants.py`: no changes needed (no new constants required).
- `dnsdle/config.py`: add `verbose` field to Config namedtuple; wire
  parsed arg through `build_config`.
- `dnsdle/logging_runtime.py`: add `_NullStream` class; change
  `_bootstrap_logger` to use `_NullStream()` instead of `sys.stdout`;
  have `build_logger_from_config` read `config.verbose` and
  `config.log_file` to select the stream (no new parameters on
  `configure_active_logger`).
- `dnsdle/console.py` (new file): all human-friendly stderr output
  helpers and color logic.
- `dnsdle/__init__.py`: call `configure_console` after
  `configure_active_logger`.
- `dnsdle.py`: call `console_startup`, `console_error` in `main()`.
- `dnsdle/server.py`: call `console_server_start`, `console_activity`,
  `console_shutdown`, and `console_error` (at each runtime-fault site)
  from `serve_runtime`; build file_tag-to-filename map; track seen
  file_tags set.

## Execution Notes

Implemented as planned with the following deviations from the review:

1. **Review finding (Medium): `serve_runtime` cannot identify universal
   client.** Addressed by building the `file_tag -> display_name` dict
   in `build_startup_state` (where `client_filename` is already known)
   and passing it to `serve_runtime` via a `display_names` keyword
   argument.  The universal client is labeled `"(universal client)"` in
   the map; `server.py` never imports `_UNIVERSAL_CLIENT_FILENAME`.

2. **Review finding (Medium): `console_activity` signature includes
   unused `file_id`.** Dropped `file_id` from the signature.
   `console_activity(file_tag, display_name)` takes only the two values
   actually shown in the output.

3. **Review finding (Low): Console `_ENABLED` not reset alongside
   `reset_active_logger()`.** Added `reset_console()` to `console.py`
   and `main()` calls it alongside `reset_active_logger()` to ensure
   clean state when `main()` is called multiple times.

4. **Review finding (Low): `cli.py` help color inconsistency.**
   Not addressed -- the existing `print_help` colorization is unrelated
   to the plan scope and changing it would add unnecessary churn.

### Files changed

- `dnsdle/cli.py`: added `--verbose` to `_KNOWN_LONG_OPTIONS` and
  `_build_parser` (store_true, logging group).
- `dnsdle/config.py`: added `verbose` field to `Config` namedtuple;
  wired through `build_config` via `_arg_value_default`.
- `dnsdle/logging_runtime.py`: added `_NullStream`; bootstrap logger
  now uses `_NullStream()`; `build_logger_from_config` selects
  `_NullStream()` when `not config.verbose and not config.log_file`.
- `dnsdle/console.py`: new module with `configure_console`,
  `reset_console`, `console_startup`, `console_server_start`,
  `console_activity`, `console_error`, `console_shutdown`, color
  helpers, and `_ENABLED`/`_USE_COLOR` flags.
- `dnsdle/__init__.py`: calls `configure_console` after
  `configure_active_logger`; builds `display_names` dict mapping
  `file_tag -> display_name` (universal client labeled
  `"(universal client)"`); returns it as fourth element from
  `build_startup_state`.
- `dnsdle.py`: unpacks fourth return value; calls `reset_console` in
  `main()`; calls `console_startup` after startup emits; calls
  `console_error` in all except blocks; passes `display_names` to
  `serve_runtime`.
- `dnsdle/server.py`: accepts `display_names` kwarg; initializes
  `seen_tags` set; calls `console_server_start` after server_start
  emit; calls `console_activity` on first serve per file_tag; calls
  `console_error` at all three runtime-fault sites; calls
  `console_shutdown` before shutdown emit.

### Commits

- `1c888c6`: Add human-friendly console output with --verbose flag
