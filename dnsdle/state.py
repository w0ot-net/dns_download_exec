from __future__ import absolute_import

from collections import namedtuple


class StartupError(Exception):
    def __init__(self, phase, reason_code, message, context=None):
        Exception.__init__(self, message)
        self.phase = phase
        self.reason_code = reason_code
        self.message = message
        self.context = context or {}

    def to_log_record(self):
        record = {
            "classification": "startup_error",
            "phase": self.phase,
            "reason_code": self.reason_code,
            "message": self.message,
        }
        for key, value in self.context.items():
            if key not in record:
                record[key] = value
        return record


class FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("FrozenDict is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


PublishItem = namedtuple(
    "PublishItem",
    [
        "file_id",
        "publish_version",
        "file_tag",
        "plaintext_sha256",
        "compressed_size",
        "total_slices",
        "slice_token_len",
        "slice_tokens",
        "slice_bytes_by_index",
        "crypto_profile",
        "wire_profile",
        "source_filename",
    ],
)


RuntimeState = namedtuple(
    "RuntimeState",
    [
        "config",
        "max_ciphertext_slice_bytes",
        "budget_info",
        "publish_items",
        "lookup_by_key",
        "slice_bytes_by_identity",
        "publish_meta_by_identity",
    ],
)


def to_publish_item(mapped_item):
    return PublishItem(
        file_id=mapped_item["file_id"],
        publish_version=mapped_item["publish_version"],
        file_tag=mapped_item["file_tag"],
        plaintext_sha256=mapped_item["plaintext_sha256"],
        compressed_size=mapped_item["compressed_size"],
        total_slices=mapped_item["total_slices"],
        slice_token_len=mapped_item["slice_token_len"],
        slice_tokens=tuple(mapped_item["slice_tokens"]),
        slice_bytes_by_index=tuple(mapped_item["slice_bytes_by_index"]),
        crypto_profile=mapped_item["crypto_profile"],
        wire_profile=mapped_item["wire_profile"],
        source_filename=mapped_item["source_filename"],
    )


def build_runtime_state(config, mapped_publish_items, max_ciphertext_slice_bytes, budget_info):
    publish_items = []
    lookup = {}
    slice_bytes_by_identity = {}
    publish_meta_by_identity = {}

    for item in mapped_publish_items:
        publish_item = to_publish_item(item)
        publish_items.append(publish_item)
        identity = (publish_item.file_id, publish_item.publish_version)
        if identity in slice_bytes_by_identity:
            raise StartupError(
                "publish",
                "duplicate_publish_identity",
                "duplicate publish identity while building final state",
                {
                    "file_id": publish_item.file_id,
                    "publish_version": publish_item.publish_version,
                },
            )
        slice_bytes_by_identity[identity] = publish_item.slice_bytes_by_index
        publish_meta_by_identity[identity] = (
            publish_item.total_slices,
            publish_item.compressed_size,
        )

        for index, token in enumerate(publish_item.slice_tokens):
            key = (publish_item.file_tag, token)
            if key in lookup:
                raise StartupError(
                    "mapping",
                    "mapping_collision",
                    "lookup key collision while building final state",
                    {
                        "file_tag": publish_item.file_tag,
                        "slice_token": token,
                    },
                )
            lookup[key] = (publish_item.file_id, publish_item.publish_version, index)

    return RuntimeState(
        config=config,
        max_ciphertext_slice_bytes=max_ciphertext_slice_bytes,
        budget_info=FrozenDict(budget_info),
        publish_items=tuple(publish_items),
        lookup_by_key=FrozenDict(lookup),
        slice_bytes_by_identity=FrozenDict(slice_bytes_by_identity),
        publish_meta_by_identity=FrozenDict(publish_meta_by_identity),
    )
