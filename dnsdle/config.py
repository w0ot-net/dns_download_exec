from __future__ import absolute_import

import argparse
import os
import re
from collections import namedtuple

from dnsdle.constants import ALLOWED_TARGET_OS
from dnsdle.constants import FIXED_CONFIG
from dnsdle.constants import MAX_DNS_EDNS_SIZE
from dnsdle.constants import MIN_DNS_EDNS_SIZE
from dnsdle.constants import TOKEN_ALPHABET_CHARS
from dnsdle.state import StartupError


TOKEN_ALPHABET = set(TOKEN_ALPHABET_CHARS)
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


Config = namedtuple(
    "Config",
    [
        "domain",
        "domain_labels",
        "files",
        "psk",
        "listen_addr",
        "listen_host",
        "listen_port",
        "ttl",
        "dns_edns_size",
        "dns_max_label_len",
        "response_label",
        "mapping_seed",
        "file_tag_len",
        "target_os",
        "target_os_csv",
        "client_out_dir",
        "compression_level",
        "fixed",
    ],
)


def _dns_name_wire_length(labels):
    return 1 + sum(1 + len(label) for label in labels)


def _is_printable_ascii(value):
    for ch in value:
        code = ord(ch)
        if code < 32 or code > 126:
            return False
    return True


def _normalize_domain(value):
    if value is None:
        raise StartupError("config", "invalid_config", "domain is required")

    domain = value.strip().lower()
    while domain.endswith("."):
        domain = domain[:-1]

    if not domain:
        raise StartupError("config", "invalid_config", "domain is empty")

    labels = domain.split(".")
    for label in labels:
        if not LABEL_RE.match(label):
            raise StartupError(
                "config",
                "invalid_config",
                "domain label is invalid",
                {"label": label},
            )

    if _dns_name_wire_length(labels) > 255:
        raise StartupError(
            "config",
            "invalid_config",
            "domain exceeds DNS name-length limits",
        )

    return domain, tuple(labels)


def _normalize_response_label(value):
    label = (value or "").strip().lower()
    if not label:
        raise StartupError("config", "invalid_config", "response_label is empty")
    if not LABEL_RE.match(label):
        raise StartupError(
            "config",
            "invalid_config",
            "response_label is invalid",
            {"response_label": label},
        )
    if all(ch in TOKEN_ALPHABET for ch in label):
        raise StartupError(
            "config",
            "invalid_config",
            "response_label must contain a non-token character",
            {"response_label": label},
        )
    return label


def _parse_int_in_range(name, raw_value, min_value, max_value):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise StartupError(
            "config",
            "invalid_config",
            "%s is not a valid integer" % name,
            {"field": name},
        )
    if value < min_value or value > max_value:
        raise StartupError(
            "config",
            "invalid_config",
            "%s is out of range" % name,
            {"field": name, "min": min_value, "max": max_value},
        )
    return value


def _normalize_files(raw_value):
    if raw_value is None:
        raise StartupError("config", "invalid_config", "files is required")

    values = []
    for part in raw_value.split(","):
        path = part.strip()
        if path:
            values.append(path)

    if not values:
        raise StartupError("config", "invalid_config", "files list is empty")

    normalized = []
    seen = set()
    for path in values:
        canonical = os.path.abspath(os.path.normpath(path))
        if canonical in seen:
            raise StartupError(
                "config",
                "invalid_config",
                "duplicate file path after normalization",
                {"path": canonical},
            )
        seen.add(canonical)
        if not os.path.isfile(canonical):
            raise StartupError(
                "config",
                "unreadable_file",
                "file does not exist",
                {"path": canonical},
            )
        if not os.access(canonical, os.R_OK):
            raise StartupError(
                "config",
                "unreadable_file",
                "file is not readable",
                {"path": canonical},
            )
        normalized.append(canonical)

    return tuple(normalized)


def _normalize_mapping_seed(value):
    seed = str(value)
    if not seed:
        raise StartupError("config", "invalid_config", "mapping_seed is empty")
    if not _is_printable_ascii(seed):
        raise StartupError(
            "config",
            "invalid_config",
            "mapping_seed must be printable ASCII",
        )
    return seed


