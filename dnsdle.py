#!/usr/bin/env python
from __future__ import print_function

import sys

from dnsdle import build_startup_state
from dnsdle import serve_runtime
from dnsdle.logging_runtime import emit_structured_record
from dnsdle.logging_runtime import reset_active_logger
from dnsdle.state import StartupError


def _emit_record(record, level=None, category=None, required=False):
    emit_structured_record(
        record,
        level=level,
        category=category,
        required=required,
    )


def main(argv=None):
    reset_active_logger()
    try:
        runtime_state, generation_result, stagers = build_startup_state(argv)
    except StartupError as exc:
        _emit_record(
            exc.to_log_record(),
            level="error",
            category=exc.phase,
            required=True,
        )
        return 1
    except Exception as exc:
        _emit_record(
            {
                "classification": "startup_error",
                "phase": "startup",
                "reason_code": "unexpected_exception",
                "message": str(exc),
            },
            level="error",
            category="startup",
            required=True,
        )
        return 1

    for artifact in generation_result["artifacts"]:
        _emit_record(
            {
                "classification": "generation_ok",
                "phase": "publish",
                "reason_code": "generation_ok",
                "file_id": artifact["file_id"],
                "publish_version": artifact["publish_version"],
                "file_tag": artifact["file_tag"],
                "target_os": artifact["target_os"],
                "path": artifact["path"],
            },
            level="info",
            category="publish",
        )

    _emit_record(
        {
            "classification": "generation_summary",
            "phase": "startup",
            "reason_code": "generation_summary",
            "managed_dir": generation_result["managed_dir"],
            "artifact_count": generation_result["artifact_count"],
            "target_os": ",".join(generation_result["target_os"]),
            "file_ids": sorted(set(item["file_id"] for item in generation_result["artifacts"])),
        },
        level="info",
        category="startup",
    )

    for stager in stagers:
        _emit_record(
            {
                "classification": "stager_ready",
                "phase": "startup",
                "reason_code": "stager_ready",
                "source_filename": stager["source_filename"],
                "target_os": stager["target_os"],
                "oneliner": stager["oneliner"],
            },
            level="info",
            category="startup",
        )

    config = runtime_state.config
    _emit_record(
        {
            "classification": "startup_ok",
            "phase": "startup",
            "domains": list(config.domains),
            "longest_domain": config.longest_domain,
            "file_count": len(runtime_state.publish_items),
            "max_ciphertext_slice_bytes": runtime_state.max_ciphertext_slice_bytes,
            "dns_edns_size": config.dns_edns_size,
            "dns_max_label_len": config.dns_max_label_len,
            "compression_level": config.compression_level,
            "target_os": config.target_os_csv,
        },
        level="info",
        category="startup",
    )

    for publish_item in runtime_state.publish_items:
        _emit_record(
            {
                "classification": "startup_ok",
                "phase": "publish",
                "file_id": publish_item.file_id,
                "publish_version": publish_item.publish_version,
                "plaintext_sha256": publish_item.plaintext_sha256,
                "file_tag": publish_item.file_tag,
                "compressed_size": publish_item.compressed_size,
                "total_slices": publish_item.total_slices,
                "slice_token_len": publish_item.slice_token_len,
            },
            level="info",
            category="publish",
        )

    try:
        return serve_runtime(runtime_state, _emit_record)
    except StartupError as exc:
        _emit_record(
            exc.to_log_record(),
            level="error",
            category=exc.phase,
            required=True,
        )
        return 1
    except Exception as exc:
        _emit_record(
            {
                "classification": "startup_error",
                "phase": "server",
                "reason_code": "unexpected_exception",
                "message": str(exc),
            },
            level="error",
            category="server",
            required=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
