from __future__ import absolute_import

import os
import shutil
import tempfile
import unittest

from dnsdle.config import parse_cli_config
from dnsdle.state import StartupError


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
        cfg = parse_cli_config(
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
        self.assertEqual(("api", "example", "net"), cfg.longest_domain_labels)
        self.assertEqual("api.example.net", cfg.longest_domain)
        self.assertEqual("0.0.0.0", cfg.listen_host)
        self.assertEqual(53, cfg.listen_port)
        self.assertEqual(1232, cfg.dns_edns_size)
        self.assertEqual("0", cfg.mapping_seed)
        self.assertEqual(("windows", "linux"), cfg.target_os)
        self.assertEqual("windows,linux", cfg.target_os_csv)

    def test_rejects_overlapping_domains(self):
        args = self._base_args()
        args[1] = "example.com,sub.example.com"

        with self.assertRaises(StartupError) as ctx:
            parse_cli_config(args)

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("overlapping_domains", ctx.exception.reason_code)

    def test_rejects_token_only_response_label(self):
        args = self._base_args() + ["--response-label", "abc123"]

        with self.assertRaises(StartupError) as ctx:
            parse_cli_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)

    def test_rejects_non_printable_mapping_seed(self):
        args = self._base_args() + ["--mapping-seed", "bad\nseed"]

        with self.assertRaises(StartupError) as ctx:
            parse_cli_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)

    def test_rejects_invalid_dns_max_label_len(self):
        args = self._base_args() + ["--dns-max-label-len", "15"]

        with self.assertRaises(StartupError) as ctx:
            parse_cli_config(args)

        self.assertEqual("invalid_config", ctx.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
