from __future__ import absolute_import

import base64
import struct

from dnsdle.constants import PAYLOAD_FLAGS_V1_BYTE
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN
from dnsdle.constants import PAYLOAD_PROFILE_V1_BYTE


def _to_bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode("ascii")


def _base32_lower_no_pad(raw_bytes):
    encoded = base64.b32encode(raw_bytes)
    if not isinstance(encoded, str):
        encoded = encoded.decode("ascii")
    return encoded.rstrip("=").lower()


def _split_payload_labels(payload_text, label_cap):
    if label_cap <= 0:
        raise ValueError("label_cap must be positive")
    if not payload_text:
        raise ValueError("payload_text must be non-empty")

    labels = []
    index = 0
    payload_len = len(payload_text)
    while index < payload_len:
        labels.append(payload_text[index : index + label_cap])
        index += label_cap
    return tuple(labels)


def build_slice_record(slice_bytes):
    payload = _to_bytes(slice_bytes)
    payload_len = len(payload)
    if payload_len <= 0:
        raise ValueError("slice_bytes must be non-empty")
    if payload_len > 65535:
        raise ValueError("slice_bytes exceeds u16 length field")

    header = struct.pack("!BBH", PAYLOAD_PROFILE_V1_BYTE, PAYLOAD_FLAGS_V1_BYTE, payload_len)
    mac = b"\x00" * PAYLOAD_MAC_TRUNC_LEN
    return header + payload + mac


def payload_labels_for_slice(slice_bytes, label_cap):
    record_bytes = build_slice_record(slice_bytes)
    payload_text = _base32_lower_no_pad(record_bytes)
    return _split_payload_labels(payload_text, label_cap)


def build_cname_target_labels(
    slice_bytes,
    response_label,
    selected_domain_labels,
    dns_max_label_len,
):
    payload_labels = payload_labels_for_slice(slice_bytes, dns_max_label_len)
    return payload_labels + (response_label,) + tuple(selected_domain_labels)
