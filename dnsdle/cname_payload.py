from __future__ import absolute_import, unicode_literals

import struct

from dnsdle.compat import base32_lower_no_pad
from dnsdle.compat import encode_ascii
from dnsdle.compat import encode_ascii_int
from dnsdle.compat import encode_utf8
from dnsdle.compat import iter_byte_values
from dnsdle.constants import PAYLOAD_ENC_KEY_LABEL
from dnsdle.constants import PAYLOAD_ENC_STREAM_LABEL
from dnsdle.constants import PAYLOAD_FLAGS_V1_BYTE
from dnsdle.constants import PAYLOAD_MAC_KEY_LABEL
from dnsdle.constants import PAYLOAD_MAC_MESSAGE_LABEL
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN
from dnsdle.constants import PAYLOAD_PROFILE_V1_BYTE
from dnsdle.helpers import hmac_sha256


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


# __EXTRACT: _derive_file_bound_key__
def _derive_file_bound_key(psk, file_id, publish_version, key_label):
    psk_bytes = encode_utf8(psk)
    if not psk_bytes:
        raise ValueError("psk must be non-empty")
    file_id_bytes = encode_ascii(file_id)
    publish_version_bytes = encode_ascii(publish_version)
    return hmac_sha256(psk_bytes, key_label + file_id_bytes + b"|" + publish_version_bytes)
# __END_EXTRACT__


# __EXTRACT: _keystream_bytes__
def _keystream_bytes(enc_key, file_id, publish_version, slice_index, output_len):
    if output_len <= 0:
        raise ValueError("output_len must be positive")
    file_id_bytes = encode_ascii(file_id)
    publish_version_bytes = encode_ascii(publish_version)
    slice_index_bytes = encode_ascii_int(slice_index, "slice_index")
    blocks = []
    counter = 0
    produced = 0
    while produced < output_len:
        counter_bytes = encode_ascii_int(counter, "counter")
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
        block = hmac_sha256(enc_key, block_input)
        blocks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(blocks)[:output_len]
# __END_EXTRACT__


# __EXTRACT: _xor_bytes__
def _xor_bytes(left_bytes, right_bytes):
    if len(left_bytes) != len(right_bytes):
        raise ValueError("xor inputs must have equal length")

    out = bytearray(len(left_bytes))
    for index, (a, b) in enumerate(zip(iter_byte_values(left_bytes), iter_byte_values(right_bytes))):
        out[index] = a ^ b
    return bytes(out)
# __END_EXTRACT__


def _encrypt_slice_bytes(psk, file_id, publish_version, slice_index, slice_bytes):
    if not slice_bytes:
        raise ValueError("slice_bytes must be non-empty")
    key = _derive_file_bound_key(psk, file_id, publish_version, PAYLOAD_ENC_KEY_LABEL)
    stream = _keystream_bytes(
        key,
        file_id,
        publish_version,
        slice_index,
        len(slice_bytes),
    )
    return _xor_bytes(slice_bytes, stream)


def _mac_bytes(
    psk,
    file_id,
    publish_version,
    slice_index,
    total_slices,
    compressed_size,
    ciphertext_bytes,
):
    slice_index_bytes = encode_ascii_int(slice_index, "slice_index")
    total_slices_bytes = encode_ascii_int(total_slices, "total_slices")
    compressed_size_bytes = encode_ascii_int(compressed_size, "compressed_size")
    if int(total_slices) <= 0:
        raise ValueError("total_slices must be positive")
    if int(compressed_size) <= 0:
        raise ValueError("compressed_size must be positive")
    message = (
        PAYLOAD_MAC_MESSAGE_LABEL
        + encode_ascii(file_id)
        + b"|"
        + encode_ascii(publish_version)
        + b"|"
        + slice_index_bytes
        + b"|"
        + total_slices_bytes
        + b"|"
        + compressed_size_bytes
        + b"|"
        + ciphertext_bytes
    )
    mac_key = _derive_file_bound_key(
        psk,
        file_id,
        publish_version,
        PAYLOAD_MAC_KEY_LABEL,
    )
    return hmac_sha256(mac_key, message)[:PAYLOAD_MAC_TRUNC_LEN]


def build_slice_record(
    psk,
    file_id,
    publish_version,
    slice_index,
    total_slices,
    compressed_size,
    slice_bytes,
):
    if not slice_bytes:
        raise ValueError("slice_bytes must be non-empty")
    if len(slice_bytes) > 65535:
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
        slice_bytes,
    )

    header = struct.pack("!BBH", PAYLOAD_PROFILE_V1_BYTE, PAYLOAD_FLAGS_V1_BYTE, len(slice_bytes))
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
