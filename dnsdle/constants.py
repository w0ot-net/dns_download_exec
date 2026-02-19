from __future__ import absolute_import


# Fixed v1 profiles
PROFILE_V1 = "v1"
QTYPE_RESPONSE_CNAME = "CNAME"


# Mapping/token constants
TOKEN_ALPHABET_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
ALLOWED_TARGET_OS = ("windows", "linux")
DIGEST_TEXT_CAPACITY = 52  # base32(lower, no-pad) chars from SHA-256 digest
FILE_ID_PREFIX = "dnsdle:file-id:v1|"
MAPPING_FILE_LABEL = b"dnsdle:file:v1|"
MAPPING_SLICE_LABEL = b"dnsdle:slice:v1|"


# DNS/packet sizing constants
MAX_DNS_NAME_WIRE_LENGTH = 255
MAX_DNS_NAME_TEXT_LENGTH = 253
CLASSIC_DNS_PACKET_LIMIT = 512
BINARY_RECORD_OVERHEAD = 12  # 4-byte header + 8-byte truncated MAC
DNS_HEADER_BYTES = 12
QUESTION_FIXED_BYTES = 4  # QTYPE + QCLASS
ANSWER_FIXED_BYTES = 12  # NAME ptr + TYPE + CLASS + TTL + RDLENGTH
OPT_RR_BYTES = 11  # root NAME + TYPE + CLASS + TTL + RDLEN
BASE32_BITS_PER_CHAR = 5
BITS_PER_BYTE = 8


# DNS wire constants
DNS_QTYPE_A = 1
DNS_QTYPE_CNAME = 5
DNS_QTYPE_OPT = 41
DNS_QCLASS_IN = 1

DNS_RCODE_NOERROR = 0
DNS_RCODE_SERVFAIL = 2
DNS_RCODE_NXDOMAIN = 3

DNS_FLAG_QR = 0x8000
DNS_OPCODE_MASK = 0x7800
DNS_FLAG_AA = 0x0400
DNS_FLAG_TC = 0x0200
DNS_FLAG_RD = 0x0100
DNS_FLAG_RA = 0x0080

DNS_POINTER_TAG = 0xC0
DNS_POINTER_MASK = 0xC000
DNS_POINTER_VALUE_MASK = 0x3FFF

DNS_UDP_RECV_MAX = 65535
SYNTHETIC_A_RDATA = b"\x00\x00\x00\x00"


# CNAME payload binary-record constants
PAYLOAD_PROFILE_V1_BYTE = 0x01
PAYLOAD_FLAGS_V1_BYTE = 0x00
PAYLOAD_MAC_TRUNC_LEN = 8
PAYLOAD_MAC_KEY_LABEL = b"dnsdle-mac-v1|"
PAYLOAD_MAC_MESSAGE_LABEL = b"dnsdle-mac-msg-v1|"


# Config bounds
MIN_DNS_EDNS_SIZE = CLASSIC_DNS_PACKET_LIMIT
MAX_DNS_EDNS_SIZE = 4096


FIXED_CONFIG = {
    "query_mapping_alphabet": "[a-z0-9]",
    "query_mapping_case": "lowercase",
    "wire_profile": PROFILE_V1,
    "crypto_profile": PROFILE_V1,
    "qtype_response": QTYPE_RESPONSE_CNAME,
    "generated_client_single_file": True,
    "generated_client_download_only": True,
}
