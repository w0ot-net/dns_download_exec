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
