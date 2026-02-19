from __future__ import absolute_import

import base64
import json
import os
import shutil
import socket
import struct
import tempfile
import unittest

import dnsdle.cname_payload as cname_payload
import dnsdle.dnswire as dnswire
import dnsdle.logging_runtime as logging_runtime
import dnsdle.server as server_module
from dnsdle.cli import parse_cli_args
from dnsdle.config import build_config
from dnsdle.constants import DNS_QCLASS_IN
from dnsdle.constants import DNS_QTYPE_A
from dnsdle.constants import DNS_QTYPE_CNAME
from dnsdle.constants import DNS_RCODE_NOERROR
from dnsdle.constants import DNS_RCODE_NXDOMAIN
from dnsdle.constants import DNS_RCODE_SERVFAIL
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN
from dnsdle.state import FrozenDict
from dnsdle.state import StartupError
from dnsdle.state import build_runtime_state


class _FakeSocket(object):
    def __init__(self):
        self.bound = None
        self.timeout = None
        self.closed = False
        self.recv_calls = 0
        self.send_calls = 0

    def bind(self, addr):
        self.bound = addr

    def settimeout(self, value):
        self.timeout = value

    def recvfrom(self, _size):
        self.recv_calls += 1
        raise socket.timeout()

    def sendto(self, _payload, _addr):
        self.send_calls += 1
        return 0

    def close(self):
        self.closed = True


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


def _query_message(labels, qtype=DNS_QTYPE_A, qclass=DNS_QCLASS_IN, qdcount=1):
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, qdcount, 0, 0, 0)
    question = dnswire.encode_name(labels) + struct.pack("!HH", qtype, qclass)
    return header + question


def _header(message):
    return struct.unpack("!HHHHHH", message[:12])


def _decode_cname_record_bytes(message, qname_labels):
    question_len = len(dnswire.encode_name(qname_labels)) + 4
    answer_offset = 12 + question_len
    rdlength = struct.unpack("!H", message[answer_offset + 10 : answer_offset + 12])[0]
    rdata_offset = answer_offset + 12
    _labels, name_end = dnswire._decode_name(message, rdata_offset)
    if name_end != rdata_offset + rdlength:
        raise AssertionError("decoded CNAME RDATA length mismatch")

    payload_text = "".join(_labels[:-3])
    pad_len = (8 - (len(payload_text) % 8)) % 8
    padded = (payload_text + ("=" * pad_len)).upper()
    if not isinstance(padded, bytes):
        padded = padded.encode("ascii")
    return base64.b32decode(padded)


class ServerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dnsdle_server_")
        self.file_path = os.path.join(self.tmpdir, "sample.bin")
        with open(self.file_path, "wb") as handle:
            handle.write(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _config(self, dns_edns_size=1232):
        return build_config(
            parse_cli_args(
                [
                    "--domains",
                    "example.com",
                    "--files",
                    self.file_path,
                    "--psk",
                    "k",
                    "--dns-edns-size",
                    str(dns_edns_size),
                ]
            )
        )

    def _runtime_state(self, dns_edns_size=1232):
        config = self._config(dns_edns_size=dns_edns_size)
        mapped = [
            {
                "file_id": "1" * 16,
                "publish_version": "a" * 64,
                "file_tag": "tag001",
                "plaintext_sha256": "b" * 64,
                "compressed_size": 10,
                "total_slices": 1,
                "slice_token_len": 5,
                "slice_tokens": ("tok01",),
                "slice_bytes_by_index": (b"slice-data",),
                "crypto_profile": "v1",
                "wire_profile": "v1",
            }
        ]
        return build_runtime_state(
            config=config,
            mapped_publish_items=mapped,
            max_ciphertext_slice_bytes=64,
            budget_info={"query_token_len": 1},
        )

    def test_slice_query_returns_noerror_cname(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("tok01", "tag001", "example", "com")

        response, record = server_module.handle_request_message(
            runtime_state,
            _query_message(labels),
        )

        _rid, flags, _qd, ancount, _ns, arcount = _header(response)
        question_len = len(dnswire.encode_name(labels)) + 4
        answer_offset = 12 + question_len
        answer_type = struct.unpack("!H", response[answer_offset + 2 : answer_offset + 4])[0]

        self.assertEqual(DNS_RCODE_NOERROR, flags & 0x000F)
        self.assertEqual(1, ancount)
        self.assertEqual(1, arcount)
        self.assertEqual(DNS_QTYPE_CNAME, answer_type)
        self.assertEqual("served", record["classification"])
        self.assertEqual("slice_served", record["reason_code"])

        record_bytes = _decode_cname_record_bytes(response, labels)
        expected_record = cname_payload.build_slice_record(
            runtime_state.config.psk,
            "1" * 16,
            "a" * 64,
            0,
            1,
            10,
            b"slice-data",
        )
        self.assertEqual(expected_record, record_bytes)
        self.assertNotEqual(b"\x00" * PAYLOAD_MAC_TRUNC_LEN, record_bytes[-PAYLOAD_MAC_TRUNC_LEN:])

    def test_followup_shape_is_classified_before_slice_mapping(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("abc", runtime_state.config.response_label, "example", "com")

        response, record = server_module.handle_request_message(
            runtime_state,
            _query_message(labels),
        )

        _rid, flags, _qd, ancount, _ns, _ar = _header(response)
        question_len = len(dnswire.encode_name(labels)) + 4
        answer_offset = 12 + question_len
        answer_type = struct.unpack("!H", response[answer_offset + 2 : answer_offset + 4])[0]

        self.assertEqual(DNS_RCODE_NOERROR, flags & 0x000F)
        self.assertEqual(1, ancount)
        self.assertEqual(DNS_QTYPE_A, answer_type)
        self.assertEqual("followup", record["classification"])

    def test_unknown_mapping_returns_nxdomain(self):
        runtime_state = self._runtime_state(dns_edns_size=512)
        labels = ("missing", "tag001", "example", "com")

        response, record = server_module.handle_request_message(
            runtime_state,
            _query_message(labels),
        )

        _rid, flags, _qd, ancount, _ns, arcount = _header(response)
        self.assertEqual(DNS_RCODE_NXDOMAIN, flags & 0x000F)
        self.assertEqual(0, ancount)
        self.assertEqual(0, arcount)
        self.assertEqual("miss", record["classification"])
        self.assertEqual("mapping_not_found", record["reason_code"])

    def test_runtime_identity_gap_returns_servfail(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        broken = runtime_state._replace(slice_bytes_by_identity=FrozenDict({}))
        labels = ("tok01", "tag001", "example", "com")

        response, record = server_module.handle_request_message(
            broken,
            _query_message(labels),
        )

        _rid, flags, _qd, ancount, _ns, _ar = _header(response)
        self.assertEqual(DNS_RCODE_SERVFAIL, flags & 0x000F)
        self.assertEqual(0, ancount)
        self.assertEqual("runtime_fault", record["classification"])
        self.assertEqual("identity_missing", record["reason_code"])

    def test_unparseable_datagram_is_dropped(self):
        header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
        malformed = header + b"\xc0\x0c" + struct.pack("!HH", DNS_QTYPE_A, DNS_QCLASS_IN)

        response, record = server_module.handle_request_message(
            self._runtime_state(dns_edns_size=1232),
            malformed,
        )

        self.assertIsNone(response)
        self.assertIsNone(record)

    def test_serve_runtime_emits_shutdown_and_closes_socket(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        fake_socket = _FakeSocket()
        records = []
        original_socket_factory = server_module.socket.socket

        def _stop_immediately():
            return True

        try:
            server_module.socket.socket = lambda *_args, **_kwargs: fake_socket
            exit_code = server_module.serve_runtime(
                runtime_state,
                records.append,
                stop_requested=_stop_immediately,
            )
        finally:
            server_module.socket.socket = original_socket_factory

        shutdown_records = [record for record in records if record["classification"] == "shutdown"]
        self.assertEqual(0, exit_code)
        self.assertTrue(fake_socket.closed)
        self.assertEqual(1, len(shutdown_records))

    def test_serve_runtime_fails_pre_bind_when_runtime_invariant_check_fails(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        records = []
        created = {"count": 0}

        original_payload_labels = server_module.cname_payload.payload_labels_for_slice
        original_socket_factory = server_module.socket.socket

        def _raise_payload_error(*_args, **_kwargs):
            raise ValueError("forced payload encode failure")

        def _counting_socket_factory(*_args, **_kwargs):
            created["count"] += 1
            return _FakeSocket()

        try:
            server_module.cname_payload.payload_labels_for_slice = _raise_payload_error
            server_module.socket.socket = _counting_socket_factory
            with self.assertRaises(StartupError) as ctx:
                server_module.serve_runtime(
                    runtime_state,
                    records.append,
                    stop_requested=lambda: True,
                )
        finally:
            server_module.cname_payload.payload_labels_for_slice = original_payload_labels
            server_module.socket.socket = original_socket_factory

        self.assertEqual("startup", ctx.exception.phase)
        self.assertEqual("server_runtime_invalid", ctx.exception.reason_code)
        self.assertIn("invariant check failed", ctx.exception.message)
        self.assertEqual(0, created["count"])
        self.assertEqual([], records)

    def test_serve_runtime_timeout_wake_loop_stops_deterministically(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        fake_socket = _FakeSocket()
        records = []
        original_socket_factory = server_module.socket.socket

        def _stop_after_three_timeouts():
            return fake_socket.recv_calls >= 3

        try:
            server_module.socket.socket = lambda *_args, **_kwargs: fake_socket
            exit_code = server_module.serve_runtime(
                runtime_state,
                records.append,
                stop_requested=_stop_after_three_timeouts,
            )
        finally:
            server_module.socket.socket = original_socket_factory

        shutdown_records = [record for record in records if record["classification"] == "shutdown"]
        self.assertEqual(0, exit_code)
        self.assertEqual(0.5, fake_socket.timeout)
        self.assertGreaterEqual(fake_socket.recv_calls, 3)
        self.assertEqual(0, fake_socket.send_calls)
        self.assertTrue(fake_socket.closed)
        self.assertEqual(1, len(shutdown_records))
        self.assertEqual("stop_callback", shutdown_records[0]["reason_code"])

    def test_lifecycle_logs_are_not_suppressed_by_logger_controls(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        fake_socket = _FakeSocket()
        capture = _CaptureStream()
        logger = logging_runtime.RuntimeLogger(
            level="info",
            categories=("startup",),
            sample_rate=0.0,
            rate_limit_per_sec=0,
            output="stdout",
            stream=capture,
        )
        original_socket_factory = server_module.socket.socket

        try:
            server_module.socket.socket = lambda *_args, **_kwargs: fake_socket
            exit_code = server_module.serve_runtime(
                runtime_state,
                logger.emit_record,
                stop_requested=lambda: True,
            )
        finally:
            server_module.socket.socket = original_socket_factory
            logger.close()

        records = [json.loads(line) for line in capture.lines]
        classifications = [record.get("classification") for record in records]

        self.assertEqual(0, exit_code)
        self.assertIn("server_start", classifications)
        self.assertIn("shutdown", classifications)


if __name__ == "__main__":
    unittest.main()
