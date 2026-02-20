from __future__ import absolute_import

import base64
import os
import shutil
import tempfile
import unittest
import zlib

from dnsdle.stager_generator import generate_stager
from dnsdle.stager_generator import generate_stagers
from dnsdle.stager_template import build_stager_template
from dnsdle.state import StartupError


def _make_config():
    """Minimal config-like object with required attributes."""

    class _Config(object):
        domain_labels_by_domain = [("example", "com")]
        response_label = "r"
        dns_edns_size = 1232

    return _Config()


def _make_publish_item(source_filename="client.py"):
    return {
        "source_filename": source_filename,
        "file_tag": "tag001",
        "file_id": "file001",
        "publish_version": "v1",
        "total_slices": 2,
        "compressed_size": 100,
        "plaintext_sha256": "a" * 64,
        "slice_tokens": ("tok0", "tok1"),
    }


class StagerGeneratorTests(unittest.TestCase):

    def test_happy_path_returns_expected_keys(self):
        config = _make_config()
        template = build_stager_template()
        item = _make_publish_item()

        result = generate_stager(config, template, item, "linux")

        self.assertEqual("client.py", result["source_filename"])
        self.assertEqual("linux", result["target_os"])
        self.assertIn("python3 -c", result["oneliner"])
        self.assertIn("RESOLVER PSK", result["oneliner"])
        compile(result["minified_source"], "<test>", "exec")

    def test_unreplaced_placeholder_raises(self):
        config = _make_config()
        template = build_stager_template() + "\n@@UNKNOWN@@\n"
        item = _make_publish_item()

        with self.assertRaises(StartupError) as ctx:
            generate_stager(config, template, item, "linux")
        self.assertEqual("stager_generation_failed", ctx.exception.reason_code)

    def test_compile_failure_raises(self):
        config = _make_config()
        # A template that substitutes to invalid syntax.
        template = "def (@@DOMAIN_LABELS@@"
        item = _make_publish_item()

        with self.assertRaises(StartupError) as ctx:
            generate_stager(config, template, item, "linux")
        self.assertEqual("stager_generation_failed", ctx.exception.reason_code)

    def test_round_trip_integrity(self):
        config = _make_config()
        template = build_stager_template()
        item = _make_publish_item()

        result = generate_stager(config, template, item, "linux")

        # Extract the base64 payload from the oneliner.
        oneliner = result["oneliner"]
        start = oneliner.index("'") + 1
        end = oneliner.index("'", start)
        payload_b64 = oneliner[start:end]

        recovered = zlib.decompress(base64.b64decode(payload_b64))
        self.assertEqual(result["minified_source"].encode("ascii"), recovered)


class GenerateStagersBatchTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_batch_happy_path(self):
        config = _make_config()
        item = _make_publish_item("client.py")
        generation_result = {
            "managed_dir": self.tmpdir,
            "artifacts": [{"filename": "client.py", "target_os": "linux"}],
        }

        stagers = generate_stagers(config, generation_result, [item])

        self.assertEqual(1, len(stagers))
        self.assertIn("path", stagers[0])
        self.assertTrue(os.path.isfile(stagers[0]["path"]))

    def test_missing_client_item_raises(self):
        config = _make_config()
        generation_result = {
            "managed_dir": self.tmpdir,
            "artifacts": [{"filename": "missing.py", "target_os": "linux"}],
        }

        with self.assertRaises(StartupError) as ctx:
            generate_stagers(config, generation_result, [])
        self.assertEqual("stager_generation_failed", ctx.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
