from __future__ import absolute_import, unicode_literals

# stdlib -- mirrors what _PREAMBLE_HEADER injects into the assembled client
import sys, os, re, struct, socket, subprocess, time, random
import hashlib, zlib, argparse, base64, hmac, tempfile

# dnsdle utility functions extracted into the assembled client ahead of this block
from dnsdle.compat import (
    encode_ascii, encode_ascii_int,
    base32_lower_no_pad, base32_decode_no_pad,
    byte_value, constant_time_equals,
)
from dnsdle.helpers import (
    hmac_sha256, dns_name_wire_length,
    _derive_file_id, _derive_file_tag, _derive_slice_token,
)
from dnsdle.dnswire import _decode_name
from dnsdle.cname_payload import _derive_file_bound_key, _keystream_bytes, _xor_bytes

# dnsdle constants -- DNS/payload/mapping/exit/tuning values used by the
# extract block; canonical home is constants.py
from dnsdle.constants import (
    DNS_FLAG_QR, DNS_FLAG_TC, DNS_FLAG_RD,
    DNS_HEADER_BYTES, DNS_OPCODE_MASK, DNS_OPCODE_QUERY,
    DNS_QCLASS_IN, DNS_QTYPE_A, DNS_QTYPE_CNAME, DNS_QTYPE_OPT,
    DNS_RCODE_NOERROR,
    EXIT_CRYPTO, EXIT_PARSE, EXIT_REASSEMBLY,
    EXIT_TRANSPORT, EXIT_USAGE, EXIT_WRITE,
    FILE_ID_PREFIX, MAPPING_FILE_LABEL, MAPPING_SLICE_LABEL,
    MAX_CONSECUTIVE_TIMEOUTS, MAX_ROUNDS,
    NO_PROGRESS_TIMEOUT_SECONDS,
    PAYLOAD_ENC_KEY_LABEL,
    PAYLOAD_FLAGS_V1_BYTE, PAYLOAD_MAC_KEY_LABEL,
    PAYLOAD_MAC_MESSAGE_LABEL, PAYLOAD_MAC_TRUNC_LEN,
    PAYLOAD_PROFILE_V1_BYTE,
    QUERY_INTERVAL_MS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_SLEEP_BASE_MS, RETRY_SLEEP_JITTER_MS,
)

class ClientError(Exception):
    def __init__(self, code, phase, message):
        Exception.__init__(self, message)
        self.code = int(code)
        self.phase = phase
        self.message = message
class RetryableTransport(Exception): pass


# __EXTRACT: client_runtime__
_VERBOSE = False


def _log(message):
    if _VERBOSE:
        sys.stderr.write(str(message) + "\n")
        sys.stderr.flush()


_TOKEN_RE = re.compile(r"^[a-z0-9]+$")
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _encode_name(labels):
    parts = []
    for label in labels:
        raw = encode_ascii(label)
        if not raw:
            raise ClientError(EXIT_PARSE, "parse", "empty DNS label")
        if len(raw) > 63:
            raise ClientError(EXIT_PARSE, "parse", "DNS label too long")
        parts.append(struct.pack("!B", len(raw)))
        parts.append(raw)
    parts.append(b"\x00")
    return b"".join(parts)


def _build_dns_query(query_id, qname_labels, dns_edns_size):
    qname = _encode_name(qname_labels)
    question = qname + struct.pack("!HH", DNS_QTYPE_A, DNS_QCLASS_IN)
    include_opt = dns_edns_size > 512
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
        message += b"\x00" + struct.pack("!HHIH", DNS_QTYPE_OPT, dns_edns_size, 0, 0)
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
        raise RetryableTransport("response truncated (TC)")
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


def _extract_payload_text(cname_labels, selected_domain_labels, response_label, dns_max_label_len):
    suffix = (response_label,) + tuple(selected_domain_labels)
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
        if len(label) > dns_max_label_len:
            raise ClientError(EXIT_PARSE, "parse", "payload label exceeds dns_max_label_len")

    return "".join(payload_labels)


def _enc_key(psk, file_id, publish_version):
    return _derive_file_bound_key(
        psk,
        file_id,
        publish_version,
        PAYLOAD_ENC_KEY_LABEL,
    )


def _mac_key(psk, file_id, publish_version):
    return _derive_file_bound_key(
        psk,
        file_id,
        publish_version,
        PAYLOAD_MAC_KEY_LABEL,
    )


