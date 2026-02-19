from __future__ import absolute_import

import json
import random
import sys
import time

from dnsdle.compat import is_binary
from dnsdle.compat import key_text
from dnsdle.constants import DEFAULT_LOG_CATEGORIES_CSV
from dnsdle.constants import DEFAULT_LOG_FILE
from dnsdle.constants import DEFAULT_LOG_FOCUS
from dnsdle.constants import DEFAULT_LOG_LEVEL
from dnsdle.constants import DEFAULT_LOG_OUTPUT
from dnsdle.constants import DEFAULT_LOG_RATE_LIMIT_PER_SEC
from dnsdle.constants import DEFAULT_LOG_SAMPLE_RATE
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
_LEVEL_FROM_CLASSIFICATION = {
    "startup_error": "error",
    "runtime_fault": "error",
    "server_start": "info",
    "shutdown": "info",
    "startup_ok": "info",
    "generation_start": "info",
    "generation_ok": "info",
    "generation_summary": "info",
    "generation_error": "error",
    "publish": "info",
    "served": "info",
    "followup": "info",
    "miss": "warn",
}
_CATEGORY_FROM_PHASE = {
    "startup": "startup",
    "config": "config",
    "budget": "budget",
    "publish": "publish",
    "mapping": "mapping",
    "dnswire": "dnswire",
    "server": "server",
}
_SENSITIVE_EXACT_KEYS = frozenset(
    (
        "psk",
        "key",
        "derived_key",
        "payload",
        "payload_bytes",
        "slice_bytes",
        "plaintext_bytes",
    )
)
_SENSITIVE_KEY_PARTS = ("psk", "key", "payload")


def _now_unix_ms():
    return int(time.time() * 1000)


def _safe_json_value(value):
    if is_binary(value):
        return "<bytes:%d>" % len(value)
    if isinstance(value, (tuple, list)):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, dict):
        return _redact_map(value)
    return value


def _is_sensitive_key(key):
    key_lower = key_text(key).lower()
    if key_lower in _SENSITIVE_EXACT_KEYS:
        return True
    for part in _SENSITIVE_KEY_PARTS:
        if part in key_lower:
            return True
    return False


def _redact_map(record):
    output = {}
    for key, value in record.items():
        text_key = key_text(key)
        if _is_sensitive_key(text_key):
            output[text_key] = "[redacted]"
        else:
            output[text_key] = _safe_json_value(value)
    return output


def _normalize_level_name(level):
    level_name = (level or "").strip().lower()
    if level_name not in LOG_LEVELS:
        raise ValueError("unsupported log level: %s" % level)
    return level_name


def _normalize_category_name(category):
    category_name = (category or "").strip().lower()
    if category_name not in LOG_CATEGORIES:
        raise ValueError("unsupported log category: %s" % category)
    return category_name


def _record_category(record):
    phase = str(record.get("phase", "")).lower()
    return _CATEGORY_FROM_PHASE.get(phase, "startup")


def _record_level(record):
    classification = str(record.get("classification", "")).lower()
    return _LEVEL_FROM_CLASSIFICATION.get(classification, "info")


def _record_is_required(record, level_name):
    classification = str(record.get("classification", "")).lower()
    if level_name == "error":
        return True
    return classification in REQUIRED_LIFECYCLE_CLASSIFICATIONS


