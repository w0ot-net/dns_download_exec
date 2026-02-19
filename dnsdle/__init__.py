from __future__ import absolute_import

from dnsdle.budget import compute_max_ciphertext_slice_bytes
from dnsdle.cli import parse_cli_args
from dnsdle.config import build_config
from dnsdle.mapping import apply_mapping
from dnsdle.publish import build_publish_items
from dnsdle.server import serve_runtime
from dnsdle.state import build_runtime_state


def _max_slice_token_len(mapped_publish_items):
    max_len = 1
    for item in mapped_publish_items:
        token_len = item["slice_token_len"]
        if token_len > max_len:
            max_len = token_len
    return max_len


def build_startup_state(argv=None):
    parsed_args = parse_cli_args(argv)
    config = build_config(parsed_args)

    query_token_len = 1
    while True:
        max_ciphertext_slice_bytes, budget_info = compute_max_ciphertext_slice_bytes(
            config, query_token_len=query_token_len
        )
        publish_items = build_publish_items(config, max_ciphertext_slice_bytes)
        mapped_items = apply_mapping(publish_items, config)

        realized_max_token_len = _max_slice_token_len(mapped_items)
        if realized_max_token_len <= query_token_len:
            break
        query_token_len = realized_max_token_len

    return build_runtime_state(
        config=config,
        mapped_publish_items=mapped_items,
        max_ciphertext_slice_bytes=max_ciphertext_slice_bytes,
        budget_info=budget_info,
    )
