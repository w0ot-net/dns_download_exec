from __future__ import absolute_import, unicode_literals

import re


# Rename table: (old_name, new_name) pairs.
# Ordered longest-first so re.sub on longer names runs before shorter ones,
# preventing substring interference.
#
# NOT renamed (appear as whole words inside string literals):
#   compressed, counter, label, labels, mac, message, msg, name, pointer,
#   psk, resolver, slice_index, slices, stream, text, verbose
# NOT renamed (stdlib method/attribute names):
#   upper, encode, decode, append, lower, strip, split, etc.
# NOT renamed (imported module names):
#   base64, hashlib, hmac, random, socket, struct, subprocess, sys, time, zlib
# NOT renamed (already single-char or two-char, no benefit):
#   a, b, i, j, r, v, _h, _i, ba, ek, em, mk, si
_RENAME_TABLE = [
    ("PAYLOAD_MAC_MESSAGE_LABEL", "e"),
    ("PAYLOAD_ENC_STREAM_LABEL", "f"),
    ("PAYLOAD_COMPRESSED_SIZE", "g"),
    ("PAYLOAD_PUBLISH_VERSION", "h"),
    ("_load_windows_resolvers", "j"),
    ("_derive_file_bound_key", "k"),
    ("_parse_nslookup_output", "l"),
    ("PAYLOAD_ENC_KEY_LABEL", "m"),
    ("PAYLOAD_MAC_KEY_LABEL", "o"),
    ("PAYLOAD_MAC_TRUNC_LEN", "p"),
    ("publish_version_bytes", "q"),
    ("PAYLOAD_TOTAL_SLICES", "u"),
    ("PLAINTEXT_SHA256_HEX", "v"),
    ("_load_unix_resolvers", "w"),
    ("base32_decode_no_pad", "x"),
    ("constant_time_equals", "y"),
    ("MAPPING_SLICE_LABEL", "z"),
    ("_derive_slice_token", "A"),
    ("base32_lower_no_pad", "C"),
    ("_discover_resolver", "D"),
    ("PAYLOAD_TOKEN_LEN", "E"),
    ("slice_index_bytes", "F"),
    ("_extract_payload", "G"),
    ("_keystream_bytes", "I"),
    ("encode_ascii_int", "J"),
    ("COMPRESSED_SIZE", "K"),
    ("DNS_POINTER_TAG", "L"),
    ("PUBLISH_VERSION", "M"),
    ("SLICE_TOKEN_LEN", "N"),
    ("publish_version", "O"),
    ("read_end_offset", "P"),
    ("visited_offsets", "Q"),
    ("PAYLOAD_SHA256", "R"),
    ("RESPONSE_LABEL", "S"),
    ("_process_slice", "T"),
    ("DNS_EDNS_SIZE", "U"),
    ("DOMAIN_LABELS", "V"),
    ("DnsParseError", "W"),
    ("_run_nslookup", "X"),
    ("client_source", "Y"),
    ("counter_bytes", "Z"),
    ("file_id_bytes", "aa"),
    ("message_bytes", "ab"),
    ("FILE_TAG_LEN", "ac"),
    ("MAPPING_SEED", "ad"),
    ("TOTAL_SLICES", "ae"),
    ("_build_query", "af"),
    ("_decode_name", "ag"),
    ("_encode_name", "ah"),
    ("_parse_cname", "ai"),
    ("cname_labels", "aj"),
    ("decode_ascii", "ak"),
    ("encode_ascii", "al"),
    ("payload_text", "am"),
    ("qname_labels", "ao"),
    ("server_index", "ap"),
    ("start_offset", "aq"),
    ("DOMAINS_STR", "ar"),
    ("_send_query", "at"),
    ("binary_type", "au"),
    ("block_input", "av"),
    ("encode_utf8", "aw"),
    ("hmac_sha256", "ax"),
    ("message_len", "ay"),
    ("padding_len", "az"),
    ("right_bytes", "aA"),
    ("right_value", "aB"),
    ("_xor_bytes", "aC"),
    ("ciphertext", "aD"),
    ("end_offset", "aE"),
    ("field_name", "aF"),
    ("left_bytes", "aG"),
    ("left_value", "aH"),
    ("right_byte", "aI"),
    ("seed_bytes", "aJ"),
    ("_deadline", "aK"),
    ("addresses", "aL"),
    ("int_value", "aM"),
    ("is_binary", "aN"),
    ("key_bytes", "aO"),
    ("key_label", "aP"),
    ("left_byte", "aQ"),
    ("plaintext", "aR"),
    ("psk_bytes", "aS"),
    ("raw_bytes", "aT"),
    ("rdata_off", "aU"),
    ("resolvers", "aV"),
    ("seen_addr", "aW"),
    ("text_type", "aX"),
    ("token_len", "aY"),
    ("FILE_TAG", "aZ"),
    ("_qlabels", "bb"),
    ("expected", "bc"),
    ("produced", "bd"),
    ("question", "bf"),
    ("raw_line", "bg"),
    ("rr_class", "bh"),
    ("stripped", "bi"),
    ("use_edns", "bj"),
    ("FILE_ID", "bk"),
    ("_port_s", "bl"),
    ("ancount", "bm"),
    ("arcount", "bn"),
    ("compare", "bo"),
    ("enc_key", "bp"),
    ("encoded", "bq"),
    ("file_id", "br"),
    ("mac_msg", "bs"),
    ("payload", "bt"),
    ("qdcount", "bu"),
    ("rr_name", "bv"),
    ("rr_type", "bw"),
    ("_hosts", "bx"),
    ("blocks", "by"),
    ("handle", "bz"),
    ("header", "bA"),
    ("jumped", "bB"),
    ("offset", "bC"),
    ("output", "bD"),
    ("padded", "bE"),
    ("record", "bF"),
    ("result", "bG"),
    ("run_fn", "bH"),
    ("suffix", "bI"),
    ("_rest", "bJ"),
    ("block", "bK"),
    ("cname", "bL"),
    ("first", "bM"),
    ("flags", "bN"),
    ("index", "bO"),
    ("lines", "bP"),
    ("parts", "bQ"),
    ("qname", "bR"),
    ("rdlen", "bS"),
    ("value", "bT"),
    ("_end", "bU"),
    ("_src", "bV"),
    ("_ttl", "bW"),
    ("addr", "bX"),
    ("args", "bY"),
    ("clen", "bZ"),
    ("host", "ca"),
    ("line", "cb"),
    ("port", "cc"),
    ("proc", "cd"),
    ("resp", "ce"),
    ("slen", "cf"),
    ("sock", "cg"),
    ("PSK", "ch"),
    ("_af", "ci"),
    ("_ai", "cj"),
    ("_ce", "ck"),
    ("_sa", "cl"),
    ("off", "cm"),
    ("pkt", "cn"),
    ("qid", "co"),
    ("raw", "cp"),
    ("rid", "cq"),
]

