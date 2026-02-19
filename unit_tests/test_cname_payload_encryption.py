from __future__ import absolute_import

import struct
import unittest

import dnsdle.cname_payload as cname_payload
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN


class CnamePayloadEncryptionTests(unittest.TestCase):
    def _record(self, **overrides):
        params = {
            "psk": "k",
            "file_id": "1" * 16,
            "publish_version": "a" * 64,
            "slice_index": 0,
            "total_slices": 3,
            "compressed_size": 321,
            "slice_bytes": b"slice-data-not-trivial",
        }
        params.update(overrides)
        return cname_payload.build_slice_record(**params)

    def _payload_and_mac(self, **overrides):
        record = self._record(**overrides)
        payload_len = struct.unpack("!H", record[2:4])[0]
        payload = record[4 : 4 + payload_len]
        mac = record[-PAYLOAD_MAC_TRUNC_LEN:]
        return payload, mac

    def test_payload_bytes_are_ciphertext_not_plaintext(self):
        plaintext = b"slice-data-not-trivial"
        payload, _mac = self._payload_and_mac(slice_bytes=plaintext)
        self.assertNotEqual(plaintext, payload)

    def test_record_is_deterministic_for_identical_inputs(self):
        first = self._record()
        second = self._record()
        self.assertEqual(first, second)

    def test_mac_changes_when_ciphertext_metadata_changes(self):
        _payload_a, mac_a = self._payload_and_mac(compressed_size=321)
        _payload_b, mac_b = self._payload_and_mac(compressed_size=322)
        self.assertNotEqual(mac_a, mac_b)

    def test_ciphertext_changes_with_slice_index(self):
        payload_a, _mac_a = self._payload_and_mac(slice_index=0)
        payload_b, _mac_b = self._payload_and_mac(slice_index=1)
        self.assertNotEqual(payload_a, payload_b)


if __name__ == "__main__":
    unittest.main()
