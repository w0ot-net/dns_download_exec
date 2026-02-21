from __future__ import absolute_import, unicode_literals

import base64
import os
import re
import zlib

from dnsdle.stager_minify import minify
from dnsdle.stager_template import build_stager_template
from dnsdle.state import StartupError


def generate_stager(config, template, client_publish_item, payload_publish_item):
    """Generate a stager one-liner for a single payload file.

    template is the stager template source from build_stager_template().
    client_publish_item is the mapped publish item dict for the universal
    client script.  payload_publish_item is the mapped publish item dict
    for the user payload file.

    Returns a dict with keys: source_filename, oneliner, minified_source.
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
        "PSK": config.psk,
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

    minified_bytes = minified.encode("ascii")
    compressed = zlib.compress(minified_bytes, 9)
    payload_str = base64.b64encode(compressed).decode("ascii")
    oneliner = (
        "python3 -c "
        '"import base64,zlib;'
        "exec(zlib.decompress(base64.b64decode('%s')))\""
        % payload_str
    )

    return {
        "source_filename": payload_publish_item["source_filename"],
        "oneliner": oneliner,
        "minified_source": minified,
    }


def _stager_txt_filename(source_filename):
    """Derive the stager .txt filename from the payload source filename."""
    base = os.path.basename(source_filename)
    name, _ext = os.path.splitext(base)
    return name + ".1-liner.txt"


def _write_stager_file(managed_dir, stager):
    """Write a stager one-liner to a .txt file in the managed directory."""
    txt_name = _stager_txt_filename(stager["source_filename"])
    path = os.path.join(managed_dir, txt_name)
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(stager["oneliner"].encode("ascii"))
            handle.write(b"\n")
        os.rename(temp_path, path)
    except Exception as exc:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise StartupError(
            "startup",
            "stager_generation_failed",
            "failed to write stager file: %s" % exc,
            {"path": path},
        )
    return path


def generate_stagers(config, generation_result, client_publish_item, payload_publish_items):
    """Generate stagers for all payload files.

    client_publish_item is the single mapped publish item dict for the
    universal client.  payload_publish_items is the list of mapped
    publish item dicts for user payload files.

    Returns a list of stager dicts (one per payload file).
    """
    template = build_stager_template()
    managed_dir = generation_result["managed_dir"]
    stagers = []
    for payload_item in payload_publish_items:
        stager = generate_stager(config, template, client_publish_item, payload_item)
        stager["path"] = _write_stager_file(managed_dir, stager)
        stagers.append(stager)

    return stagers
