from __future__ import absolute_import

from dnsdle.constants import ANSWER_FIXED_BYTES
from dnsdle.constants import BASE32_BITS_PER_CHAR
from dnsdle.constants import BINARY_RECORD_OVERHEAD
from dnsdle.constants import BITS_PER_BYTE
from dnsdle.constants import CLASSIC_DNS_PACKET_LIMIT
from dnsdle.constants import DNS_HEADER_BYTES
from dnsdle.constants import MAX_DNS_NAME_TEXT_LENGTH
from dnsdle.constants import MAX_DNS_NAME_WIRE_LENGTH
from dnsdle.constants import OPT_RR_BYTES
from dnsdle.constants import QUESTION_FIXED_BYTES
from dnsdle.constants import dns_name_wire_length
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.state import StartupError


def _payload_labels_for_chars(char_count, label_cap):
    labels = []
    remaining = char_count
    while remaining > 0:
        take = min(label_cap, remaining)
        labels.append("a" * take)
        remaining -= take
    return tuple(labels)


def _domain_labels(config):
    labels = getattr(config, "longest_domain_labels", None)
    if labels is None:
        raise StartupError(
            "budget",
            "budget_unusable",
            "config does not expose longest_domain_labels",
        )
    return tuple(labels)


def _validate_query_token_len(config, query_token_len):
    if query_token_len <= 0:
        raise StartupError(
            "budget",
            "budget_unusable",
            "query token length must be positive",
            {"query_token_len": query_token_len},
        )
    if query_token_len > config.dns_max_label_len:
        raise StartupError(
            "budget",
            "budget_unusable",
            "query token length exceeds dns_max_label_len",
            {"query_token_len": query_token_len},
        )

    qname_labels = ("a" * query_token_len, "b" * config.file_tag_len) + _domain_labels(config)
    if dns_name_wire_length(qname_labels) > MAX_DNS_NAME_WIRE_LENGTH:
        raise StartupError(
            "budget",
            "budget_unusable",
            "query name budget cannot fit query token and file tag labels",
            {"query_token_len": query_token_len},
        )


def _response_size_estimate(config, query_token_len, target_wire_len):
    qname_labels = ("a" * query_token_len, "b" * config.file_tag_len) + _domain_labels(config)
    qname_wire_len = dns_name_wire_length(qname_labels)
    question_size = qname_wire_len + QUESTION_FIXED_BYTES

    # Conservative packet sizing:
    # - answer owner name uses pointer (2 bytes, in ANSWER_FIXED_BYTES)
    # - CNAME target is sized as full expanded DNS name wire length
    #   (no suffix-compression credit during startup budgeting)
    answer_size = ANSWER_FIXED_BYTES + target_wire_len

    additional_size = OPT_RR_BYTES if config.dns_edns_size > CLASSIC_DNS_PACKET_LIMIT else 0
    return DNS_HEADER_BYTES + question_size + answer_size + additional_size


def compute_max_ciphertext_slice_bytes(config, query_token_len=1):
    domain_labels = _domain_labels(config)
    suffix_labels = (config.response_label,) + domain_labels
    packet_size_limit = max(config.dns_edns_size, CLASSIC_DNS_PACKET_LIMIT)
    query_token_len = int(query_token_len)
    _validate_query_token_len(config, query_token_len)

    max_payload_chars = 0
    winning_response_size = 0
    # 253 textual chars is the practical upper bound without trailing dot.
    for candidate in range(MAX_DNS_NAME_TEXT_LENGTH, 0, -1):
        payload_labels = _payload_labels_for_chars(candidate, config.dns_max_label_len)
        target_wire_len = dns_name_wire_length(payload_labels + suffix_labels)
        candidate_response_size = _response_size_estimate(config, query_token_len, target_wire_len)
        if (
            target_wire_len <= MAX_DNS_NAME_WIRE_LENGTH
            and candidate_response_size <= packet_size_limit
        ):
            max_payload_chars = candidate
            winning_response_size = candidate_response_size
            break

    if max_payload_chars <= 0:
        raise StartupError(
            "budget",
            "budget_unusable",
            "no payload capacity available within DNS name and packet limits",
        )

    max_record_bytes = (max_payload_chars * BASE32_BITS_PER_CHAR) // BITS_PER_BYTE
    max_ciphertext_slice_bytes = max_record_bytes - BINARY_RECORD_OVERHEAD
    if max_ciphertext_slice_bytes <= 0:
        raise StartupError(
            "budget",
            "budget_unusable",
            "max_ciphertext_slice_bytes is not positive",
            {
                "max_payload_chars": max_payload_chars,
                "max_record_bytes": max_record_bytes,
                "response_size_limit": packet_size_limit,
                "query_token_len": query_token_len,
            },
        )

    budget_info = {
        "domains": tuple(config.domains),
        "longest_domain": config.longest_domain,
        "longest_domain_wire_len": config.longest_domain_wire_len,
        "max_payload_chars": max_payload_chars,
        "max_record_bytes": max_record_bytes,
        "binary_record_overhead": BINARY_RECORD_OVERHEAD,
        "dns_edns_size": config.dns_edns_size,
        "response_size_limit": packet_size_limit,
        "response_size_estimate": winning_response_size,
        "query_token_len": query_token_len,
    }
    if logger_enabled("debug"):
        log_event(
            "debug",
            "budget",
            {
                "phase": "budget",
                "classification": "diagnostic",
                "reason_code": "budget_computed",
            },
            context_fn=lambda: dict(budget_info, max_ciphertext_slice_bytes=max_ciphertext_slice_bytes),
        )
    return max_ciphertext_slice_bytes, budget_info
