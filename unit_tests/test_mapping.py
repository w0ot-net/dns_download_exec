from __future__ import absolute_import

import os
import shutil
import tempfile
import unittest

import dnsdle.mapping as mapping_module
from dnsdle.config import parse_cli_config
from dnsdle.mapping import apply_mapping
from dnsdle.state import StartupError


def _ascii_bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode("ascii")


def _publish_item(file_id, publish_version, total_slices):
    return {
        "file_id": file_id,
        "publish_version": publish_version,
        "plaintext_sha256": "a" * 64,
        "compressed_size": total_slices,
        "total_slices": total_slices,
        "slice_bytes_by_index": tuple(
            _ascii_bytes("x%d" % idx) for idx in range(total_slices)
        ),
        "crypto_profile": "v1",
        "wire_profile": "v1",
    }


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dnsdle_mapping_")
        self.file_path = os.path.join(self.tmpdir, "sample.bin")
        with open(self.file_path, "wb") as handle:
            handle.write(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _parse_config(self, mapping_seed="0"):
        return parse_cli_config(
            [
                "--domains",
                "example.com",
                "--files",
                self.file_path,
                "--psk",
                "k",
                "--mapping-seed",
                mapping_seed,
            ]
        )

    def test_deterministic_for_same_input(self):
        cfg = self._parse_config(mapping_seed="0")
        items = [_publish_item("1" * 16, "a" * 64, 3)]

        mapped_a = apply_mapping(items, cfg)
        mapped_b = apply_mapping(items, cfg)

        self.assertEqual(mapped_a[0]["file_tag"], mapped_b[0]["file_tag"])
        self.assertEqual(mapped_a[0]["slice_token_len"], mapped_b[0]["slice_token_len"])
        self.assertEqual(mapped_a[0]["slice_tokens"], mapped_b[0]["slice_tokens"])

    def test_mapping_seed_changes_materialized_mapping(self):
        item = _publish_item("1" * 16, "a" * 64, 3)
        mapped_a = apply_mapping([item], self._parse_config(mapping_seed="0"))
        mapped_b = apply_mapping([item], self._parse_config(mapping_seed="1"))

        self.assertNotEqual(mapped_a[0]["file_tag"], mapped_b[0]["file_tag"])
        self.assertNotEqual(mapped_a[0]["slice_tokens"], mapped_b[0]["slice_tokens"])

    def test_collision_promotion_is_deterministic(self):
        cfg = self._parse_config(mapping_seed="0")
        shared_version = "b" * 64
        items = [
            _publish_item("0" * 16, shared_version, 1),
            _publish_item("f" * 16, shared_version, 1),
        ]

        mapped = apply_mapping(items, cfg)
        by_file_id = {}
        keys = []
        for entry in mapped:
            by_file_id[entry["file_id"]] = entry
            for token in entry["slice_tokens"]:
                keys.append((entry["file_tag"], token))

        self.assertEqual(2, by_file_id["0" * 16]["slice_token_len"])
        self.assertEqual(1, by_file_id["f" * 16]["slice_token_len"])
        self.assertEqual(len(keys), len(set(keys)))

    def test_unresolved_collision_raises_when_max_len_exhausted(self):
        cfg = self._parse_config(mapping_seed="0")
        shared_version = "c" * 64
        items = [
            _publish_item("0" * 16, shared_version, 1),
            _publish_item("f" * 16, shared_version, 1),
        ]

        original = mapping_module._max_token_len_for_file
        mapping_module._max_token_len_for_file = lambda _cfg, _tag: 1
        try:
            with self.assertRaises(StartupError) as ctx:
                apply_mapping(items, cfg)
        finally:
            mapping_module._max_token_len_for_file = original

        self.assertEqual("mapping", ctx.exception.phase)
        self.assertEqual("mapping_collision", ctx.exception.reason_code)

    def test_rejects_when_qname_limits_disallow_even_shortest_token(self):
        longest_domain = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 52))
        cfg = parse_cli_config(
            [
                "--domains",
                longest_domain,
                "--files",
                self.file_path,
                "--psk",
                "k",
                "--file-tag-len",
                "16",
            ]
        )

        items = [_publish_item("1" * 16, "d" * 64, 1)]
        with self.assertRaises(StartupError) as ctx:
            apply_mapping(items, cfg)

        self.assertEqual("mapping", ctx.exception.phase)
        self.assertEqual("mapping_capacity_exceeded", ctx.exception.reason_code)
        self.assertIn("QNAME limits do not allow", ctx.exception.message)
        self.assertEqual("1" * 16, ctx.exception.context.get("file_id"))

    def test_multi_domain_mapping_clamps_to_longest_domain(self):
        longest_domain = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 52))
        cfg = parse_cli_config(
            [
                "--domains",
                "example.com,%s" % longest_domain,
                "--files",
                self.file_path,
                "--psk",
                "k",
                "--file-tag-len",
                "16",
            ]
        )
        self.assertEqual(tuple(sorted(("example.com", longest_domain))), cfg.domains)
        self.assertEqual(longest_domain, cfg.longest_domain)

        items = [_publish_item("1" * 16, "e" * 64, 1)]
        with self.assertRaises(StartupError) as ctx:
            apply_mapping(items, cfg)

        self.assertEqual("mapping", ctx.exception.phase)
        self.assertEqual("mapping_capacity_exceeded", ctx.exception.reason_code)
        self.assertIn("QNAME limits do not allow", ctx.exception.message)

    def test_rejects_local_collisions_when_token_len_cap_is_too_small(self):
        cfg = self._parse_config(mapping_seed="0")
        items = [_publish_item("1" * 16, "f" * 64, 33)]

        original = mapping_module._max_token_len_for_file
        mapping_module._max_token_len_for_file = lambda _cfg, _tag: 1
        try:
            with self.assertRaises(StartupError) as ctx:
                apply_mapping(items, cfg)
        finally:
            mapping_module._max_token_len_for_file = original

        self.assertEqual("mapping", ctx.exception.phase)
        self.assertEqual("mapping_collision", ctx.exception.reason_code)
        self.assertIn("unable to resolve local slice-token collisions", ctx.exception.message)
        self.assertEqual("1" * 16, ctx.exception.context.get("file_id"))


if __name__ == "__main__":
    unittest.main()
