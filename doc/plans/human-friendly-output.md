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
  A module-level `_ENABLED` flag, set by `configure_console(enabled)`,
  gates every function.

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
regardless of mode.  In default mode they appear as human text; in
`--verbose` mode they still go to JSON on stdout (current behavior) but
the console module is disabled so no duplicate stderr line.

### Suppressing JSON in default mode

When `--verbose` is *not* set and `--log-file` is *not* set,
`logging_runtime.configure_active_logger` receives a stream that
discards all writes (a `_NullStream` class).  This ensures every
existing `log_event` / `emit_structured_record` call site works without
modification; they just write into the void.

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
- `--log-file` + `--verbose`: JSON to file, no human output.
- Neither: human output to stderr, JSON suppressed.

## Affected Components

- `dnsdle/cli.py`: add `--verbose` flag to parser and known-options set.
- `dnsdle/constants.py`: no changes needed (no new constants required).
- `dnsdle/config.py`: add `verbose` field to Config namedtuple; wire
  parsed arg through `build_config`.
- `dnsdle/logging_runtime.py`: add `_NullStream` class; use it in
  `configure_active_logger` when verbose is False and log_file is empty.
  Accept `verbose` parameter.
- `dnsdle/console.py` (new file): all human-friendly stderr output
  helpers and color logic.
- `dnsdle/__init__.py`: call `configure_console` after
  `configure_active_logger`; pass `verbose` to logger configuration.
- `dnsdle.py`: call `console_startup`, `console_error` in `main()`.
- `dnsdle/server.py`: call `console_server_start`, `console_activity`,
  `console_shutdown` from `serve_runtime`; build file_tag-to-filename
  map; track seen file_tags set.
