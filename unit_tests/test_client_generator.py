from __future__ import absolute_import

import unittest

from dnsdle.client_generator import _build_artifacts
from dnsdle.client_generator import _render_client_source
from dnsdle.config import Config
from dnsdle.state import PublishItem
from dnsdle.state import RuntimeState
from dnsdle.state import StartupError


def _make_config(**overrides):
    defaults = {
        "domains": ("example.com",),
        "domain_labels_by_domain": (("example", "com"),),
        "longest_domain": "example.com",
        "longest_domain_labels": ("example", "com"),
        "longest_domain_wire_len": 13,
        "files": ("/tmp/a.bin",),
        "psk": "testpsk",
        "listen_addr": "0.0.0.0:53",
        "listen_host": "0.0.0.0",
        "listen_port": 53,
        "ttl": 30,
        "dns_edns_size": 1232,
        "dns_max_label_len": 63,
        "response_label": "r-x",
        "mapping_seed": "0",
        "file_tag_len": 6,
        "target_os": ("linux",),
        "target_os_csv": "linux",
        "client_out_dir": "/tmp/out",
        "compression_level": 9,
        "log_level": "info",
        "log_categories": ("startup", "publish", "server"),
        "log_sample_rate": 1.0,
        "log_rate_limit_per_sec": 200,
        "log_output": "stdout",
        "log_file": "",
        "log_focus": "",
        "fixed": {},
    }
    defaults.update(overrides)
    return Config(**defaults)


def _make_publish_item(**overrides):
    defaults = {
        "file_id": "a" * 16,
        "publish_version": "b" * 64,
        "file_tag": "abc123",
        "plaintext_sha256": "c" * 64,
        "compressed_size": 100,
        "total_slices": 1,
        "slice_token_len": 4,
        "slice_tokens": ("tok1",),
        "slice_bytes_by_index": (b"data",),
        "crypto_profile": "v1",
        "wire_profile": "v1",
        "source_filename": "test.bin",
    }
    defaults.update(overrides)
    return PublishItem(**defaults)


def _make_runtime_state(config=None, publish_items=None):
    if config is None:
        config = _make_config()
    if publish_items is None:
        publish_items = (_make_publish_item(),)
    return RuntimeState(
        config=config,
        max_ciphertext_slice_bytes=200,
        budget_info={},
        publish_items=publish_items,
        lookup_by_key={},
        slice_bytes_by_identity={},
        publish_meta_by_identity={},
    )


class BuildArtifactsTests(unittest.TestCase):
    def test_happy_path_single_artifact(self):
        state = _make_runtime_state()
        artifacts = _build_artifacts(state)
        self.assertEqual(1, len(artifacts))
        artifact = artifacts[0]
        self.assertEqual("a" * 16, artifact["file_id"])
        self.assertEqual("abc123", artifact["file_tag"])
        self.assertEqual("linux", artifact["target_os"])
        self.assertIn("source", artifact)
        self.assertTrue(len(artifact["source"]) > 0)

    def test_publish_version_in_artifact_dict(self):
        state = _make_runtime_state()
        artifacts = _build_artifacts(state)
        self.assertEqual("b" * 64, artifacts[0]["publish_version"])

    def test_multi_os_cardinality(self):
        config = _make_config(target_os=("linux", "windows"))
        state = _make_runtime_state(config=config)
        artifacts = _build_artifacts(state)
        self.assertEqual(2, len(artifacts))
        os_values = sorted(a["target_os"] for a in artifacts)
        self.assertEqual(["linux", "windows"], os_values)

    def test_multi_file_cardinality(self):
        item_a = _make_publish_item(file_id="a" * 16, file_tag="aaa111")
        item_b = _make_publish_item(
            file_id="d" * 16, file_tag="bbb222",
            publish_version="e" * 64,
            plaintext_sha256="f" * 64,
            slice_tokens=("tok2",),
        )
        state = _make_runtime_state(publish_items=(item_a, item_b))
        artifacts = _build_artifacts(state)
        self.assertEqual(2, len(artifacts))

    def test_rejects_filename_collision(self):
        item_a = _make_publish_item()
        item_b = _make_publish_item()  # same file_id/file_tag produces same filename
        state = _make_runtime_state(publish_items=(item_a, item_b))
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)
        self.assertIn("filename collision", raised.exception.message)

    def test_rejects_empty_domains(self):
        config = _make_config(domains=())
        state = _make_runtime_state(config=config)
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)

    def test_rejects_empty_response_label(self):
        config = _make_config(response_label="")
        state = _make_runtime_state(config=config)
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)

    def test_rejects_unsupported_target_os(self):
        config = _make_config(target_os=("macos",))
        state = _make_runtime_state(config=config)
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)


