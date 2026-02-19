from __future__ import absolute_import

import unittest

from dnsdle.state import StartupError
from dnsdle.state import build_runtime_state


def _mapped_item(file_id, file_tag, tokens):
    return {
        "file_id": file_id,
        "publish_version": "a" * 64,
        "file_tag": file_tag,
        "plaintext_sha256": "b" * 64,
        "compressed_size": len(tokens),
        "total_slices": len(tokens),
        "slice_token_len": len(tokens[0]) if tokens else 1,
        "slice_tokens": tuple(tokens),
        "slice_bytes_by_index": tuple(b"x" for _ in tokens),
        "crypto_profile": "v1",
        "wire_profile": "v1",
    }


class StateTests(unittest.TestCase):
    def test_build_runtime_state_creates_immutable_views(self):
        mapped_items = [_mapped_item("1" * 16, "tag001", ("a", "b"))]
        runtime = build_runtime_state(
            config=None,
            mapped_publish_items=mapped_items,
            max_ciphertext_slice_bytes=123,
            budget_info={"query_token_len": 1},
        )

        self.assertEqual(1, len(runtime.publish_items))
        self.assertEqual(2, len(runtime.lookup_by_key))
        self.assertEqual(("1" * 16, "a" * 64, 0), runtime.lookup_by_key[("tag001", "a")])

        with self.assertRaises(TypeError):
            runtime.lookup_by_key[("tag001", "c")] = ("x", "y", 2)
        with self.assertRaises(TypeError):
            runtime.budget_info["query_token_len"] = 2

    def test_build_runtime_state_rejects_lookup_collisions(self):
        mapped_items = [
            _mapped_item("1" * 16, "tag001", ("a",)),
            _mapped_item("2" * 16, "tag001", ("a",)),
        ]

        with self.assertRaises(StartupError) as ctx:
            build_runtime_state(
                config=None,
                mapped_publish_items=mapped_items,
                max_ciphertext_slice_bytes=123,
                budget_info={"query_token_len": 1},
            )

        self.assertEqual("mapping", ctx.exception.phase)
        self.assertEqual("mapping_collision", ctx.exception.reason_code)

    def test_startup_error_log_record_preserves_core_fields(self):
        err = StartupError(
            "phase_a",
            "reason_x",
            "message_text",
            {"phase": "ignored", "reason_code": "ignored", "extra": "ok"},
        )
        record = err.to_log_record()

        self.assertEqual("startup_error", record["classification"])
        self.assertEqual("phase_a", record["phase"])
        self.assertEqual("reason_x", record["reason_code"])
        self.assertEqual("message_text", record["message"])
        self.assertEqual("ok", record["extra"])


if __name__ == "__main__":
    unittest.main()
