from __future__ import absolute_import

import hashlib
import hmac

from dnsdle.compat import base32_lower_no_pad
from dnsdle.compat import to_ascii_bytes
from dnsdle.constants import DIGEST_TEXT_CAPACITY
from dnsdle.constants import MAPPING_FILE_LABEL
from dnsdle.constants import MAPPING_SLICE_LABEL
from dnsdle.constants import MAX_DNS_NAME_WIRE_LENGTH
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.state import StartupError


def _dns_name_wire_length(labels):
    return 1 + sum(1 + len(label) for label in labels)


def _hmac_sha256(key_bytes, message_bytes):
    return hmac.new(key_bytes, message_bytes, hashlib.sha256).digest()


def _derive_file_digest(seed_bytes, publish_version_bytes):
    return _hmac_sha256(seed_bytes, MAPPING_FILE_LABEL + publish_version_bytes)


def _derive_slice_digest(seed_bytes, publish_version_bytes, slice_index):
    slice_index_bytes = to_ascii_bytes(str(slice_index))
    return _hmac_sha256(
        seed_bytes,
        MAPPING_SLICE_LABEL + publish_version_bytes + b"|" + slice_index_bytes,
    )


def _derive_file_tag(seed_bytes, publish_version, file_tag_len):
    publish_version_bytes = to_ascii_bytes(publish_version)
    digest_text = base32_lower_no_pad(
        _derive_file_digest(seed_bytes, publish_version_bytes)
    )
    return digest_text[:file_tag_len]


def _derive_slice_token(seed_bytes, publish_version, slice_index, token_len):
    publish_version_bytes = to_ascii_bytes(publish_version)
    digest_text = base32_lower_no_pad(
        _derive_slice_digest(seed_bytes, publish_version_bytes, slice_index)
    )
    return digest_text[:token_len]


def _compute_tokens(seed_bytes, publish_version, total_slices, token_len):
    return tuple(
        _derive_slice_token(seed_bytes, publish_version, index, token_len)
        for index in range(total_slices)
    )


def _max_token_len_for_file(config, file_tag):
    max_candidate = min(config.dns_max_label_len, DIGEST_TEXT_CAPACITY)
    for token_len in range(max_candidate, 0, -1):
        labels = ("a" * token_len, file_tag) + tuple(config.longest_domain_labels)
        if _dns_name_wire_length(labels) <= MAX_DNS_NAME_WIRE_LENGTH:
            return token_len
    return 0


def _find_min_local_len(seed_bytes, publish_version, total_slices, max_token_len):
    for token_len in range(1, max_token_len + 1):
        tokens = _compute_tokens(seed_bytes, publish_version, total_slices, token_len)
        if len(set(tokens)) == total_slices:
            return token_len, tokens
    return None, None


def _find_colliding_files(entries):
    owner_by_key = {}
    colliding_files = set()

    for index, entry in enumerate(entries):
        for token in entry["slice_tokens"]:
            key = (entry["file_tag"], token)
            owner = owner_by_key.get(key)
            if owner is None:
                owner_by_key[key] = index
            else:
                colliding_files.add(owner)
                colliding_files.add(index)

    return colliding_files


def _entry_sort_key(entry):
    return (
        entry["file_tag"],
        entry["file_id"],
        entry["publish_version"],
    )


def apply_mapping(publish_items, config):
    seed_bytes = to_ascii_bytes(config.mapping_seed)

    entries = []
    max_len_by_index = []
    for item in publish_items:
        entry = dict(item)
        file_tag = _derive_file_tag(
            seed_bytes, entry["publish_version"], config.file_tag_len
        )
        if not file_tag:
            raise StartupError(
                "mapping",
                "mapping_capacity_exceeded",
                "file_tag derivation produced an empty value",
                {"file_id": entry["file_id"]},
            )

        max_token_len = _max_token_len_for_file(config, file_tag)
        if max_token_len <= 0:
            raise StartupError(
                "mapping",
                "mapping_capacity_exceeded",
                "QNAME limits do not allow a slice token label",
                {"file_id": entry["file_id"], "file_tag": file_tag},
            )

        local_len, local_tokens = _find_min_local_len(
            seed_bytes,
            entry["publish_version"],
            entry["total_slices"],
            max_token_len,
        )
        if local_len is None:
            raise StartupError(
                "mapping",
                "mapping_collision",
                "unable to resolve local slice-token collisions within limits",
                {"file_id": entry["file_id"], "file_tag": file_tag},
            )

        entry["file_tag"] = file_tag
        entry["slice_token_len"] = local_len
        entry["slice_tokens"] = local_tokens
        entries.append(entry)
        max_len_by_index.append(max_token_len)

    canonical_order = sorted(
        range(len(entries)),
        key=lambda idx: _entry_sort_key(entries[idx]),
    )

    while True:
        colliding_files = _find_colliding_files(entries)
        if not colliding_files:
            break

        promote_idx = next(
            (idx for idx in canonical_order if idx in colliding_files),
            None,
        )

        if promote_idx is None:
            raise StartupError(
                "mapping",
                "mapping_collision",
                "collision set could not be resolved deterministically",
            )

        entry = entries[promote_idx]
        current_len = entry["slice_token_len"]
        max_len = max_len_by_index[promote_idx]
        if current_len >= max_len:
            raise StartupError(
                "mapping",
                "mapping_collision",
                "unresolved mapping collision after deterministic promotion",
                {
                    "file_id": entry["file_id"],
                    "file_tag": entry["file_tag"],
                    "slice_token_len": current_len,
                    "slice_token_len_max": max_len,
                },
            )

        new_len = current_len + 1
        tokens = _compute_tokens(
            seed_bytes,
            entry["publish_version"],
            entry["total_slices"],
            new_len,
        )
        entry["slice_token_len"] = new_len
        entry["slice_tokens"] = tokens

    if logger_enabled("debug", "mapping"):
        log_event(
            "debug",
            "mapping",
            {
                "phase": "mapping",
                "classification": "diagnostic",
                "reason_code": "mapping_applied",
            },
            context_fn=lambda: {
                "file_count": len(entries),
                "total_slice_tokens": sum(len(item["slice_tokens"]) for item in entries),
            },
        )

    return entries
