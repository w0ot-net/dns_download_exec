from __future__ import absolute_import

import unittest

from dnsdle.helpers import dns_name_wire_length
from dnsdle.helpers import hmac_sha256
from dnsdle.helpers import labels_is_suffix


class HelpersTests(unittest.TestCase):

    # -- dns_name_wire_length --

    def test_dns_name_wire_length_single_label(self):
        # 1 (root) + 1 (length byte) + 3 (label bytes) = 5
        self.assertEqual(5, dns_name_wire_length(["com"]))

    def test_dns_name_wire_length_multiple_labels(self):
        # 1 (root) + (1+7) + (1+3) = 13
        self.assertEqual(13, dns_name_wire_length(["example", "com"]))

    def test_dns_name_wire_length_empty_labels(self):
        # root only = 1
        self.assertEqual(1, dns_name_wire_length([]))

    # -- labels_is_suffix --

    def test_labels_is_suffix_exact_match(self):
        labels = ["example", "com"]
        self.assertTrue(labels_is_suffix(labels, labels))

    def test_labels_is_suffix_proper_suffix(self):
        self.assertTrue(labels_is_suffix(["com"], ["example", "com"]))

    def test_labels_is_suffix_non_suffix(self):
        self.assertFalse(labels_is_suffix(["org"], ["example", "com"]))

    def test_labels_is_suffix_longer_than_full(self):
        self.assertFalse(labels_is_suffix(["a", "example", "com"], ["example", "com"]))

    def test_labels_is_suffix_empty_suffix(self):
        self.assertTrue(labels_is_suffix([], ["example", "com"]))

    # -- hmac_sha256 --

    def test_hmac_sha256_output_length(self):
        result = hmac_sha256(b"key", b"message")
        self.assertEqual(32, len(result))

    def test_hmac_sha256_known_vector(self):
        # RFC 4231 Test Case 2: key = "Jefe", data = "what do ya want for nothing?"
        import hmac as hmac_mod
        import hashlib
        key = b"Jefe"
        data = b"what do ya want for nothing?"
        expected = hmac_mod.new(key, data, hashlib.sha256).digest()
        self.assertEqual(expected, hmac_sha256(key, data))

    def test_hmac_sha256_deterministic(self):
        a = hmac_sha256(b"k", b"m")
        b = hmac_sha256(b"k", b"m")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
