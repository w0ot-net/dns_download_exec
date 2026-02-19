# Plan: Simplify Logging System

## Summary

Strip the logging system down to two CLI arguments (`--log-level` and
`--log-file`) by removing category filtering, sampling, rate limiting, and
focus filtering.  The two-arg `--log-output`/`--log-file` pair collapses to
just `--log-file` (if provided, logs go to the file; if omitted, stdout).
Categories remain as labels in JSON output but are no longer filterable.  The
result is ~140 fewer lines of runtime code, 5 fewer CLI arguments, and a far
simpler mental model.

## Problem

The current logging system has 7 CLI arguments and 5 independent filtering
layers (level, category, sampling, rate limiting, focus).  This is
disproportionate to the operational needs of a single-purpose DNS server:

- `--log-categories`: selectable subset of 7 categories, with a separate
  "diagnostic vs non-diagnostic" distinction controlling which INFO events
  are subject to category filtering.
- `--log-sample-rate`: probabilistic sampling for debug/trace only.
- `--log-rate-limit-per-sec`: per-second windowed rate limiter for
  debug/trace only.
- `--log-focus`: deterministic request-key filter for debug/trace only.
- `--log-output` / `--log-file`: two-argument file output with
  cross-validation (one is redundant).

The sampling, rate limiting, and focus features only apply to debug/trace
events, which are already disabled by default.  If someone enables debug or
trace output, they want to see all of it -- not a sampled/throttled subset.
The `--log-output` flag is redundant: the presence or absence of `--log-file`
already determines the destination.

## Goal

After implementation:

1. Two logging CLI arguments exist: `--log-level` and `--log-file`.
2. `RuntimeLogger` has three constructor parameters: `level`, `log_file`,
   and `stream`.
3. Filtering is level-threshold only (plus the existing required-event
   invariant for errors and lifecycle events).
4. Categories remain as labels in emitted JSON -- they are not filterable.
5. Redaction, required-event invariants, lazy `context_fn`, and the
   classification-to-level mapping are all preserved unchanged.
6. All architecture docs are updated.

## Design

### What stays

- Five-tier level hierarchy: error > warn > info > debug > trace.
- `--log-level` CLI argument (default `info`).
- `--log-file` CLI argument (optional; if omitted, logs go to stdout).
- File output with `_owns_stream` and `close()` semantics.
- Required-event invariant: errors and lifecycle events always emit.
- Redaction of sensitive keys.
- Lazy `context_fn` pattern for zero-cost disabled paths.
- `_LEVEL_FROM_CLASSIFICATION` mapping.
- `category` field in JSON output (derived from record `phase`).
- `_normalize_category_name` validation (categories are still passed to
  `log_event` / `emit` for the output label).
- `LOG_CATEGORIES` and `LOG_CATEGORY_*` constants (used by category
  validation).

### What is removed

| Removed feature | CLI arg | RuntimeLogger fields | Config fields |
|---|---|---|---|
| Category filtering | `--log-categories` | `category_set` | `log_categories` |
| Sampling | `--log-sample-rate` | `sample_rate` | `log_sample_rate` |
| Rate limiting | `--log-rate-limit-per-sec` | `rate_limit_per_sec`, `_window_second`, `_window_count` | `log_rate_limit_per_sec` |
| Focus filtering | `--log-focus` | `focus` | `log_focus` |
| Output mode toggle | `--log-output` | `output` | `log_output` |

### Removed internal functions/methods

- `RuntimeLogger._passes_focus()`
- `RuntimeLogger._passes_sampling()`
- `RuntimeLogger._passes_rate_limit()`
- `_subject_to_category_filter()`
- `_normalize_log_categories()` in config.py
- `_normalize_log_output()` in config.py
- `_normalize_log_focus()` in config.py
- `_parse_float_in_range()` in config.py (only used by sample_rate;
  check no other callers)

### Simplified `logger_enabled` signature

