from __future__ import absolute_import

import struct

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


class DnsParseError(Exception):
    pass


def _ord_byte(value):
    if isinstance(value, int):
        return value
    return ord(value)


def _to_label_bytes(label):
    if isinstance(label, bytes):
        raw = label
    else:
        raw = label.encode("ascii")
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


def _decode_name(message, start_offset):
    labels = []
    offset = start_offset
    jumped = False
    read_end_offset = None
    visited_offsets = set()

    while True:
        if offset >= len(message):
            raise DnsParseError("name extends past message")

        first = _ord_byte(message[offset])
        if (first & DNS_POINTER_TAG) == DNS_POINTER_TAG:
            if offset + 1 >= len(message):
                raise DnsParseError("truncated name pointer")
            pointer = ((first & 0x3F) << 8) | _ord_byte(message[offset + 1])
            if pointer >= len(message):
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
        if end_offset > len(message):
            raise DnsParseError("label extends past message")
        raw = message[offset:end_offset]
        try:
            if isinstance(raw, str):
                label = raw
            else:
                label = raw.decode("ascii")
        except Exception:
            raise DnsParseError("label is not ASCII")
        labels.append(label.lower())
        offset = end_offset

        if len(labels) > 127:
            raise DnsParseError("name has too many labels")

    return tuple(labels), (read_end_offset if jumped else offset)


def parse_request(message):
    if len(message) < 12:
        raise DnsParseError("message shorter than DNS header")

    request_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
        "!HHHHHH", message[:12]
    )

    question = None
    if qdcount >= 1:
        labels, offset = _decode_name(message, 12)
        if offset + 4 > len(message):
            raise DnsParseError("truncated DNS question")
        qtype, qclass = struct.unpack("!HH", message[offset : offset + 4])
        question = {
            "qname_labels": labels,
            "qtype": qtype,
            "qclass": qclass,
        }

    return {
        "id": request_id,
        "flags": flags,
        "opcode": (flags & DNS_OPCODE_MASK),
        "qdcount": qdcount,
        "ancount": ancount,
        "nscount": nscount,
        "arcount": arcount,
        "question": question,
    }


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
    owner_name = _pack_pointer(12)
    domain_pointer = _qname_label_offset(question_labels, domain_label_index, 12)
    rdata = encode_name_with_pointer(
        tuple(payload_labels) + (response_label,),
        domain_pointer,
    )
    rr_head = struct.pack("!HHIH", DNS_QTYPE_CNAME, DNS_QCLASS_IN, ttl, len(rdata))
    return owner_name + rr_head + rdata


def build_a_answer(ttl):
    owner_name = _pack_pointer(12)
    rr_head = struct.pack("!HHIH", DNS_QTYPE_A, DNS_QCLASS_IN, ttl, len(SYNTHETIC_A_RDATA))
    return owner_name + rr_head + SYNTHETIC_A_RDATA


def _encode_opt_record(edns_size):
    if edns_size <= 0:
        raise ValueError("edns_size must be positive")
    return b"\x00" + struct.pack("!HHIH", DNS_QTYPE_OPT, edns_size, 0, 0)


def _response_flags(request_flags, rcode):
    return (
        DNS_FLAG_QR
        | DNS_FLAG_AA
        | (request_flags & DNS_FLAG_RD)
        | (request_flags & DNS_OPCODE_MASK)
        | (rcode & 0x000F)
    )


def build_response(request, rcode, answer_bytes=None, include_opt=False, edns_size=512):
    question = request.get("question")
    question_bytes = b""
    qdcount = 0
    if question is not None:
        question_bytes = (
            encode_name(question["qname_labels"])
            + struct.pack("!HH", question["qtype"], question["qclass"])
        )
        qdcount = 1

    ancount = 1 if answer_bytes else 0
    arcount = 1 if include_opt else 0
    flags = _response_flags(request["flags"], rcode)

    header = struct.pack(
        "!HHHHHH",
        request["id"],
        flags,
        qdcount,
        ancount,
        0,
        arcount,
    )

    parts = [header, question_bytes]
    if answer_bytes:
        parts.append(answer_bytes)
    if include_opt:
        parts.append(_encode_opt_record(edns_size))
    return b"".join(parts)
