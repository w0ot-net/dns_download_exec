from __future__ import absolute_import, unicode_literals

from dnsdle.compat import encode_ascii
from dnsdle import constants as _c
from dnsdle.extract import extract_functions
from dnsdle.state import StartupError


# Extraction specifications: function names to extract from each module
_COMPAT_EXTRACTIONS = [
    "encode_ascii",
    "encode_utf8",
    "decode_ascii",
    "base32_lower_no_pad",
    "base32_decode_no_pad",
    "byte_value",
    "iter_byte_values",
    "constant_time_equals",
    "encode_ascii_int",
    "is_binary",
]

_HELPERS_EXTRACTIONS = ["hmac_sha256", "dns_name_wire_length"]

_DNSWIRE_EXTRACTIONS = ["_decode_name"]

_CNAME_PAYLOAD_EXTRACTIONS = [
    "_derive_file_bound_key",
    "_keystream_bytes",
    "_xor_bytes",
]

_CLIENT_RUNTIME_EXTRACTIONS = ["client_runtime"]


_PREAMBLE_CONSTANTS = (
    # DNS wire
    "DNS_FLAG_QR",
    "DNS_FLAG_TC",
    "DNS_FLAG_RD",
    "DNS_OPCODE_QUERY",
    "DNS_OPCODE_MASK",
    "DNS_QTYPE_A",
    "DNS_QTYPE_CNAME",
    "DNS_QTYPE_OPT",
    "DNS_QCLASS_IN",
    "DNS_HEADER_BYTES",
    "DNS_POINTER_TAG",
    "DNS_POINTER_VALUE_MASK",
    "DNS_RCODE_NOERROR",
    # payload
    "PAYLOAD_PROFILE_V1_BYTE",
    "PAYLOAD_FLAGS_V1_BYTE",
    "PAYLOAD_MAC_TRUNC_LEN",
    "PAYLOAD_ENC_KEY_LABEL",
    "PAYLOAD_ENC_STREAM_LABEL",
    "PAYLOAD_MAC_KEY_LABEL",
    "PAYLOAD_MAC_MESSAGE_LABEL",
    # mapping
    "MAPPING_FILE_LABEL",
    "MAPPING_SLICE_LABEL",
    "FILE_ID_PREFIX",
    # exit codes
    "EXIT_USAGE",
    "EXIT_TRANSPORT",
    "EXIT_PARSE",
    "EXIT_CRYPTO",
    "EXIT_REASSEMBLY",
    "EXIT_WRITE",
    # runtime tuning
    "REQUEST_TIMEOUT_SECONDS",
    "NO_PROGRESS_TIMEOUT_SECONDS",
    "MAX_ROUNDS",
    "MAX_CONSECUTIVE_TIMEOUTS",
    "RETRY_SLEEP_BASE_MS",
    "RETRY_SLEEP_JITTER_MS",
    "QUERY_INTERVAL_MS",
)


_PREAMBLE_HEADER = '''\
#!/usr/bin/env python
# -*- coding: ascii -*-
from __future__ import print_function

import argparse
import base64
import hashlib
import hmac
import os
import random
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

'''

_PREAMBLE_FOOTER = '''
try:
    text_type = unicode
    binary_type = str
    integer_types = (int, long)
    PY2 = True
except NameError:
    text_type = str
    binary_type = bytes
    integer_types = (int,)
    PY2 = False


class ClientError(Exception):
    def __init__(self, code, phase, message):
        Exception.__init__(self, message)
        self.code = int(code)
        self.phase = phase
        self.message = message


class RetryableTransport(Exception):
    pass


class DnsParseError(ClientError):
    def __init__(self, message):
        ClientError.__init__(self, EXIT_PARSE, "parse", message)

'''


_UNIVERSAL_CLIENT_FILENAME = "dnsdle_universal_client.py"


def build_client_source():
    constants_lines = "\n".join(
        "%s = %s" % (name, repr(getattr(_c, name)))
        for name in _PREAMBLE_CONSTANTS
    )
    preamble = _PREAMBLE_HEADER + constants_lines + "\n" + _PREAMBLE_FOOTER

    compat_blocks = extract_functions("compat.py", _COMPAT_EXTRACTIONS)
    helpers_blocks = extract_functions("helpers.py", _HELPERS_EXTRACTIONS)
    dnswire_blocks = extract_functions("dnswire.py", _DNSWIRE_EXTRACTIONS)
    cname_blocks = extract_functions("cname_payload.py", _CNAME_PAYLOAD_EXTRACTIONS)
    runtime_blocks = extract_functions("client_runtime.py", _CLIENT_RUNTIME_EXTRACTIONS)

    extracted_parts = (
        compat_blocks + helpers_blocks + dnswire_blocks
        + cname_blocks + runtime_blocks
    )

    extracted_source = "\n\n".join(extracted_parts)
    source = preamble + extracted_source + "\n"

    try:
        compile(source, "<universal_client>", "exec")
    except SyntaxError as exc:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "universal client source fails compilation: %s" % exc,
        )

    try:
        encode_ascii(source)
    except Exception:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "universal client source is not ASCII",
        )

    return source