def _parse_slice_record(payload_text):
    record = base32_decode_no_pad(payload_text)
    if len(record) < 12:
        raise ClientError(EXIT_PARSE, "parse", "slice record is too short")

    profile = byte_value(record[0])
    flags = byte_value(record[1])
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


def _expected_mac(mac_key_bytes, file_id, publish_version, slice_index, total_slices, compressed_size, ciphertext):
    message = (
        PAYLOAD_MAC_MESSAGE_LABEL
        + encode_ascii(file_id)
        + b"|"
        + encode_ascii(publish_version)
        + b"|"
        + encode_ascii_int(slice_index, "slice_index")
        + b"|"
        + encode_ascii_int(total_slices, "total_slices")
        + b"|"
        + encode_ascii_int(compressed_size, "compressed_size")
        + b"|"
        + ciphertext
    )
    return hmac_sha256(mac_key_bytes, message)[:PAYLOAD_MAC_TRUNC_LEN]


def _decrypt_and_verify_slice(enc_key_bytes, mac_key_bytes, file_id, publish_version, slice_index, total_slices, compressed_size, ciphertext, mac):
    expected = _expected_mac(mac_key_bytes, file_id, publish_version, slice_index, total_slices, compressed_size, ciphertext)
    if not constant_time_equals(expected, mac):
        raise ClientError(EXIT_CRYPTO, "crypto", "MAC verification failed")

    stream = _keystream_bytes(enc_key_bytes, file_id, publish_version, slice_index, len(ciphertext))
    plaintext = _xor_bytes(ciphertext, stream)
    if not plaintext:
        raise ClientError(EXIT_CRYPTO, "crypto", "decrypted slice is empty")
    return plaintext


def _reassemble_plaintext(slice_bytes_by_index, total_slices, compressed_size, plaintext_sha256_hex):
    ordered = []
    for index in range(total_slices):
        if index not in slice_bytes_by_index:
            raise ClientError(EXIT_REASSEMBLY, "reassembly", "missing slice index %d" % index)
        ordered.append(slice_bytes_by_index[index])

    compressed = b"".join(ordered)
    if len(compressed) != compressed_size:
        raise ClientError(
            EXIT_REASSEMBLY,
            "reassembly",
            "compressed size mismatch expected=%d got=%d" % (compressed_size, len(compressed)),
        )

    try:
        plaintext = zlib.decompress(compressed)
    except Exception as exc:
        raise ClientError(EXIT_REASSEMBLY, "reassembly", "decompress failed: %s" % exc)

    digest = hashlib.sha256(plaintext).hexdigest().lower()
    if digest != plaintext_sha256_hex:
        raise ClientError(EXIT_REASSEMBLY, "reassembly", "plaintext sha256 mismatch")
    return plaintext


def _deterministic_output_path(file_id):
    source_filename = "dnsdle_" + file_id
    return os.path.join(tempfile.gettempdir(), source_filename)


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


def _parse_non_negative_int(raw_value, flag_name):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ClientError(EXIT_USAGE, "usage", "%s must be an integer" % flag_name)
    if value < 0:
        raise ClientError(EXIT_USAGE, "usage", "%s must be >= 0" % flag_name)
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


_IPV4_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


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


def _load_system_resolvers():
    if sys.platform == "win32":
        try:
            output = _run_nslookup()
        except Exception:
            return []
        ip = _parse_nslookup_output(output)
        if not ip:
            return []
        return [ip]
    else:
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


def _discover_system_resolver():
    resolver_hosts = _load_system_resolvers()

    for host in resolver_hosts:
        try:
            return _resolve_udp_address(host, 53)
        except Exception:
            continue

    raise ClientError(EXIT_TRANSPORT, "dns", "no system DNS resolver found")


def _send_dns_query(resolver_addr, query_packet, timeout_seconds, dns_edns_size):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout_seconds)
        sock.sendto(query_packet, resolver_addr)
        response, source = sock.recvfrom(max(2048, dns_edns_size + 2048))
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


