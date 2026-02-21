# Plan: Simplify logging_runtime.py Internals

## Summary

Three unnecessary-complexity issues exist inside `logging_runtime.py`: a loop
in `emit` that builds the output dict in a roundabout way, two structurally
identical normalisation functions, and a duplicated close-and-replace pattern
across `reset_active_logger` and `configure_active_logger`.  All changes are
internal to the module with no impact on external callers.

## Problem

1. **`emit` builds `output` with an explicit loop (lines 175–182).**  The
   method first creates a dict with three fixed keys, then iterates over the
   redacted event to add any key not already in `output`.  The intent — fixed
   keys always win over event fields — is more directly expressed by building
   from the redacted event first and then overwriting the fixed keys, with no
   loop required.

2. **`_normalize_level_name` and `_normalize_category_name` are structurally
   identical (lines 84–95).**  Both strip-and-lowercase their input, validate
   it against a constant set, and raise `ValueError` on failure.  Only the
   valid-values set and the error label differ.  The duplication means any
   future change to normalisation logic (e.g. a better error message format)
   must be applied twice.

3. **`reset_active_logger` and `configure_active_logger` duplicate the
   close-and-replace pattern (lines 232–246).**  Both functions repeat:
   ```python
   if _ACTIVE_LOGGER is not None:
       _ACTIVE_LOGGER.close()
   _ACTIVE_LOGGER = <new logger>
   return _ACTIVE_LOGGER
   ```
   The only difference is the source of the new logger.

## Goal

- `emit` builds its output dict without an explicit loop; the three fixed keys
  are assigned directly after `_redact_map`.
- `_normalize_level_name` and `_normalize_category_name` are replaced by a
  single `_normalize_name(value, valid_set, label)` helper; all internal call
  sites updated.
- A private `_swap_active_logger(new_logger)` helper owns the close-and-replace
  pattern; `reset_active_logger` and `configure_active_logger` delegate to it.

## Design

### 1. Simplify `emit` output-dict construction

Replace lines 175–182:
```python
output = {
    "ts_unix_ms": _now_unix_ms(),
    "level": level_name.upper(),
    "category": category_name,
}
for key, value in _redact_map(base_event).items():
    if key not in output:
        output[key] = value
```
With:
```python
output = _redact_map(base_event)
output["ts_unix_ms"] = _now_unix_ms()
output["level"] = level_name.upper()
output["category"] = category_name
```
Semantics are identical: the fixed keys always take precedence over any
same-named event field.

### 2. Merge normalisation functions into `_normalize_name`

Replace `_normalize_level_name` and `_normalize_category_name` with:
```python
def _normalize_name(value, valid_set, label):
    name = (value or "").strip().lower()
    if name not in valid_set:
        raise ValueError("unsupported log %s: %s" % (label, value))
    return name
```

Update the four internal call sites:
- `RuntimeLogger.__init__`: `_normalize_level_name(level)` →
  `_normalize_name(level, LOG_LEVELS, "level")`
- `RuntimeLogger.enabled`: same substitution
- `RuntimeLogger.emit` (level): same substitution
- `RuntimeLogger.emit` (category): `_normalize_category_name(category)` →
  `_normalize_name(category, LOG_CATEGORIES, "category")`

### 3. Extract `_swap_active_logger`

Add:
```python
def _swap_active_logger(new_logger):
    global _ACTIVE_LOGGER
    if _ACTIVE_LOGGER is not None:
        _ACTIVE_LOGGER.close()
    _ACTIVE_LOGGER = new_logger
    return _ACTIVE_LOGGER
```

Simplify the two public functions to:
```python
def reset_active_logger():
    return _swap_active_logger(_bootstrap_logger())

def configure_active_logger(config):
    return _swap_active_logger(build_logger_from_config(config))
```

## Affected Components

- `dnsdle/logging_runtime.py`: simplify `emit` output-dict construction;
  replace `_normalize_level_name` / `_normalize_category_name` with
  `_normalize_name`; extract `_swap_active_logger` and update
  `reset_active_logger` / `configure_active_logger`
