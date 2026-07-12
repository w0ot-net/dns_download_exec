from __future__ import absolute_import, unicode_literals

import re

from dnsdle.compat import decode_ascii
from dnsdle.compat import encode_ascii
from dnsdle.compat import text_type
from dnsdle.constants import MAX_CONSECUTIVE_TIMEOUTS
from dnsdle.constants import MAX_ROUNDS
from dnsdle.constants import NO_PROGRESS_TIMEOUT_SECONDS
from dnsdle.constants import PAYLOAD_ENC_KEY_LABEL
from dnsdle.constants import PAYLOAD_ENC_STREAM_LABEL
from dnsdle.constants import PAYLOAD_MAC_KEY_LABEL
from dnsdle.constants import PAYLOAD_MAC_MESSAGE_LABEL
from dnsdle.constants import QUERY_INTERVAL_MS
from dnsdle.constants import REQUEST_TIMEOUT_SECONDS
from dnsdle.constants import RETRY_SLEEP_BASE_MS
from dnsdle.constants import RETRY_SLEEP_JITTER_MS
from dnsdle.state import StartupError


_PLACEHOLDER_RE = re.compile(r"@@[A-Z0-9_]+@@")
_FILE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z0-9]+$")


_BASH_TEMPLATE = r'''#!/usr/bin/env bash
set -u
umask 077

FILE_ID=@@FILE_ID@@
PUBLISH_VERSION=@@PUBLISH_VERSION@@
FILE_TAG=@@FILE_TAG@@
TOTAL_SLICES=@@TOTAL_SLICES@@
COMPRESSED_SIZE=@@COMPRESSED_SIZE@@
PLAINTEXT_SHA256=@@PLAINTEXT_SHA256@@
RESPONSE_LABEL=@@RESPONSE_LABEL@@
DNS_MAX_LABEL_LEN=@@DNS_MAX_LABEL_LEN@@
DNS_EDNS_SIZE=@@DNS_EDNS_SIZE@@
REQUEST_TIMEOUT=@@REQUEST_TIMEOUT@@
NO_PROGRESS_TIMEOUT=@@NO_PROGRESS_TIMEOUT@@
MAX_ROUNDS=@@MAX_ROUNDS@@
MAX_CONSECUTIVE_TIMEOUTS=@@MAX_CONSECUTIVE_TIMEOUTS@@
RETRY_SLEEP_BASE_MS=@@RETRY_SLEEP_BASE_MS@@
RETRY_SLEEP_JITTER_MS=@@RETRY_SLEEP_JITTER_MS@@
QUERY_INTERVAL_MS=@@QUERY_INTERVAL_MS@@
ENC_KEY_LABEL=@@ENC_KEY_LABEL@@
ENC_STREAM_LABEL=@@ENC_STREAM_LABEL@@
MAC_KEY_LABEL=@@MAC_KEY_LABEL@@
MAC_MESSAGE_LABEL=@@MAC_MESSAGE_LABEL@@
DOMAINS=(@@DOMAINS@@)
SLICE_TOKENS=(@@SLICE_TOKENS@@)

verbose=0
psk=""
resolver=""
out=""
temp_dir=""
output_temp=""
DNS_OUTPUT=""
DNS_CNAME=""
HMAC_RESULT=""

_log() {
    if (( verbose )); then
        printf '%s\n' "$1" >&2
    fi
}

_usage() {
    printf 'usage: bash %s --psk SECRET [--resolver HOST[:PORT]] [--out PATH] [--verbose]\n' "$0" >&2
}

_fail() {
    local code="$1"
    shift
    printf 'error code=%s %s\n' "$code" "$*" >&2
    exit "$code"
}

_cleanup() {
    if [[ -n "$output_temp" ]]; then
        rm -f -- "$output_temp" 2>/dev/null || true
    fi
    if [[ -n "$temp_dir" ]]; then
        rm -rf -- "$temp_dir" 2>/dev/null || true
    fi
}

_sleep_ms() {
    local milliseconds="$1"
    local seconds
    if (( milliseconds <= 0 )); then
        return
    fi
    printf -v seconds '%d.%03d' "$((milliseconds / 1000))" "$((milliseconds % 1000))"
    sleep "$seconds"
}

_hmac_file() {
    local key_hex="$1"
    local input_path="$2"
    local output
    output=$(openssl dgst -sha256 -mac HMAC -macopt "hexkey:${key_hex}" "$input_path" 2>/dev/null) || return 1
    output=${output##*= }
    [[ "$output" =~ ^[0-9A-Fa-f]{64}$ ]] || return 1
    HMAC_RESULT=${output,,}
}

_hmac_text() {
    local key_hex="$1"
    local value="$2"
    printf '%s' "$value" > "$temp_dir/hmac-input" || return 1
    _hmac_file "$key_hex" "$temp_dir/hmac-input"
}

_constant_hex_equal() {
    local left="$1"
    local right="$2"
    local difference=0
    local index
    [[ "$left" =~ ^[0-9a-f]{16}$ && "$right" =~ ^[0-9a-f]{16}$ ]] || return 1
    for ((index = 0; index < 16; index++)); do
        difference=$((difference | (16#${left:index:1} ^ 16#${right:index:1})))
    done
    (( difference == 0 ))
}

_validate_embedded() {
    local value
    [[ "$FILE_ID" =~ ^[0-9a-f]{16}$ ]] || _fail 2 "invalid embedded file_id"
    [[ "$PUBLISH_VERSION" =~ ^[0-9a-f]{64}$ ]] || _fail 2 "invalid embedded publish_version"
    [[ "$PLAINTEXT_SHA256" =~ ^[0-9a-f]{64}$ ]] || _fail 2 "invalid embedded sha256"
    [[ "$FILE_TAG" =~ ^[a-z0-9]+$ ]] || _fail 2 "invalid embedded file_tag"
    [[ "$RESPONSE_LABEL" =~ ^[a-z0-9][a-z0-9-]{0,62}$ && "$RESPONSE_LABEL" != *- ]] || _fail 2 "invalid embedded response label"
    (( TOTAL_SLICES > 0 )) || _fail 2 "invalid embedded total_slices"
    (( COMPRESSED_SIZE > 0 )) || _fail 2 "invalid embedded compressed_size"
    (( DNS_MAX_LABEL_LEN >= 16 && DNS_MAX_LABEL_LEN <= 63 )) || _fail 2 "invalid embedded label cap"
    (( DNS_EDNS_SIZE >= 512 && DNS_EDNS_SIZE <= 4096 )) || _fail 2 "invalid embedded EDNS size"
    (( ${#DOMAINS[@]} > 0 )) || _fail 2 "empty embedded domains"
    (( ${#SLICE_TOKENS[@]} == TOTAL_SLICES )) || _fail 2 "slice token cardinality mismatch"
    for value in "${DOMAINS[@]}"; do
        [[ "$value" =~ ^[a-z0-9.-]+$ && "$value" != .* && "$value" != *. && "$value" != *..* ]] || _fail 2 "invalid embedded domain"
    done
    for value in "${SLICE_TOKENS[@]}"; do
        [[ "$value" =~ ^[a-z0-9]+$ ]] || _fail 2 "invalid embedded slice token"
    done
}

_require_commands() {
    local command_name
    for command_name in base32 cat dd dig gzip mktemp mv od openssl rm sha256sum sleep wc xxd; do
        command -v "$command_name" >/dev/null 2>&1 || _fail 2 "missing command ${command_name}"
    done
}

_capability_check() {
    local decoded
    local uncompressed
    decoded=$(printf '%s\n' 'MY======' | base32 -d 2>/dev/null) || _fail 2 "base32 decode is incompatible"
    [[ "$decoded" == "f" ]] || _fail 2 "base32 decode is incompatible"
    printf '%s' '1f8b08000000000000ffab00008316dc8c01000000' | xxd -r -p > "$temp_dir/gzip-vector" || _fail 2 "xxd is incompatible"
    uncompressed=$(gzip -dc "$temp_dir/gzip-vector" 2>/dev/null) || _fail 2 "gzip decode is incompatible"
    [[ "$uncompressed" == "x" ]] || _fail 2 "gzip decode is incompatible"
    printf '%s' 'abc' > "$temp_dir/hmac-vector" || _fail 2 "cannot create capability vector"
    _hmac_file '6b6579' "$temp_dir/hmac-vector" || _fail 2 "openssl HMAC is incompatible"
    [[ "$HMAC_RESULT" == '9c196e32dc0175f86f4b1cb89289d6619de6bee699e4c378e68309ed97a1a6ab' ]] || _fail 2 "openssl HMAC is incompatible"
}

_parse_resolver() {
    resolver_host=""
    resolver_port=53
    if [[ -z "$resolver" ]]; then
        return
    fi
    if [[ "$resolver" =~ ^\[([^][]+)\](:([0-9]+))?$ ]]; then
        resolver_host=${BASH_REMATCH[1]}
        if [[ -n "${BASH_REMATCH[3]:-}" ]]; then
            resolver_port=${BASH_REMATCH[3]}
        fi
    else
        local colons=${resolver//[^:]/}
        if (( ${#colons} == 1 )); then
            resolver_host=${resolver%:*}
            resolver_port=${resolver##*:}
        else
            resolver_host=$resolver
        fi
    fi
    [[ -n "$resolver_host" ]] || _fail 2 "invalid resolver host"
    [[ "$resolver_port" =~ ^[0-9]+$ ]] || _fail 2 "invalid resolver port"
    (( resolver_port > 0 && resolver_port <= 65535 )) || _fail 2 "resolver port out of range"
}

_query_cname() {
    local qname="$1"
    local -a arguments
    local line owner ttl class type target extra
    local normalized_owner normalized_qname flags
    local count=0
    arguments=(+notcp +tries=1 "+time=${REQUEST_TIMEOUT}" +noall +comments +answer)
    if (( DNS_EDNS_SIZE > 512 )); then
        arguments+=(+edns=0 "+bufsize=${DNS_EDNS_SIZE}")
    else
        arguments+=(+noedns)
    fi
    if [[ -n "$resolver_host" ]]; then
        arguments+=("@${resolver_host}" -p "$resolver_port")
    fi
    arguments+=("${qname}." A)
    DNS_OUTPUT=$(dig "${arguments[@]}" 2>/dev/null) || return 1
    [[ "$DNS_OUTPUT" == *"status: NOERROR"* ]] || return 1
    DNS_CNAME=""
    while IFS= read -r line; do
        if [[ "$line" == ';; flags:'* ]]; then
            flags=${line#*flags: }
            flags=${flags%%;*}
            [[ " $flags " == *' tc '* ]] && return 1
            continue
        fi
        [[ -z "$line" || "$line" == ';;'* ]] && continue
        owner="" ttl="" class="" type="" target="" extra=""
        read -r owner ttl class type target extra <<< "$line"
        [[ "${class^^}" == "IN" && "${type^^}" == "CNAME" ]] || continue
        normalized_owner=${owner%.}
        normalized_owner=${normalized_owner,,}
        normalized_qname=${qname%.}
        normalized_qname=${normalized_qname,,}
        [[ "$normalized_owner" == "$normalized_qname" ]] || continue
        [[ -n "$target" && -z "$extra" ]] || return 4
        DNS_CNAME=$target
        count=$((count + 1))
    done <<< "$DNS_OUTPUT"
    (( count == 1 )) || {
        (( count == 0 )) && return 1
        return 4
    }
}

_decode_cname_record() {
    local cname="$1"
    local domain="$2"
    local record_path="$3"
    local suffix payload label joined="" padding="" residue
    local -a labels
    cname=${cname%.}
    cname=${cname,,}
    suffix="${RESPONSE_LABEL}.${domain}"
    [[ "$cname" == *."$suffix" ]] || _fail 4 "CNAME suffix mismatch"
    payload=${cname%."$suffix"}
    [[ -n "$payload" && "$payload" != .* && "$payload" != *. && "$payload" != *..* ]] || _fail 4 "invalid payload labels"
    IFS='.' read -r -a labels <<< "$payload"
    (( ${#labels[@]} > 0 )) || _fail 4 "empty payload labels"
    for label in "${labels[@]}"; do
        [[ "$label" =~ ^[a-z2-7]+$ ]] || _fail 4 "invalid payload alphabet"
        (( ${#label} <= DNS_MAX_LABEL_LEN )) || _fail 4 "payload label exceeds cap"
        joined+=$label
    done
    joined=${joined^^}
    residue=$((${#joined} % 8))
    case "$residue" in
        0) padding="" ;;
        2) padding='======' ;;
        4) padding='====' ;;
        5) padding='===' ;;
        7) padding='=' ;;
        *) _fail 4 "invalid base32 length" ;;
    esac
    printf '%s\n' "${joined}${padding}" | base32 -d > "$record_path" 2>/dev/null || _fail 4 "invalid base32 payload"
}

_process_record() {
    local slice_index="$1"
    local record_path="$2"
    local slice_path="$3"
    local record_size profile flags length_hi length_lo cipher_len
    local ciphertext_path mac_path message_path actual_mac expected_mac
    local stream_hex="" block cipher_hex plain_hex="" byte
    local counter=0 produced=0 index offset
    record_size=$(wc -c < "$record_path") || _fail 4 "cannot size record"
    record_size=$((record_size))
    (( record_size >= 12 )) || _fail 4 "slice record too short"
    read -r profile flags length_hi length_lo < <(od -An -tu1 -N4 "$record_path") || _fail 4 "cannot parse record header"
    [[ -n "${length_lo:-}" ]] || _fail 4 "incomplete record header"
    (( profile == 1 )) || _fail 4 "unsupported payload profile"
    (( flags == 0 )) || _fail 4 "unsupported payload flags"
    cipher_len=$((length_hi * 256 + length_lo))
    (( cipher_len > 0 )) || _fail 4 "zero ciphertext length"
    (( record_size == 4 + cipher_len + 8 )) || _fail 4 "slice record length mismatch"
    ciphertext_path="$temp_dir/cipher-${slice_index}"
    mac_path="$temp_dir/mac-${slice_index}"
    message_path="$temp_dir/mac-message-${slice_index}"
    dd if="$record_path" of="$ciphertext_path" bs=1 skip=4 count="$cipher_len" status=none || _fail 4 "cannot extract ciphertext"
    dd if="$record_path" of="$mac_path" bs=1 skip="$((4 + cipher_len))" count=8 status=none || _fail 4 "cannot extract MAC"
    actual_mac=$(xxd -p -c 256 "$mac_path") || _fail 4 "cannot encode MAC"
    printf '%s%s|%s|%d|%d|%d|' "$MAC_MESSAGE_LABEL" "$FILE_ID" "$PUBLISH_VERSION" "$slice_index" "$TOTAL_SLICES" "$COMPRESSED_SIZE" > "$message_path" || _fail 5 "cannot construct MAC message"
    cat "$ciphertext_path" >> "$message_path" || _fail 5 "cannot construct MAC message"
    _hmac_file "$mac_key" "$message_path" || _fail 5 "cannot calculate MAC"
    expected_mac=${HMAC_RESULT:0:16}
    _constant_hex_equal "$actual_mac" "$expected_mac" || _fail 5 "MAC verification failed"
    while (( produced < cipher_len )); do
        _hmac_text "$enc_key" "${ENC_STREAM_LABEL}${FILE_ID}|${PUBLISH_VERSION}|${slice_index}|${counter}" || _fail 5 "cannot calculate keystream"
        block=$HMAC_RESULT
        stream_hex+=$block
        produced=$((produced + 32))
        counter=$((counter + 1))
    done
    stream_hex=${stream_hex:0:$((cipher_len * 2))}
    cipher_hex=$(xxd -p -c 65535 "$ciphertext_path") || _fail 5 "cannot encode ciphertext"
    (( ${#cipher_hex} == cipher_len * 2 )) || _fail 5 "ciphertext hex length mismatch"
    for ((index = 0; index < cipher_len; index++)); do
        offset=$((index * 2))
        printf -v byte '%02x' "$((16#${cipher_hex:offset:2} ^ 16#${stream_hex:offset:2}))"
        plain_hex+=$byte
    done
    printf '%s' "$plain_hex" | xxd -r -p > "$slice_path" || _fail 5 "cannot decode plaintext slice"
    (( $(wc -c < "$slice_path") == cipher_len )) || _fail 5 "plaintext slice length mismatch"
}

if (( BASH_VERSINFO[0] < 4 )); then
    exit 2
fi

while (( $# )); do
    case "$1" in
        --psk)
            (( $# >= 2 )) || _fail 2 "--psk requires a value"
            psk=$2
            shift 2
            ;;
        --resolver)
            (( $# >= 2 )) || _fail 2 "--resolver requires a value"
            resolver=$2
            shift 2
            ;;
        --out)
            (( $# >= 2 )) || _fail 2 "--out requires a value"
            out=$2
            shift 2
            ;;
        --verbose)
            verbose=1
            shift
            ;;
        --help|-h)
            _usage
            exit 0
            ;;
        *)
            _fail 2 "unknown argument $1"
            ;;
    esac
done

[[ -n "$psk" ]] || _fail 2 "--psk is required; use --help for usage"
_validate_embedded
_parse_resolver
if [[ -z "$out" ]]; then
    out="${TMPDIR:-/tmp}/dnsdle_${FILE_ID}"
fi
if [[ "$out" != "-" ]]; then
    output_dir=${out%/*}
    [[ "$output_dir" != "$out" ]] || output_dir='.'
    [[ -n "$output_dir" ]] || output_dir='/'
    [[ -d "$output_dir" ]] || _fail 2 "output directory does not exist"
fi
_require_commands
temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/dnsdle.XXXXXX") || _fail 2 "cannot create temporary directory"
trap _cleanup EXIT
trap 'exit 3' HUP INT TERM
_capability_check

psk_hex=$(printf '%s' "$psk" | xxd -p -c 65535) || _fail 5 "cannot encode PSK"
[[ -n "$psk_hex" && "$psk_hex" =~ ^[0-9a-f]+$ ]] || _fail 5 "invalid PSK bytes"
_hmac_text "$psk_hex" "${ENC_KEY_LABEL}${FILE_ID}|${PUBLISH_VERSION}" || _fail 5 "cannot derive encryption key"
enc_key=$HMAC_RESULT
_hmac_text "$psk_hex" "${MAC_KEY_LABEL}${FILE_ID}|${PUBLISH_VERSION}" || _fail 5 "cannot derive MAC key"
mac_key=$HMAC_RESULT

declare -a received
received_count=0
rounds=0
consecutive_timeouts=0
domain_index=0
last_progress=$SECONDS

while (( received_count < TOTAL_SLICES )); do
    rounds=$((rounds + 1))
    (( rounds <= MAX_ROUNDS )) || _fail 3 "max rounds exhausted"
    for ((slice_index = 0; slice_index < TOTAL_SLICES; slice_index++)); do
        [[ "${received[slice_index]:-0}" == "1" ]] && continue
        (( SECONDS - last_progress < NO_PROGRESS_TIMEOUT )) || _fail 3 "no-progress timeout"
        domain=${DOMAINS[domain_index]}
        qname="${SLICE_TOKENS[slice_index]}.${FILE_TAG}.${domain}"
        _query_cname "$qname"
        query_status=$?
        if (( query_status != 0 )); then
            (( query_status == 4 )) && _fail 4 "ambiguous CNAME answer"
            consecutive_timeouts=$((consecutive_timeouts + 1))
            (( consecutive_timeouts <= MAX_CONSECUTIVE_TIMEOUTS )) || _fail 3 "transport retries exhausted"
            domain_index=$(((domain_index + 1) % ${#DOMAINS[@]}))
            retry_ms=$((RETRY_SLEEP_BASE_MS + (RETRY_SLEEP_JITTER_MS > 0 ? RANDOM % (RETRY_SLEEP_JITTER_MS + 1) : 0)))
            _sleep_ms "$retry_ms"
            continue
        fi
        record_path="$temp_dir/record-${slice_index}"
        slice_path="$temp_dir/slice-${slice_index}"
        _decode_cname_record "$DNS_CNAME" "$domain" "$record_path"
        _process_record "$slice_index" "$record_path" "$slice_path"
        received[slice_index]=1
        received_count=$((received_count + 1))
        consecutive_timeouts=0
        last_progress=$SECONDS
        _log "progress received=${received_count} missing=$((TOTAL_SLICES - received_count))"
        _sleep_ms "$QUERY_INTERVAL_MS"
    done
done

compressed_path="$temp_dir/compressed"
: > "$compressed_path" || _fail 6 "cannot create compressed output"
for ((slice_index = 0; slice_index < TOTAL_SLICES; slice_index++)); do
    cat "$temp_dir/slice-${slice_index}" >> "$compressed_path" || _fail 6 "cannot reassemble slices"
done
(( $(wc -c < "$compressed_path") == COMPRESSED_SIZE )) || _fail 6 "compressed size mismatch"
plaintext_path="$temp_dir/plaintext"
gzip -dc "$compressed_path" > "$plaintext_path" 2>/dev/null || _fail 6 "gzip decompression failed"
plaintext_hash=$(sha256sum "$plaintext_path") || _fail 6 "cannot hash plaintext"
plaintext_hash=${plaintext_hash%% *}
[[ "$plaintext_hash" == "$PLAINTEXT_SHA256" ]] || _fail 6 "plaintext sha256 mismatch"

if [[ "$out" == "-" ]]; then
    cat "$plaintext_path" || _fail 7 "stdout write failed"
    wrote='<stdout>'
else
    output_temp=$(mktemp "${out}.tmp.XXXXXX") || _fail 7 "cannot create output temporary file"
    cat "$plaintext_path" > "$output_temp" || _fail 7 "cannot stage output"
    mv -f -- "$output_temp" "$out" || _fail 7 "cannot commit output"
    output_temp=""
    wrote=$out
fi
_log "success wrote=${wrote}"
exit 0
'''


