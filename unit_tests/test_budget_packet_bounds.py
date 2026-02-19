from __future__ import absolute_import

import unittest
from collections import namedtuple

from dnsdle.budget import compute_max_ciphertext_slice_bytes
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
from dnsdle.mapping import apply_mapping
from dnsdle.state import StartupError


_TestConfig = namedtuple(
    "TestConfig",
    [
        "domain_labels",
        "longest_domain_labels",
        "dns_max_label_len",
        "file_tag_len",
        "response_label",
        "dns_edns_size",
        "mapping_seed",
    ],
)


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


def _response_size_estimate(config, query_token_len, target_wire_len):
    qname_labels = ("a" * query_token_len, "b" * config.file_tag_len) + tuple(
        config.longest_domain_labels
    )
    qname_wire_len = _dns_name_wire_length(qname_labels)
    question_size = qname_wire_len + QUESTION_FIXED_BYTES
    answer_size = ANSWER_FIXED_BYTES + target_wire_len
    additional_size = OPT_RR_BYTES if config.dns_edns_size > CLASSIC_DNS_PACKET_LIMIT else 0
    return DNS_HEADER_BYTES + question_size + answer_size + additional_size


def _max_ciphertext_for_query_token_len(config, query_token_len):
    suffix_labels = (config.response_label,) + tuple(config.longest_domain_labels)
    packet_size_limit = (
        config.dns_edns_size
        if config.dns_edns_size > CLASSIC_DNS_PACKET_LIMIT
        else CLASSIC_DNS_PACKET_LIMIT
    )
    max_payload_chars = 0
    for candidate in range(MAX_DNS_NAME_TEXT_LENGTH, 0, -1):
        payload_labels = _payload_labels_for_chars(candidate, config.dns_max_label_len)
        target_wire_len = _dns_name_wire_length(payload_labels + suffix_labels)
        response_size_estimate = _response_size_estimate(config, query_token_len, target_wire_len)
        if (
            target_wire_len <= MAX_DNS_NAME_WIRE_LENGTH
            and response_size_estimate <= packet_size_limit
        ):
            max_payload_chars = candidate
            break

    if max_payload_chars <= 0:
        return 0
    max_record_bytes = (max_payload_chars * BASE32_BITS_PER_CHAR) // BITS_PER_BYTE
    return max_record_bytes - BINARY_RECORD_OVERHEAD


def _single_slice_token_len(config):
    publish_item = {
        "file_id": "0" * 16,
        "publish_version": "1" * 64,
        "plaintext_sha256": "2" * 64,
        "compressed_size": 1,
        "total_slices": 1,
        "slice_bytes_by_index": (b"x",),
        "crypto_profile": "v1",
        "wire_profile": "v1",
    }
    mapped = apply_mapping([publish_item], config)
    return mapped[0]["slice_token_len"]


class BudgetPacketBoundsTests(unittest.TestCase):
    def _build_config(self, domain, dns_edns_size, file_tag_len):
        labels = tuple(domain.split("."))
        return _TestConfig(
            domain_labels=labels,
            longest_domain_labels=labels,
            dns_max_label_len=63,
            file_tag_len=file_tag_len,
            response_label="r-x",
            dns_edns_size=dns_edns_size,
            mapping_seed="0",
        )

    def test_classic_mode_rejects_oversized_packet_envelope(self):
        domain = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 22))
        config = self._build_config(domain, dns_edns_size=512, file_tag_len=16)

        with self.assertRaises(StartupError) as ctx:
            compute_max_ciphertext_slice_bytes(config)

        self.assertEqual("budget_unusable", ctx.exception.reason_code)

    def test_budget_metadata_packet_estimate_is_bounded(self):
        config = self._build_config("example.com", dns_edns_size=1232, file_tag_len=6)
        max_ciphertext_slice_bytes, budget_info = compute_max_ciphertext_slice_bytes(config)

        self.assertGreater(max_ciphertext_slice_bytes, 0)
        self.assertGreaterEqual(budget_info["query_token_len"], 1)
        self.assertLessEqual(
            budget_info["response_size_estimate"],
            budget_info["response_size_limit"],
        )

    def test_classic_mode_allows_feasible_realized_token_lengths(self):
        domain = ".".join(("a" * 63, "b" * 63, "c" * 61))
        config = self._build_config(domain, dns_edns_size=512, file_tag_len=16)

        token_len = _single_slice_token_len(config)
        self.assertEqual(1, token_len)

        feasible_budget = _max_ciphertext_for_query_token_len(config, token_len)
        self.assertGreater(
            feasible_budget,
            0,
            "expected positive ciphertext budget for realized token length",
        )

        try:
            max_ciphertext_slice_bytes, _budget_info = compute_max_ciphertext_slice_bytes(config)
        except StartupError as exc:
            self.fail(
                "budget rejected feasible classic packet config: %s (%s)"
                % (exc.reason_code, exc.message)
            )

        self.assertGreater(max_ciphertext_slice_bytes, 0)


if __name__ == "__main__":
    unittest.main()
