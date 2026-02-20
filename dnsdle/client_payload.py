from __future__ import absolute_import, unicode_literals

import struct

import dnsdle.cname_payload as cname_payload
import dnsdle.dnswire as dnswire
from dnsdle.compat import base32_decode_no_pad
from dnsdle.compat import constant_time_equals
from dnsdle.constants import DNS_FLAG_QR
from dnsdle.constants import DNS_FLAG_TC
from dnsdle.constants import DNS_OPCODE_QUERY
from dnsdle.constants import DNS_QCLASS_IN
from dnsdle.constants import DNS_QTYPE_CNAME
from dnsdle.constants import DNS_RCODE_NOERROR
from dnsdle.constants import PAYLOAD_FLAGS_V1_BYTE
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN
from dnsdle.constants import PAYLOAD_PROFILE_V1_BYTE


class ClientPayloadError(Exception):
    def __init__(self, reason_code, message):
        Exception.__init__(self, message)
        self.reason_code = reason_code


class ClientParseError(ClientPayloadError):
    pass


class ClientCryptoError(ClientPayloadError):
    pass


def _raise_parse(reason_code, message):
    raise ClientParseError(reason_code, message)


def _raise_crypto(reason_code, message):
    raise ClientCryptoError(reason_code, message)


def _is_suffix(suffix_labels, labels):
    suffix_len = len(suffix_labels)
    label_len = len(labels)
    if suffix_len > label_len:
        return False
    return labels[label_len - suffix_len :] == suffix_labels


def _extract_payload_labels(cname_labels, response_label, selected_domain_labels):
    suffix = (response_label,) + tuple(selected_domain_labels)
    if not _is_suffix(suffix, cname_labels):
        _raise_parse("cname_suffix_mismatch", "response CNAME suffix does not match expected suffix")
    payload_labels = tuple(cname_labels[: len(cname_labels) - len(suffix)])
    if not payload_labels:
        _raise_parse("missing_payload_labels", "response CNAME payload labels are missing")
    return payload_labels


def _decode_payload_record_bytes(payload_labels):
    payload_text = "".join(payload_labels)
    try:
        return base32_decode_no_pad(payload_text)
    except ValueError as exc:
        _raise_parse("invalid_payload_base32", "payload labels are not valid lowercase base32: %s" % exc)


def parse_payload_record(record_bytes):
    min_record_len = 4 + PAYLOAD_MAC_TRUNC_LEN
    if len(record_bytes) <= min_record_len:
        _raise_parse("record_too_short", "payload record is shorter than minimum record size")

    profile = struct.unpack("!B", record_bytes[0:1])[0]
    flags = struct.unpack("!B", record_bytes[1:2])[0]
    cipher_len = struct.unpack("!H", record_bytes[2:4])[0]

    if profile != PAYLOAD_PROFILE_V1_BYTE:
        _raise_parse("unsupported_profile", "unsupported payload profile")
    if flags != PAYLOAD_FLAGS_V1_BYTE:
        _raise_parse("unsupported_flags", "payload flags are non-zero")
    if cipher_len <= 0:
        _raise_parse("invalid_cipher_len", "ciphertext length must be positive")

    expected_len = 4 + cipher_len + PAYLOAD_MAC_TRUNC_LEN
    if len(record_bytes) != expected_len:
        _raise_parse("record_length_mismatch", "payload record length does not match encoded ciphertext length")

    return {
        "profile": profile,
        "flags": flags,
        "cipher_len": cipher_len,
        "ciphertext": record_bytes[4 : 4 + cipher_len],
        "mac_trunc": record_bytes[4 + cipher_len :],
    }


def verify_and_decrypt_record(
    parsed_record,
    psk,
    file_id,
    publish_version,
    slice_index,
    total_slices,
    compressed_size,
):
    expected_mac = cname_payload.compute_slice_mac(
        psk,
        file_id,
        publish_version,
        slice_index,
        total_slices,
        compressed_size,
        parsed_record["ciphertext"],
    )
    if not constant_time_equals(parsed_record["mac_trunc"], expected_mac):
        _raise_crypto("mac_mismatch", "payload MAC does not match expected value")
    return cname_payload.decrypt_slice_ciphertext(
        psk,
        file_id,
        publish_version,
        slice_index,
        parsed_record["ciphertext"],
    )


def extract_response_cname_labels(
    response_message,
    request_id,
    request_qname_labels,
    request_qtype,
    request_qclass,
):
    try:
        parsed = dnswire.parse_message(response_message)
    except dnswire.DnsParseError as exc:
        _raise_parse("dns_parse_error", "response DNS message parse failed: %s" % exc)

    flags = parsed["flags"]
    if (flags & DNS_FLAG_QR) == 0:
        _raise_parse("response_not_qr", "response does not set QR")
    if flags & DNS_FLAG_TC:
        _raise_parse("response_truncated", "response must not set TC")
    if parsed["opcode"] != DNS_OPCODE_QUERY:
        _raise_parse("response_opcode_invalid", "response opcode is not QUERY")
    if parsed["id"] != request_id:
        _raise_parse("response_id_mismatch", "response ID does not match request ID")
    if parsed["rcode"] != DNS_RCODE_NOERROR:
        _raise_parse("response_rcode_invalid", "response RCODE is not NOERROR")

    if len(parsed["questions"]) != 1:
        _raise_parse("response_question_count_invalid", "response does not contain exactly one question")
    question = parsed["questions"][0]
    if (
        question["qname_labels"] != tuple(request_qname_labels)
        or question["qtype"] != request_qtype
        or question["qclass"] != request_qclass
    ):
        _raise_parse("response_question_mismatch", "response question does not match request question")

    matching_answers = []
    for answer in parsed["answers"]:
        if answer["type"] != DNS_QTYPE_CNAME or answer["class"] != DNS_QCLASS_IN:
            continue
        if answer["name_labels"] != tuple(request_qname_labels):
            continue
        cname_labels = answer.get("cname_labels")
        if cname_labels is None:
            _raise_parse("missing_cname_labels", "response CNAME answer did not decode CNAME labels")
        matching_answers.append(tuple(cname_labels))

    if len(matching_answers) == 0:
        _raise_parse("required_cname_missing", "response does not contain a matching IN CNAME answer")
    if len(matching_answers) > 1:
        _raise_parse("required_cname_ambiguous", "response contains multiple matching IN CNAME answers")
    return matching_answers[0]


def decode_response_slice(
    response_message,
    request_id,
    request_qname_labels,
    request_qtype,
    request_qclass,
    response_label,
    selected_domain_labels,
    psk,
    file_id,
    publish_version,
    slice_index,
    total_slices,
    compressed_size,
):
    cname_labels = extract_response_cname_labels(
        response_message,
        request_id,
        request_qname_labels,
        request_qtype,
        request_qclass,
    )
    payload_labels = _extract_payload_labels(cname_labels, response_label, selected_domain_labels)
    record_bytes = _decode_payload_record_bytes(payload_labels)
    parsed_record = parse_payload_record(record_bytes)
    return verify_and_decrypt_record(
        parsed_record,
        psk,
        file_id,
        publish_version,
        slice_index,
        total_slices,
        compressed_size,
    )
