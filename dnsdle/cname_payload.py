from __future__ import absolute_import

import base64
import hashlib
import hmac
import struct

from dnsdle.constants import PAYLOAD_FLAGS_V1_BYTE
from dnsdle.constants import PAYLOAD_MAC_KEY_LABEL
from dnsdle.constants import PAYLOAD_MAC_MESSAGE_LABEL
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN
from dnsdle.constants import PAYLOAD_PROFILE_V1_BYTE


def _to_bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode("ascii")


def _to_utf8_bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def _to_ascii_int_bytes(value, field_name):
    try:
        int_value = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer" % field_name)
    if int_value < 0:
        raise ValueError("%s must be non-negative" % field_name)
    return _to_bytes(str(int_value))


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


def _mac_key(psk, file_id, publish_version):
    psk_bytes = _to_utf8_bytes(psk)
    if not psk_bytes:
        raise ValueError("psk must be non-empty")
    file_id_bytes = _to_bytes(file_id)
    publish_version_bytes = _to_bytes(publish_version)
    return hmac.new(
        psk_bytes,
        PAYLOAD_MAC_KEY_LABEL + file_id_bytes + b"|" + publish_version_bytes,
        hashlib.sha256,
    ).digest()


def _mac_bytes(
    psk,
    file_id,
    publish_version,
    slice_index,
    total_slices,
    compressed_size,
    slice_bytes,
):
    slice_index_bytes = _to_ascii_int_bytes(slice_index, "slice_index")
    total_slices_bytes = _to_ascii_int_bytes(total_slices, "total_slices")
    compressed_size_bytes = _to_ascii_int_bytes(compressed_size, "compressed_size")
    if int(total_slices) <= 0:
        raise ValueError("total_slices must be positive")
    if int(compressed_size) <= 0:
        raise ValueError("compressed_size must be positive")
    payload = _to_bytes(slice_bytes)
    message = (
        PAYLOAD_MAC_MESSAGE_LABEL
        + _to_bytes(file_id)
        + b"|"
        + _to_bytes(publish_version)
        + b"|"
        + slice_index_bytes
        + b"|"
        + total_slices_bytes
        + b"|"
        + compressed_size_bytes
        + b"|"
        + payload
    )
    mac_key = _mac_key(psk, file_id, publish_version)
    return hmac.new(mac_key, message, hashlib.sha256).digest()[:PAYLOAD_MAC_TRUNC_LEN]


def build_slice_record(
    psk,
    file_id,
    publish_version,
    slice_index,
    total_slices,
    compressed_size,
    slice_bytes,
):
    payload = _to_bytes(slice_bytes)
    payload_len = len(payload)
    if payload_len <= 0:
        raise ValueError("slice_bytes must be non-empty")
    if payload_len > 65535:
        raise ValueError("slice_bytes exceeds u16 length field")
    if int(slice_index) >= int(total_slices):
        raise ValueError("slice_index must be within total_slices")

    header = struct.pack("!BBH", PAYLOAD_PROFILE_V1_BYTE, PAYLOAD_FLAGS_V1_BYTE, payload_len)
    mac = _mac_bytes(
        psk,
        file_id,
        publish_version,
        slice_index,
        total_slices,
        compressed_size,
        payload,
    )
    return header + payload + mac


def payload_labels_for_slice(
    psk,
    file_id,
    publish_version,
    slice_index,
    total_slices,
    compressed_size,
    slice_bytes,
    label_cap,
):
    record_bytes = build_slice_record(
        psk,
        file_id,
        publish_version,
        slice_index,
        total_slices,
        compressed_size,
        slice_bytes,
    )
    payload_text = _base32_lower_no_pad(record_bytes)
    return _split_payload_labels(payload_text, label_cap)


def build_cname_target_labels(
    psk,
    file_id,
    publish_version,
    slice_index,
    total_slices,
    compressed_size,
    slice_bytes,
    response_label,
    selected_domain_labels,
    dns_max_label_len,
):
    payload_labels = payload_labels_for_slice(
        psk,
        file_id,
        publish_version,
        slice_index,
        total_slices,
        compressed_size,
        slice_bytes,
        dns_max_label_len,
    )
    return payload_labels + (response_label,) + tuple(selected_domain_labels)