def _validate_cli_params(base_domains, file_tag, mapping_seed, token_len, total_slices, compressed_size, plaintext_sha256_hex, response_label, dns_max_label_len):
    if not base_domains:
        raise ClientError(EXIT_USAGE, "usage", "BASE_DOMAINS is empty")

    domain_labels = []
    for domain in base_domains:
        normalized = (domain or "").strip().lower().rstrip(".")
        if not normalized:
            raise ClientError(EXIT_USAGE, "usage", "invalid base domain")
        labels = tuple(normalized.split("."))
        for label in labels:
            if not _LABEL_RE.match(label):
                raise ClientError(EXIT_USAGE, "usage", "invalid base-domain label")
        domain_labels.append(labels)

    if not file_tag or not _TOKEN_RE.match(file_tag):
        raise ClientError(EXIT_USAGE, "usage", "invalid file_tag")

    if total_slices <= 0:
        raise ClientError(EXIT_USAGE, "usage", "total_slices must be > 0")

    if not mapping_seed:
        raise ClientError(EXIT_USAGE, "usage", "mapping_seed is empty")
    if token_len <= 0:
        raise ClientError(EXIT_USAGE, "usage", "token_len must be > 0")
    if token_len > dns_max_label_len:
        raise ClientError(EXIT_USAGE, "usage", "token_len exceeds dns_max_label_len")

    if compressed_size <= 0:
        raise ClientError(EXIT_USAGE, "usage", "compressed_size must be > 0")
    if not plaintext_sha256_hex or len(plaintext_sha256_hex) != 64:
        raise ClientError(EXIT_USAGE, "usage", "sha256 is invalid")

    if not response_label or not _LABEL_RE.match(response_label):
        raise ClientError(EXIT_USAGE, "usage", "response_label is invalid")

    for labels in domain_labels:
        if dns_name_wire_length((response_label,) + labels) > 255:
            raise ClientError(EXIT_USAGE, "usage", "response suffix exceeds DNS limits")

    return tuple(domain_labels)


