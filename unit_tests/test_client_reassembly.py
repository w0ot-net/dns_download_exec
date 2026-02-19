from __future__ import absolute_import

import hashlib
import unittest
import zlib

import dnsdle.client_reassembly as client_reassembly


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest().lower()


class ClientReassemblyTests(unittest.TestCase):
    def test_store_slice_enforces_duplicate_equality(self):
        slice_map = {}
        self.assertTrue(client_reassembly.store_slice_bytes(slice_map, 0, b"abc"))
        self.assertFalse(client_reassembly.store_slice_bytes(slice_map, 0, b"abc"))
        with self.assertRaises(client_reassembly.ClientReassemblyError) as raised:
            client_reassembly.store_slice_bytes(slice_map, 0, b"abd")
        self.assertEqual("duplicate_slice_mismatch", raised.exception.reason_code)

    def test_reassemble_and_verify_success(self):
        plaintext = b"this is a deterministic plaintext payload"
        compressed = zlib.compress(plaintext, 9)
        slice_map = {}
        chunk_len = 7
        for index, start in enumerate(range(0, len(compressed), chunk_len)):
            chunk = compressed[start : start + chunk_len]
            client_reassembly.store_slice_bytes(slice_map, index, chunk)

        rebuilt = client_reassembly.reassemble_and_verify(
            slice_map,
            len(slice_map),
            len(compressed),
            _sha256_hex(plaintext),
        )
        self.assertEqual(plaintext, rebuilt)

    def test_reassemble_rejects_missing_index_coverage(self):
        plaintext = b"abc123"
        compressed = zlib.compress(plaintext, 9)
        slice_map = {0: compressed[:2], 2: compressed[2:]}
        with self.assertRaises(client_reassembly.ClientReassemblyError) as raised:
            client_reassembly.reassemble_and_verify(
                slice_map,
                3,
                len(compressed),
                _sha256_hex(plaintext),
            )
        self.assertEqual("slice_index_coverage_invalid", raised.exception.reason_code)

    def test_reassemble_rejects_decompress_failure(self):
        slice_map = {0: b"\x00\x01", 1: b"\x02\x03"}
        with self.assertRaises(client_reassembly.ClientReassemblyError) as raised:
            client_reassembly.reassemble_and_verify(
                slice_map,
                2,
                4,
                "0" * 64,
            )
        self.assertEqual("decompress_failed", raised.exception.reason_code)

    def test_reassemble_rejects_hash_mismatch(self):
        plaintext = b"hash-me"
        compressed = zlib.compress(plaintext, 9)
        slice_map = {0: compressed}
        with self.assertRaises(client_reassembly.ClientReassemblyError) as raised:
            client_reassembly.reassemble_and_verify(
                slice_map,
                1,
                len(compressed),
                "f" * 64,
            )
        self.assertEqual("plaintext_hash_mismatch", raised.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
