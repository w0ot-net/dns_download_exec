from __future__ import absolute_import

import struct
import unittest

import dnsdle.cname_payload as cname_payload
from dnsdle.constants import PAYLOAD_MAC_TRUNC_LEN


class CnamePayloadTests(unittest.TestCase):
    def _record(self, psk="k", slice_index=0):
        return cname_payload.build_slice_record(
            psk=psk,
            file_id="1" * 16,
            publish_version="a" * 64,
            slice_index=slice_index,
            total_slices=3,
            compressed_size=123,
            slice_bytes=b"slice-data",
        )

    def test_build_slice_record_is_deterministic(self):
        first = self._record()
        second = self._record()
        self.assertEqual(first, second)

    def test_build_slice_record_mac_is_non_zero_and_truncated(self):
        record = self._record()
        payload_len = struct.unpack("!H", record[2:4])[0]
        self.assertEqual(len(b"slice-data"), payload_len)
        self.assertEqual(PAYLOAD_MAC_TRUNC_LEN, len(record[-PAYLOAD_MAC_TRUNC_LEN:]))
        self.assertNotEqual(b"\x00" * PAYLOAD_MAC_TRUNC_LEN, record[-PAYLOAD_MAC_TRUNC_LEN:])

    def test_mac_changes_when_psk_changes(self):
        record_a = self._record(psk="k")
        record_b = self._record(psk="other")
        self.assertNotEqual(record_a[-PAYLOAD_MAC_TRUNC_LEN:], record_b[-PAYLOAD_MAC_TRUNC_LEN:])

    def test_mac_changes_when_slice_index_changes(self):
        record_a = self._record(slice_index=0)
        record_b = self._record(slice_index=1)
        self.assertNotEqual(record_a[-PAYLOAD_MAC_TRUNC_LEN:], record_b[-PAYLOAD_MAC_TRUNC_LEN:])


if __name__ == "__main__":
    unittest.main()
