from __future__ import absolute_import, unicode_literals

import argparse
import sys

from dnsdle.constants import DEFAULT_LOG_FILE
from dnsdle.constants import DEFAULT_LOG_LEVEL
from dnsdle.state import StartupError


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


def _build_parser():
    try:
        parser = _RaisingArgumentParser(allow_abbrev=False)
    except TypeError:
        parser = _RaisingArgumentParser()

    required = parser.add_argument_group("required")
    required.add_argument("--domain", default=None,
                          help="single base domain")
    required.add_argument("--domains", default=None,
                          help="comma-separated base domains")
    required.add_argument("--file", default=None,
                          help="single file path to publish")
    required.add_argument("--files", default=None,
                          help="comma-separated file paths to publish")
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
    dns_wire.add_argument("--dns-max-response-bytes", default="0",
                          help="cap CNAME response bytes, 0=disabled (default: %(default)s)")
    dns_wire.add_argument("--dns-max-label-len", default="40",
                          help="payload label cap, 16..63 (default: %(default)s)")
    dns_wire.add_argument("--response-label", default="r-x",
                          help="CNAME response discriminator (default: %(default)s)")

    mapping = parser.add_argument_group("mapping")
    mapping.add_argument("--mapping-seed", default="0",
                         help="deterministic mapping seed (default: %(default)s)")
    mapping.add_argument("--file-tag-len", default="6",
                         help="file-tag length, 4..16 (default: %(default)s)")

    generation = parser.add_argument_group("generation")
    generation.add_argument("--client-out-dir", default="./generated_clients",
                            help="output dir for generated clients (default: %(default)s)")
    generation.add_argument("--compression-level", default="9",
                            help="zlib level, 0..9 (default: %(default)s)")

    logging_grp = parser.add_argument_group("logging")
    logging_grp.add_argument("--log-level", default=DEFAULT_LOG_LEVEL,
                             help="error|warn|info|debug|trace (default: %(default)s)")
    logging_grp.add_argument("--log-file", default=DEFAULT_LOG_FILE,
                             help="log file path (default: none)")
    logging_grp.add_argument("--verbose", action="store_true", default=False,
                             help="emit JSON logs to stdout instead of human-friendly "
                                  "output on stderr (with --log-file, JSON goes to "
                                  "file only)")
    return parser


def _merge_singular_plural(singular, plural, name):
    parts = []
    if singular is not None:
        parts.append(singular)
    if plural is not None:
        parts.append(plural)
    if not parts:
        raise StartupError(
            "config",
            "invalid_config",
            "--%s or --%ss is required" % (name, name),
        )
    return ",".join(parts)


def parse_cli_args(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.domains = _merge_singular_plural(args.domain, args.domains, "domain")
    args.files = _merge_singular_plural(args.file, args.files, "file")
    return args