def _shell_quote(value):
    text = value if isinstance(value, text_type) else text_type(value)
    try:
        encode_ascii(text)
    except UnicodeEncodeError:
        raise StartupError(
            "startup",
            "bash_downloader_generation_failed",
            "Bash literal is not ASCII",
        )
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _label_text(value):
    return decode_ascii(value) if not isinstance(value, text_type) else value


def render_bash_downloader(config, payload_publish_item):
    """Render one direct Bash downloader without writing it."""
    file_id = payload_publish_item["file_id"]
    publish_version = payload_publish_item["publish_version"]
    plaintext_sha256 = payload_publish_item["plaintext_sha256"]
    file_tag = payload_publish_item["file_tag"]
    slice_tokens = tuple(payload_publish_item["slice_tokens"])
    total_slices = int(payload_publish_item["total_slices"])

    if not _FILE_ID_RE.match(file_id):
        raise StartupError("startup", "bash_downloader_generation_failed", "invalid file_id")
    if not _HEX_64_RE.match(publish_version):
        raise StartupError("startup", "bash_downloader_generation_failed", "invalid publish_version")
    if not _HEX_64_RE.match(plaintext_sha256):
        raise StartupError("startup", "bash_downloader_generation_failed", "invalid plaintext sha256")
    if not _TOKEN_RE.match(file_tag):
        raise StartupError("startup", "bash_downloader_generation_failed", "invalid file_tag")
    if total_slices <= 0 or len(slice_tokens) != total_slices:
        raise StartupError(
            "startup",
            "bash_downloader_generation_failed",
            "slice token cardinality mismatch",
        )
    if len(set(slice_tokens)) != total_slices:
        raise StartupError(
            "startup",
            "bash_downloader_generation_failed",
            "slice tokens are not unique",
        )
    for token in slice_tokens:
        if not _TOKEN_RE.match(token):
            raise StartupError(
                "startup",
                "bash_downloader_generation_failed",
                "invalid slice token",
            )

    replacements = {
        "FILE_ID": _shell_quote(file_id),
        "PUBLISH_VERSION": _shell_quote(publish_version),
        "FILE_TAG": _shell_quote(file_tag),
        "TOTAL_SLICES": text_type(total_slices),
        "COMPRESSED_SIZE": text_type(int(payload_publish_item["compressed_size"])),
        "PLAINTEXT_SHA256": _shell_quote(plaintext_sha256),
        "RESPONSE_LABEL": _shell_quote(config.response_label),
        "DNS_MAX_LABEL_LEN": text_type(int(config.dns_max_label_len)),
        "DNS_EDNS_SIZE": text_type(int(config.dns_edns_size)),
        "REQUEST_TIMEOUT": text_type(int(REQUEST_TIMEOUT_SECONDS)),
        "NO_PROGRESS_TIMEOUT": text_type(int(NO_PROGRESS_TIMEOUT_SECONDS)),
        "MAX_ROUNDS": text_type(int(MAX_ROUNDS)),
        "MAX_CONSECUTIVE_TIMEOUTS": text_type(int(MAX_CONSECUTIVE_TIMEOUTS)),
        "RETRY_SLEEP_BASE_MS": text_type(int(RETRY_SLEEP_BASE_MS)),
        "RETRY_SLEEP_JITTER_MS": text_type(int(RETRY_SLEEP_JITTER_MS)),
        "QUERY_INTERVAL_MS": text_type(int(QUERY_INTERVAL_MS)),
        "ENC_KEY_LABEL": _shell_quote(_label_text(PAYLOAD_ENC_KEY_LABEL)),
        "ENC_STREAM_LABEL": _shell_quote(_label_text(PAYLOAD_ENC_STREAM_LABEL)),
        "MAC_KEY_LABEL": _shell_quote(_label_text(PAYLOAD_MAC_KEY_LABEL)),
        "MAC_MESSAGE_LABEL": _shell_quote(_label_text(PAYLOAD_MAC_MESSAGE_LABEL)),
        "DOMAINS": " ".join(_shell_quote(domain) for domain in config.domains),
        "SLICE_TOKENS": " ".join(_shell_quote(token) for token in slice_tokens),
    }

    source = _BASH_TEMPLATE
    for key, value in replacements.items():
        source = source.replace("@@%s@@" % key, value)
    unreplaced = _PLACEHOLDER_RE.search(source)
    if unreplaced:
        raise StartupError(
            "startup",
            "bash_downloader_generation_failed",
            "unreplaced Bash downloader placeholder",
            {"placeholder": unreplaced.group(0)},
        )
    try:
        encode_ascii(source)
    except UnicodeEncodeError:
        raise StartupError(
            "startup",
            "bash_downloader_generation_failed",
            "Bash downloader source is not ASCII",
        )

    return {
        "language": "bash",
        "kind": "downloader",
        "source_filename": payload_publish_item["source_filename"],
        "filename": "dnsdle_%s.bash.sh" % file_id,
        "content": source,
    }


def render_bash_downloaders(config, payload_publish_items):
    return tuple(
        render_bash_downloader(config, payload_item)
        for payload_item in payload_publish_items
    )
