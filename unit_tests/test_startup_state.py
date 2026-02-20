from __future__ import absolute_import

import os
import random
import shutil
import tempfile
import unittest

from dnsdle import build_startup_state
from dnsdle.state import StartupError


class StartupStateTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dnsdle_startup_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write_file(self, name, data):
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_build_startup_state_end_to_end_single_file(self):
        rng = random.Random(42)
        payload = bytes(bytearray(rng.randint(0, 255) for _ in range(10000)))
        file_path = self._write_file("sample.bin", payload)
        client_out = os.path.join(self.tmpdir, "clients_out")

        runtime, generation_result, stagers = build_startup_state(
            [
                "--domains",
                "example.com",
                "--files",
                file_path,
                "--psk",
                "k",
                "--client-out-dir",
                client_out,
            ]
        )

        user_file_count = 1
        target_os_count = len(runtime.config.target_os)
        expected_count = user_file_count + user_file_count * target_os_count
        self.assertEqual(expected_count, len(runtime.publish_items))

        item = runtime.publish_items[0]
        self.assertGreater(item.total_slices, 0)
        self.assertEqual(item.total_slices, len(item.slice_tokens))
        self.assertEqual(item.total_slices, len(item.slice_bytes_by_index))
        self.assertEqual(item.compressed_size, len(b"".join(item.slice_bytes_by_index)))

        for index, token in enumerate(item.slice_tokens):
            key = (item.file_tag, token)
            self.assertIn(key, runtime.lookup_by_key)
            lookup_value = runtime.lookup_by_key[key]
            self.assertEqual((item.file_id, item.publish_version, index), lookup_value)

    def test_build_startup_state_rejects_duplicate_plaintext(self):
        payload = b"duplicate-content"
        file_a = self._write_file("a.bin", payload)
        file_b = self._write_file("b.bin", payload)

        with self.assertRaises(StartupError) as ctx:
            build_startup_state(
                [
                    "--domains",
                    "example.com",
                    "--files",
                    "%s,%s" % (file_a, file_b),
                    "--psk",
                    "k",
                ]
            )

        self.assertEqual("publish", ctx.exception.phase)
        self.assertEqual("duplicate_plaintext_sha256", ctx.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
