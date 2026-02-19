from __future__ import absolute_import

import os
import shutil
import tempfile
import unittest

from dnsdle.cli import parse_cli_args
from dnsdle.config import build_config
from dnsdle.state import StartupError


def _build_config(argv):
    return build_config(parse_cli_args(argv))


class ConfigParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dnsdle_config_")
        self.sample_file = os.path.join(self.tmpdir, "sample.bin")
        with open(self.sample_file, "wb") as handle:
            handle.write(b"hello")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _base_args(self):
        return [
            "--domains",
            "example.com",
            "--files",
            self.sample_file,
            "--psk",
            "secret",
        ]

    def test_parses_and_normalizes_domains_and_defaults(self):
        cfg = _build_config(
            [
                "--domains",
                "Example.COM.,api.Example.net.",
                "--files",
                self.sample_file,
                "--psk",
                "secret",
            ]
        )

        self.assertEqual(("api.example.net", "example.com"), cfg.domains)
        self.assertEqual(
            (("api", "example", "net"), ("example", "com")),
            cfg.domain_labels_by_domain,
        )
        self.assertEqual(("api", "example", "net"), cfg.longest_domain_labels)
        self.assertEqual("api.example.net", cfg.longest_domain)
        self.assertEqual("0.0.0.0", cfg.listen_host)
        self.assertEqual(53, cfg.listen_port)
        self.assertEqual(1232, cfg.dns_edns_size)
        self.assertEqual("0", cfg.mapping_seed)
        self.assertEqual(("windows", "linux"), cfg.target_os)
        self.assertEqual("windows,linux", cfg.target_os_csv)
        self.assertEqual("info", cfg.log_level)
        self.assertEqual(("startup", "publish", "server"), cfg.log_categories)
        self.assertEqual(1.0, cfg.log_sample_rate)
        self.assertEqual(200, cfg.log_rate_limit_per_sec)
        self.assertEqual("stdout", cfg.log_output)
        self.assertEqual("", cfg.log_file)
        self.assertEqual("", cfg.log_focus)

    def test_rejects_overlapping_domains(self):
        args = self._base_args()
        args[1] = "example.com,sub.example.com"

        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("overlapping_domains", ctx.exception.reason_code)

    def test_rejects_duplicate_normalized_domains(self):
        args = self._base_args()
        args[1] = "EXAMPLE.com,example.com."

        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("duplicate_domain", ctx.exception.reason_code)
        self.assertEqual("example.com", ctx.exception.context.get("domain"))

    def test_rejects_domains_with_empty_entry(self):
        args = self._base_args()
        args[1] = "example.com,,hello.com"

        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("invalid_domains", ctx.exception.reason_code)
        self.assertIn("empty entry", ctx.exception.message)

    def test_rejects_legacy_domain_flag(self):
        with self.assertRaises(StartupError) as ctx:
            _build_config(
                [
                    "--domain",
                    "example.com",
                    "--files",
                    self.sample_file,
                    "--psk",
                    "secret",
                ]
            )

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertIn("--domain is removed; use --domains", ctx.exception.message)

    def test_selects_longest_domain_even_when_not_first_in_canonical_order(self):
        longer = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz.com"
        cfg = _build_config(
            [
                "--domains",
                "a.com,%s" % longer,
                "--files",
                self.sample_file,
                "--psk",
                "secret",
            ]
        )

        self.assertEqual(("a.com", longer), cfg.domains)
        self.assertEqual(longer, cfg.longest_domain)
        self.assertEqual(tuple(longer.split(".")), cfg.longest_domain_labels)
        short_wire_len = 1 + sum(1 + len(label) for label in ("a", "com"))
        self.assertGreater(cfg.longest_domain_wire_len, short_wire_len)

    def test_rejects_response_suffix_exceeding_dns_limit_for_longest_domain(self):
        longest_domain = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 61))
        args = self._base_args()
        args[1] = longest_domain

        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertIn("response suffix exceeds DNS name-length limits", ctx.exception.message)
        self.assertEqual(longest_domain, ctx.exception.context.get("longest_domain"))

    def test_rejects_token_only_response_label(self):
        args = self._base_args() + ["--response-label", "abc123"]

        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)

    def test_rejects_non_printable_mapping_seed(self):
        args = self._base_args() + ["--mapping-seed", "bad\nseed"]

        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)

    def test_rejects_invalid_dns_max_label_len(self):
        args = self._base_args() + ["--dns-max-label-len", "15"]

        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)

    def test_accepts_explicit_logging_configuration(self):
        log_file = os.path.join(self.tmpdir, "dnsdle.log")
        cfg = _build_config(
            self._base_args()
            + [
                "--log-level",
                "debug",
                "--log-categories",
                "all",
                "--log-sample-rate",
                "0.5",
                "--log-rate-limit-per-sec",
                "17",
                "--log-output",
                "file",
                "--log-file",
                log_file,
                "--log-focus",
                "tag001",
            ]
        )

        self.assertEqual("debug", cfg.log_level)
        self.assertIn("server", cfg.log_categories)
        self.assertEqual(0.5, cfg.log_sample_rate)
        self.assertEqual(17, cfg.log_rate_limit_per_sec)
        self.assertEqual("file", cfg.log_output)
        self.assertEqual(log_file, cfg.log_file)
        self.assertEqual("tag001", cfg.log_focus)

    def test_rejects_invalid_log_level(self):
        args = self._base_args() + ["--log-level", "verbose"]
        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertIn("log_level", ctx.exception.message)

    def test_rejects_invalid_log_categories(self):
        args = self._base_args() + ["--log-categories", "startup,unknown"]
        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertEqual("log_categories", ctx.exception.context.get("field"))

    def test_rejects_file_output_without_log_file(self):
        args = self._base_args() + ["--log-output", "file"]
        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertEqual("log_file", ctx.exception.context.get("field"))

    def test_rejects_log_file_when_output_is_stdout(self):
        args = self._base_args() + ["--log-file", "/tmp/not-used.log"]
        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertEqual("log_file", ctx.exception.context.get("field"))

    def test_rejects_invalid_log_sample_rate(self):
        args = self._base_args() + ["--log-sample-rate", "1.5"]
        with self.assertRaises(StartupError) as ctx:
            _build_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertEqual("log_sample_rate", ctx.exception.context.get("field"))


if __name__ == "__main__":
    unittest.main()
