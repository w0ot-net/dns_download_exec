from __future__ import absolute_import

import hashlib
import hmac
import struct
import unittest

from dnsdle.stager_template import build_stager_template


# Test values substituted into template placeholders.
_DOMAIN_LABELS = ("example", "com")
_FILE_TAG = "tag001"
_FILE_ID = "file001"
_PUBLISH_VERSION = "v1"
_TOTAL_SLICES = 2
_COMPRESSED_SIZE = 100
_PLAINTEXT_SHA256_HEX = "a" * 64
_SLICE_TOKENS = ("tok0", "tok1")
_RESPONSE_LABEL = "r"
_DNS_EDNS_SIZE = 1232


def _build_ns():
    """Exec the stager template with test values into a namespace dict."""
    template = build_stager_template()
    replacements = {
        "DOMAIN_LABELS": _DOMAIN_LABELS,
        "FILE_TAG": _FILE_TAG,
        "FILE_ID": _FILE_ID,
        "PUBLISH_VERSION": _PUBLISH_VERSION,
        "TOTAL_SLICES": _TOTAL_SLICES,
        "COMPRESSED_SIZE": _COMPRESSED_SIZE,
        "PLAINTEXT_SHA256_HEX": _PLAINTEXT_SHA256_HEX,
        "SLICE_TOKENS": _SLICE_TOKENS,
        "RESPONSE_LABEL": _RESPONSE_LABEL,
        "DNS_EDNS_SIZE": _DNS_EDNS_SIZE,
    }
    source = template
    for key, value in replacements.items():
        source = source.replace("@@%s@@" % key, repr(value))

    # Strip the runtime main body (everything after the sentinel comment).
    source_defs = source.split("# __RUNTIME__", 1)[0]

    ns = {}
    exec(compile(source_defs, "<stager-template-test>", "exec"), ns)
    return ns


class StagerTemplateFunctionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ns = _build_ns()

    def _fn(self, name):
        return self.ns[name]

    # -- _encode_name / _decode_name round-trip --

    def test_encode_decode_round_trip_single_label(self):
        encode = self._fn("_encode_name")
        decode = self._fn("_decode_name")
        wire = encode(["hello"])
        labels, end = decode(wire, 0)
        self.assertEqual(("hello",), labels)
        self.assertEqual(len(wire), end)

    def test_encode_decode_round_trip_many_labels(self):
        encode = self._fn("_encode_name")
        decode = self._fn("_decode_name")
        original = ["www", "example", "com"]
        wire = encode(original)
        labels, end = decode(wire, 0)
        self.assertEqual(tuple(original), labels)

    # -- _decode_name pointer decompression --

    def test_decode_name_pointer_decompression(self):
        decode = self._fn("_decode_name")
        # Build: name "com" at offset 0, then a pointer to offset 0 at offset 5.
        name_wire = b"\x03com\x00"   # "com" ending at offset 5
        pointer = b"\xc0\x00"        # pointer to offset 0
        msg = name_wire + pointer
        labels, end = decode(msg, 5)
        self.assertEqual(("com",), labels)
        self.assertEqual(7, end)

    # -- _decode_name error paths --

    def test_decode_name_truncated_raises(self):
        decode = self._fn("_decode_name")
        with self.assertRaises((ValueError, IndexError)):
            decode(b"\x05ab", 0)

    def test_decode_name_pointer_loop_raises(self):
        decode = self._fn("_decode_name")
        # Pointer at offset 0 points to offset 0 -> loop.
        msg = b"\xc0\x00"
        with self.assertRaises(ValueError):
            decode(msg, 0)

    def test_decode_name_invalid_label_type_raises(self):
        decode = self._fn("_decode_name")
        # Byte 0x80 has high bits 10 -> invalid (not 00 or 11).
        msg = b"\x80"
        with self.assertRaises(ValueError):
            decode(msg, 0)

    # -- _build_query --

    def test_build_query_header_structure(self):
        build = self._fn("_build_query")
        pkt = build(0x1234, ["example", "com"])
        # Header: 12 bytes.
        qid, flags, qdcount, ancount, nscount, arcount = struct.unpack(
            "!HHHHHH", pkt[:12]
        )
        self.assertEqual(0x1234, qid)
        self.assertEqual(0, flags & 0x8000)  # QR=0
        self.assertEqual(0, flags & 0x7800)  # OPCODE=0
        self.assertEqual(1, qdcount)

    def test_build_query_edns_present(self):
        build = self._fn("_build_query")
        pkt = build(0x1234, ["example", "com"])
        # DNS_EDNS_SIZE is 1232 > 512, so EDNS OPT should be present.
        _qid, _flags, _qd, _an, _ns, arcount = struct.unpack(
            "!HHHHHH", pkt[:12]
        )
        self.assertEqual(1, arcount)

    # -- _parse_cname --

    def _build_cname_response(self, qid, qname_labels, cname_labels):
        """Build a minimal CNAME response message."""
        encode = self._fn("_encode_name")
        qname_wire = encode(qname_labels)
        question = qname_wire + struct.pack("!HH", 1, 1)  # QTYPE A, QCLASS IN

        # Answer: CNAME RR
        # Name matches qname (use full encoding, no compression for simplicity).
        rr_name = encode(qname_labels)
        cname_wire = encode(cname_labels)
        rdlen = len(cname_wire)
        rr = rr_name + struct.pack("!HHIH", 5, 1, 300, rdlen) + cname_wire

        flags = 0x8000  # QR=1, RCODE=0
        header = struct.pack("!HHHHHH", qid & 0xFFFF, flags, 1, 1, 0, 0)
        return header + question + rr

    def test_parse_cname_valid(self):
        parse = self._fn("_parse_cname")
        qname = ("tok0", "tag001", "example", "com")
        cname_target = ("abc", "r", "example", "com")
        msg = self._build_cname_response(0x1234, qname, cname_target)
        result = parse(msg, 0x1234, qname)
        self.assertEqual(tuple(cname_target), result)

    def test_parse_cname_wrong_id_raises(self):
        parse = self._fn("_parse_cname")
        qname = ("tok0", "tag001", "example", "com")
        cname_target = ("abc", "r", "example", "com")
        msg = self._build_cname_response(0x1234, qname, cname_target)
        with self.assertRaises(ValueError):
            parse(msg, 0x5678, qname)

    def test_parse_cname_missing_qr_raises(self):
        parse = self._fn("_parse_cname")
        encode = self._fn("_encode_name")
        qname = ("tok0", "tag001", "example", "com")
        qname_wire = encode(qname)
        question = qname_wire + struct.pack("!HH", 1, 1)
        # flags=0 -> QR not set
        header = struct.pack("!HHHHHH", 0x1234, 0, 1, 0, 0, 0)
        msg = header + question
        with self.assertRaises(ValueError):
            parse(msg, 0x1234, qname)

    def test_parse_cname_tc_flag_raises(self):
        parse = self._fn("_parse_cname")
        encode = self._fn("_encode_name")
        qname = ("tok0", "tag001", "example", "com")
        qname_wire = encode(qname)
        question = qname_wire + struct.pack("!HH", 1, 1)
        # QR=1, TC=1
        header = struct.pack("!HHHHHH", 0x1234, 0x8200, 1, 0, 0, 0)
        msg = header + question
        with self.assertRaises(ValueError):
            parse(msg, 0x1234, qname)

    def test_parse_cname_nonzero_rcode_raises(self):
        parse = self._fn("_parse_cname")
        encode = self._fn("_encode_name")
        qname = ("tok0", "tag001", "example", "com")
        qname_wire = encode(qname)
        question = qname_wire + struct.pack("!HH", 1, 1)
        # QR=1, RCODE=3 (NXDOMAIN)
        header = struct.pack("!HHHHHH", 0x1234, 0x8003, 1, 0, 0, 0)
        msg = header + question
        with self.assertRaises(ValueError):
            parse(msg, 0x1234, qname)

    def test_parse_cname_no_answer_raises(self):
        parse = self._fn("_parse_cname")
        encode = self._fn("_encode_name")
        qname = ("tok0", "tag001", "example", "com")
        qname_wire = encode(qname)
        question = qname_wire + struct.pack("!HH", 1, 1)
        # QR=1, ancount=0 -> no CNAME
        header = struct.pack("!HHHHHH", 0x1234, 0x8000, 1, 0, 0, 0)
        msg = header + question
        with self.assertRaises(ValueError):
            parse(msg, 0x1234, qname)

    # -- _extract_payload --

    def test_extract_payload_valid(self):
        extract = self._fn("_extract_payload")
        cname = ("abc", "def", "r", "example", "com")
        result = extract(cname)
        self.assertEqual("abcdef", result)

    def test_extract_payload_short_raises(self):
        extract = self._fn("_extract_payload")
        # Suffix is ("r", "example", "com") -> 3 labels.
        # Input must be strictly longer.
        with self.assertRaises(ValueError):
            extract(("r", "example", "com"))

    def test_extract_payload_suffix_mismatch_raises(self):
        extract = self._fn("_extract_payload")
        with self.assertRaises(ValueError):
            extract(("abc", "r", "wrong", "com"))

    # -- _b32d --

    def test_b32d_round_trip(self):
        import base64 as b64
        b32d = self._fn("_b32d")
        original = b"\x00\x01\x02\xff"
        encoded = b64.b32encode(original).decode("ascii").rstrip("=").lower()
        self.assertEqual(original, b32d(encoded))

    # -- _secure_compare --

    def test_secure_compare_equal(self):
        cmp_fn = self._fn("_secure_compare")
        self.assertTrue(cmp_fn(b"abc", b"abc"))

    def test_secure_compare_different(self):
        cmp_fn = self._fn("_secure_compare")
        self.assertFalse(cmp_fn(b"abc", b"abd"))

    def test_secure_compare_different_length(self):
        cmp_fn = self._fn("_secure_compare")
        self.assertFalse(cmp_fn(b"abc", b"ab"))

    # -- _xor --

    def test_xor_known_vector(self):
        xor_fn = self._fn("_xor")
        self.assertEqual(b"\x00\x00", xor_fn(b"\xff\xff", b"\xff\xff"))
        self.assertEqual(b"\xff\x00", xor_fn(b"\xaa\x55", b"\x55\x55"))

    # -- Crypto round-trip --

    def test_crypto_round_trip(self):
        """Encrypt a slice, build the binary record with MAC, verify _process_slice decrypts it."""
        enc_key_fn = self._fn("_enc_key")
        mac_key_fn = self._fn("_mac_key")
        keystream_fn = self._fn("_keystream")
        expected_mac_fn = self._fn("_expected_mac")
        xor_fn = self._fn("_xor")
        process_slice_fn = self._fn("_process_slice")

        psk = b"test-psk"
        si = 0
        plaintext = b"hello world slice data"

        ek = enc_key_fn(psk)
        mk = mac_key_fn(psk)

        # Encrypt.
        stream = keystream_fn(ek, si, len(plaintext))
        ciphertext = xor_fn(plaintext, stream)

        # Build MAC.
        mac = expected_mac_fn(mk, si, ciphertext)
        self.assertEqual(8, len(mac))

        # Build binary record: version(1) + reserved(1) + clen(2) + ciphertext + mac
        record = struct.pack("!BBH", 0x01, 0x00, len(ciphertext)) + ciphertext + mac

        # Base32-encode the record for _process_slice input.
        import base64 as b64
        payload_text = b64.b32encode(record).decode("ascii").rstrip("=").lower()

        # Decrypt via _process_slice.
        recovered = process_slice_fn(ek, mk, si, payload_text)
        self.assertEqual(plaintext, recovered)


if __name__ == "__main__":
    unittest.main()
