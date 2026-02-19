from __future__ import absolute_import

import os
import shutil
import tempfile
import unittest
import zlib

from dnsdle.cli import parse_cli_args
from dnsdle.config import build_config
from dnsdle.publish import build_publish_items
from dnsdle.state import StartupError


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dnsdle_publish_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_file(self, name, data):
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _parse_config(self, paths, compression_level=9):
        return build_config(
            parse_cli_args(
                [
                    "--domains",
                    "example.com",
                    "--files",
                    ",".join(paths),
                    "--psk",
                    "k",
                    "--compression-level",
                    str(compression_level),
                ]
            )
        )

    def test_build_publish_items_chunks_and_metadata(self):
        payload = os.urandom(1024)
        file_path = self._write_file("sample.bin", payload)
        cfg = self._parse_config([file_path], compression_level=9)

        max_slice = 64
        items = build_publish_items(cfg, max_slice)
        self.assertEqual(1, len(items))
        item = items[0]

        recombined = b"".join(item["slice_bytes_by_index"])
        self.assertGreater(item["total_slices"], 1)
        self.assertEqual(len(item["slice_bytes_by_index"]), item["total_slices"])
        self.assertEqual(recombined, zlib.compress(payload, cfg.compression_level))
        self.assertEqual(item["compressed_size"], len(recombined))
        self.assertEqual(16, len(item["file_id"]))
        self.assertEqual(64, len(item["publish_version"]))
        self.assertEqual(64, len(item["plaintext_sha256"]))
        self.assertEqual("v1", item["crypto_profile"])
        self.assertEqual("v1", item["wire_profile"])

    def test_rejects_duplicate_plaintext_content(self):
        payload = b"same-content"
        file_a = self._write_file("a.bin", payload)
        file_b = self._write_file("b.bin", payload)
        cfg = self._parse_config([file_a, file_b], compression_level=9)

        with self.assertRaises(StartupError) as ctx:
            build_publish_items(cfg, 64)

        self.assertEqual("publish", ctx.exception.phase)
        self.assertEqual("duplicate_plaintext_sha256", ctx.exception.reason_code)

    def test_rejects_non_positive_budget(self):
        file_path = self._write_file("sample.bin", b"x")
        cfg = self._parse_config([file_path])

        with self.assertRaises(StartupError) as ctx:
            build_publish_items(cfg, 0)

        self.assertEqual("publish", ctx.exception.phase)
        self.assertEqual("budget_unusable", ctx.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
