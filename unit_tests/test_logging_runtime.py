from __future__ import absolute_import

import json
import unittest

import dnsdle.logging_runtime as logging_runtime


class _CaptureStream(object):
    def __init__(self):
        self._buffer = ""
        self.lines = []

    def write(self, text):
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self.lines.append(line)

    def flush(self):
        return None


class _BrokenStream(object):
    def write(self, _text):
        raise IOError("write failed")

    def flush(self):
        raise IOError("flush failed")


class LoggingRuntimeTests(unittest.TestCase):
    def test_context_fn_not_evaluated_when_disabled(self):
        capture = _CaptureStream()
        logger = logging_runtime.RuntimeLogger(
            level="info",
            categories=("startup",),
            sample_rate=1.0,
            rate_limit_per_sec=10,
            output="stdout",
            stream=capture,
        )
        calls = {"count": 0}

        def _context_fn():
            calls["count"] += 1
            return {"expensive": True}

        emitted = logger.emit(
            "debug",
            "mapping",
            {
                "phase": "mapping",
                "classification": "diagnostic",
                "reason_code": "disabled_path",
            },
            context_fn=_context_fn,
        )
        logger.close()

        self.assertFalse(emitted)
        self.assertEqual(0, calls["count"])
        self.assertEqual([], capture.lines)

    def test_error_bypasses_category_filter(self):
        capture = _CaptureStream()
        logger = logging_runtime.RuntimeLogger(
            level="info",
            categories=("startup",),
            sample_rate=1.0,
            rate_limit_per_sec=10,
            output="stdout",
            stream=capture,
        )

        emitted = logger.emit_record(
            {
                "classification": "startup_error",
                "phase": "config",
                "reason_code": "invalid_config",
                "message": "bad config",
            }
        )
        logger.close()

        self.assertTrue(emitted)
        self.assertEqual(1, len(capture.lines))
        record = json.loads(capture.lines[0])
        self.assertEqual("ERROR", record["level"])
        self.assertEqual("config", record["category"])
        self.assertEqual("startup_error", record["classification"])

    def test_lifecycle_events_bypass_sampling_and_rate_limit(self):
        capture = _CaptureStream()
        logger = logging_runtime.RuntimeLogger(
            level="info",
            categories=("startup",),
            sample_rate=0.0,
            rate_limit_per_sec=0,
            output="stdout",
            stream=capture,
        )

        logger.emit_record(
            {
                "classification": "server_start",
                "phase": "server",
            }
        )
        logger.emit_record(
            {
                "classification": "shutdown",
                "phase": "server",
                "reason_code": "stop_callback",
            }
        )
        logger.close()

        self.assertEqual(2, len(capture.lines))
        first = json.loads(capture.lines[0])
        second = json.loads(capture.lines[1])
        self.assertEqual("server_start", first["classification"])
        self.assertEqual("shutdown", second["classification"])

    def test_sensitive_fields_are_redacted(self):
        capture = _CaptureStream()
        logger = logging_runtime.RuntimeLogger(
            level="trace",
            categories=("server",),
            sample_rate=1.0,
            rate_limit_per_sec=10,
            output="stdout",
            stream=capture,
        )

        logger.emit(
            "trace",
            "server",
            {
                "phase": "server",
                "classification": "diagnostic",
                "reason_code": "sensitive_check",
                "psk": "secret",
                "payload_bytes": b"abcdef",
                "user_key": "abc",
            },
        )
        logger.close()

        self.assertEqual(1, len(capture.lines))
        record = json.loads(capture.lines[0])
        self.assertEqual("[redacted]", record["psk"])
        self.assertEqual("[redacted]", record["payload_bytes"])
        self.assertEqual("[redacted]", record["user_key"])

    def test_required_event_write_failure_raises(self):
        logger = logging_runtime.RuntimeLogger(
            level="info",
            categories=("startup",),
            sample_rate=1.0,
            rate_limit_per_sec=10,
            output="stdout",
            stream=_BrokenStream(),
        )
        try:
            with self.assertRaises(logging_runtime.RequiredLogEmissionError):
                logger.emit_record(
                    {
                        "classification": "startup_error",
                        "phase": "startup",
                        "reason_code": "required_write_failed",
                        "message": "primary sink unavailable",
                    }
                )
        finally:
            logger.close()

    def test_warn_non_diagnostic_event_bypasses_category_filter(self):
        capture = _CaptureStream()
        logger = logging_runtime.RuntimeLogger(
            level="info",
            categories=("startup",),
            sample_rate=1.0,
            rate_limit_per_sec=10,
            output="stdout",
            stream=capture,
        )
        try:
            miss_emitted = logger.emit_record(
                {
                    "classification": "miss",
                    "phase": "server",
                    "reason_code": "mapping_not_found",
                }
            )
            diagnostic_emitted = logger.emit(
                "info",
                "server",
                {
                    "classification": "diagnostic",
                    "phase": "server",
                    "reason_code": "suppressed_by_category",
                },
            )
        finally:
            logger.close()

        self.assertTrue(miss_emitted)
        self.assertFalse(diagnostic_emitted)
        self.assertEqual(1, len(capture.lines))
        record = json.loads(capture.lines[0])
        self.assertEqual("miss", record["classification"])
        self.assertEqual("WARN", record["level"])
        self.assertEqual("server", record["category"])


if __name__ == "__main__":
    unittest.main()