_BLOCK_STARTERS = frozenset((
    "if", "for", "while", "try", "except", "else",
    "elif", "finally", "def", "return", "with",
    "break", "continue",
))

# Pre-compile rename patterns for performance.
_RENAME_COMPILED = [
    (re.compile(r"\b" + re.escape(old) + r"\b"), new)
    for old, new in _RENAME_TABLE
]

# Match string literals (single/double quoted, optional b prefix) so the
# rename pass never touches content inside quotes.
_STRING_RE = re.compile(r'''b?(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')''')
_PLACEHOLDER_RE = re.compile(r"__S(\d+)__")


def minify(source):
    """Minify stager source. Deterministic: same input -> same output."""
    lines = source.split("\n")
    # Pass 1: strip comment lines.
    lines = [ln for ln in lines if not ln.strip().startswith("#")]
    # Pass 2: strip blank lines.
    lines = [ln for ln in lines if ln.strip()]
    src = "\n".join(lines)
    # Extract string literals before renaming so renames cannot corrupt
    # content inside quotes.
    saved = []
    def _extract(m):
        saved.append(m.group(0))
        return "__S%d__" % (len(saved) - 1)
    src = _STRING_RE.sub(_extract, src)
    # Pass 3: rename variables (longest names first).
    for pattern, new in _RENAME_COMPILED:
        src = pattern.sub(new, src)
    # Restore original string literals.
    src = _PLACEHOLDER_RE.sub(lambda m: saved[int(m.group(1))], src)
    lines = src.split("\n")
    # Pass 4: reduce indentation (4 spaces -> 1 space per level).
    reduced = []
    for ln in lines:
        stripped = ln.lstrip(" ")
        spaces = len(ln) - len(stripped)
        level = spaces // 4
        remainder = spaces % 4
        if remainder:
            reduced.append(ln)
        else:
            reduced.append(" " * level + stripped)
    lines = reduced
    # Pass 5: semicolon-join consecutive same-indent non-block lines.
    # Skip joining inside multiline parenthesized expressions.
    result = []
    for ln in lines:
        stripped = ln.lstrip(" ")
        indent = len(ln) - len(stripped)
        token = stripped.split(None, 1)[0].rstrip(":") if stripped else ""
        first_char = stripped[0] if stripped else ""
        if result:
            prev = result[-1]
            prev_stripped = prev.lstrip(" ")
            prev_indent = len(prev) - len(prev_stripped)
            if (indent == prev_indent
                    and token not in _BLOCK_STARTERS
                    and not prev.rstrip().endswith(",")
                    and not prev.rstrip().endswith("(")
                    and first_char not in "+-*/|&^%~)"):
                result[-1] = prev + ";" + stripped
                continue
        result.append(ln)
    return "\n".join(result)
