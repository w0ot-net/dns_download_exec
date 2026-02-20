from __future__ import absolute_import, unicode_literals

import hashlib
import hmac


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
