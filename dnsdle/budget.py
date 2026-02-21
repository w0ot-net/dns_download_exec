from __future__ import absolute_import, unicode_literals

from dnsdle.constants import ANSWER_FIXED_BYTES
from dnsdle.constants import BASE32_BITS_PER_CHAR
from dnsdle.constants import BINARY_RECORD_OVERHEAD
from dnsdle.constants import BITS_PER_BYTE
from dnsdle.constants import CLASSIC_DNS_PACKET_LIMIT
from dnsdle.constants import DNS_HEADER_BYTES
from dnsdle.constants import MAX_DNS_NAME_WIRE_LENGTH
from dnsdle.constants import OPT_RR_BYTES
from dnsdle.constants import QUESTION_FIXED_BYTES
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.state import StartupError


def _payload_wire_contribution(char_count, label_cap):
    if char_count <= 0:
        return 0
    return char_count + (char_count + label_cap - 1) // label_cap


def _max_chars_for_wire_budget(wire_budget, label_cap):
    if wire_budget <= 0:
        return 0
    k = wire_budget // (label_cap + 1)
    remaining = wire_budget - k * (label_cap + 1)
    return k * label_cap + max(remaining - 1, 0)


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

    qname_wire = 2 + query_token_len + config.file_tag_len + config.longest_domain_wire_len
    if qname_wire > MAX_DNS_NAME_WIRE_LENGTH:
        raise StartupError(
            "budget",
            "budget_unusable",
            "query name budget cannot fit query token and file tag labels",
            {"query_token_len": query_token_len},
        )


def compute_max_ciphertext_slice_bytes(config, query_token_len=1):
    packet_size_limit = max(config.dns_edns_size, CLASSIC_DNS_PACKET_LIMIT)
    if config.dns_max_response_bytes > 0:
        packet_size_limit = min(packet_size_limit, config.dns_max_response_bytes)
    query_token_len = int(query_token_len)
    _validate_query_token_len(config, query_token_len)

    label_cap = config.dns_max_label_len
    # CNAME target suffix: response_label + domain labels.
    # Wire = root(1) + length-prefix(1) + response_label + domain wire - root(1)
    suffix_wire = 1 + len(config.response_label) + config.longest_domain_wire_len

    # Constraint 1: CNAME target wire length <= 255
    wire_budget = MAX_DNS_NAME_WIRE_LENGTH - suffix_wire

    # Constraint 2: total response packet <= packet_size_limit
    # Question QNAME wire: root(1) + 2 length-prefixes + token + file_tag + domain wire - root(1)
    qname_wire = 2 + query_token_len + config.file_tag_len + config.longest_domain_wire_len
    additional_size = OPT_RR_BYTES if config.dns_edns_size > CLASSIC_DNS_PACKET_LIMIT else 0
    response_fixed = (
        DNS_HEADER_BYTES + qname_wire + QUESTION_FIXED_BYTES
        + ANSWER_FIXED_BYTES + suffix_wire + additional_size
    )
    response_budget = packet_size_limit - response_fixed

    max_payload_chars = _max_chars_for_wire_budget(
        min(wire_budget, response_budget), label_cap
    )
    payload_wire = _payload_wire_contribution(max_payload_chars, label_cap)
    winning_response_size = response_fixed + payload_wire

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
