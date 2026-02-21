from __future__ import absolute_import, unicode_literals

import struct

from dnsdle.compat import decode_ascii
from dnsdle.compat import encode_ascii
from dnsdle.constants import DNS_HEADER_BYTES
from dnsdle.constants import DNS_FLAG_AA
from dnsdle.constants import DNS_FLAG_QR
from dnsdle.constants import DNS_FLAG_RD
from dnsdle.constants import DNS_OPCODE_MASK
from dnsdle.constants import DNS_POINTER_MASK
from dnsdle.constants import DNS_POINTER_TAG
from dnsdle.constants import DNS_POINTER_VALUE_MASK
from dnsdle.constants import DNS_QCLASS_IN
from dnsdle.constants import DNS_QTYPE_A
from dnsdle.constants import DNS_QTYPE_CNAME
from dnsdle.constants import DNS_QTYPE_OPT
from dnsdle.constants import SYNTHETIC_A_RDATA
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled


class DnsParseError(Exception):
    pass



def _to_label_bytes(label):
    raw = encode_ascii(label)
    if not raw:
        raise ValueError("label must be non-empty")
    if len(raw) > 63:
        raise ValueError("label exceeds DNS max label length")
    return raw


def encode_name(labels):
    parts = []
    for label in labels:
        raw = _to_label_bytes(label)
        parts.append(struct.pack("!B", len(raw)))
        parts.append(raw)
    parts.append(b"\x00")
    return b"".join(parts)


# __EXTRACT: _decode_name__
def _decode_name(message, start_offset):
    ba = bytearray(message)
    message_len = len(ba)
    labels = []
    offset = start_offset
    jumped = False
    read_end_offset = None
    visited_offsets = set()

    while True:
        if offset >= message_len:
            raise DnsParseError("name extends past message")

        first = ba[offset]
        if (first & DNS_POINTER_TAG) == DNS_POINTER_TAG:
            if offset + 1 >= message_len:
                raise DnsParseError("truncated name pointer")
            pointer = ((first & 0x3F) << 8) | ba[offset + 1]
            if pointer >= message_len:
                raise DnsParseError("name pointer is out of bounds")
            if pointer in visited_offsets:
                raise DnsParseError("name pointer loop detected")
            visited_offsets.add(pointer)
            if not jumped:
                read_end_offset = offset + 2
                jumped = True
            offset = pointer
            continue

        if first & DNS_POINTER_TAG:
            raise DnsParseError("invalid name label type")

        offset += 1
        if first == 0:
            break

        end_offset = offset + first
        if end_offset > message_len:
            raise DnsParseError("label extends past message")
        raw = bytes(ba[offset:end_offset])
        try:
            label = decode_ascii(raw)
        except Exception:
            raise DnsParseError("label is not ASCII")
        labels.append(label.lower())
        offset = end_offset

        if len(labels) > 127:
            raise DnsParseError("name has too many labels")

    return tuple(labels), (read_end_offset if jumped else offset)
# __END_EXTRACT__


def _unpack_header(message):
    return struct.unpack("!HHHHHH", message[:DNS_HEADER_BYTES])


def _decode_question(message, start_offset):
    labels, offset = _decode_name(message, start_offset)
    if offset + 4 > len(message):
        raise DnsParseError("truncated DNS question")
    qtype, qclass = struct.unpack("!HH", message[offset : offset + 4])
    return (
        {
            "qname_labels": labels,
            "qtype": qtype,
            "qclass": qclass,
        },
        offset + 4,
    )


