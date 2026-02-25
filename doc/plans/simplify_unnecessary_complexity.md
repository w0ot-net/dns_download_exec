# Simplify Unnecessary Complexity

**Status**: Draft
**Created**: 2026-02-24

## Overview

Analysis of the codebase for processes/algorithms/systems that are more
complicated than necessary, where simplification would yield meaningful
reduction in code and cognitive overhead.

## Affected Components

- `dnsdle/stager_minify.py`
- `dnsdle/logging_runtime.py`
- `dnsdle/config.py`

---

## Finding 1: `_collect_kwarg_names` depth tracking in `stager_minify.py`

`_collect_kwarg_names` builds a per-character depth array for the entire source
string to distinguish kwargs (`f(x=5)`) from assignments (`x = 5`). The
minifier does global renames (all occurrences or none), so this depth tracking
only helps when an identifier appears as a top-level assignment but never as a
kwarg. In practice this applies to ~15-20 uppercase stager constants
(`DOMAIN_LABELS`, `FILE_TAG`, etc.). Renaming them saves ~200-300 bytes
pre-compression, which collapses to ~50-100 bytes after zlib -- negligible in a
3-5KB base64 one-liner.

**Current code** (~11 lines):

```python
def _collect_kwarg_names(source):
    depth = 0
    depths = []
    for ch in source:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        depths.append(depth)
    kwarg_names = set()
    for m in _KWARG_RE.finditer(source):
        if depths[m.start()] > 0:
            kwarg_names.add(m.group(1))
    return kwarg_names
```

**Simplified** (~2 lines):

```python
def _collect_kwarg_names(source):
    return set(m.group(1) for m in _KWARG_RE.finditer(source))
```

Slightly less aggressive renaming, virtually zero impact after compression.

---

## Finding 2: Dual emit paths in `logging_runtime.py`

Two parallel emit methods exist:

- `emit(level, category, event)` -- caller provides explicit level + category
- `emit_record(record, level=None, category=None)` -- auto-infers
  level/category from record fields, or uses explicit overrides

Both call `_do_emit()`. `emit_record` is a strict superset of `emit` -- it does
everything `emit` does when you pass explicit level and category. The two
methods then surface as two module-level wrappers:

- `log_event()` wraps `emit()`
- `emit_structured_record()` wraps `emit_record()`

This means 4 functions/methods where 2 would suffice. `emit()` and
`log_event()` could be removed; callers would use
`emit_record(event_dict, level=level, category=category)` directly.

**Impact**: ~10 lines removed, one conceptual code path eliminated.

---

## Finding 3: Per-emission `_normalize_name` validation in `logging_runtime.py`

`_normalize_name` does `.strip().lower()` and membership-checks against a valid
set on every log emission, even though the level/category strings are always
hardcoded constants like `"debug"` or `"server"`. The guard pattern
`if logger_enabled("debug"): log_event("debug", ...)` calls `_normalize_name`
twice on the same string.

**Simplification**: Remove `_normalize_name` from the hot path entirely (trust
internal callers with constant strings) or validate once at logger configuration
time and use a direct dict lookup at emit time.

**Impact**: ~5 lines removed, eliminates unnecessary per-call overhead.

---

## Finding 4: `listen_addr` dead field in `config.py`

`_normalize_listen_addr` returns a 3-tuple `(value, host, port)`. The raw
`value` string is stored as `Config.listen_addr`, but every consumer uses
`config.listen_host` and `config.listen_port` directly. `listen_addr` is never
referenced outside `config.py`.

**Simplification**: Return only `(host, port)` and remove `listen_addr` from
the Config namedtuple.

**Impact**: ~3 lines removed, one dead namedtuple field eliminated.

---

## Summary

| Area | Nature | Impact |
|------|--------|--------|
| `_collect_kwarg_names` depth tracking | Algorithm more sophisticated than the benefit justifies | ~10 lines, one concept removed |
| Dual `emit`/`emit_record` paths | Two parallel APIs where one suffices | ~10 lines, one code path removed |
| Per-emission `_normalize_name` | Validates constant strings on every call | ~5 lines, removes hot-path overhead |
| `listen_addr` dead field | Computed/stored but never read | ~3 lines, one namedtuple field removed |
