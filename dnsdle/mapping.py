from __future__ import absolute_import, unicode_literals

from dnsdle.compat import encode_ascii
from dnsdle.constants import DIGEST_TEXT_CAPACITY
from dnsdle.constants import MAX_DNS_NAME_WIRE_LENGTH
from dnsdle.helpers import _derive_file_tag
from dnsdle.helpers import _derive_slice_token
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.state import StartupError


def _max_token_len_for_file(config, file_tag):
    budget = MAX_DNS_NAME_WIRE_LENGTH - 2 - len(file_tag) - config.longest_domain_wire_len
    return min(max(budget, 0), config.dns_max_label_len, DIGEST_TEXT_CAPACITY)


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
    seed_bytes = encode_ascii(config.mapping_seed)

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

        full_tokens = tuple(
            _derive_slice_token(seed_bytes, entry["publish_version"], i, max_token_len)
            for i in range(entry["total_slices"])
        )

        local_len = None
        for length in range(1, max_token_len + 1):
            if len(set(t[:length] for t in full_tokens)) == entry["total_slices"]:
                local_len = length
                break
        if local_len is None:
            raise StartupError(
                "mapping",
                "mapping_collision",
                "unable to resolve local slice-token collisions within limits",
                {"file_id": entry["file_id"], "file_tag": file_tag},
            )

        entry["file_tag"] = file_tag
        entry["slice_token_len"] = local_len
        entry["slice_tokens"] = tuple(t[:local_len] for t in full_tokens)
        entry["max_token_len"] = max_token_len
        entry["_full_tokens"] = full_tokens
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

        promote_idx = next(
            (idx for idx in canonical_order if idx in colliding_files),
            None,
        )
        assert promote_idx is not None

        entry = entries[promote_idx]
        current_len = entry["slice_token_len"]
        max_len = entry["max_token_len"]
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
        entry["slice_token_len"] = new_len
        entry["slice_tokens"] = tuple(t[:new_len] for t in entry["_full_tokens"])

    for entry in entries:
        del entry["_full_tokens"]

    if logger_enabled("debug"):
        log_event("debug", "mapping", {
            "phase": "mapping",
            "classification": "diagnostic",
            "reason_code": "mapping_applied",
            "file_count": len(entries),
            "total_slice_tokens": sum(len(item["slice_tokens"]) for item in entries),
        })

    return entries