def _download_slices(psk_value, file_id, file_tag, publish_version, total_slices, compressed_size, mapping_seed, token_len, resolver_addr, request_timeout, no_progress_timeout, max_rounds, query_interval_ms, domain_labels_by_domain, base_domains, response_label, dns_max_label_len, dns_edns_size):
    missing = set(range(total_slices))
    stored = {}
    seed_bytes = encode_ascii(mapping_seed)
    enc_key_bytes = _enc_key(psk_value, file_id, publish_version)
    mac_key_bytes = _mac_key(psk_value, file_id, publish_version)

    query_interval_sec = float(query_interval_ms) / 1000.0

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
            slice_token = _derive_slice_token(seed_bytes, publish_version, slice_index, token_len)
            qname_labels = (slice_token, file_tag) + domain_labels
            query_id = random.randint(0, 0xFFFF)
            query_packet = _build_dns_query(query_id, qname_labels, dns_edns_size)

            try:
                response = _send_dns_query(resolver_addr, query_packet, request_timeout, dns_edns_size)
                cname_labels = _parse_response_for_cname(response, query_id, qname_labels)
            except (RetryableTransport, ClientError):
                consecutive_timeouts += 1
                if consecutive_timeouts > MAX_CONSECUTIVE_TIMEOUTS:
                    raise ClientError(
                        EXIT_TRANSPORT,
                        "dns",
                        "transport retries exhausted",
                    )
                domain_index = (domain_index + 1) % len(base_domains)
                _retry_sleep()
                continue

            consecutive_timeouts = 0
            payload_text = _extract_payload_text(cname_labels, domain_labels, response_label, dns_max_label_len)
            ciphertext, mac = _parse_slice_record(payload_text)
            slice_plain = _decrypt_and_verify_slice(
                enc_key_bytes,
                mac_key_bytes,
                file_id,
                publish_version,
                slice_index,
                total_slices,
                compressed_size,
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

            if query_interval_sec > 0:
                time.sleep(query_interval_sec)

        if not progress_this_round and (time.time() - last_progress_time) >= no_progress_timeout:
            raise ClientError(EXIT_TRANSPORT, "dns", "no-progress timeout")

    return stored


def _build_parser():
    parser = argparse.ArgumentParser(description="dnsdle universal file downloader")
    parser.add_argument("--psk", required=True)
    parser.add_argument("--domains", required=True)
    parser.add_argument("--mapping-seed", required=True)
    parser.add_argument("--publish-version", required=True)
    parser.add_argument("--total-slices", required=True)
    parser.add_argument("--compressed-size", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--token-len", required=True)
    parser.add_argument("--resolver", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--file-tag-len", default="4")
    parser.add_argument("--response-label", default="r-x")
    parser.add_argument("--dns-max-label-len", default="40")
    parser.add_argument("--dns-edns-size", default="512")
    parser.add_argument("--timeout", default=str(REQUEST_TIMEOUT_SECONDS))
    parser.add_argument(
        "--no-progress-timeout",
        default=str(NO_PROGRESS_TIMEOUT_SECONDS),
    )
    parser.add_argument("--max-rounds", default=str(MAX_ROUNDS))
    parser.add_argument("--query-interval", default=str(QUERY_INTERVAL_MS))
    parser.add_argument("--verbose", action="store_true", default=False)
    return parser


def _parse_runtime_args(argv):
    global _VERBOSE
    parser = _build_parser()
    args = parser.parse_args(argv)
    _VERBOSE = args.verbose

    psk_value = (args.psk or "").strip()
    if not psk_value:
        raise ClientError(EXIT_USAGE, "usage", "--psk must be non-empty")

    base_domains = tuple(d.strip() for d in args.domains.split(",") if d.strip())
    mapping_seed = args.mapping_seed
    publish_version = args.publish_version
    total_slices = _parse_positive_int(args.total_slices, "--total-slices")
    compressed_size = _parse_positive_int(args.compressed_size, "--compressed-size")
    plaintext_sha256_hex = args.sha256.strip().lower()
    token_len = _parse_positive_int(args.token_len, "--token-len")
    file_tag_len = _parse_positive_int(args.file_tag_len, "--file-tag-len")
    response_label = (args.response_label or "").strip().lower()
    dns_max_label_len = _parse_positive_int(args.dns_max_label_len, "--dns-max-label-len")
    dns_edns_size = _parse_positive_int(args.dns_edns_size, "--dns-edns-size")

    file_id = _derive_file_id(publish_version)
    file_tag = _derive_file_tag(encode_ascii(mapping_seed), publish_version, file_tag_len)

    timeout_seconds = _parse_positive_float(args.timeout, "--timeout")
    no_progress_timeout = _parse_positive_float(
        args.no_progress_timeout,
        "--no-progress-timeout",
    )
    max_rounds = _parse_positive_int(args.max_rounds, "--max-rounds")
    query_interval_ms = _parse_non_negative_int(args.query_interval, "--query-interval")

    resolver_arg = (args.resolver or "").strip()
    if resolver_arg:
        resolver_addr = _parse_resolver_arg(resolver_arg)
    else:
        resolver_addr = _discover_system_resolver()

    out_path = (args.out or "").strip()
    if not out_path:
        out_path = _deterministic_output_path(file_id)

    return (
        psk_value,
        base_domains,
        mapping_seed,
        publish_version,
        total_slices,
        compressed_size,
        plaintext_sha256_hex,
        token_len,
        file_tag_len,
        file_id,
        file_tag,
        response_label,
        dns_max_label_len,
        dns_edns_size,
        resolver_addr,
        out_path,
        timeout_seconds,
        no_progress_timeout,
        max_rounds,
        query_interval_ms,
    )


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    try:
        (
            psk_value,
            base_domains,
            mapping_seed,
            publish_version,
            total_slices,
            compressed_size,
            plaintext_sha256_hex,
            token_len,
            file_tag_len,
            file_id,
            file_tag,
            response_label,
            dns_max_label_len,
            dns_edns_size,
            resolver_addr,
            out_path,
            timeout_seconds,
            no_progress_timeout,
            max_rounds,
            query_interval_ms,
        ) = _parse_runtime_args(argv)

        domain_labels_by_domain = _validate_cli_params(
            base_domains, file_tag, mapping_seed, token_len,
            total_slices, compressed_size, plaintext_sha256_hex,
            response_label, dns_max_label_len,
        )

        _log(
            "start file_id=%s resolver=%s:%d slices=%d" % (
                file_id,
                resolver_addr[0],
                resolver_addr[1],
                total_slices,
            )
        )

        slices = _download_slices(
            psk_value,
            file_id,
            file_tag,
            publish_version,
            total_slices,
            compressed_size,
            mapping_seed,
            token_len,
            resolver_addr,
            timeout_seconds,
            no_progress_timeout,
            max_rounds,
            query_interval_ms,
            domain_labels_by_domain,
            base_domains,
            response_label,
            dns_max_label_len,
            dns_edns_size,
        )
        plaintext = _reassemble_plaintext(slices, total_slices, compressed_size, plaintext_sha256_hex)
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
# __END_EXTRACT__
