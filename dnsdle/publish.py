from __future__ import absolute_import, unicode_literals

import hashlib
import zlib

import os

from dnsdle.constants import PROFILE_V1
from dnsdle.helpers import _derive_file_id
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.state import StartupError


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _chunk_bytes(data, chunk_size):
    return tuple(
        data[i:i + chunk_size]
        for i in range(0, len(data), chunk_size)
    )


def _build_single_publish_item(
    source_filename,
    plaintext_bytes,
    compression_level,
    max_ciphertext_slice_bytes,
    seen_plaintext_sha256,
    seen_file_ids,
    item_context,
):
    plaintext_sha256 = _sha256_hex(plaintext_bytes)
    if plaintext_sha256 in seen_plaintext_sha256:
        ctx = dict(item_context)
        ctx["plaintext_sha256"] = plaintext_sha256
        raise StartupError(
            "publish",
            "duplicate_plaintext_sha256",
            "duplicate file content detected",
            ctx,
        )
    seen_plaintext_sha256.add(plaintext_sha256)

    try:
        compressed_bytes = zlib.compress(plaintext_bytes, compression_level)
    except Exception as exc:
        ctx = dict(item_context)
        raise StartupError(
            "publish",
            "compression_failed",
            "compression failed: %s" % exc,
            ctx,
        )

    if not compressed_bytes:
        raise StartupError(
            "publish",
            "compression_empty",
            "compression produced empty output",
            dict(item_context),
        )

    publish_version = _sha256_hex(compressed_bytes)
    file_id = _derive_file_id(publish_version)
    if file_id in seen_file_ids:
        ctx = dict(item_context)
        ctx["file_id"] = file_id
        raise StartupError(
            "publish",
            "file_id_collision",
            "file_id collision detected across publish set",
            ctx,
        )
    seen_file_ids.add(file_id)

    compressed_size = len(compressed_bytes)
    slice_bytes_by_index = _chunk_bytes(compressed_bytes, max_ciphertext_slice_bytes)
    total_slices = len(slice_bytes_by_index)

    return {
        "file_id": file_id,
        "publish_version": publish_version,
        "plaintext_sha256": plaintext_sha256,
        "compressed_size": compressed_size,
        "total_slices": total_slices,
        "slice_bytes_by_index": slice_bytes_by_index,
        "crypto_profile": PROFILE_V1,
        "wire_profile": PROFILE_V1,
        "source_filename": source_filename,
    }


def _log_publish_item_built(item, extra_context):
    if not logger_enabled("debug"):
        return
    event = {
        "phase": "publish",
        "classification": "diagnostic",
        "reason_code": "publish_item_built",
        "file_id": item["file_id"],
        "publish_version": item["publish_version"],
        "plaintext_sha256": item["plaintext_sha256"],
        "compressed_size": item["compressed_size"],
        "total_slices": item["total_slices"],
    }
    event.update(extra_context)
    log_event("debug", "publish", event)


def build_publish_items(
    config,
    max_ciphertext_slice_bytes,
    seen_plaintext_sha256=None,
    seen_file_ids=None,
):
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

    return build_publish_items_from_sources(
        sources,
        config.compression_level,
        max_ciphertext_slice_bytes,
        seen_plaintext_sha256,
        seen_file_ids,
    )


def build_publish_items_from_sources(
    sources,
    compression_level,
    max_ciphertext_slice_bytes,
    seen_plaintext_sha256=None,
    seen_file_ids=None,
):
    if max_ciphertext_slice_bytes <= 0:
        raise StartupError(
            "publish",
            "budget_unusable",
            "max_ciphertext_slice_bytes must be positive",
        )

    if seen_plaintext_sha256 is None:
        seen_plaintext_sha256 = set()
    if seen_file_ids is None:
        seen_file_ids = set()

    publish_items = []

    for source_index, (source_filename, plaintext_bytes) in enumerate(sources):
        item = _build_single_publish_item(
            source_filename=source_filename,
            plaintext_bytes=plaintext_bytes,
            compression_level=compression_level,
            max_ciphertext_slice_bytes=max_ciphertext_slice_bytes,
            seen_plaintext_sha256=seen_plaintext_sha256,
            seen_file_ids=seen_file_ids,
            item_context={"source_filename": source_filename},
        )
        publish_items.append(item)
        _log_publish_item_built(
            item, {"source_index": source_index, "source_filename": source_filename}
        )

    return publish_items
