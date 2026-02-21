from __future__ import absolute_import, unicode_literals

import json
import sys
import time

from dnsdle.constants import DEFAULT_LOG_LEVEL
from dnsdle.constants import LOG_CATEGORIES
from dnsdle.constants import LOG_LEVELS
from dnsdle.constants import REQUIRED_LIFECYCLE_CLASSIFICATIONS
from dnsdle.state import StartupError


_LEVEL_RANK = {
    "trace": 10,
    "debug": 20,
    "info": 30,
    "warn": 40,
    "error": 50,
}
_ERROR_CLASSIFICATIONS = frozenset(("startup_error", "runtime_fault"))
_WARN_CLASSIFICATIONS  = frozenset(("miss",))
_VALID_PHASE_CATEGORIES = frozenset(
    ("startup", "config", "budget", "publish", "mapping", "dnswire", "server")
)
_SENSITIVE_KEY_PARTS = ("psk", "key", "payload")


def _now_unix_ms():
    return int(time.time() * 1000)


def _is_sensitive_key(key):
    lower = key.lower()
    for part in _SENSITIVE_KEY_PARTS:
        if part in lower:
            return True
    return False


def _redact_map(record):
    output = {}
    for key, value in record.items():
        k = key if isinstance(key, str) else str(key)
        if _is_sensitive_key(k):
            output[k] = "[redacted]"
        elif isinstance(value, (tuple, list)):
            output[k] = list(value)
        else:
            output[k] = value
    return output


def _normalize_name(value, valid_set, label):
    name = (value or "").strip().lower()
    if name not in valid_set:
        raise ValueError("unsupported log %s: %s" % (label, value))
    return name


def _record_category(record):
    phase = str(record.get("phase", "")).lower()
    return phase if phase in _VALID_PHASE_CATEGORIES else "startup"


def _record_level(record):
    c = str(record.get("classification", "")).lower()
    if c in _ERROR_CLASSIFICATIONS:
        return "error"
    if c in _WARN_CLASSIFICATIONS:
        return "warn"
    return "info"


def _record_is_required(record):
    classification = str(record.get("classification", "")).lower()
    return classification in REQUIRED_LIFECYCLE_CLASSIFICATIONS


def _write_line(stream, line):
    try:
        stream.write(line)
        stream.write("\n")
        stream.flush()
    except Exception:
        return False
    return True


class RequiredLogEmissionError(Exception):
    pass


class RuntimeLogger(object):
    def __init__(self, level=DEFAULT_LOG_LEVEL, log_file="", stream=None):
        self.level = _normalize_name(level, LOG_LEVELS, "level")
        self.log_file = log_file
        self._owns_stream = False
        self._stream = stream
        if self._stream is None:
            if log_file:
                self._stream = open(log_file, "a")
                self._owns_stream = True
            else:
                self._stream = sys.stdout

    def close(self):
        if self._owns_stream and self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._owns_stream = False

    def enabled(self, level, required=False):
        if required:
            return True
        return _LEVEL_RANK[_normalize_name(level, LOG_LEVELS, "level")] >= _LEVEL_RANK[self.level]

    def _write_record(self, record):
        line = json.dumps(record, sort_keys=True)
        return _write_line(self._stream, line)

    def _do_emit(self, level_name, category_name, base_event, required):
        event_required = required or _record_is_required(base_event)
        if not event_required and _LEVEL_RANK[level_name] < _LEVEL_RANK[self.level]:
            return False
        output = _redact_map(base_event)
        output["ts_unix_ms"] = _now_unix_ms()
        output["level"] = level_name.upper()
        output["category"] = category_name
        emitted = self._write_record(output)
        if event_required and not emitted:
            raise RequiredLogEmissionError("required log emission failed")
        return emitted

    def emit(self, level, category, event, required=False):
        level_name = _normalize_name(level, LOG_LEVELS, "level")
        category_name = _normalize_name(category, LOG_CATEGORIES, "category")
        return self._do_emit(level_name, category_name, dict(event or {}), required)

    def emit_record(self, record, level=None, category=None, required=False):
        base = dict(record or {})
        level_name = _record_level(base) if level is None else level
        category_name = _record_category(base) if category is None else category
        return self._do_emit(level_name, category_name, base, required)


class _NullStream(object):
    def write(self, data):
        pass

    def flush(self):
        pass


def _create_logger(level, log_file, stream):
    try:
        return RuntimeLogger(level=level, log_file=log_file, stream=stream)
    except IOError as exc:
        raise StartupError(
            "startup",
            "log_output_unusable",
            "failed to open log output file: %s" % exc,
            {"log_file": log_file},
        )


def build_logger_from_config(config):
    if not config.verbose and not config.log_file:
        stream = _NullStream()
    else:
        stream = None
    return _create_logger(
        level=config.log_level,
        log_file=config.log_file,
        stream=stream,
    )


def _bootstrap_logger():
    return _create_logger(
        level=DEFAULT_LOG_LEVEL,
        log_file="",
        stream=_NullStream(),
    )


_ACTIVE_LOGGER = _bootstrap_logger()


def _swap_active_logger(new_logger):
    global _ACTIVE_LOGGER
    if _ACTIVE_LOGGER is not None:
        _ACTIVE_LOGGER.close()
    _ACTIVE_LOGGER = new_logger
    return _ACTIVE_LOGGER


def reset_active_logger():
    return _swap_active_logger(_bootstrap_logger())


def configure_active_logger(config):
    return _swap_active_logger(build_logger_from_config(config))


def logger_enabled(level, required=False):
    return _ACTIVE_LOGGER.enabled(level, required=required)


def log_event(level, category, event, required=False):
    return _ACTIVE_LOGGER.emit(level, category, event, required=required)


def emit_structured_record(record, level=None, category=None, required=False):
    return _ACTIVE_LOGGER.emit_record(
        record,
        level=level,
        category=category,
        required=required,
    )
