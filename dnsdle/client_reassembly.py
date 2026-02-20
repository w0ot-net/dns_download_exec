from __future__ import absolute_import, unicode_literals

import hashlib
import zlib

from dnsdle.compat import decode_ascii
from dnsdle.compat import is_binary


class ClientReassemblyError(Exception):
    def __init__(self, reason_code, message):
        Exception.__init__(self, message)
        self.reason_code = reason_code


def _raise_reassembly(reason_code, message):
    raise ClientReassemblyError(reason_code, message)


def _to_positive_int(value, field_name):
    try:
        int_value = int(value)
    except (TypeError, ValueError):
        _raise_reassembly("%s_not_integer" % field_name, "%s must be an integer" % field_name)
    if int_value <= 0:
        _raise_reassembly("%s_not_positive" % field_name, "%s must be positive" % field_name)
    return int_value


def store_slice_bytes(slice_map, slice_index, slice_bytes):
    if not isinstance(slice_map, dict):
        _raise_reassembly("slice_map_invalid", "slice_map must be a dict")
    try:
        index = int(slice_index)
    except (TypeError, ValueError):
        _raise_reassembly("slice_index_not_integer", "slice_index must be an integer")
    if index < 0:
        _raise_reassembly("slice_index_negative", "slice_index must be non-negative")
    if not is_binary(slice_bytes):
        _raise_reassembly("slice_bytes_invalid", "slice_bytes must be bytes")

    existing = slice_map.get(index)
    if existing is not None and existing != slice_bytes:
        _raise_reassembly("duplicate_slice_mismatch", "duplicate slice bytes mismatch for slice index")
    is_new = existing is None
    if is_new:
        slice_map[index] = slice_bytes
    return is_new


def reassemble_and_verify(
    slice_map,
    total_slices,
    compressed_size,
    plaintext_sha256,
):
    if not isinstance(slice_map, dict):
        _raise_reassembly("slice_map_invalid", "slice_map must be a dict")

    total_slices_int = _to_positive_int(total_slices, "total_slices")
    compressed_size_int = _to_positive_int(compressed_size, "compressed_size")
    expected_indices = tuple(range(total_slices_int))
    actual_indices = tuple(sorted(slice_map.keys()))
    if actual_indices != expected_indices:
        _raise_reassembly("slice_index_coverage_invalid", "slice index coverage does not match expected range")

    ordered_parts = []
    for index in expected_indices:
        value = slice_map[index]
        if not is_binary(value):
            _raise_reassembly("slice_bytes_invalid", "slice bytes must be bytes")
        ordered_parts.append(value)

    compressed_bytes = b"".join(ordered_parts)
    if len(compressed_bytes) != compressed_size_int:
        _raise_reassembly("compressed_size_mismatch", "reassembled compressed size does not match expected size")

    try:
        plaintext_bytes = zlib.decompress(compressed_bytes)
    except Exception as exc:
        _raise_reassembly("decompress_failed", "zlib decompression failed: %s" % exc)

    try:
        expected_hash = decode_ascii(plaintext_sha256).lower()
    except Exception:
        _raise_reassembly("plaintext_sha256_invalid", "plaintext_sha256 must be ASCII text")
    if len(expected_hash) != 64:
        _raise_reassembly("plaintext_sha256_length_invalid", "plaintext_sha256 must be 64 hex characters")
    actual_hash = hashlib.sha256(plaintext_bytes).hexdigest().lower()
    if actual_hash != expected_hash:
        _raise_reassembly("plaintext_hash_mismatch", "plaintext sha256 does not match expected hash")
    return plaintext_bytes
