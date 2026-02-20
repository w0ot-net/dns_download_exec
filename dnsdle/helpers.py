from __future__ import absolute_import, unicode_literals

import hashlib
import hmac

from dnsdle.compat import base32_lower_no_pad
from dnsdle.compat import encode_ascii
from dnsdle.compat import encode_ascii_int
from dnsdle.constants import FILE_ID_PREFIX
from dnsdle.constants import MAPPING_FILE_LABEL
from dnsdle.constants import MAPPING_SLICE_LABEL


# __EXTRACT: dns_name_wire_length__
def dns_name_wire_length(labels):
    return 1 + sum(1 + len(label) for label in labels)
# __END_EXTRACT__


def labels_is_suffix(suffix_labels, full_labels):
    suffix_len = len(suffix_labels)
    full_len = len(full_labels)
    if suffix_len > full_len:
        return False
    return full_labels[full_len - suffix_len:] == suffix_labels


# __EXTRACT: hmac_sha256__
def hmac_sha256(key_bytes, message_bytes):
    return hmac.new(key_bytes, message_bytes, hashlib.sha256).digest()
# __END_EXTRACT__


# __EXTRACT: _derive_file_id__
def _derive_file_id(publish_version):
    return hashlib.sha256(FILE_ID_PREFIX + encode_ascii(publish_version)).hexdigest()[:16]
# __END_EXTRACT__


# __EXTRACT: _derive_file_tag__
def _derive_file_tag(seed_bytes, publish_version, file_tag_len):
    digest = hmac_sha256(seed_bytes, MAPPING_FILE_LABEL + encode_ascii(publish_version))
    return base32_lower_no_pad(digest)[:file_tag_len]
# __END_EXTRACT__


# __EXTRACT: _derive_slice_token__
def _derive_slice_token(seed_bytes, publish_version, slice_index, token_len):
    msg = MAPPING_SLICE_LABEL + encode_ascii(publish_version) + b"|" + encode_ascii_int(slice_index, "slice_index")
    return base32_lower_no_pad(hmac_sha256(seed_bytes, msg))[:token_len]
# __END_EXTRACT__
