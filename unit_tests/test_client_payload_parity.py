from __future__ import absolute_import

import struct
import unittest

import dnsdle.client_payload as client_payload
import dnsdle.cname_payload as cname_payload
import dnsdle.dnswire as dnswire
from dnsdle.compat import base32_lower_no_pad
from dnsdle.constants import DNS_FLAG_AA
from dnsdle.constants import DNS_FLAG_RA
from dnsdle.constants import DNS_FLAG_TC
from dnsdle.constants import DNS_QCLASS_IN
from dnsdle.constants import DNS_QTYPE_A
from dnsdle.constants import DNS_RCODE_NOERROR
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN


def _split_labels(payload_text, label_cap):
    labels = []
    start = 0
    while start < len(payload_text):
        labels.append(payload_text[start : start + label_cap])
        start += label_cap
    return tuple(labels)


def _query_message(labels, request_id=0x1234):
    header = struct.pack("!HHHHHH", request_id, 0x0100, 1, 0, 0, 0)
    question = dnswire.encode_name(labels) + struct.pack("!HH", DNS_QTYPE_A, DNS_QCLASS_IN)
    return header + question


def _response_with_record(
    record_bytes,
    request_qname_labels,
    selected_domain_labels,
    response_label,
    include_opt=True,
    edns_size=1232,
):
    payload_labels = _split_labels(base32_lower_no_pad(record_bytes), 63)
    answer = dnswire.build_cname_answer(
        request_qname_labels,
        2,
        payload_labels,
        response_label,
        ttl=30,
    )
    request = dnswire.parse_request(_query_message(request_qname_labels))
    return dnswire.build_response(
        request,
        DNS_RCODE_NOERROR,
        answer_bytes=answer,
        include_opt=include_opt,
        edns_size=edns_size,
    )


def _patch_response_flags(message, clear_flags=0, set_flags=0):
    flags = struct.unpack("!H", message[2:4])[0]
    flags = (flags & ~clear_flags) | set_flags
    return message[:2] + struct.pack("!H", flags) + message[4:]


def _append_authority_rrs(message, rr_bytes_list):
    nscount = struct.unpack("!H", message[8:10])[0]
    nscount += len(rr_bytes_list)
    patched = message[:8] + struct.pack("!H", nscount) + message[10:]
    for rr_bytes in rr_bytes_list:
        patched += rr_bytes
    return patched


def _dummy_ns_rr():
    """Build a minimal dummy NS RR using a pointer to the question name."""
    name = b'\xc0\x0c'
    rr_type = 2  # NS
    rr_class = 1  # IN
    ttl = 30
    rdata = b'\xc0\x0c'  # pointer to question name
    rdlength = len(rdata)
    return name + struct.pack("!HHIH", rr_type, rr_class, ttl, rdlength) + rdata


