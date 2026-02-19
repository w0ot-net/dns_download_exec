#!/usr/bin/env python
from __future__ import print_function

import json
import sys

from dnsdle import build_startup_state
from dnsdle.state import StartupError


def _emit_record(record):
    print(json.dumps(record, sort_keys=True))


def main(argv=None):
    try:
        runtime_state = build_startup_state(argv)
    except StartupError as exc:
        _emit_record(exc.to_log_record())
        return 1
    except Exception as exc:
        _emit_record(
            {
                "classification": "startup_error",
                "phase": "startup",
                "reason_code": "unexpected_exception",
                "message": str(exc),
            }
        )
        return 1

    config = runtime_state.config
    _emit_record(
        {
            "classification": "startup_ok",
            "phase": "startup",
            "domain": config.domain,
            "file_count": len(runtime_state.publish_items),
            "max_ciphertext_slice_bytes": runtime_state.max_ciphertext_slice_bytes,
            "dns_edns_size": config.dns_edns_size,
            "dns_max_label_len": config.dns_max_label_len,
            "compression_level": config.compression_level,
            "target_os": config.target_os_csv,
        }
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
            }
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
