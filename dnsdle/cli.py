from __future__ import absolute_import

import argparse
import sys

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
    parser.add_argument("--domains", required=True)
    parser.add_argument("--files", required=True)
    parser.add_argument("--psk", required=True)
    parser.add_argument("--listen-addr", default="0.0.0.0:53")
    parser.add_argument("--ttl", default="30")
    parser.add_argument("--dns-edns-size", default="1232")
    parser.add_argument("--dns-max-label-len", default="63")
    parser.add_argument("--response-label", default="r-x")
    parser.add_argument("--mapping-seed", default="0")
    parser.add_argument("--file-tag-len", default="6")
    parser.add_argument("--target-os", default="windows,linux")
    parser.add_argument("--client-out-dir", default="./generated_clients")
    parser.add_argument("--compression-level", default="9")
    return parser


def parse_cli_args(argv=None):
    raw_argv = _raw_argv(argv)
    _validate_long_option_tokens(raw_argv)
    parser = _build_parser()
    return parser.parse_args(raw_argv)
