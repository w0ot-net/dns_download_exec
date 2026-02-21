from __future__ import absolute_import, unicode_literals

import hashlib
import zlib

import os

from dnsdle.helpers import _derive_file_id
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.state import StartupError


def _prepare_single_source(
    source_filename,
    plaintext_bytes,
    compression_level,
    seen_plaintext_sha256,
    seen_file_ids,
):
    plaintext_sha256 = hashlib.sha256(plaintext_bytes).hexdigest()
    if plaintext_sha256 in seen_plaintext_sha256:
        raise StartupError(
            "publish",
            "duplicate_plaintext_sha256",
            "duplicate file content detected",
            {"source_filename": source_filename, "plaintext_sha256": plaintext_sha256},
        )
    seen_plaintext_sha256.add(plaintext_sha256)

    try:
        compressed_bytes = zlib.compress(plaintext_bytes, compression_level)
    except Exception as exc:
        raise StartupError(
            "publish",
            "compression_failed",
            "compression failed: %s" % exc,
            {"source_filename": source_filename},
        )

    if not compressed_bytes:
        raise StartupError(
            "publish",
            "compression_empty",
            "compression produced empty output",
            {"source_filename": source_filename},
        )

    publish_version = hashlib.sha256(compressed_bytes).hexdigest()
    file_id = _derive_file_id(publish_version)
    if file_id in seen_file_ids:
        raise StartupError(
            "publish",
            "file_id_collision",
            "file_id collision detected across publish set",
            {"source_filename": source_filename, "file_id": file_id},
        )
    seen_file_ids.add(file_id)

    return {
        "source_filename": source_filename,
        "plaintext_sha256": plaintext_sha256,
        "compressed_bytes": compressed_bytes,
        "compressed_size": len(compressed_bytes),
        "publish_version": publish_version,
        "file_id": file_id,
    }


def read_payload_sources(config):
    sources = []
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
        sources.append((os.path.basename(path), plaintext_bytes))
    return sources


def prepare_publish_sources(sources, compression_level):
    seen_plaintext_sha256 = set()
    seen_file_ids = set()
    prepared = []
    for source_index, (source_filename, plaintext_bytes) in enumerate(sources):
        item = _prepare_single_source(
            source_filename=source_filename,
            plaintext_bytes=plaintext_bytes,
            compression_level=compression_level,
            seen_plaintext_sha256=seen_plaintext_sha256,
            seen_file_ids=seen_file_ids,
        )
        if logger_enabled("debug"):
            log_event("debug", "publish", {
                "phase": "publish",
                "classification": "diagnostic",
                "reason_code": "publish_item_built",
                "file_id": item["file_id"],
                "publish_version": item["publish_version"],
                "plaintext_sha256": item["plaintext_sha256"],
                "compressed_size": item["compressed_size"],
                "source_filename": item["source_filename"],
                "source_index": source_index,
            })
        prepared.append(item)
    return prepared


def slice_prepared_sources(prepared_sources, max_ciphertext_slice_bytes):
    if max_ciphertext_slice_bytes <= 0:
        raise StartupError(
            "publish",
            "budget_unusable",
            "max_ciphertext_slice_bytes must be positive",
        )
    publish_items = []
    for item in prepared_sources:
        compressed_bytes = item["compressed_bytes"]
        compressed_size = item["compressed_size"]
        slice_bytes_by_index = tuple(
            compressed_bytes[i:i + max_ciphertext_slice_bytes]
            for i in range(0, compressed_size, max_ciphertext_slice_bytes)
        )
        publish_items.append({
            "file_id": item["file_id"],
            "publish_version": item["publish_version"],
            "plaintext_sha256": item["plaintext_sha256"],
            "compressed_size": compressed_size,
            "total_slices": len(slice_bytes_by_index),
            "slice_bytes_by_index": slice_bytes_by_index,
            "source_filename": item["source_filename"],
        })
    return publish_items
