from __future__ import absolute_import


CLIENT_TEMPLATE = '''#!/usr/bin/env python
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


BASE_DOMAINS = @@BASE_DOMAINS@@
FILE_TAG = @@FILE_TAG@@
FILE_ID = @@FILE_ID@@
PUBLISH_VERSION = @@PUBLISH_VERSION@@
TARGET_OS = @@TARGET_OS@@
TOTAL_SLICES = @@TOTAL_SLICES@@
COMPRESSED_SIZE = @@COMPRESSED_SIZE@@
PLAINTEXT_SHA256_HEX = @@PLAINTEXT_SHA256_HEX@@
SLICE_TOKENS = @@SLICE_TOKENS@@
CRYPTO_PROFILE = @@CRYPTO_PROFILE@@
WIRE_PROFILE = @@WIRE_PROFILE@@
RESPONSE_LABEL = @@RESPONSE_LABEL@@
DNS_MAX_LABEL_LEN = @@DNS_MAX_LABEL_LEN@@
DNS_EDNS_SIZE = @@DNS_EDNS_SIZE@@

REQUEST_TIMEOUT_SECONDS = @@REQUEST_TIMEOUT_SECONDS@@
NO_PROGRESS_TIMEOUT_SECONDS = @@NO_PROGRESS_TIMEOUT_SECONDS@@
MAX_ROUNDS = @@MAX_ROUNDS@@
MAX_CONSECUTIVE_TIMEOUTS = @@MAX_CONSECUTIVE_TIMEOUTS@@
RETRY_SLEEP_BASE_MS = @@RETRY_SLEEP_BASE_MS@@
RETRY_SLEEP_JITTER_MS = @@RETRY_SLEEP_JITTER_MS@@

DNS_FLAG_QR = 0x8000
DNS_FLAG_TC = 0x0200
DNS_FLAG_RD = 0x0100
DNS_OPCODE_QUERY = 0x0000
DNS_OPCODE_MASK = 0x7800
DNS_QTYPE_A = 1
DNS_QTYPE_CNAME = 5
DNS_QTYPE_OPT = 41
DNS_QCLASS_IN = 1
DNS_HEADER_BYTES = 12
DNS_POINTER_TAG = 0xC0
DNS_POINTER_VALUE_MASK = 0x3FFF
DNS_RCODE_NOERROR = 0

PAYLOAD_PROFILE_V1_BYTE = 0x01
PAYLOAD_FLAGS_V1_BYTE = 0x00
PAYLOAD_MAC_TRUNC_LEN = 8
PAYLOAD_ENC_KEY_LABEL = b"dnsdle-enc-v1|"
PAYLOAD_ENC_STREAM_LABEL = b"dnsdle-enc-stream-v1|"
PAYLOAD_MAC_KEY_LABEL = b"dnsdle-mac-v1|"
PAYLOAD_MAC_MESSAGE_LABEL = b"dnsdle-mac-msg-v1|"

EXIT_USAGE = 2
EXIT_TRANSPORT = 3
EXIT_PARSE = 4
EXIT_CRYPTO = 5
EXIT_REASSEMBLY = 6
EXIT_WRITE = 7

_TOKEN_RE = re.compile(r"^[a-z0-9]+$")
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_IPV4_RE = re.compile(r"(\\d{1,3}(?:\\.\\d{1,3}){3})")


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


def _log(message):
    sys.stderr.write(str(message) + "\\n")
    sys.stderr.flush()


def _to_ascii_bytes(value):
    if isinstance(value, binary_type):
        return value
    if isinstance(value, text_type):
        return value.encode("ascii")
    raise TypeError("value must be text or bytes")


def _to_utf8_bytes(value):
    if isinstance(value, binary_type):
        return value
    if isinstance(value, text_type):
        return value.encode("utf-8")
    raise TypeError("value must be text or bytes")


def _to_ascii_text(value):
    if isinstance(value, text_type):
        return value
    if isinstance(value, binary_type):
        return value.decode("ascii")
    raise TypeError("value must be text or bytes")


def _to_ascii_int_bytes(value, field_name):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer" % field_name)
    if number < 0:
        raise ValueError("%s must be non-negative" % field_name)
    return _to_ascii_bytes(str(number))


def _byte_value(value):
    if isinstance(value, integer_types):
        return int(value)
    if isinstance(value, binary_type):
        if len(value) != 1:
            raise ValueError("byte value must be length 1")
        if PY2:
            return ord(value)
        return value[0]
    raise TypeError("invalid byte value type")


def _byte_at(raw, index):
    return _byte_value(raw[index])


def _bytes_from_bytearray(values):
    if PY2:
        return "".join(chr(v) for v in values)
    return bytes(values)


def _hmac_sha256(key_bytes, message_bytes):
    return hmac.new(key_bytes, message_bytes, hashlib.sha256).digest()


def _secure_compare(left, right):
    compare = getattr(hmac, "compare_digest", None)
    if compare is not None:
        try:
            return bool(compare(left, right))
        except Exception:
            pass
    if len(left) != len(right):
        return False
    result = 0
    for index in range(len(left)):
        result |= _byte_at(left, index) ^ _byte_at(right, index)
    return result == 0


def _dns_name_wire_length(labels):
    return 1 + sum(1 + len(label) for label in labels)


def _encode_name(labels):
    parts = []
    for label in labels:
        raw = _to_ascii_bytes(label)
        if not raw:
            raise ClientError(EXIT_PARSE, "parse", "empty DNS label")
        if len(raw) > 63:
            raise ClientError(EXIT_PARSE, "parse", "DNS label too long")
        parts.append(struct.pack("!B", len(raw)))
        parts.append(raw)
    parts.append(b"\\x00")
    return b"".join(parts)


def _decode_name(message, start_offset):
    labels = []
    message_len = len(message)
    offset = start_offset
    jumped = False
    read_end_offset = None
    visited_offsets = set()

    while True:
        if offset >= message_len:
            raise ClientError(EXIT_PARSE, "parse", "name extends past message")

        first = _byte_at(message, offset)
        if (first & DNS_POINTER_TAG) == DNS_POINTER_TAG:
            if offset + 1 >= message_len:
                raise ClientError(EXIT_PARSE, "parse", "truncated name pointer")
            pointer = ((first << 8) | _byte_at(message, offset + 1)) & DNS_POINTER_VALUE_MASK
            if pointer >= message_len:
                raise ClientError(EXIT_PARSE, "parse", "name pointer out of bounds")
            if pointer in visited_offsets:
                raise ClientError(EXIT_PARSE, "parse", "name pointer loop detected")
            visited_offsets.add(pointer)
            if not jumped:
                read_end_offset = offset + 2
                jumped = True
            offset = pointer
            continue

        if first & DNS_POINTER_TAG:
            raise ClientError(EXIT_PARSE, "parse", "invalid name label type")

        offset += 1
        if first == 0:
            break

        end_offset = offset + first
        if end_offset > message_len:
            raise ClientError(EXIT_PARSE, "parse", "label extends past message")
        raw = message[offset:end_offset]
        try:
            label = _to_ascii_text(raw).lower()
        except Exception:
            raise ClientError(EXIT_PARSE, "parse", "label is not ASCII")
        labels.append(label)
        offset = end_offset

        if len(labels) > 127:
            raise ClientError(EXIT_PARSE, "parse", "too many labels")

    return tuple(labels), (read_end_offset if jumped else offset)


def _build_dns_query(query_id, qname_labels):
    qname = _encode_name(qname_labels)
    question = qname + struct.pack("!HH", DNS_QTYPE_A, DNS_QCLASS_IN)
    include_opt = DNS_EDNS_SIZE > 512
    arcount = 1 if include_opt else 0
    header = struct.pack(
        "!HHHHHH",
        int(query_id) & 0xFFFF,
        DNS_FLAG_RD,
        1,
        0,
        0,
        arcount,
    )
    message = header + question
    if include_opt:
        message += b"\\x00" + struct.pack("!HHIH", DNS_QTYPE_OPT, DNS_EDNS_SIZE, 0, 0)
    return message


def _parse_response_for_cname(message, expected_id, expected_qname_labels):
    if len(message) < DNS_HEADER_BYTES:
        raise ClientError(EXIT_PARSE, "parse", "response shorter than DNS header")

    response_id, flags, qdcount, ancount, nscount, arcount = struct.unpack(
        "!HHHHHH", message[:DNS_HEADER_BYTES]
    )
    if response_id != (int(expected_id) & 0xFFFF):
        raise ClientError(EXIT_PARSE, "parse", "response ID mismatch")
    if (flags & DNS_FLAG_QR) == 0:
        raise ClientError(EXIT_PARSE, "parse", "response missing QR flag")
    if flags & DNS_FLAG_TC:
        raise ClientError(EXIT_PARSE, "parse", "response sets TC")
    if (flags & DNS_OPCODE_MASK) != DNS_OPCODE_QUERY:
        raise ClientError(EXIT_PARSE, "parse", "response opcode is not QUERY")

    rcode = flags & 0x000F
    if rcode != DNS_RCODE_NOERROR:
        raise ClientError(EXIT_PARSE, "parse", "unexpected DNS rcode=%d" % rcode)
    if qdcount != 1:
        raise ClientError(EXIT_PARSE, "parse", "response qdcount is not 1")

    offset = DNS_HEADER_BYTES
    qname_labels, offset = _decode_name(message, offset)
    if offset + 4 > len(message):
        raise ClientError(EXIT_PARSE, "parse", "truncated response question")
    qtype, qclass = struct.unpack("!HH", message[offset:offset + 4])
    offset += 4

    expected_qname = tuple(expected_qname_labels)
    if qname_labels != expected_qname:
        raise ClientError(EXIT_PARSE, "parse", "response question name mismatch")
    if qtype != DNS_QTYPE_A or qclass != DNS_QCLASS_IN:
        raise ClientError(EXIT_PARSE, "parse", "response question type/class mismatch")

    cname_labels = None
    cname_matches = 0

    def _consume_rrs(current_offset, count):
        results = []
        for _ in range(count):
            rr_name, current_offset = _decode_name(message, current_offset)
            if current_offset + 10 > len(message):
                raise ClientError(EXIT_PARSE, "parse", "truncated answer RR header")
            rr_type, rr_class, _rr_ttl, rdlength = struct.unpack(
                "!HHIH", message[current_offset:current_offset + 10]
            )
            current_offset += 10
            rdata_offset = current_offset
            rdata_end = current_offset + rdlength
            if rdata_end > len(message):
                raise ClientError(EXIT_PARSE, "parse", "truncated answer RDATA")
            results.append((rr_name, rr_type, rr_class, rdata_offset, rdata_end))
            current_offset = rdata_end
        return results, current_offset

    answers, offset = _consume_rrs(offset, ancount)
    for rr_name, rr_type, rr_class, rdata_offset, rdata_end in answers:
        if rr_type == DNS_QTYPE_CNAME and rr_class == DNS_QCLASS_IN and rr_name == expected_qname:
            parsed_labels, parsed_end = _decode_name(message, rdata_offset)
            if parsed_end != rdata_end:
                raise ClientError(EXIT_PARSE, "parse", "CNAME RDATA length mismatch")
            cname_matches += 1
            if cname_matches > 1:
                raise ClientError(EXIT_PARSE, "parse", "multiple matching CNAME answers")
            cname_labels = parsed_labels

    _, offset = _consume_rrs(offset, nscount)
    _, offset = _consume_rrs(offset, arcount)
    if offset != len(message):
        raise ClientError(EXIT_PARSE, "parse", "trailing bytes in response message")

    if cname_labels is None:
        raise ClientError(EXIT_PARSE, "parse", "missing required CNAME answer")

    return cname_labels


def _base32_decode_no_pad(text_value):
    text = _to_ascii_text(text_value)
    if not text:
        raise ClientError(EXIT_PARSE, "parse", "empty base32 payload")
    padded = text.upper()
    pad_len = (8 - (len(padded) % 8)) % 8
    padded = padded + ("=" * pad_len)
    try:
        return base64.b32decode(_to_ascii_bytes(padded))
    except Exception:
        raise ClientError(EXIT_PARSE, "parse", "invalid base32 payload")


def _extract_payload_text(cname_labels, selected_domain_labels):
    suffix = (RESPONSE_LABEL,) + tuple(selected_domain_labels)
    if len(cname_labels) <= len(suffix):
        raise ClientError(EXIT_PARSE, "parse", "CNAME target too short")
    if tuple(cname_labels[-len(suffix):]) != suffix:
        raise ClientError(EXIT_PARSE, "parse", "CNAME target suffix mismatch")

    payload_labels = cname_labels[:-len(suffix)]
    if not payload_labels:
        raise ClientError(EXIT_PARSE, "parse", "payload labels are empty")
    for label in payload_labels:
        if not label:
            raise ClientError(EXIT_PARSE, "parse", "empty payload label")
        if len(label) > DNS_MAX_LABEL_LEN:
            raise ClientError(EXIT_PARSE, "parse", "payload label exceeds DNS_MAX_LABEL_LEN")

    return "".join(payload_labels)


def _enc_key(psk):
    return _hmac_sha256(
        _to_utf8_bytes(psk),
        PAYLOAD_ENC_KEY_LABEL
        + _to_ascii_bytes(FILE_ID)
        + b"|"
        + _to_ascii_bytes(PUBLISH_VERSION),
    )


def _mac_key(psk):
    return _hmac_sha256(
        _to_utf8_bytes(psk),
        PAYLOAD_MAC_KEY_LABEL
        + _to_ascii_bytes(FILE_ID)
        + b"|"
        + _to_ascii_bytes(PUBLISH_VERSION),
    )


def _keystream_bytes(enc_key_bytes, slice_index, output_len):
    if output_len <= 0:
        raise ClientError(EXIT_CRYPTO, "crypto", "output_len must be positive")
    blocks = []
    produced = 0
    counter = 0
    slice_index_bytes = _to_ascii_int_bytes(slice_index, "slice_index")
    while produced < output_len:
        counter_bytes = _to_ascii_int_bytes(counter, "counter")
        block_input = (
            PAYLOAD_ENC_STREAM_LABEL
            + _to_ascii_bytes(FILE_ID)
            + b"|"
            + _to_ascii_bytes(PUBLISH_VERSION)
            + b"|"
            + slice_index_bytes
            + b"|"
            + counter_bytes
        )
        block = _hmac_sha256(enc_key_bytes, block_input)
        blocks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(blocks)[:output_len]


def _xor_bytes(left_bytes, right_bytes):
    if len(left_bytes) != len(right_bytes):
        raise ClientError(EXIT_CRYPTO, "crypto", "xor input length mismatch")
    out = bytearray(len(left_bytes))
    for index in range(len(left_bytes)):
        out[index] = _byte_at(left_bytes, index) ^ _byte_at(right_bytes, index)
    return _bytes_from_bytearray(out)


def _parse_slice_record(payload_text):
    record = _base32_decode_no_pad(payload_text)
    if len(record) < 12:
        raise ClientError(EXIT_PARSE, "parse", "slice record is too short")

    profile = _byte_at(record, 0)
    flags = _byte_at(record, 1)
    if profile != PAYLOAD_PROFILE_V1_BYTE:
        raise ClientError(EXIT_PARSE, "parse", "unsupported payload profile")
    if flags != PAYLOAD_FLAGS_V1_BYTE:
        raise ClientError(EXIT_PARSE, "parse", "unsupported payload flags")

    cipher_len = struct.unpack("!H", record[2:4])[0]
    if cipher_len <= 0:
        raise ClientError(EXIT_PARSE, "parse", "cipher_len must be positive")

    expected_total = 4 + cipher_len + PAYLOAD_MAC_TRUNC_LEN
    if len(record) != expected_total:
        raise ClientError(EXIT_PARSE, "parse", "slice record length mismatch")

    ciphertext = record[4:4 + cipher_len]
    mac = record[4 + cipher_len:]
    return ciphertext, mac


def _expected_mac(mac_key_bytes, slice_index, ciphertext):
    message = (
        PAYLOAD_MAC_MESSAGE_LABEL
        + _to_ascii_bytes(FILE_ID)
        + b"|"
        + _to_ascii_bytes(PUBLISH_VERSION)
        + b"|"
        + _to_ascii_int_bytes(slice_index, "slice_index")
        + b"|"
        + _to_ascii_int_bytes(TOTAL_SLICES, "total_slices")
        + b"|"
        + _to_ascii_int_bytes(COMPRESSED_SIZE, "compressed_size")
        + b"|"
        + ciphertext
    )
    return _hmac_sha256(mac_key_bytes, message)[:PAYLOAD_MAC_TRUNC_LEN]


def _decrypt_and_verify_slice(enc_key_bytes, mac_key_bytes, slice_index, ciphertext, mac):
    expected_mac = _expected_mac(mac_key_bytes, slice_index, ciphertext)
    if not _secure_compare(expected_mac, mac):
        raise ClientError(EXIT_CRYPTO, "crypto", "MAC verification failed")

    stream = _keystream_bytes(enc_key_bytes, slice_index, len(ciphertext))
    plaintext = _xor_bytes(ciphertext, stream)
    if not plaintext:
        raise ClientError(EXIT_CRYPTO, "crypto", "decrypted slice is empty")
    return plaintext


def _reassemble_plaintext(slice_bytes_by_index):
    ordered = []
    for index in range(TOTAL_SLICES):
        if index not in slice_bytes_by_index:
            raise ClientError(EXIT_REASSEMBLY, "reassembly", "missing slice index %d" % index)
        ordered.append(slice_bytes_by_index[index])

    compressed = b"".join(ordered)
    if len(compressed) != COMPRESSED_SIZE:
        raise ClientError(
            EXIT_REASSEMBLY,
            "reassembly",
            "compressed size mismatch expected=%d got=%d" % (COMPRESSED_SIZE, len(compressed)),
        )

    try:
        plaintext = zlib.decompress(compressed)
    except Exception as exc:
        raise ClientError(EXIT_REASSEMBLY, "reassembly", "decompress failed: %s" % exc)

    digest = hashlib.sha256(plaintext).hexdigest().lower()
    if digest != PLAINTEXT_SHA256_HEX:
        raise ClientError(EXIT_REASSEMBLY, "reassembly", "plaintext sha256 mismatch")
    return plaintext


def _deterministic_output_path():
    name = "dnsdl_%s_%s_%s.bin" % (
        FILE_ID,
        PUBLISH_VERSION[:8],
        PLAINTEXT_SHA256_HEX[:8],
    )
    return os.path.join(tempfile.gettempdir(), name)


def _write_output_atomic(output_path, payload):
    directory = os.path.dirname(output_path) or "."
    if not os.path.isdir(directory):
        raise ClientError(EXIT_WRITE, "write", "output directory does not exist")

    temp_path = output_path + ".tmp-%d" % os.getpid()
    try:
        with open(temp_path, "wb") as handle:
            handle.write(payload)
        if os.path.exists(output_path):
            os.remove(output_path)
        os.rename(temp_path, output_path)
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        raise ClientError(EXIT_WRITE, "write", "failed to write output: %s" % exc)


def _parse_positive_float(raw_value, flag_name):
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise ClientError(EXIT_USAGE, "usage", "%s must be a number" % flag_name)
    if value <= 0:
        raise ClientError(EXIT_USAGE, "usage", "%s must be > 0" % flag_name)
    return value


def _parse_positive_int(raw_value, flag_name):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ClientError(EXIT_USAGE, "usage", "%s must be an integer" % flag_name)
    if value <= 0:
        raise ClientError(EXIT_USAGE, "usage", "%s must be > 0" % flag_name)
    return value


def _resolve_udp_address(host, port):
    infos = socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_DGRAM)
    if not infos:
        raise ValueError("resolver lookup returned no addresses")
    sockaddr = infos[0][4]
    return sockaddr[0], int(sockaddr[1])


def _parse_resolver_arg(raw_value):
    value = (raw_value or "").strip()
    if not value:
        raise ClientError(EXIT_USAGE, "usage", "--resolver is empty")

    host = value
    port_text = "53"
    if value.startswith("["):
        end = value.find("]")
        if end <= 1:
            raise ClientError(EXIT_USAGE, "usage", "--resolver has invalid bracket form")
        host = value[1:end]
        remainder = value[end + 1:]
        if remainder:
            if not remainder.startswith(":"):
                raise ClientError(EXIT_USAGE, "usage", "--resolver has invalid bracket form")
            port_text = remainder[1:]
    elif value.count(":") == 1:
        host, port_text = value.rsplit(":", 1)

    host = host.strip()
    port_text = (port_text or "53").strip()
    if not host:
        raise ClientError(EXIT_USAGE, "usage", "--resolver host is empty")

    try:
        port = int(port_text)
    except (TypeError, ValueError):
        raise ClientError(EXIT_USAGE, "usage", "--resolver port is invalid")
    if port <= 0 or port > 65535:
        raise ClientError(EXIT_USAGE, "usage", "--resolver port is out of range")

    try:
        return _resolve_udp_address(host, port)
    except Exception as exc:
        raise ClientError(EXIT_USAGE, "usage", "--resolver lookup failed: %s" % exc)


def _load_unix_resolvers():
    resolvers = []
    try:
        with open("/etc/resolv.conf", "r") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "#" in line:
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                if parts[0].lower() != "nameserver":
                    continue
                host = parts[1].strip()
                if not host:
                    continue
                if host not in resolvers:
                    resolvers.append(host)
    except Exception:
        return []
    return resolvers


def _run_nslookup():
    args = ["nslookup", "google.com"]
    run_fn = getattr(subprocess, "run", None)
    if run_fn is not None:
        result = run_fn(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            universal_newlines=True,
        )
        return result.stdout or ""

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    output, _ = proc.communicate()
    return output or ""


def _parse_nslookup_output(output):
    lines = output.splitlines()
    server_index = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("server:"):
            server_index = index
            break
    if server_index is None:
        return None

    for line in lines[server_index + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("non-authoritative answer"):
            break
        match = _IPV4_RE.search(stripped)
        if match:
            return match.group(1)
    return None


def _load_windows_resolvers():
    try:
        output = _run_nslookup()
    except Exception:
        return []

    ip = _parse_nslookup_output(output)
    if not ip:
        return []
    return [ip]


def _discover_system_resolver():
    if os.name == "nt":
        resolver_hosts = _load_windows_resolvers()
    else:
        resolver_hosts = _load_unix_resolvers()

    for host in resolver_hosts:
        try:
            return _resolve_udp_address(host, 53)
        except Exception:
            continue

    raise ClientError(EXIT_TRANSPORT, "dns", "no system DNS resolver found")


def _send_dns_query(resolver_addr, query_packet, timeout_seconds):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout_seconds)
        sock.sendto(query_packet, resolver_addr)
        response, source = sock.recvfrom(max(2048, DNS_EDNS_SIZE + 2048))
    except socket.timeout:
        raise RetryableTransport("dns timeout")
    except socket.error as exc:
        raise RetryableTransport("socket error: %s" % exc)
    finally:
        try:
            sock.close()
        except Exception:
            pass

    if source[0] != resolver_addr[0] or int(source[1]) != int(resolver_addr[1]):
        raise RetryableTransport("unexpected resolver response source")
    return response


def _retry_sleep():
    delay_ms = RETRY_SLEEP_BASE_MS
    if RETRY_SLEEP_JITTER_MS > 0:
        delay_ms += random.randint(0, RETRY_SLEEP_JITTER_MS)
    time.sleep(float(delay_ms) / 1000.0)


def _validate_embedded_constants():
    if CRYPTO_PROFILE != "v1":
        raise ClientError(EXIT_USAGE, "usage", "unsupported CRYPTO_PROFILE")
    if WIRE_PROFILE != "v1":
        raise ClientError(EXIT_USAGE, "usage", "unsupported WIRE_PROFILE")

    if not BASE_DOMAINS:
        raise ClientError(EXIT_USAGE, "usage", "BASE_DOMAINS is empty")

    domain_labels = []
    for domain in BASE_DOMAINS:
        normalized = (domain or "").strip().lower().rstrip(".")
        if not normalized:
            raise ClientError(EXIT_USAGE, "usage", "invalid base domain")
        labels = tuple(normalized.split("."))
        for label in labels:
            if not _LABEL_RE.match(label):
                raise ClientError(EXIT_USAGE, "usage", "invalid base-domain label")
        domain_labels.append(labels)

    if not FILE_TAG or not _TOKEN_RE.match(FILE_TAG):
        raise ClientError(EXIT_USAGE, "usage", "invalid FILE_TAG")

    if TOTAL_SLICES <= 0:
        raise ClientError(EXIT_USAGE, "usage", "TOTAL_SLICES must be > 0")
    if len(SLICE_TOKENS) != TOTAL_SLICES:
        raise ClientError(EXIT_USAGE, "usage", "SLICE_TOKENS length mismatch")

    seen_tokens = set()
    for token in SLICE_TOKENS:
        if not token or not _TOKEN_RE.match(token):
            raise ClientError(EXIT_USAGE, "usage", "invalid slice token")
        if len(token) > DNS_MAX_LABEL_LEN:
            raise ClientError(EXIT_USAGE, "usage", "slice token too long")
        if token in seen_tokens:
            raise ClientError(EXIT_USAGE, "usage", "duplicate slice token")
        seen_tokens.add(token)

    if COMPRESSED_SIZE <= 0:
        raise ClientError(EXIT_USAGE, "usage", "COMPRESSED_SIZE must be > 0")
    if not PLAINTEXT_SHA256_HEX or len(PLAINTEXT_SHA256_HEX) != 64:
        raise ClientError(EXIT_USAGE, "usage", "PLAINTEXT_SHA256_HEX is invalid")

    if not RESPONSE_LABEL or not _LABEL_RE.match(RESPONSE_LABEL):
        raise ClientError(EXIT_USAGE, "usage", "RESPONSE_LABEL is invalid")

    for labels in domain_labels:
        if _dns_name_wire_length((RESPONSE_LABEL,) + labels) > 255:
            raise ClientError(EXIT_USAGE, "usage", "response suffix exceeds DNS limits")

    return tuple(domain_labels)


def _download_slices(psk_value, resolver_addr, request_timeout, no_progress_timeout, max_rounds, domain_labels_by_domain):
    missing = set(range(TOTAL_SLICES))
    stored = {}
    enc_key_bytes = _enc_key(psk_value)
    mac_key_bytes = _mac_key(psk_value)

    last_progress_time = time.time()
    domain_index = 0
    rounds = 0
    consecutive_timeouts = 0

    while missing:
        rounds += 1
        if rounds > max_rounds:
            raise ClientError(EXIT_TRANSPORT, "dns", "max rounds exhausted")

        progress_this_round = False
        current_missing = sorted(missing)
        for slice_index in current_missing:
            if (time.time() - last_progress_time) >= no_progress_timeout:
                raise ClientError(EXIT_TRANSPORT, "dns", "no-progress timeout")

            domain_labels = domain_labels_by_domain[domain_index]
            qname_labels = (SLICE_TOKENS[slice_index], FILE_TAG) + domain_labels
            query_id = random.randint(0, 0xFFFF)
            query_packet = _build_dns_query(query_id, qname_labels)

            try:
                response = _send_dns_query(resolver_addr, query_packet, request_timeout)
            except RetryableTransport:
                consecutive_timeouts += 1
                if consecutive_timeouts > MAX_CONSECUTIVE_TIMEOUTS:
                    raise ClientError(
                        EXIT_TRANSPORT,
                        "dns",
                        "transport retries exhausted",
                    )
                domain_index = (domain_index + 1) % len(BASE_DOMAINS)
                _retry_sleep()
                continue

            consecutive_timeouts = 0
            cname_labels = _parse_response_for_cname(response, query_id, qname_labels)
            payload_text = _extract_payload_text(cname_labels, domain_labels)
            ciphertext, mac = _parse_slice_record(payload_text)
            slice_plain = _decrypt_and_verify_slice(
                enc_key_bytes,
                mac_key_bytes,
                slice_index,
                ciphertext,
                mac,
            )

            current_value = stored.get(slice_index)
            if current_value is None:
                stored[slice_index] = slice_plain
                missing.remove(slice_index)
                progress_this_round = True
                last_progress_time = time.time()
                _log(
                    "progress received=%d missing=%d" % (
                        len(stored),
                        len(missing),
                    )
                )
            elif current_value != slice_plain:
                raise ClientError(EXIT_CRYPTO, "crypto", "duplicate slice mismatch")

        if not progress_this_round and (time.time() - last_progress_time) >= no_progress_timeout:
            raise ClientError(EXIT_TRANSPORT, "dns", "no-progress timeout")

    return stored


def _build_parser():
    parser = argparse.ArgumentParser(description="dnsdle generated file downloader")
    parser.add_argument("--psk", required=True)
    parser.add_argument("--resolver", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--timeout", default=str(REQUEST_TIMEOUT_SECONDS))
    parser.add_argument(
        "--no-progress-timeout",
        default=str(NO_PROGRESS_TIMEOUT_SECONDS),
    )
    parser.add_argument("--max-rounds", default=str(MAX_ROUNDS))
    return parser


def _parse_runtime_args(argv):
    parser = _build_parser()
    args = parser.parse_args(argv)

    psk_value = (args.psk or "").strip()
    if not psk_value:
        raise ClientError(EXIT_USAGE, "usage", "--psk must be non-empty")

    timeout_seconds = _parse_positive_float(args.timeout, "--timeout")
    no_progress_timeout = _parse_positive_float(
        args.no_progress_timeout,
        "--no-progress-timeout",
    )
    max_rounds = _parse_positive_int(args.max_rounds, "--max-rounds")

    resolver_arg = (args.resolver or "").strip()
    if resolver_arg:
        resolver_addr = _parse_resolver_arg(resolver_arg)
    else:
        resolver_addr = _discover_system_resolver()

    out_path = (args.out or "").strip()
    if not out_path:
        out_path = _deterministic_output_path()

    return psk_value, resolver_addr, out_path, timeout_seconds, no_progress_timeout, max_rounds


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    try:
        domain_labels_by_domain = _validate_embedded_constants()
        (
            psk_value,
            resolver_addr,
            out_path,
            timeout_seconds,
            no_progress_timeout,
            max_rounds,
        ) = _parse_runtime_args(argv)
        _log(
            "start file_id=%s target_os=%s resolver=%s:%d slices=%d" % (
                FILE_ID,
                TARGET_OS,
                resolver_addr[0],
                resolver_addr[1],
                TOTAL_SLICES,
            )
        )

        slices = _download_slices(
            psk_value,
            resolver_addr,
            timeout_seconds,
            no_progress_timeout,
            max_rounds,
            domain_labels_by_domain,
        )
        plaintext = _reassemble_plaintext(slices)
        _write_output_atomic(out_path, plaintext)
        _log("success wrote=%s bytes=%d" % (out_path, len(plaintext)))
        return 0
    except ClientError as exc:
        _log("error phase=%s code=%d message=%s" % (exc.phase, exc.code, exc.message))
        return exc.code
    except KeyboardInterrupt:
        _log("error phase=dns code=%d message=interrupted" % EXIT_TRANSPORT)
        return EXIT_TRANSPORT


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''
