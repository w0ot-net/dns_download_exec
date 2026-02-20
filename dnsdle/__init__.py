from __future__ import absolute_import, unicode_literals

from dnsdle.budget import compute_max_ciphertext_slice_bytes
from dnsdle.cli import parse_cli_args
from dnsdle.compat import encode_ascii
from dnsdle.config import build_config
from dnsdle.client_generator import generate_client_artifacts
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
from dnsdle.state import to_publish_item


class _GeneratorInput(object):

    def __init__(self, config, mapped_items):
        self.config = config
        self._mapped_items = mapped_items
        self._publish_items = None

    @property
    def publish_items(self):
        if self._publish_items is None:
            self._publish_items = tuple(
                to_publish_item(item) for item in self._mapped_items
            )
        return self._publish_items


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

    # Convergence loop: Phase 1 converges user files, Phase 2 adds client
    # scripts.  If client scripts push the combined token length past the
    # Phase 1 budget we restart with the higher requirement.
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

        # Phase 2: generate client scripts and combine
        generator_input = _GeneratorInput(config=config, mapped_items=mapped_items)
        generation_result = generate_client_artifacts(generator_input)

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

        sources = [
            (artifact["filename"], encode_ascii(artifact["source"]))
            for artifact in generation_result["artifacts"]
        ]

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

    # Partition client mapped items for stager generation
    artifact_filenames = set(
        a["filename"] for a in generation_result["artifacts"]
    )
    client_mapped_items = [
        item for item in combined_mapped
        if item["source_filename"] in artifact_filenames
    ]

    stagers = generate_stagers(config, generation_result, client_mapped_items)

    return runtime_state, generation_result, stagers