def _normalize_target_os(raw_value):
    value = (raw_value or "").strip().lower()
    if not value:
        raise StartupError("config", "invalid_config", "target_os is empty")

    requested = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if token not in ALLOWED_TARGET_OS:
            raise StartupError(
                "config",
                "invalid_config",
                "target_os value is unsupported",
                {"value": token},
            )
        if token not in requested:
            requested.append(token)

    if not requested:
        raise StartupError("config", "invalid_config", "target_os is empty")

    ordered = []
    for allowed in ALLOWED_TARGET_OS:
        if allowed in requested:
            ordered.append(allowed)

    return tuple(ordered), ",".join(ordered)


def _normalize_client_out_dir(raw_value):
    value = (raw_value or "").strip()
    if not value:
        raise StartupError("config", "invalid_config", "client_out_dir is empty")
    return value


def _normalize_listen_addr(raw_value):
    value = (raw_value or "").strip()
    if not value:
        raise StartupError("config", "invalid_config", "listen_addr is empty")
    if ":" not in value:
        raise StartupError(
            "config",
            "invalid_config",
            "listen_addr must be in host:port format",
        )
    host, port_raw = value.rsplit(":", 1)
    host = host.strip()
    if not host:
        raise StartupError(
            "config",
            "invalid_config",
            "listen_addr host is empty",
        )
    port = _parse_int_in_range("listen_addr_port", port_raw.strip(), 1, 65535)
    return value, host, port


class _RaisingArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise StartupError(
            "config",
            "invalid_config",
            "argument parsing failed: %s" % message,
        )


def parse_cli_config(argv=None):
    parser = _RaisingArgumentParser(add_help=True)
    parser.add_argument("--domain", required=True)
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
    args = parser.parse_args(argv)

    domain, domain_labels = _normalize_domain(args.domain)
    files = _normalize_files(args.files)

    psk = args.psk
    if psk is None or psk == "":
        raise StartupError("config", "invalid_config", "psk must be non-empty")

    listen_addr, listen_host, listen_port = _normalize_listen_addr(args.listen_addr)
    ttl = _parse_int_in_range("ttl", args.ttl, 1, 300)
    dns_edns_size = _parse_int_in_range(
        "dns_edns_size",
        args.dns_edns_size,
        MIN_DNS_EDNS_SIZE,
        MAX_DNS_EDNS_SIZE,
    )
    dns_max_label_len = _parse_int_in_range(
        "dns_max_label_len", args.dns_max_label_len, 16, 63
    )
    response_label = _normalize_response_label(args.response_label)
    mapping_seed = _normalize_mapping_seed(args.mapping_seed)
    file_tag_len = _parse_int_in_range("file_tag_len", args.file_tag_len, 4, 16)
    target_os, target_os_csv = _normalize_target_os(args.target_os)
    client_out_dir = _normalize_client_out_dir(args.client_out_dir)
    compression_level = _parse_int_in_range(
        "compression_level", args.compression_level, 0, 9
    )

    if file_tag_len > dns_max_label_len:
        raise StartupError(
            "config",
            "invalid_config",
            "file_tag_len cannot exceed dns_max_label_len",
        )

    if _dns_name_wire_length((response_label,) + domain_labels) > 255:
        raise StartupError(
            "config",
            "invalid_config",
            "response suffix exceeds DNS name-length limits",
        )

    return Config(
        domain=domain,
        domain_labels=domain_labels,
        files=files,
        psk=psk,
        listen_addr=listen_addr,
        listen_host=listen_host,
        listen_port=listen_port,
        ttl=ttl,
        dns_edns_size=dns_edns_size,
        dns_max_label_len=dns_max_label_len,
        response_label=response_label,
        mapping_seed=mapping_seed,
        file_tag_len=file_tag_len,
        target_os=target_os,
        target_os_csv=target_os_csv,
        client_out_dir=client_out_dir,
        compression_level=compression_level,
        fixed=FIXED_CONFIG.copy(),
    )