def parse_request(message):
    if len(message) < DNS_HEADER_BYTES:
        raise DnsParseError("message shorter than DNS header")

    request_id, flags, qdcount, ancount, nscount, arcount = _unpack_header(message)

    question = None
    raw_question_bytes = b""
    if qdcount >= 1:
        question, question_end = _decode_question(message, DNS_HEADER_BYTES)
        raw_question_bytes = message[DNS_HEADER_BYTES:question_end]

    parsed = {
        "id": request_id,
        "flags": flags,
        "opcode": (flags & DNS_OPCODE_MASK),
        "qdcount": qdcount,
        "ancount": ancount,
        "nscount": nscount,
        "arcount": arcount,
        "question": question,
        "raw_question_bytes": raw_question_bytes,
    }
    if logger_enabled("trace"):
        log_event(
            "trace",
            "dnswire",
            {
                "phase": "server",
                "classification": "diagnostic",
                "reason_code": "dns_request_parsed",
            },
            context_fn=lambda: {
                "qdcount": qdcount,
                "ancount": ancount,
                "nscount": nscount,
                "arcount": arcount,
                "has_question": question is not None,
            },
        )
    return parsed


def _pack_pointer(offset):
    if offset < 0 or offset > DNS_POINTER_VALUE_MASK:
        raise ValueError("pointer offset is out of range")
    return struct.pack("!H", DNS_POINTER_MASK | offset)


def _qname_label_offset(question_labels, label_index, qname_offset):
    if label_index < 0 or label_index >= len(question_labels):
        raise ValueError("label_index is out of range")
    offset = qname_offset
    for label in question_labels[:label_index]:
        offset += 1 + len(label)
    return offset


def encode_name_with_pointer(prefix_labels, pointer_offset):
    return encode_name(prefix_labels)[:-1] + _pack_pointer(pointer_offset)


def build_cname_answer(question_labels, domain_label_index, payload_labels, response_label, ttl):
    owner_name = _pack_pointer(DNS_HEADER_BYTES)
    domain_pointer = _qname_label_offset(
        question_labels, domain_label_index, DNS_HEADER_BYTES
    )
    rdata = encode_name_with_pointer(
        tuple(payload_labels) + (response_label,),
        domain_pointer,
    )
    rr_head = struct.pack("!HHIH", DNS_QTYPE_CNAME, DNS_QCLASS_IN, ttl, len(rdata))
    return owner_name + rr_head + rdata


def build_a_answer(ttl):
    owner_name = _pack_pointer(DNS_HEADER_BYTES)
    rr_head = struct.pack("!HHIH", DNS_QTYPE_A, DNS_QCLASS_IN, ttl, len(SYNTHETIC_A_RDATA))
    return owner_name + rr_head + SYNTHETIC_A_RDATA


def _encode_opt_record(edns_size):
    if edns_size <= 0:
        raise ValueError("edns_size must be positive")
    return b"\x00" + struct.pack("!HHIH", DNS_QTYPE_OPT, edns_size, 0, 0)


def build_response(request, rcode, answer_bytes=None, include_opt=False, edns_size=512):
    raw_question_bytes = request["raw_question_bytes"]
    qdcount = 1 if raw_question_bytes else 0

    ancount = 1 if answer_bytes else 0
    arcount = 1 if include_opt else 0
    flags = (
        DNS_FLAG_QR
        | DNS_FLAG_AA
        | (request["flags"] & DNS_FLAG_RD)
        | (request["flags"] & DNS_OPCODE_MASK)
        | (rcode & 0x000F)
    )

    header = struct.pack(
        "!HHHHHH",
        request["id"],
        flags,
        qdcount,
        ancount,
        0,
        arcount,
    )

    parts = [header, raw_question_bytes]
    if answer_bytes:
        parts.append(answer_bytes)
    if include_opt:
        parts.append(_encode_opt_record(edns_size))
    response = b"".join(parts)
    if logger_enabled("trace"):
        log_event(
            "trace",
            "dnswire",
            {
                "phase": "server",
                "classification": "diagnostic",
                "reason_code": "dns_response_built",
            },
            context_fn=lambda: {
                "rcode": rcode,
                "qdcount": qdcount,
                "ancount": ancount,
                "arcount": arcount,
                "include_opt": include_opt,
                "response_len": len(response),
            },
        )
    return response
