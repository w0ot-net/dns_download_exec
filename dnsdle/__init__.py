from __future__ import absolute_import, unicode_literals

import os

from dnsdle.budget import compute_max_ciphertext_slice_bytes
from dnsdle.cli import parse_cli_args
from dnsdle.client_standalone import _UNIVERSAL_CLIENT_FILENAME
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
    configure_active_logger(config)
    configure_console(enabled=not config.verbose)

    # Convergence loop: Phase 1 converges user files, Phase 2 adds the
    # universal client.  If the client pushes the combined token length
    # past the Phase 1 budget we restart with the higher requirement.
    query_token_len = 4
    for _outer in range(10):
        # Phase 1: user files -- inner convergence loop
        while True:
            max_ciphertext_slice_bytes, budget_info = compute_max_ciphertext_slice_bytes(
                config, query_token_len=query_token_len
            )
            publish_items = build_publish_items(config, max_ciphertext_slice_bytes)
            mapped_items = apply_mapping(publish_items, config)

            realized_max_token_len = _max_slice_token_len(mapped_items)
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
                        "realized_max_token_len": realized_max_token_len,
                        "max_ciphertext_slice_bytes": max_ciphertext_slice_bytes,
                    },
                )
            if realized_max_token_len <= query_token_len:
                break
            query_token_len = realized_max_token_len

        # Phase 2: build one universal client and publish as single file
        generation_result = generate_client_artifacts(config)

        # Snapshot user file mappings for stability invariant
        user_file_snapshot = {}
        for item in mapped_items:
            user_file_snapshot[item["file_id"]] = (
                item["file_tag"],
                item["slice_token_len"],
                item["slice_tokens"],
            )

        seen_plaintext_sha256 = set(item["plaintext_sha256"] for item in publish_items)
        seen_file_ids = set(item["file_id"] for item in publish_items)

        client_source = generation_result["source"]
        client_filename = generation_result["filename"]
        sources = [(client_filename, encode_ascii(client_source))]

        client_publish_items = build_publish_items_from_sources(
            sources,
            config.compression_level,
            max_ciphertext_slice_bytes,
            seen_plaintext_sha256,
            seen_file_ids,
        )

        combined_items = list(publish_items) + client_publish_items
        combined_mapped = apply_mapping(combined_items, config)

        # Invariant: user file mappings unchanged after combining with client items
        for item in combined_mapped:
            snapshot = user_file_snapshot.get(item["file_id"])
            if snapshot is None:
                continue
            expected_tag, expected_token_len, expected_tokens = snapshot
            if (item["file_tag"] != expected_tag
                    or item["slice_token_len"] != expected_token_len
                    or item["slice_tokens"] != expected_tokens):
                raise StartupError(
                    "startup",
                    "mapping_stability_violation",
                    "user file mapping changed after combining with client publish items",
                    {"file_id": item["file_id"]},
                )

        combined_max_token_len = _max_slice_token_len(combined_mapped)
        if combined_max_token_len <= query_token_len:
            break
        query_token_len = combined_max_token_len
    else:
        raise StartupError(
            "startup",
            "token_convergence_failed",
            "combined token length did not converge after 10 iterations",
            {
                "combined_max_token_len": combined_max_token_len,
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