def _apply_category_filter(level_name, record):
    if level_name in ("debug", "trace"):
        return True
    if level_name != "info":
        return False
    if record is None:
        return True
    return str(record.get("classification", "")).lower() == "diagnostic"


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
    def __init__(
        self,
        level=DEFAULT_LOG_LEVEL,
        categories=None,
        sample_rate=1.0,
        rate_limit_per_sec=200,
        output=DEFAULT_LOG_OUTPUT,
        log_file=DEFAULT_LOG_FILE,
        focus=DEFAULT_LOG_FOCUS,
        stream=None,
    ):
        self.level = _normalize_level_name(level)
        category_values = (
            categories
            if categories is not None
            else tuple(DEFAULT_LOG_CATEGORIES_CSV.split(","))
        )
        self.category_set = frozenset(category_values)
        self.sample_rate = float(sample_rate)
        self.rate_limit_per_sec = int(rate_limit_per_sec)
        self.output = output
        self.log_file = log_file
        self.focus = (focus or "").strip()
        self._owns_stream = False
        self._stream = stream
        if self._stream is None:
            if output == "file":
                self._stream = open(log_file, "a")
                self._owns_stream = True
            else:
                self._stream = sys.stdout
        self._window_second = int(time.time())
        self._window_count = 0

    def close(self):
        if self._owns_stream and self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._owns_stream = False

    def _enabled_normalized(self, level_name, category_name, required=False, event=None):
        if required:
            return True
        if _LEVEL_RANK[level_name] < _LEVEL_RANK[self.level]:
            return False
        if _apply_category_filter(level_name, event) and category_name not in self.category_set:
            return False
        return True

    def enabled(self, level, category, required=False, event=None):
        return self._enabled_normalized(
            _normalize_level_name(level),
            _normalize_category_name(category),
            required=required,
            event=event,
        )

    def _passes_focus(self, level_name, event):
        if not self.focus:
            return True
        if level_name not in ("debug", "trace"):
            return True
        for key in ("file_tag", "slice_token", "selected_base_domain"):
            value = event.get(key)
            if value is not None and self.focus == str(value):
                return True
        return False

    def _passes_sampling(self, level_name):
        if level_name not in ("debug", "trace"):
            return True
        if self.sample_rate <= 0.0:
            return False
        if self.sample_rate >= 1.0:
            return True
        return random.random() < self.sample_rate

    def _passes_rate_limit(self, level_name):
        if level_name not in ("debug", "trace"):
            return True
        if self.rate_limit_per_sec <= 0:
            return False
        second = int(time.time())
        if second != self._window_second:
            self._window_second = second
            self._window_count = 0
        if self._window_count >= self.rate_limit_per_sec:
            return False
        self._window_count += 1
        return True

    def _write_record(self, record):
        line = json.dumps(record, sort_keys=True)
        return _write_line(self._stream, line)

    def emit(self, level, category, event, context_fn=None, required=False):
        level_name = _normalize_level_name(level)
        category_name = _normalize_category_name(category)
        base_event = dict(event or {})
        event_required = required or _record_is_required(base_event, level_name)
        if not self._enabled_normalized(
            level_name,
            category_name,
            required=event_required,
            event=base_event,
        ):
            return False
        if not event_required:
            if not self._passes_focus(level_name, base_event):
                return False
            if not self._passes_sampling(level_name):
                return False
            if not self._passes_rate_limit(level_name):
                return False

        if context_fn is not None:
            context = context_fn()
            if context:
                for key, value in context.items():
                    if key not in base_event:
                        base_event[key] = value

        output = {
            "ts_unix_ms": _now_unix_ms(),
            "level": level_name.upper(),
            "category": category_name,
        }
        for key, value in _redact_map(base_event).items():
            if key not in output:
                output[key] = value
        emitted = self._write_record(output)
        if event_required and not emitted:
            raise RequiredLogEmissionError("required log emission failed")
        return emitted

    def emit_record(self, record, level=None, category=None, required=False):
        base = dict(record or {})
        level_name = _record_level(base) if level is None else level
        category_name = _record_category(base) if category is None else category
        return self.emit(
            level_name,
            category_name,
            base,
            context_fn=None,
            required=required,
        )


def _create_logger(
    level,
    categories,
    sample_rate,
    rate_limit_per_sec,
    output,
    log_file,
    focus,
    stream,
):
    try:
        return RuntimeLogger(
            level=level,
            categories=categories,
            sample_rate=sample_rate,
            rate_limit_per_sec=rate_limit_per_sec,
            output=output,
            log_file=log_file,
            focus=focus,
            stream=stream,
        )
    except IOError as exc:
        raise StartupError(
            "startup",
            "log_output_unusable",
            "failed to open log output file: %s" % exc,
            {"log_file": log_file},
        )


def build_logger_from_config(config):
    return _create_logger(
        level=config.log_level,
        categories=config.log_categories,
        sample_rate=config.log_sample_rate,
        rate_limit_per_sec=config.log_rate_limit_per_sec,
        output=config.log_output,
        log_file=config.log_file,
        focus=config.log_focus,
        stream=None,
    )


def _bootstrap_logger():
    return _create_logger(
        level=DEFAULT_LOG_LEVEL,
        categories=tuple(DEFAULT_LOG_CATEGORIES_CSV.split(",")),
        sample_rate=DEFAULT_LOG_SAMPLE_RATE,
        rate_limit_per_sec=DEFAULT_LOG_RATE_LIMIT_PER_SEC,
        output=DEFAULT_LOG_OUTPUT,
        log_file=DEFAULT_LOG_FILE,
        focus=DEFAULT_LOG_FOCUS,
        stream=sys.stdout,
    )


_ACTIVE_LOGGER = _bootstrap_logger()


def reset_active_logger():
    global _ACTIVE_LOGGER
    if _ACTIVE_LOGGER is not None:
        _ACTIVE_LOGGER.close()
    _ACTIVE_LOGGER = _bootstrap_logger()
    return _ACTIVE_LOGGER


def configure_active_logger(config):
    global _ACTIVE_LOGGER
    logger = build_logger_from_config(config)
    if _ACTIVE_LOGGER is not None:
        _ACTIVE_LOGGER.close()
    _ACTIVE_LOGGER = logger
    return _ACTIVE_LOGGER


def get_active_logger():
    return _ACTIVE_LOGGER


def logger_enabled(level, category, required=False):
    return _ACTIVE_LOGGER.enabled(level, category, required=required)


def log_event(level, category, event, context_fn=None, required=False):
    return _ACTIVE_LOGGER.emit(
        level,
        category,
        event,
        context_fn=context_fn,
        required=required,
    )


def emit_structured_record(record, level=None, category=None, required=False):
    return _ACTIVE_LOGGER.emit_record(
        record,
        level=level,
        category=category,
        required=required,
    )
