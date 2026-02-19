from __future__ import absolute_import

from dnsdle.budget import compute_max_ciphertext_slice_bytes
from dnsdle.config import parse_cli_config
from dnsdle.mapping import apply_mapping
from dnsdle.publish import build_publish_items
from dnsdle.state import build_runtime_state


def build_startup_state(argv=None):
    config = parse_cli_config(argv)
    max_ciphertext_slice_bytes, budget_info = compute_max_ciphertext_slice_bytes(config)
    publish_items = build_publish_items(config, max_ciphertext_slice_bytes)
    mapped_items = apply_mapping(publish_items, config)
    return build_runtime_state(
        config=config,
        mapped_publish_items=mapped_items,
        max_ciphertext_slice_bytes=max_ciphertext_slice_bytes,
        budget_info=budget_info,
    )
