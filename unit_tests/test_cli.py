from __future__ import absolute_import

import os
import shutil
import tempfile
import unittest

from dnsdle.cli import parse_cli_args
from dnsdle.state import StartupError


class CliParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dnsdle_cli_")
        self.sample_file = os.path.join(self.tmpdir, "sample.bin")
        with open(self.sample_file, "wb") as handle:
            handle.write(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _valid_args(self):
        return [
            "--domains",
            "Example.COM.,api.Example.net.",
            "--files",
            self.sample_file,
            "--psk",
            "secret",
        ]

    def test_rejects_removed_domain_flag(self):
        with self.assertRaises(StartupError) as ctx:
            parse_cli_args(
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

    def test_rejects_removed_domain_flag_equals_form(self):
        with self.assertRaises(StartupError) as ctx:
            parse_cli_args(
                [
                    "--domain=example.com",
                    "--files",
                    self.sample_file,
                    "--psk",
                    "secret",
                ]
            )

        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertIn("--domain is removed; use --domains", ctx.exception.message)

    def test_rejects_abbreviated_long_option(self):
        with self.assertRaises(StartupError) as ctx:
            parse_cli_args(
                [
                    "--dom",
                    "example.com",
                    "--files",
                    self.sample_file,
                    "--psk",
                    "secret",
                ]
            )

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertIn("unrecognized arguments", ctx.exception.message)
        self.assertIn("--dom", ctx.exception.message)

    def test_rejects_unknown_long_option(self):
        with self.assertRaises(StartupError) as ctx:
            parse_cli_args(self._valid_args() + ["--unknown-flag"])

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertIn("unrecognized arguments", ctx.exception.message)
        self.assertIn("--unknown-flag", ctx.exception.message)

    def test_invalid_syntax_raises_startup_error_not_system_exit(self):
        with self.assertRaises(StartupError) as ctx:
            parse_cli_args(["--domains"])

        self.assertEqual("config", ctx.exception.phase)
        self.assertEqual("invalid_config", ctx.exception.reason_code)
        self.assertIn("argument parsing failed", ctx.exception.message)

    def test_valid_parse_preserves_raw_values(self):
        parsed = parse_cli_args(
            self._valid_args()
            + [
                "--ttl",
                "42",
                "--log-level",
                "debug",
                "--log-categories",
                "startup,server",
                "--log-sample-rate",
                "0.25",
                "--log-rate-limit-per-sec",
                "7",
                "--log-output",
                "file",
                "--log-file",
                "/tmp/dnsdle.log",
                "--log-focus",
                "tag001",
            ]
        )

        self.assertEqual("Example.COM.,api.Example.net.", parsed.domains)
        self.assertEqual(self.sample_file, parsed.files)
        self.assertEqual("secret", parsed.psk)
        self.assertEqual("42", parsed.ttl)
        self.assertEqual("1232", parsed.dns_edns_size)
        self.assertEqual("debug", parsed.log_level)
        self.assertEqual("startup,server", parsed.log_categories)
        self.assertEqual("0.25", parsed.log_sample_rate)
        self.assertEqual("7", parsed.log_rate_limit_per_sec)
        self.assertEqual("file", parsed.log_output)
        self.assertEqual("/tmp/dnsdle.log", parsed.log_file)
        self.assertEqual("tag001", parsed.log_focus)


if __name__ == "__main__":
    unittest.main()
