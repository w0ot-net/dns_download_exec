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
from dnsdle.constants import DNS_RCODE_NXDOMAIN
from dnsdle.state import build_runtime_state


def _query_message(labels, flags=0x0100, qdcount=1, ancount=0, nscount=0, arcount=0):
    header = struct.pack("!HHHHHH", 0x1234, flags, qdcount, ancount, nscount, arcount)
    question = dnswire.encode_name(labels) + struct.pack("!HH", 1, 1)
    return header + question


def _rcode(response_bytes):
    _request_id, flags, _qd, _an, _ns, _ar = struct.unpack("!HHHHHH", response_bytes[:12])
    return flags & 0x000F


class ServerRequestEnvelopeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dnsdle_env_integ_")
        self.file_path = os.path.join(self.tmpdir, "sample.bin")
        with open(self.file_path, "wb") as handle:
            handle.write(b"x")

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
                    "1232",
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
                "source_filename": "test.bin",
            }
        ]
        self.runtime_state = build_runtime_state(
            config=config,
            mapped_publish_items=mapped,
            max_ciphertext_slice_bytes=64,
            budget_info={"query_token_len": 1},
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_envelope_rejection_happens_before_mapping(self):
        labels = ("missing", "tag001", "example", "com")
        message = _query_message(labels, flags=0x8100)
        response, record = server_module.handle_request_message(self.runtime_state, message)
        self.assertEqual(DNS_RCODE_NXDOMAIN, _rcode(response))
        self.assertEqual("invalid_query_flags", record["reason_code"])

    def test_invalid_additional_count_precedes_mapping(self):
        labels = ("missing", "tag001", "example", "com")
        message = _query_message(labels, arcount=2)
        response, record = server_module.handle_request_message(self.runtime_state, message)
        self.assertEqual(DNS_RCODE_NXDOMAIN, _rcode(response))
        self.assertEqual("invalid_additional_count", record["reason_code"])

    def test_valid_envelope_with_unknown_mapping_keeps_mapping_reason(self):
        labels = ("missing", "tag001", "example", "com")
        message = _query_message(labels, arcount=1)
        response, record = server_module.handle_request_message(self.runtime_state, message)
        self.assertEqual(DNS_RCODE_NXDOMAIN, _rcode(response))
        self.assertEqual("mapping_not_found", record["reason_code"])


if __name__ == "__main__":
    unittest.main()
