from __future__ import absolute_import

import struct
import unittest

import dnsdle.dnswire as dnswire
from dnsdle.constants import DNS_FLAG_QR
from dnsdle.constants import DNS_QCLASS_IN
from dnsdle.constants import DNS_QTYPE_A
from dnsdle.constants import DNS_RCODE_NXDOMAIN


def _query_message(labels, qtype=DNS_QTYPE_A, qclass=DNS_QCLASS_IN, qdcount=1):
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, qdcount, 0, 0, 0)
    question = dnswire.encode_name(labels) + struct.pack("!HH", qtype, qclass)
    return header + question


class DnsWireTests(unittest.TestCase):
    def test_parse_request_reads_single_question(self):
        message = _query_message(("Tok", "Tag", "Example", "COM"))
        parsed = dnswire.parse_request(message)

        self.assertEqual(0x1234, parsed["id"])
        self.assertEqual(1, parsed["qdcount"])
        self.assertEqual(("tok", "tag", "example", "com"), parsed["question"]["qname_labels"])
        self.assertEqual(DNS_QTYPE_A, parsed["question"]["qtype"])
        self.assertEqual(DNS_QCLASS_IN, parsed["question"]["qclass"])

    def test_parse_request_rejects_pointer_loop(self):
        header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
        # QNAME pointer that references itself at offset 12.
        question = b"\xc0\x0c" + struct.pack("!HH", DNS_QTYPE_A, DNS_QCLASS_IN)

        with self.assertRaises(dnswire.DnsParseError):
            dnswire.parse_request(header + question)

    def test_build_response_sets_expected_header_counts_and_opt(self):
        request = dnswire.parse_request(_query_message(("a", "b", "example", "com")))
        response = dnswire.build_response(
            request,
            DNS_RCODE_NXDOMAIN,
            answer_bytes=None,
            include_opt=True,
            edns_size=1232,
        )

        response_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            "!HHHHHH", response[:12]
        )
        self.assertEqual(0x1234, response_id)
        self.assertTrue(flags & DNS_FLAG_QR)
        self.assertEqual(DNS_RCODE_NXDOMAIN, flags & 0x000F)
        self.assertEqual(1, qdcount)
        self.assertEqual(0, ancount)
        self.assertEqual(0, nscount)
        self.assertEqual(1, arcount)

    def test_build_response_omits_opt_in_classic_mode(self):
        request = dnswire.parse_request(_query_message(("a", "b", "example", "com")))
        response = dnswire.build_response(
            request,
            DNS_RCODE_NXDOMAIN,
            answer_bytes=None,
            include_opt=False,
            edns_size=512,
        )

        _rid, _flags, _qdcount, _ancount, _nscount, arcount = struct.unpack(
            "!HHHHHH", response[:12]
        )
        self.assertEqual(0, arcount)

    def test_build_cname_answer_points_suffix_at_question_domain(self):
        question_labels = ("token", "tag001", "example", "com")
        payload_labels = ("abc", "def")

        answer = dnswire.build_cname_answer(
            question_labels,
            2,
            payload_labels,
            "r-x",
            ttl=30,
        )

        # Answer NAME ptr (2) + TYPE/CLASS/TTL/RDLEN (10) -> RDATA starts at 12.
        pointer = struct.unpack("!H", answer[-2:])[0]
        expected_domain_offset = 12 + (1 + len("token")) + (1 + len("tag001"))
        self.assertEqual(0xC000 | expected_domain_offset, pointer)


if __name__ == "__main__":
    unittest.main()