The `category` parameter is removed from `logger_enabled()` since it is no
longer used for filtering.  All call sites are updated:

```python
# Before
if logger_enabled("debug", "startup"):
# After
if logger_enabled("debug"):
```

The `enabled()` method on `RuntimeLogger` keeps `category` as an ignored
parameter for internal use by `emit()`, or is simplified similarly.

### Simplified `RuntimeLogger.__init__`

```python
def __init__(self, level=DEFAULT_LOG_LEVEL, log_file="", stream=None):
    self.level = _normalize_level_name(level)
    self.log_file = log_file
    self._owns_stream = False
    self._stream = stream
    if self._stream is None:
        if log_file:
            self._stream = open(log_file, "a")
            self._owns_stream = True
        else:
            self._stream = sys.stdout
```

`close()` stays as-is for file stream cleanup.

### Simplified filtering in `emit()`

Only level threshold + required-event bypass:

```python
if not event_required and _LEVEL_RANK[level_name] < _LEVEL_RANK[self.level]:
    return False
```

No focus, sampling, rate limit, or category checks.

### Removed imports in `logging_runtime.py`

- `random` (was only used for sampling)
- All removed `DEFAULT_*` / `LOG_CATEGORIES` imports that are no longer needed

## Affected Components

- `dnsdle/constants.py`: Remove 6 `DEFAULT_LOG_*` constants (`DEFAULT_LOG_CATEGORIES`, `DEFAULT_LOG_CATEGORIES_CSV`, `DEFAULT_LOG_SAMPLE_RATE`, `DEFAULT_LOG_RATE_LIMIT_PER_SEC`, `DEFAULT_LOG_OUTPUT`, `DEFAULT_LOG_FOCUS`). Keep `DEFAULT_LOG_FILE`.
- `dnsdle/cli.py`: Remove 5 CLI args (`--log-categories`, `--log-sample-rate`, `--log-rate-limit-per-sec`, `--log-output`, `--log-focus`), remove their `DEFAULT_*` imports, remove from `_LONG_OPTIONS`. Keep `--log-file`.
- `dnsdle/config.py`: Remove 5 `Config` namedtuple fields (`log_categories`, `log_sample_rate`, `log_rate_limit_per_sec`, `log_output`, `log_focus`), remove `_normalize_log_categories`, `_normalize_log_output`, `_normalize_log_focus`, remove `_parse_float_in_range` if no other callers, remove cross-validation block for log_output/log_file, remove unused imports. Keep `log_file` field and `_normalize_log_file`.
- `dnsdle/logging_runtime.py`: Simplify `RuntimeLogger` to level+log_file+stream; remove `_subject_to_category_filter`, `_passes_focus`, `_passes_sampling`, `_passes_rate_limit`; simplify `_create_logger`, `build_logger_from_config`, `_bootstrap_logger`, `configure_active_logger`, `reset_active_logger`; remove `import random`; remove unused constant imports. Keep `close()` for file stream cleanup.
- `dnsdle/__init__.py`: Update `logger_enabled("debug", "startup")` call to `logger_enabled("debug")`.
- `dnsdle/budget.py`: Update `logger_enabled("debug", "budget")` to `logger_enabled("debug")`.
- `dnsdle/publish.py`: Update `logger_enabled("debug", "publish")` to `logger_enabled("debug")`.
- `dnsdle/mapping.py`: Update `logger_enabled("debug", "mapping")` to `logger_enabled("debug")`.
- `dnsdle/dnswire.py`: Update two `logger_enabled("trace", "dnswire")` calls to `logger_enabled("trace")`.
- `dnsdle/server.py`: Update three `logger_enabled` calls to remove category argument.
- `doc/architecture/LOGGING.md`: Rewrite Configuration and Suppression Rules sections.
- `doc/architecture/CONFIG.md`: Remove 5 log config field descriptions and simplify logging validation rules.
