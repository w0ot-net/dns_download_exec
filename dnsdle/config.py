from __future__ import absolute_import

import os
import re
from collections import namedtuple

from dnsdle.constants import ALLOWED_TARGET_OS
from dnsdle.constants import DEFAULT_LOG_CATEGORIES_CSV
from dnsdle.constants import DEFAULT_LOG_FILE
from dnsdle.constants import DEFAULT_LOG_FOCUS
from dnsdle.constants import DEFAULT_LOG_LEVEL
from dnsdle.constants import DEFAULT_LOG_OUTPUT
from dnsdle.constants import DEFAULT_LOG_RATE_LIMIT_PER_SEC
from dnsdle.constants import DEFAULT_LOG_SAMPLE_RATE
from dnsdle.constants import FIXED_CONFIG
from dnsdle.constants import LOG_CATEGORIES
from dnsdle.constants import LOG_LEVELS
from dnsdle.constants import MAX_DNS_EDNS_SIZE
from dnsdle.constants import MIN_DNS_EDNS_SIZE
from dnsdle.constants import TOKEN_ALPHABET_CHARS
from dnsdle.state import StartupError


TOKEN_ALPHABET = set(TOKEN_ALPHABET_CHARS)
LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


Config = namedtuple(
    "Config",
    [
        "domains",
        "domain_labels_by_domain",
        "longest_domain",
        "longest_domain_labels",
        "longest_domain_wire_len",
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
        "log_level",
        "log_categories",
        "log_sample_rate",
        "log_rate_limit_per_sec",
        "log_output",
        "log_file",
        "log_focus",
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


def _labels_is_suffix(suffix_labels, full_labels):
    suffix_len = len(suffix_labels)
    full_len = len(full_labels)
    if suffix_len > full_len:
        return False
    return full_labels[full_len - suffix_len :] == suffix_labels


def _normalize_domains(raw_value):
    if raw_value is None:
        raise StartupError("config", "invalid_domains", "domains is required")

    raw_tokens = raw_value.split(",")
    normalized = {}
    for raw_token in raw_tokens:
        token = raw_token.strip()
        if not token:
            raise StartupError(
                "config",
                "invalid_domains",
                "domains contains an empty entry",
            )
        domain, labels = _normalize_domain(token)
        if domain in normalized:
            raise StartupError(
                "config",
                "duplicate_domain",
                "duplicate normalized domain",
                {"domain": domain},
            )
        normalized[domain] = labels

    domains = tuple(sorted(normalized.keys()))
    if not domains:
        raise StartupError("config", "invalid_domains", "domains list is empty")

    domain_labels_by_domain = tuple(normalized[domain] for domain in domains)

    for index in range(len(domains)):
        domain_a = domains[index]
        labels_a = domain_labels_by_domain[index]
        for other_index in range(index + 1, len(domains)):
            domain_b = domains[other_index]
            labels_b = domain_labels_by_domain[other_index]
            if _labels_is_suffix(labels_a, labels_b):
                raise StartupError(
                    "config",
                    "overlapping_domains",
                    "configured domains overlap on label boundaries",
                    {"domain": domain_a, "other_domain": domain_b},
                )
            if _labels_is_suffix(labels_b, labels_a):
                raise StartupError(
                    "config",
                    "overlapping_domains",
                    "configured domains overlap on label boundaries",
                    {"domain": domain_b, "other_domain": domain_a},
                )

    longest_domain = domains[0]
    longest_domain_labels = domain_labels_by_domain[0]
    longest_domain_wire_len = _dns_name_wire_length(longest_domain_labels)
    for index in range(1, len(domains)):
        wire_len = _dns_name_wire_length(domain_labels_by_domain[index])
        if wire_len > longest_domain_wire_len:
            longest_domain = domains[index]
            longest_domain_labels = domain_labels_by_domain[index]
            longest_domain_wire_len = wire_len

    return (
        domains,
        domain_labels_by_domain,
        longest_domain,
        longest_domain_labels,
        longest_domain_wire_len,
    )


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


def _parse_float_in_range(name, raw_value, min_value, max_value):
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise StartupError(
            "config",
            "invalid_config",
            "%s is not a valid number" % name,
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
    if "\x00" in value:
        raise StartupError("config", "invalid_config", "client_out_dir contains NUL")
    normalized = os.path.abspath(os.path.normpath(value))
    if not normalized:
        raise StartupError("config", "invalid_config", "client_out_dir is empty")
    return normalized


def _normalize_log_level(raw_value):
    value = (raw_value or "").strip().lower()
    if value in LOG_LEVELS:
        return value
    raise StartupError(
        "config",
        "invalid_config",
        "log_level is unsupported",
        {"field": "log_level", "value": value},
    )


def _normalize_log_categories(raw_value):
    value = (raw_value or "").strip().lower()
    if not value:
        raise StartupError(
            "config",
            "invalid_config",
            "log_categories is empty",
            {"field": "log_categories"},
        )
    if value == "all":
        return tuple(LOG_CATEGORIES)

    categories = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            raise StartupError(
                "config",
                "invalid_config",
                "log_categories contains an empty entry",
                {"field": "log_categories"},
            )
        if token not in LOG_CATEGORIES:
            raise StartupError(
                "config",
                "invalid_config",
                "log_categories contains an unsupported value",
                {"field": "log_categories", "value": token},
            )
        if token not in categories:
            categories.append(token)
    return tuple(categories)


def _normalize_log_output(raw_value):
    value = (raw_value or "").strip().lower()
    if value in ("stdout", "file"):
        return value
    raise StartupError(
        "config",
        "invalid_config",
        "log_output is unsupported",
        {"field": "log_output", "value": value},
    )


def _normalize_log_file(raw_value):
    return (raw_value or "").strip()


def _normalize_log_focus(raw_value):
    return (raw_value or "").strip()


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


def _arg_value(parsed_args, name):
    if hasattr(parsed_args, name):
        return getattr(parsed_args, name)
    if isinstance(parsed_args, dict) and name in parsed_args:
        return parsed_args[name]
    raise StartupError(
        "config",
        "invalid_config",
        "missing parsed CLI argument: %s" % name,
        {"field": name},
    )


def _arg_value_default(parsed_args, name, default):
    if hasattr(parsed_args, name):
        return getattr(parsed_args, name)
    if isinstance(parsed_args, dict) and name in parsed_args:
        return parsed_args[name]
    return default


def build_config(parsed_args):
    (
        domains,
        domain_labels_by_domain,
        longest_domain,
        longest_domain_labels,
        longest_domain_wire_len,
    ) = _normalize_domains(_arg_value(parsed_args, "domains"))
    files = _normalize_files(_arg_value(parsed_args, "files"))

    psk = _arg_value(parsed_args, "psk")
    if psk is None or psk == "":
        raise StartupError("config", "invalid_config", "psk must be non-empty")

    listen_addr, listen_host, listen_port = _normalize_listen_addr(
        _arg_value(parsed_args, "listen_addr")
    )
    ttl = _parse_int_in_range("ttl", _arg_value(parsed_args, "ttl"), 1, 300)
    dns_edns_size = _parse_int_in_range(
        "dns_edns_size",
        _arg_value(parsed_args, "dns_edns_size"),
        MIN_DNS_EDNS_SIZE,
        MAX_DNS_EDNS_SIZE,
    )
    dns_max_label_len = _parse_int_in_range(
        "dns_max_label_len",
        _arg_value(parsed_args, "dns_max_label_len"),
        16,
        63,
    )
    response_label = _normalize_response_label(_arg_value(parsed_args, "response_label"))
    mapping_seed = _normalize_mapping_seed(_arg_value(parsed_args, "mapping_seed"))
    file_tag_len = _parse_int_in_range(
        "file_tag_len", _arg_value(parsed_args, "file_tag_len"), 4, 16
    )
    target_os, target_os_csv = _normalize_target_os(_arg_value(parsed_args, "target_os"))
    client_out_dir = _normalize_client_out_dir(_arg_value(parsed_args, "client_out_dir"))
    compression_level = _parse_int_in_range(
        "compression_level",
        _arg_value(parsed_args, "compression_level"),
        0,
        9,
    )
    log_level = _normalize_log_level(
        _arg_value_default(parsed_args, "log_level", DEFAULT_LOG_LEVEL)
    )
    log_categories = _normalize_log_categories(
        _arg_value_default(parsed_args, "log_categories", DEFAULT_LOG_CATEGORIES_CSV)
    )
    log_sample_rate = _parse_float_in_range(
        "log_sample_rate",
        _arg_value_default(parsed_args, "log_sample_rate", DEFAULT_LOG_SAMPLE_RATE),
        0.0,
        1.0,
    )
    log_rate_limit_per_sec = _parse_int_in_range(
        "log_rate_limit_per_sec",
        _arg_value_default(
            parsed_args,
            "log_rate_limit_per_sec",
            DEFAULT_LOG_RATE_LIMIT_PER_SEC,
        ),
        0,
        1000000,
    )
    log_output = _normalize_log_output(
        _arg_value_default(parsed_args, "log_output", DEFAULT_LOG_OUTPUT)
    )
    log_file = _normalize_log_file(
        _arg_value_default(parsed_args, "log_file", DEFAULT_LOG_FILE)
    )
    log_focus = _normalize_log_focus(
        _arg_value_default(parsed_args, "log_focus", DEFAULT_LOG_FOCUS)
    )

    if file_tag_len > dns_max_label_len:
        raise StartupError(
            "config",
            "invalid_config",
            "file_tag_len cannot exceed dns_max_label_len",
        )

    if _dns_name_wire_length((response_label,) + longest_domain_labels) > 255:
        raise StartupError(
            "config",
            "invalid_config",
            "response suffix exceeds DNS name-length limits for longest domain",
            {"longest_domain": longest_domain},
        )

    if log_output == "file" and not log_file:
        raise StartupError(
            "config",
            "invalid_config",
            "log_file is required when log_output=file",
            {"field": "log_file"},
        )
    if log_output != "file" and log_file:
        raise StartupError(
            "config",
            "invalid_config",
            "log_file is only valid when log_output=file",
            {"field": "log_file"},
        )

    return Config(
        domains=domains,
        domain_labels_by_domain=domain_labels_by_domain,
        longest_domain=longest_domain,
        longest_domain_labels=longest_domain_labels,
        longest_domain_wire_len=longest_domain_wire_len,
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
        log_level=log_level,
        log_categories=log_categories,
        log_sample_rate=log_sample_rate,
        log_rate_limit_per_sec=log_rate_limit_per_sec,
        log_output=log_output,
        log_file=log_file,
        log_focus=log_focus,
        fixed=FIXED_CONFIG.copy(),
    )
