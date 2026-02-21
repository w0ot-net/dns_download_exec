from __future__ import absolute_import, unicode_literals

import os

from dnsdle.budget import compute_max_ciphertext_slice_bytes
from dnsdle.cli import parse_cli_args
from dnsdle.compat import encode_ascii
from dnsdle.config import build_config
from dnsdle.client_generator import generate_client_artifacts
from dnsdle.console import configure_console
from dnsdle.logging_runtime import configure_active_logger
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.mapping import apply_mapping
from dnsdle.publish import build_publish_items
from dnsdle.publish import build_publish_items_from_sources
from dnsdle.server import serve_runtime
from dnsdle.stager_generator import generate_stagers
from dnsdle.state import build_runtime_state
from dnsdle.state import StartupError


def build_startup_state(argv=None):
    parsed_args = parse_cli_args(argv)
    config = build_config(parsed_args)
    configure_active_logger(config)
    configure_console(enabled=not config.verbose)

    generation_result = generate_client_artifacts(config)
    client_filename = generation_result["filename"]
    client_bytes = encode_ascii(generation_result["source"])

    query_token_len = 4
    for _iteration in range(10):
        max_ciphertext_slice_bytes, budget_info = compute_max_ciphertext_slice_bytes(
            config, query_token_len=query_token_len
        )
        publish_items = build_publish_items(config, max_ciphertext_slice_bytes)
        seen_sha256 = set(item["plaintext_sha256"] for item in publish_items)
        seen_ids = set(item["file_id"] for item in publish_items)
        client_publish_items = build_publish_items_from_sources(
            [(client_filename, client_bytes)],
            config.compression_level,
            max_ciphertext_slice_bytes,
            seen_sha256,
            seen_ids,
        )
        combined_mapped = apply_mapping(list(publish_items) + client_publish_items, config)
        realized = max(item["slice_token_len"] for item in combined_mapped)
        if logger_enabled("debug"):
            log_event(
                "debug",
                "startup",
                {
                    "phase": "startup",
                    "classification": "diagnostic",
                    "reason_code": "startup_iteration",
                },
                context_fn=lambda: {
                    "query_token_len": query_token_len,
                    "realized_max_token_len": realized,
                    "max_ciphertext_slice_bytes": max_ciphertext_slice_bytes,
                },
            )
        if realized <= query_token_len:
            break
        query_token_len = realized
    else:
        raise StartupError(
            "startup",
            "token_convergence_failed",
            "combined token length did not converge after 10 iterations",
            {
                "realized_max_token_len": realized,
                "query_token_len": query_token_len,
            },
        )

    runtime_state = build_runtime_state(
        config=config,
        mapped_publish_items=combined_mapped,
        max_ciphertext_slice_bytes=max_ciphertext_slice_bytes,
        budget_info=budget_info,
    )

    # Find the single universal client mapped item for stager generation
    client_mapped_item = None
    payload_mapped_items = []
    for item in combined_mapped:
        if item["source_filename"] == client_filename:
            client_mapped_item = item
        else:
            payload_mapped_items.append(item)

    if client_mapped_item is None:
        raise StartupError(
            "startup",
            "mapping_stability_violation",
            "universal client publish item not found in combined mapping",
        )

    stagers = generate_stagers(config, generation_result, client_mapped_item, payload_mapped_items)

    display_names = {}
    for item in runtime_state.publish_items:
        if item.source_filename == client_filename:
            display_names[item.file_tag] = "(universal client)"
        else:
            display_names[item.file_tag] = os.path.basename(item.source_filename)

    return runtime_state, generation_result, stagers, display_names