class RenderClientSourceTests(unittest.TestCase):
    def test_happy_path_produces_ascii_source(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "linux")
        self.assertIsInstance(source, str)
        source.encode("ascii")  # must not raise

    def test_no_unreplaced_placeholders(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "linux")
        self.assertNotIn("@@", source)

    def test_embedded_constants_present(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "linux")
        self.assertIn(repr(("example.com",)), source)
        self.assertIn(repr("abc123"), source)
        self.assertIn(repr("a" * 16), source)
        self.assertIn(repr("b" * 64), source)
        self.assertIn(repr("linux"), source)

    def test_source_filename_present(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "linux")
        self.assertIn("SOURCE_FILENAME = 'test.bin'", source)

    def test_no_authoritative_only_checks_in_source(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "linux")
        self.assertNotIn("DNS_FLAG_AA", source)
        self.assertNotIn("DNS_FLAG_RA", source)

    def test_rcode_checked_before_qdcount_in_source(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "linux")
        rcode_pos = source.find("rcode != DNS_RCODE_NOERROR")
        qdcount_pos = source.find("qdcount != 1")
        self.assertGreater(rcode_pos, 0, "rcode check not found in source")
        self.assertGreater(qdcount_pos, 0, "qdcount check not found in source")
        self.assertLess(rcode_pos, qdcount_pos,
                        "rcode check must appear before qdcount check")

    def test_windows_happy_path_produces_ascii_source(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "windows")
        self.assertIsInstance(source, str)
        source.encode("ascii")  # must not raise

    def test_windows_no_unreplaced_placeholders(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "windows")
        self.assertNotIn("@@", source)

    def test_windows_includes_subprocess_and_ipv4re(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "windows")
        self.assertIn("import subprocess", source)
        self.assertIn("_IPV4_RE", source)
        self.assertNotIn("_load_unix_resolvers", source)

    def test_linux_excludes_subprocess_and_ipv4re(self):
        config = _make_config()
        item = _make_publish_item()
        source = _render_client_source(config, item, "linux")
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("_IPV4_RE", source)
        self.assertNotIn("_load_windows_resolvers", source)


class ValidatePublishItemTests(unittest.TestCase):
    def test_rejects_missing_file_id(self):
        item = _make_publish_item(file_id="")
        config = _make_config()
        state = _make_runtime_state(config=config, publish_items=(item,))
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)

    def test_rejects_zero_total_slices(self):
        item = _make_publish_item(total_slices=0, slice_tokens=())
        config = _make_config()
        state = _make_runtime_state(config=config, publish_items=(item,))
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)

    def test_rejects_slice_token_count_mismatch(self):
        item = _make_publish_item(total_slices=2, slice_tokens=("tok1",))
        config = _make_config()
        state = _make_runtime_state(config=config, publish_items=(item,))
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)

    def test_rejects_duplicate_slice_tokens(self):
        item = _make_publish_item(
            total_slices=2, slice_tokens=("tok1", "tok1"),
        )
        config = _make_config()
        state = _make_runtime_state(config=config, publish_items=(item,))
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)

    def test_rejects_missing_crypto_profile(self):
        item = _make_publish_item(crypto_profile="")
        config = _make_config()
        state = _make_runtime_state(config=config, publish_items=(item,))
        with self.assertRaises(StartupError) as raised:
            _build_artifacts(state)
        self.assertEqual("generator_invalid_contract", raised.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
