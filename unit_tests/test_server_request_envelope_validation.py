from __future__ import absolute_import

import os
import shutil
import struct
import tempfile
import unittest

import dnsdle.dnswire as dnswire
import dnsdle.server as server_module
from dnsdle.cli import parse_cli_args
from dnsdle.config import build_config
from dnsdle.constants import DNS_QCLASS_IN
from dnsdle.constants import DNS_QTYPE_A
from dnsdle.constants import DNS_RCODE_NXDOMAIN
from dnsdle.state import build_runtime_state


def _query_message(labels, flags=0x0100, qdcount=1, ancount=0, nscount=0, arcount=0):
    header = struct.pack("!HHHHHH", 0x1234, flags, qdcount, ancount, nscount, arcount)
    question = dnswire.encode_name(labels) + struct.pack("!HH", DNS_QTYPE_A, DNS_QCLASS_IN)
    return header + question


def _rcode(response_bytes):
    _request_id, flags, _qd, _an, _ns, _ar = struct.unpack("!HHHHHH", response_bytes[:12])
    return flags & 0x000F


class ServerRequestEnvelopeValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dnsdle_env_")
        self.file_path = os.path.join(self.tmpdir, "sample.bin")
        with open(self.file_path, "wb") as handle:
            handle.write(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _runtime_state(self, dns_edns_size):
        config = build_config(
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

    def _assert_envelope_miss(self, runtime_state, message, expected_reason):
        response, record = server_module.handle_request_message(runtime_state, message)
        self.assertEqual(DNS_RCODE_NXDOMAIN, _rcode(response))
        self.assertEqual("miss", record["classification"])
        self.assertEqual(expected_reason, record["reason_code"])

    def test_rejects_qr_set(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("tok01", "tag001", "example", "com")
        message = _query_message(labels, flags=0x8100)
        self._assert_envelope_miss(runtime_state, message, "invalid_query_flags")

    def test_rejects_non_query_opcode(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("tok01", "tag001", "example", "com")
        message = _query_message(labels, flags=0x0900)
        self._assert_envelope_miss(runtime_state, message, "unsupported_opcode")

    def test_rejects_qdcount_not_one(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("tok01", "tag001", "example", "com")
        message = _query_message(labels, qdcount=0)
        self._assert_envelope_miss(runtime_state, message, "invalid_query_section_counts")

    def test_rejects_nonzero_ancount(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("tok01", "tag001", "example", "com")
        message = _query_message(labels, ancount=1)
        self._assert_envelope_miss(runtime_state, message, "invalid_query_section_counts")

    def test_rejects_nonzero_nscount(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("tok01", "tag001", "example", "com")
        message = _query_message(labels, nscount=1)
        self._assert_envelope_miss(runtime_state, message, "invalid_query_section_counts")

    def test_rejects_invalid_arcount_in_classic_mode(self):
        runtime_state = self._runtime_state(dns_edns_size=512)
        labels = ("tok01", "tag001", "example", "com")
        message = _query_message(labels, arcount=1)
        self._assert_envelope_miss(runtime_state, message, "invalid_additional_count")

    def test_rejects_invalid_arcount_in_edns_mode(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("tok01", "tag001", "example", "com")
        message = _query_message(labels, arcount=2)
        self._assert_envelope_miss(runtime_state, message, "invalid_additional_count")

    def test_valid_envelope_reaches_served_path(self):
        runtime_state = self._runtime_state(dns_edns_size=1232)
        labels = ("tok01", "tag001", "example", "com")
        message = _query_message(labels, arcount=1)
        _response, record = server_module.handle_request_message(runtime_state, message)
        self.assertEqual("served", record["classification"])
        self.assertEqual("slice_served", record["reason_code"])


if __name__ == "__main__":
    unittest.main()
