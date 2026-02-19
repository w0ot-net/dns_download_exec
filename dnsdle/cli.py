from __future__ import absolute_import

import argparse
import sys

from dnsdle.constants import DEFAULT_LOG_FILE
from dnsdle.constants import DEFAULT_LOG_LEVEL
from dnsdle.state import StartupError


_LONG_OPTIONS = (
    "--domains",
    "--files",
    "--psk",
    "--listen-addr",
    "--ttl",
    "--dns-edns-size",
    "--dns-max-label-len",
    "--response-label",
    "--mapping-seed",
    "--file-tag-len",
    "--target-os",
    "--client-out-dir",
    "--compression-level",
    "--log-level",
    "--log-file",
    "--help",
)
_KNOWN_LONG_OPTIONS = set(_LONG_OPTIONS)


class _RaisingArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise StartupError(
            "config",
            "invalid_config",
            "argument parsing failed: %s" % message,
        )

    def print_help(self, file=None):
        if file is None:
            file = sys.stdout
        text = self.format_help()
        if hasattr(file, "isatty") and file.isatty():
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line and not line[0].isspace() and line.endswith(":"):
                    lines[i] = "\033[1;36m%s\033[0m" % line
            text = "\n".join(lines)
        file.write(text)


def _raw_argv(argv):
    if argv is None:
        return list(sys.argv[1:])
    return list(argv)


def _validate_long_option_tokens(raw_argv):
    for token in raw_argv:
        if token == "--":
            break
        if not token.startswith("--"):
            continue

        option = token.split("=", 1)[0]
        if option == "--domain":
            raise StartupError(
                "config",
                "invalid_config",
                "--domain is removed; use --domains",
            )
        if option in _KNOWN_LONG_OPTIONS:
            continue
        raise StartupError(
            "config",
            "invalid_config",
            "argument parsing failed: unrecognized arguments: %s" % option,
        )


def _build_parser():
    parser_kwargs = {"add_help": True}
    try:
        parser = _RaisingArgumentParser(allow_abbrev=False, **parser_kwargs)
    except TypeError:
        parser = _RaisingArgumentParser(**parser_kwargs)

    required = parser.add_argument_group("required")
    required.add_argument("--domains", required=True,
                          help="comma-separated base domains (required)")
    required.add_argument("--files", required=True,
                          help="comma-separated file paths to publish (required)")
    required.add_argument("--psk", required=True,
                          help="shared secret for v1 crypto (required)")

    server = parser.add_argument_group("server")
    server.add_argument("--listen-addr", default="0.0.0.0:53",
                        help="UDP bind address (default: %(default)s)")
    server.add_argument("--ttl", default="30",
                        help="answer TTL in seconds, 1..300 (default: %(default)s)")

    dns_wire = parser.add_argument_group("dns/wire")
    dns_wire.add_argument("--dns-edns-size", default="1232",
                          help="EDNS UDP size, 512..4096 (default: %(default)s)")
    dns_wire.add_argument("--dns-max-label-len", default="63",
                          help="payload label cap, 16..63 (default: %(default)s)")
    dns_wire.add_argument("--response-label", default="r-x",
                          help="CNAME response discriminator (default: %(default)s)")

    mapping = parser.add_argument_group("mapping")
    mapping.add_argument("--mapping-seed", default="0",
                         help="deterministic mapping seed (default: %(default)s)")
    mapping.add_argument("--file-tag-len", default="6",
                         help="file-tag length, 4..16 (default: %(default)s)")

    generation = parser.add_argument_group("generation")
    generation.add_argument("--target-os", default="windows,linux",
                            help="windows,linux or subset (default: %(default)s)")
    generation.add_argument("--client-out-dir", default="./generated_clients",
                            help="output dir for generated clients (default: %(default)s)")
    generation.add_argument("--compression-level", default="9",
                            help="zlib level, 0..9 (default: %(default)s)")

    logging_grp = parser.add_argument_group("logging")
    logging_grp.add_argument("--log-level", default=DEFAULT_LOG_LEVEL,
                             help="error|warn|info|debug|trace (default: %(default)s)")
    logging_grp.add_argument("--log-file", default=DEFAULT_LOG_FILE,
                             help="log file path (if omitted, logs to stdout)")
    return parser


def parse_cli_args(argv=None):
    raw_argv = _raw_argv(argv)
    _validate_long_option_tokens(raw_argv)
    parser = _build_parser()
    return parser.parse_args(raw_argv)