class ClientPayloadParityTests(unittest.TestCase):
    def test_roundtrip_decode_verify_decrypt_matches_slice_bytes(self):
        psk = "k"
        file_id = "1" * 16
        publish_version = "a" * 64
        slice_index = 0
        total_slices = 3
        compressed_size = 321
        slice_bytes = b"slice-data-not-trivial"
        request_qname_labels = ("tok123", "tag123", "example", "com")
        response_label = "r-x"
        selected_domain_labels = ("example", "com")

        payload_labels = cname_payload.payload_labels_for_slice(
            psk,
            file_id,
            publish_version,
            slice_index,
            total_slices,
            compressed_size,
            slice_bytes,
            63,
        )
        answer = dnswire.build_cname_answer(
            request_qname_labels,
            2,
            payload_labels,
            response_label,
            ttl=30,
        )
        request = dnswire.parse_request(_query_message(request_qname_labels))
        response = dnswire.build_response(
            request,
            DNS_RCODE_NOERROR,
            answer_bytes=answer,
            include_opt=True,
            edns_size=1232,
        )

        parsed_slice = client_payload.decode_response_slice(
            response,
            request["id"],
            request_qname_labels,
            DNS_QTYPE_A,
            DNS_QCLASS_IN,
            response_label,
            selected_domain_labels,
            psk,
            file_id,
            publish_version,
            slice_index,
            total_slices,
            compressed_size,
        )
        self.assertEqual(slice_bytes, parsed_slice)

    def test_rejects_profile_flags_and_length_invariants(self):
        request_qname_labels = ("tok123", "tag123", "example", "com")
        selected_domain_labels = ("example", "com")
        response_label = "r-x"
        common_decode_args = (
            0x1234,
            request_qname_labels,
            DNS_QTYPE_A,
            DNS_QCLASS_IN,
            response_label,
            selected_domain_labels,
            "k",
            "1" * 16,
            "a" * 64,
            0,
            1,
            1,
        )

        malformed_profile = b"\x02\x00\x00\x01a" + (b"\x00" * PAYLOAD_MAC_TRUNC_LEN)
        malformed_flags = b"\x01\x01\x00\x01a" + (b"\x00" * PAYLOAD_MAC_TRUNC_LEN)
        malformed_length = b"\x01\x00\x00\x02a" + (b"\x00" * PAYLOAD_MAC_TRUNC_LEN)

        for record, reason in (
            (malformed_profile, "unsupported_profile"),
            (malformed_flags, "unsupported_flags"),
            (malformed_length, "record_length_mismatch"),
        ):
            response = _response_with_record(
                record,
                request_qname_labels,
                selected_domain_labels,
                response_label,
            )
            with self.assertRaises(client_payload.ClientParseError) as raised:
                client_payload.decode_response_slice(response, *common_decode_args)
            self.assertEqual(reason, raised.exception.reason_code)

    def test_rejects_mac_mismatch(self):
        psk = "k"
        file_id = "1" * 16
        publish_version = "a" * 64
        record = cname_payload.build_slice_record(
            psk,
            file_id,
            publish_version,
            0,
            1,
            1,
            b"x",
        )
        tampered_record = record[:-1] + struct.pack("!B", (struct.unpack("!B", record[-1:])[0] ^ 0x01))
        request_qname_labels = ("tok123", "tag123", "example", "com")
        selected_domain_labels = ("example", "com")
        response_label = "r-x"
        response = _response_with_record(
            tampered_record,
            request_qname_labels,
            selected_domain_labels,
            response_label,
        )

        with self.assertRaises(client_payload.ClientCryptoError) as raised:
            client_payload.decode_response_slice(
                response,
                0x1234,
                request_qname_labels,
                DNS_QTYPE_A,
                DNS_QCLASS_IN,
                response_label,
                selected_domain_labels,
                psk,
                file_id,
                publish_version,
                0,
                1,
                1,
            )
        self.assertEqual("mac_mismatch", raised.exception.reason_code)

    def test_rejects_wrong_metadata_context(self):
        psk = "k"
        file_id = "1" * 16
        publish_version = "a" * 64
        request_qname_labels = ("tok123", "tag123", "example", "com")
        selected_domain_labels = ("example", "com")
        response_label = "r-x"
        record = cname_payload.build_slice_record(
            psk,
            file_id,
            publish_version,
            0,
            3,
            321,
            b"slice-data-not-trivial",
        )
        response = _response_with_record(
            record,
            request_qname_labels,
            selected_domain_labels,
            response_label,
        )

        with self.assertRaises(client_payload.ClientCryptoError) as raised:
            client_payload.decode_response_slice(
                response,
                0x1234,
                request_qname_labels,
                DNS_QTYPE_A,
                DNS_QCLASS_IN,
                response_label,
                selected_domain_labels,
                psk,
                file_id,
                publish_version,
                1,  # wrong slice index
                3,
                321,
            )
        self.assertEqual("mac_mismatch", raised.exception.reason_code)

    def test_accepts_recursive_resolver_style_response(self):
        psk = "k"
        file_id = "1" * 16
        publish_version = "a" * 64
        slice_index = 0
        total_slices = 3
        compressed_size = 321
        slice_bytes = b"slice-data-not-trivial"
        request_qname_labels = ("tok123", "tag123", "example", "com")
        response_label = "r-x"
        selected_domain_labels = ("example", "com")

        record = cname_payload.build_slice_record(
            psk, file_id, publish_version,
            slice_index, total_slices, compressed_size, slice_bytes,
        )
        response = _response_with_record(
            record, request_qname_labels, selected_domain_labels, response_label,
        )
        response = _patch_response_flags(
            response, clear_flags=DNS_FLAG_AA, set_flags=DNS_FLAG_RA,
        )

        parsed_slice = client_payload.decode_response_slice(
            response,
            0x1234,
            request_qname_labels,
            DNS_QTYPE_A,
            DNS_QCLASS_IN,
            response_label,
            selected_domain_labels,
            psk,
            file_id,
            publish_version,
            slice_index,
            total_slices,
            compressed_size,
        )
        self.assertEqual(slice_bytes, parsed_slice)

    def test_accepts_non_authoritative_response_with_extra_sections(self):
        psk = "k"
        file_id = "1" * 16
        publish_version = "a" * 64
        slice_index = 0
        total_slices = 3
        compressed_size = 321
        slice_bytes = b"slice-data-not-trivial"
        request_qname_labels = ("tok123", "tag123", "example", "com")
        response_label = "r-x"
        selected_domain_labels = ("example", "com")

        record = cname_payload.build_slice_record(
            psk, file_id, publish_version,
            slice_index, total_slices, compressed_size, slice_bytes,
        )
        response = _response_with_record(
            record, request_qname_labels, selected_domain_labels,
            response_label, include_opt=False,
        )
        response = _patch_response_flags(
            response, clear_flags=DNS_FLAG_AA, set_flags=DNS_FLAG_RA,
        )
        response = _append_authority_rrs(response, [_dummy_ns_rr()])

        parsed_slice = client_payload.decode_response_slice(
            response,
            0x1234,
            request_qname_labels,
            DNS_QTYPE_A,
            DNS_QCLASS_IN,
            response_label,
            selected_domain_labels,
            psk,
            file_id,
            publish_version,
            slice_index,
            total_slices,
            compressed_size,
        )
        self.assertEqual(slice_bytes, parsed_slice)

    def test_rejects_ambiguous_matching_cname_answers(self):
        psk = "k"
        file_id = "1" * 16
        publish_version = "a" * 64
        request_qname_labels = ("tok123", "tag123", "example", "com")
        selected_domain_labels = ("example", "com")
        response_label = "r-x"

        record = cname_payload.build_slice_record(
            psk, file_id, publish_version, 0, 3, 321, b"slice-data-not-trivial",
        )
        response = _response_with_record(
            record, request_qname_labels, selected_domain_labels,
            response_label, include_opt=False,
        )

        # Compute answer section start: header(12) + question name + qtype/qclass
        qname_wire = dnswire.encode_name(request_qname_labels)
        answer_start = 12 + len(qname_wire) + 4
        answer_bytes = response[answer_start:]

        # Duplicate the answer and patch ANCOUNT to 2
        doubled = response + answer_bytes
        doubled = doubled[:6] + struct.pack("!H", 2) + doubled[8:]

        with self.assertRaises(client_payload.ClientParseError) as raised:
            client_payload.decode_response_slice(
                doubled,
                0x1234,
                request_qname_labels,
                DNS_QTYPE_A,
                DNS_QCLASS_IN,
                response_label,
                selected_domain_labels,
                psk,
                file_id,
                publish_version,
                0,
                3,
                321,
            )
        self.assertEqual("required_cname_missing", raised.exception.reason_code)

    def test_rejects_missing_matching_cname_answer(self):
        psk = "k"
        file_id = "1" * 16
        publish_version = "a" * 64
        request_qname_labels = ("tok123", "tag123", "example", "com")
        selected_domain_labels = ("example", "com")
        response_label = "r-x"

        record = cname_payload.build_slice_record(
            psk, file_id, publish_version, 0, 3, 321, b"slice-data-not-trivial",
        )
        response = _response_with_record(
            record, request_qname_labels, selected_domain_labels,
            response_label, include_opt=False,
        )

        # Strip the answer section: header(12) + question only, ANCOUNT=0
        qname_wire = dnswire.encode_name(request_qname_labels)
        question_end = 12 + len(qname_wire) + 4
        stripped = response[:6] + struct.pack("!H", 0) + response[8:question_end]

        with self.assertRaises(client_payload.ClientParseError) as raised:
            client_payload.decode_response_slice(
                stripped,
                0x1234,
                request_qname_labels,
                DNS_QTYPE_A,
                DNS_QCLASS_IN,
                response_label,
                selected_domain_labels,
                psk,
                file_id,
                publish_version,
                0,
                3,
                321,
            )
        self.assertEqual("required_cname_missing", raised.exception.reason_code)

    def test_rejects_tc_set_even_with_valid_cname(self):
        psk = "k"
        file_id = "1" * 16
        publish_version = "a" * 64
        request_qname_labels = ("tok123", "tag123", "example", "com")
        selected_domain_labels = ("example", "com")
        response_label = "r-x"

        record = cname_payload.build_slice_record(
            psk, file_id, publish_version, 0, 3, 321, b"slice-data-not-trivial",
        )
        response = _response_with_record(
            record, request_qname_labels, selected_domain_labels, response_label,
        )
        response = _patch_response_flags(response, set_flags=DNS_FLAG_TC)

        with self.assertRaises(client_payload.ClientParseError) as raised:
            client_payload.decode_response_slice(
                response,
                0x1234,
                request_qname_labels,
                DNS_QTYPE_A,
                DNS_QCLASS_IN,
                response_label,
                selected_domain_labels,
                psk,
                file_id,
                publish_version,
                0,
                3,
                321,
            )
        self.assertEqual("response_truncated", raised.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
