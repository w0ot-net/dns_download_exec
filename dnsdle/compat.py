from __future__ import absolute_import

import base64
import sys


PY2 = sys.version_info[0] == 2

if PY2:
    text_type = unicode
    binary_type = str
    string_types = (str, unicode)
    integer_types = (int, long)
else:
    text_type = str
    binary_type = bytes
    string_types = (str, bytes)
    integer_types = (int,)


def to_ascii_bytes(value):
    if isinstance(value, binary_type):
        return value
    if isinstance(value, text_type):
        return value.encode("ascii")
    raise TypeError("value must be text or bytes")


def to_utf8_bytes(value):
    if isinstance(value, binary_type):
        return value
    if isinstance(value, text_type):
        return value.encode("utf-8")
    raise TypeError("value must be text or bytes")


def to_ascii_text(value):
    if isinstance(value, text_type):
        return value
    if isinstance(value, binary_type):
        return value.decode("ascii")
    raise TypeError("value must be text or bytes")


def base32_lower_no_pad(raw_bytes):
    encoded = base64.b32encode(raw_bytes)
    text = to_ascii_text(encoded)
    return text.rstrip("=").lower()


def base32_decode_no_pad(value):
    text = to_ascii_text(value)
    if not text:
        raise ValueError("base32 text must be non-empty")
    if "=" in text:
        raise ValueError("base32 text must not include padding")
    if text != text.lower():
        raise ValueError("base32 text must be lowercase")
    padding_len = (-len(text)) % 8
    padded = text.upper() + ("=" * padding_len)
    try:
        return base64.b32decode(to_ascii_bytes(padded))
    except Exception:
        raise ValueError("invalid base32 text")


def byte_value(value):
    if isinstance(value, integer_types):
        int_value = int(value)
        if int_value < 0 or int_value > 255:
            raise ValueError("byte value out of range")
        return int_value
    if isinstance(value, binary_type):
        if len(value) != 1:
            raise ValueError("byte input must be length 1")
        if PY2:
            return ord(value)
        return value[0]
    raise TypeError("value must be integer or single-byte value")


def iter_byte_values(raw_bytes):
    for value in raw_bytes:
        yield byte_value(value)


def constant_time_equals(left_value, right_value):
    if not is_binary(left_value) or not is_binary(right_value):
        raise TypeError("values must be bytes")
    if len(left_value) != len(right_value):
        return False
    result = 0
    for left_byte, right_byte in zip(iter_byte_values(left_value), iter_byte_values(right_value)):
        result |= left_byte ^ right_byte
    return result == 0


def to_ascii_int_bytes(value, field_name):
    try:
        int_value = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer" % field_name)
    if int_value < 0:
        raise ValueError("%s must be non-negative" % field_name)
    return to_ascii_bytes(str(int_value))


def is_binary(value):
    return isinstance(value, binary_type)


def key_text(value):
    if isinstance(value, text_type):
        return value
    if is_binary(value):
        try:
            return to_ascii_text(value)
        except Exception:
            return str(value)
    return str(value)
