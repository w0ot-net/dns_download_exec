from __future__ import absolute_import

import hashlib
import hmac
import struct

from dnsdle.compat import base32_lower_no_pad
from dnsdle.compat import iter_byte_values
from dnsdle.compat import to_ascii_bytes
from dnsdle.compat import to_ascii_int_bytes
from dnsdle.compat import to_utf8_bytes
from dnsdle.constants import PAYLOAD_ENC_KEY_LABEL
from dnsdle.constants import PAYLOAD_ENC_STREAM_LABEL
from dnsdle.constants import PAYLOAD_FLAGS_V1_BYTE
from dnsdle.constants import PAYLOAD_MAC_KEY_LABEL
from dnsdle.constants import PAYLOAD_MAC_MESSAGE_LABEL
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN
from dnsdle.constants import PAYLOAD_PROFILE_V1_BYTE


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


def _enc_key(psk, file_id, publish_version):
    psk_bytes = to_utf8_bytes(psk)
    if not psk_bytes:
        raise ValueError("psk must be non-empty")
    file_id_bytes = to_ascii_bytes(file_id)
    publish_version_bytes = to_ascii_bytes(publish_version)
    return hmac.new(
        psk_bytes,
        PAYLOAD_ENC_KEY_LABEL + file_id_bytes + b"|" + publish_version_bytes,
        hashlib.sha256,
    ).digest()


def _keystream_bytes(enc_key, file_id, publish_version, slice_index, output_len):
    if output_len <= 0:
        raise ValueError("output_len must be positive")
    file_id_bytes = to_ascii_bytes(file_id)
    publish_version_bytes = to_ascii_bytes(publish_version)
    slice_index_bytes = to_ascii_int_bytes(slice_index, "slice_index")
    blocks = []
    counter = 0
    produced = 0
    while produced < output_len:
        counter_bytes = to_ascii_int_bytes(counter, "counter")
        block_input = (
            PAYLOAD_ENC_STREAM_LABEL
            + file_id_bytes
            + b"|"
            + publish_version_bytes
            + b"|"
            + slice_index_bytes
            + b"|"
            + counter_bytes
        )
        block = hmac.new(enc_key, block_input, hashlib.sha256).digest()
        blocks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(blocks)[:output_len]


def _xor_bytes(left_bytes, right_bytes):
    left_values = list(iter_byte_values(left_bytes))
    right_values = list(iter_byte_values(right_bytes))
    if len(left_values) != len(right_values):
        raise ValueError("xor inputs must have equal length")

    out = bytearray(len(left_values))
    for index, left_value in enumerate(left_values):
        out[index] = left_value ^ right_values[index]
    return bytes(out)


def _encrypt_slice_bytes(psk, file_id, publish_version, slice_index, slice_bytes):
    payload = to_ascii_bytes(slice_bytes)
    if not payload:
        raise ValueError("slice_bytes must be non-empty")
    key = _enc_key(psk, file_id, publish_version)
    stream = _keystream_bytes(
        key,
        file_id,
        publish_version,
        slice_index,
        len(payload),
    )
    return _xor_bytes(payload, stream)


def _mac_key(psk, file_id, publish_version):
    psk_bytes = to_utf8_bytes(psk)
    if not psk_bytes:
        raise ValueError("psk must be non-empty")
    file_id_bytes = to_ascii_bytes(file_id)
    publish_version_bytes = to_ascii_bytes(publish_version)
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
    ciphertext_bytes,
):
    slice_index_bytes = to_ascii_int_bytes(slice_index, "slice_index")
    total_slices_bytes = to_ascii_int_bytes(total_slices, "total_slices")
    compressed_size_bytes = to_ascii_int_bytes(compressed_size, "compressed_size")
    if int(total_slices) <= 0:
        raise ValueError("total_slices must be positive")
    if int(compressed_size) <= 0:
        raise ValueError("compressed_size must be positive")
    payload = to_ascii_bytes(ciphertext_bytes)
    message = (
        PAYLOAD_MAC_MESSAGE_LABEL
        + to_ascii_bytes(file_id)
        + b"|"
        + to_ascii_bytes(publish_version)
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
    payload = to_ascii_bytes(slice_bytes)
    payload_len = len(payload)
    if payload_len <= 0:
        raise ValueError("slice_bytes must be non-empty")
    if payload_len > 65535:
        raise ValueError("slice_bytes exceeds u16 length field")
    try:
        slice_index_int = int(slice_index)
        total_slices_int = int(total_slices)
    except (TypeError, ValueError):
        raise ValueError("slice index metadata must be integers")
    if total_slices_int <= 0:
        raise ValueError("total_slices must be positive")
    if slice_index_int < 0 or slice_index_int >= total_slices_int:
        raise ValueError("slice_index must be within total_slices")
    ciphertext = _encrypt_slice_bytes(
        psk,
        file_id,
        publish_version,
        slice_index_int,
        payload,
    )

    header = struct.pack("!BBH", PAYLOAD_PROFILE_V1_BYTE, PAYLOAD_FLAGS_V1_BYTE, payload_len)
    mac = _mac_bytes(
        psk,
        file_id,
        publish_version,
        slice_index_int,
        total_slices_int,
        compressed_size,
        ciphertext,
    )
    return header + ciphertext + mac


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
    payload_text = base32_lower_no_pad(record_bytes)
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
