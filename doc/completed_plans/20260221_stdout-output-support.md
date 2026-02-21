# Plan: Support `--out -` for stdout output

## Summary

Allow `--out -` to write the final plaintext to stdout (binary mode) instead of
a file path.  This is the standard Unix convention for "write to stdout" and
must work identically on Windows and Linux under Python 2.7 and 3.x.

## Problem

Currently `--out` only accepts file paths.  There is no way to pipe output to
another process or capture it in a shell variable without first writing to a
temporary file.  The `-` convention is widely expected by CLI tools but is not
implemented.

## Goal

- `--out -` writes raw binary plaintext to stdout, then exits 0.
- `--out -` works on Windows (no CRLF translation) and Linux.
- `--out -` works on Python 2.7 and Python 3.x.
- All other `--out` behavior (file path, default tempdir path) is unchanged.
- Architecture docs reflect the new behavior.

## Design

### Sentinel detection

In `_parse_runtime_args`, after the existing `out_path` strip, the value `"-"`
passes through unchanged.  No special handling is needed at parse time -- the
sentinel is consumed at write time.

### Stdout writer

Add a new function `_write_stdout(payload)` next to `_write_output_atomic`:

```python
def _write_stdout(payload):
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    stdout_bin = getattr(sys.stdout, "buffer", sys.stdout)
    stdout_bin.write(payload)
    stdout_bin.flush()
```

Rationale:
- Python 3: `sys.stdout.buffer` is the underlying binary stream; avoids
  encoding errors on arbitrary bytes.
- Python 2: `sys.stdout` is already byte-oriented; `getattr` falls back to it.
- Windows: `msvcrt.setmode` disables CRLF translation on the stdout fd.  The
  import is conditional so it never runs on Linux.
- Errors from `write`/`flush` are not caught here; they propagate to the
  existing `ClientError` handler in `main()`.

### Call-site branch in `main()`

Replace the unconditional `_write_output_atomic(out_path, payload)` with:

```python
if out_path == "-":
    _write_stdout(plaintext)
else:
    _write_output_atomic(out_path, plaintext)
```

### Log message

The success log currently prints the path.  For stdout, log `"<stdout>"`:

```python
wrote_label = "<stdout>" if out_path == "-" else out_path
_log("success wrote=%s bytes=%d" % (wrote_label, len(plaintext)))
```

### No validation changes

`-` is a valid `out_path` string; it does not need directory-existence checks
or any new CLI validation.  The `_write_output_atomic` path is simply bypassed.

## Affected Components

- `dnsdle/client_runtime.py`: add `_write_stdout` function; branch call site in
  `main()` between stdout and file write; adjust success log label.
- `doc/architecture/CLIENT_RUNTIME.md`: update "Output Write Behavior" section
  to document `--out -` semantics.

## Execution Notes

Executed 2026-02-21.  All plan items implemented as designed with no deviations.

- Added `_write_stdout(payload)` at line 268 of `client_runtime.py`, immediately
  after `_write_output_atomic`.
- Branched `main()` call site: `out_path == "-"` routes to `_write_stdout`,
  otherwise to `_write_output_atomic`.
- Success log uses `"<stdout>"` label when `out_path == "-"`.
- Updated `CLIENT_RUNTIME.md` "Output Write Behavior" section with stdout mode
  semantics (binary buffer selection, Windows O_BINARY, error propagation).
- No changes to `_parse_runtime_args`; `"-"` passes through the existing strip
  unchanged as designed.
