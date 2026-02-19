from __future__ import absolute_import

from dnsdle.state import StartupError


MAX_DNS_NAME_WIRE_LENGTH = 255
BINARY_RECORD_OVERHEAD = 20  # 4-byte header + 16-byte truncated MAC


def _dns_name_wire_length(labels):
    return 1 + sum(1 + len(label) for label in labels)


def _payload_labels_for_chars(char_count, label_cap):
    labels = []
    remaining = char_count
    while remaining > 0:
        take = label_cap if remaining > label_cap else remaining
        labels.append("a" * take)
        remaining -= take
    return tuple(labels)


def compute_max_ciphertext_slice_bytes(config):
    suffix_labels = (config.response_label,) + tuple(config.domain_labels)

    max_payload_chars = 0
    # 253 textual chars is the practical upper bound without trailing dot.
    for candidate in range(253, 0, -1):
        payload_labels = _payload_labels_for_chars(candidate, config.dns_max_label_len)
        target_wire_len = _dns_name_wire_length(payload_labels + suffix_labels)
        if target_wire_len <= MAX_DNS_NAME_WIRE_LENGTH:
            max_payload_chars = candidate
            break

    if max_payload_chars <= 0:
        raise StartupError(
            "budget",
            "budget_unusable",
            "no payload capacity available in CNAME target name budget",
        )

    max_record_bytes = (max_payload_chars * 5) // 8
    max_ciphertext_slice_bytes = max_record_bytes - BINARY_RECORD_OVERHEAD
    if max_ciphertext_slice_bytes <= 0:
        raise StartupError(
            "budget",
            "budget_unusable",
            "max_ciphertext_slice_bytes is not positive",
            {
                "max_payload_chars": max_payload_chars,
                "max_record_bytes": max_record_bytes,
            },
        )

    return max_ciphertext_slice_bytes, {
        "max_payload_chars": max_payload_chars,
        "max_record_bytes": max_record_bytes,
        "binary_record_overhead": BINARY_RECORD_OVERHEAD,
        "dns_edns_size": config.dns_edns_size,
    }
