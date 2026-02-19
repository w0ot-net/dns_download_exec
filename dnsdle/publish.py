from __future__ import absolute_import

import hashlib
import zlib

from dnsdle.compat import to_ascii_bytes
from dnsdle.constants import FILE_ID_PREFIX
from dnsdle.constants import PROFILE_V1
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.state import StartupError


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest().lower()


def _derive_file_id(publish_version):
    file_id_input = to_ascii_bytes(FILE_ID_PREFIX) + to_ascii_bytes(publish_version)
    return _sha256_hex(file_id_input)[:16]


def _chunk_bytes(data, chunk_size):
    chunks = []
    start = 0
    data_len = len(data)
    while start < data_len:
        end = start + chunk_size
        chunks.append(data[start:end])
        start = end
    return tuple(chunks)


def build_publish_items(config, max_ciphertext_slice_bytes):
    if max_ciphertext_slice_bytes <= 0:
        raise StartupError(
            "publish",
            "budget_unusable",
            "max_ciphertext_slice_bytes must be positive",
        )

    publish_items = []
    seen_plaintext_sha256 = set()
    seen_file_ids = set()

    for file_index, path in enumerate(config.files):
        try:
            with open(path, "rb") as handle:
                plaintext_bytes = handle.read()
        except IOError:
            raise StartupError(
                "publish",
                "unreadable_file",
                "failed to read input file",
                {"file_index": file_index},
            )

        plaintext_sha256 = _sha256_hex(plaintext_bytes)
        if plaintext_sha256 in seen_plaintext_sha256:
            raise StartupError(
                "publish",
                "duplicate_plaintext_sha256",
                "duplicate file content detected",
                {"plaintext_sha256": plaintext_sha256},
            )
        seen_plaintext_sha256.add(plaintext_sha256)

        try:
            compressed_bytes = zlib.compress(plaintext_bytes, config.compression_level)
        except Exception as exc:
            raise StartupError(
                "publish",
                "compression_failed",
                "compression failed: %s" % exc,
                {"file_index": file_index},
            )

        if not compressed_bytes:
            raise StartupError(
                "publish",
                "compression_empty",
                "compression produced empty output",
                {"file_index": file_index},
            )

        publish_version = _sha256_hex(compressed_bytes)
        file_id = _derive_file_id(publish_version)
        if file_id in seen_file_ids:
            raise StartupError(
                "publish",
                "file_id_collision",
                "file_id collision detected across publish set",
                {"file_id": file_id},
            )
        seen_file_ids.add(file_id)

        compressed_size = len(compressed_bytes)
        slice_bytes_by_index = _chunk_bytes(compressed_bytes, max_ciphertext_slice_bytes)
        total_slices = len(slice_bytes_by_index)
        if total_slices <= 0:
            raise StartupError(
                "publish",
                "invalid_slice_count",
                "total_slices must be positive",
                {"file_id": file_id},
            )

        publish_items.append(
            {
                "file_id": file_id,
                "publish_version": publish_version,
                "plaintext_sha256": plaintext_sha256,
                "compressed_size": compressed_size,
                "total_slices": total_slices,
                "slice_bytes_by_index": slice_bytes_by_index,
                "crypto_profile": PROFILE_V1,
                "wire_profile": PROFILE_V1,
            }
        )
        if logger_enabled("debug", "publish"):
            log_event(
                "debug",
                "publish",
                {
                    "phase": "publish",
                    "classification": "diagnostic",
                    "reason_code": "publish_item_built",
                    "file_id": file_id,
                    "publish_version": publish_version,
                },
                context_fn=lambda: {
                    "plaintext_sha256": plaintext_sha256,
                    "compressed_size": compressed_size,
                    "total_slices": total_slices,
                    "file_index": file_index,
                },
            )

    return publish_items
