from __future__ import absolute_import

import base64
import hashlib
import hmac

from dnsdle.constants import DIGEST_TEXT_CAPACITY
from dnsdle.constants import MAPPING_FILE_LABEL
from dnsdle.constants import MAPPING_SLICE_LABEL
from dnsdle.constants import MAX_DNS_NAME_WIRE_LENGTH
from dnsdle.state import StartupError


def _ascii_bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode("ascii")


def _dns_name_wire_length(labels):
    return 1 + sum(1 + len(label) for label in labels)


def _base32_lower_no_pad(raw_bytes):
    encoded = base64.b32encode(raw_bytes)
    if not isinstance(encoded, str):
        encoded = encoded.decode("ascii")
    return encoded.rstrip("=").lower()


def _hmac_sha256(key_bytes, message_bytes):
    return hmac.new(key_bytes, message_bytes, hashlib.sha256).digest()


def _derive_file_digest(seed_bytes, publish_version_bytes):
    return _hmac_sha256(seed_bytes, MAPPING_FILE_LABEL + publish_version_bytes)


def _derive_slice_digest(seed_bytes, publish_version_bytes, slice_index):
    slice_index_bytes = _ascii_bytes(str(slice_index))
    return _hmac_sha256(
        seed_bytes,
        MAPPING_SLICE_LABEL + publish_version_bytes + b"|" + slice_index_bytes,
    )


def _derive_file_tag(seed_bytes, publish_version, file_tag_len):
    publish_version_bytes = _ascii_bytes(publish_version)
    digest_text = _base32_lower_no_pad(
        _derive_file_digest(seed_bytes, publish_version_bytes)
    )
    return digest_text[:file_tag_len]


def _derive_slice_token(seed_bytes, publish_version, slice_index, token_len):
    publish_version_bytes = _ascii_bytes(publish_version)
    digest_text = _base32_lower_no_pad(
        _derive_slice_digest(seed_bytes, publish_version_bytes, slice_index)
    )
    return digest_text[:token_len]


def _compute_tokens(seed_bytes, publish_version, total_slices, token_len):
    return tuple(
        _derive_slice_token(seed_bytes, publish_version, index, token_len)
        for index in range(total_slices)
    )


def _max_token_len_for_file(config, file_tag):
    max_by_qname = 0
    max_candidate = config.dns_max_label_len
    if max_candidate > DIGEST_TEXT_CAPACITY:
        max_candidate = DIGEST_TEXT_CAPACITY

    for token_len in range(1, max_candidate + 1):
        labels = ("a" * token_len, file_tag) + tuple(config.domain_labels)
        if _dns_name_wire_length(labels) <= MAX_DNS_NAME_WIRE_LENGTH:
            max_by_qname = token_len

    return max_by_qname


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


def apply_mapping(publish_items, config):
    seed_bytes = _ascii_bytes(config.mapping_seed)

    entries = []
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
        entry["slice_token_len_max"] = max_token_len
        entry["slice_tokens"] = local_tokens
        entries.append(entry)

    canonical_order = sorted(
        range(len(entries)),
        key=lambda idx: (
            entries[idx]["file_tag"],
            entries[idx]["file_id"],
            entries[idx]["publish_version"],
        ),
    )

    while True:
        colliding_files = _find_colliding_files(entries)
        if not colliding_files:
            break

        promote_idx = None
        for idx in canonical_order:
            if idx in colliding_files:
                promote_idx = idx
                break

        if promote_idx is None:
            raise StartupError(
                "mapping",
                "mapping_collision",
                "collision set could not be resolved deterministically",
            )

        entry = entries[promote_idx]
        current_len = entry["slice_token_len"]
        max_len = entry["slice_token_len_max"]
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

    for entry in entries:
        entry.pop("slice_token_len_max", None)

    return entries
