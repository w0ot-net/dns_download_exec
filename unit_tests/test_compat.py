from __future__ import absolute_import

import unittest

from dnsdle.compat import base32_decode_no_pad
from dnsdle.compat import base32_lower_no_pad
from dnsdle.compat import byte_value
from dnsdle.compat import constant_time_equals
from dnsdle.compat import is_binary
from dnsdle.compat import iter_byte_values
from dnsdle.compat import key_text
from dnsdle.compat import to_ascii_bytes
from dnsdle.compat import to_ascii_int_bytes
from dnsdle.compat import to_ascii_text
from dnsdle.compat import to_utf8_bytes


class CompatTests(unittest.TestCase):

    # -- to_ascii_bytes --

    def test_to_ascii_bytes_text_input(self):
        self.assertEqual(b"hello", to_ascii_bytes("hello"))

    def test_to_ascii_bytes_bytes_passthrough(self):
        self.assertEqual(b"hello", to_ascii_bytes(b"hello"))

    def test_to_ascii_bytes_rejects_non_string(self):
        with self.assertRaises(TypeError):
            to_ascii_bytes(42)

    # -- to_utf8_bytes --

    def test_to_utf8_bytes_text_input(self):
        self.assertEqual(b"hello", to_utf8_bytes("hello"))

    def test_to_utf8_bytes_bytes_passthrough(self):
        self.assertEqual(b"hello", to_utf8_bytes(b"hello"))

    def test_to_utf8_bytes_rejects_non_string(self):
        with self.assertRaises(TypeError):
            to_utf8_bytes(42)

    # -- to_ascii_text --

    def test_to_ascii_text_text_passthrough(self):
        self.assertEqual("hello", to_ascii_text("hello"))

    def test_to_ascii_text_bytes_input(self):
        self.assertEqual("hello", to_ascii_text(b"hello"))

    def test_to_ascii_text_rejects_non_string(self):
        with self.assertRaises(TypeError):
            to_ascii_text(42)

    # -- base32_lower_no_pad / base32_decode_no_pad round-trip --

    def test_base32_round_trip(self):
        original = b"\x00\x01\x02\xff"
        encoded = base32_lower_no_pad(original)
        decoded = base32_decode_no_pad(encoded)
        self.assertEqual(original, decoded)

    def test_base32_lower_no_pad_output_format(self):
        encoded = base32_lower_no_pad(b"test")
        self.assertEqual(encoded, encoded.lower())
        self.assertNotIn("=", encoded)

    def test_base32_lower_no_pad_known_vector(self):
        # b"f" -> base32 "MY======" -> lowercase no pad "my"
        self.assertEqual("my", base32_lower_no_pad(b"f"))

    def test_base32_decode_no_pad_empty_raises(self):
        with self.assertRaises(ValueError):
            base32_decode_no_pad("")

    def test_base32_decode_no_pad_padding_chars_raises(self):
        with self.assertRaises(ValueError):
            base32_decode_no_pad("my======")

    def test_base32_decode_no_pad_uppercase_raises(self):
        with self.assertRaises(ValueError):
            base32_decode_no_pad("MY")

    def test_base32_decode_no_pad_valid(self):
        self.assertEqual(b"f", base32_decode_no_pad("my"))

    def test_base32_decode_no_pad_invalid_chars_raises(self):
        with self.assertRaises(ValueError):
            base32_decode_no_pad("!!!!")

    # -- byte_value --

    def test_byte_value_int_in_range(self):
        self.assertEqual(0, byte_value(0))
        self.assertEqual(255, byte_value(255))

    def test_byte_value_int_out_of_range(self):
        with self.assertRaises(ValueError):
            byte_value(256)
        with self.assertRaises(ValueError):
            byte_value(-1)

    def test_byte_value_single_byte(self):
        self.assertEqual(65, byte_value(b"A"))

    def test_byte_value_multi_byte_raises(self):
        with self.assertRaises(ValueError):
            byte_value(b"AB")

    def test_byte_value_wrong_type_raises(self):
        with self.assertRaises(TypeError):
            byte_value("A")

    # -- iter_byte_values --

    def test_iter_byte_values_iterates(self):
        self.assertEqual([65, 66, 67], list(iter_byte_values(b"ABC")))

    def test_iter_byte_values_empty(self):
        self.assertEqual([], list(iter_byte_values(b"")))

    # -- constant_time_equals --

    def test_constant_time_equals_equal(self):
        self.assertTrue(constant_time_equals(b"abc", b"abc"))

    def test_constant_time_equals_different(self):
        self.assertFalse(constant_time_equals(b"abc", b"abd"))

    def test_constant_time_equals_different_length(self):
        self.assertFalse(constant_time_equals(b"abc", b"ab"))

    def test_constant_time_equals_non_binary_raises(self):
        with self.assertRaises(TypeError):
            constant_time_equals("abc", "abc")

    # -- to_ascii_int_bytes --

    def test_to_ascii_int_bytes_valid(self):
        self.assertEqual(b"42", to_ascii_int_bytes(42, "test"))

    def test_to_ascii_int_bytes_zero(self):
        self.assertEqual(b"0", to_ascii_int_bytes(0, "test"))

    def test_to_ascii_int_bytes_negative_raises(self):
        with self.assertRaises(ValueError):
            to_ascii_int_bytes(-1, "test")

    def test_to_ascii_int_bytes_non_int_raises(self):
        with self.assertRaises(ValueError):
            to_ascii_int_bytes("abc", "test")

    # -- is_binary --

    def test_is_binary_bytes(self):
        self.assertTrue(is_binary(b"data"))

    def test_is_binary_text(self):
        self.assertFalse(is_binary("data"))

    def test_is_binary_int(self):
        self.assertFalse(is_binary(42))

    # -- key_text --

    def test_key_text_text_passthrough(self):
        self.assertEqual("hello", key_text("hello"))

    def test_key_text_bytes_decoded(self):
        self.assertEqual("hello", key_text(b"hello"))

    def test_key_text_non_ascii_bytes_fallback(self):
        result = key_text(b"\xff\xfe")
        self.assertIsInstance(result, str)

    def test_key_text_int_coerced(self):
        self.assertEqual("42", key_text(42))


if __name__ == "__main__":
    unittest.main()
