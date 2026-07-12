from __future__ import absolute_import, unicode_literals

import os

from dnsdle.bash_downloader import render_bash_downloaders
from dnsdle.compat import encode_ascii
from dnsdle.stager_generator import render_stagers
from dnsdle.state import StartupError


_COMMON_FIELDS = frozenset(("language", "kind", "source_filename", "path"))


def _artifact_path(managed_dir, rendered):
    filename = rendered["filename"]
    if not filename or os.path.basename(filename) != filename:
        raise StartupError(
            "startup",
            "download_artifact_invalid_contract",
            "generated artifact filename is invalid",
            {"filename": filename},
        )
    return os.path.join(managed_dir, filename)


def _write_artifact(managed_dir, rendered):
    path = _artifact_path(managed_dir, rendered)
    temp_path = path + ".tmp-%d" % os.getpid()
    try:
        content = encode_ascii(rendered["content"])
        with open(temp_path, "wb") as handle:
            handle.write(content)
        if os.path.exists(path):
            os.remove(path)
        os.rename(temp_path, path)
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        raise StartupError(
            "startup",
            "download_artifact_write_failed",
            "failed to write generated payload artifact: %s" % exc,
            {"path": path},
        )
    artifact = {
        "language": rendered["language"],
        "kind": rendered["kind"],
        "source_filename": rendered["source_filename"],
        "path": path,
    }
    if frozenset(artifact.keys()) != _COMMON_FIELDS:
        raise StartupError(
            "startup",
            "download_artifact_invalid_contract",
            "generated artifact fields violate contract",
        )
    return artifact


def generate_download_artifacts(
    config,
    generation_result,
    client_publish_item,
    payload_publish_items,
):
    """Generate one Python stager and one Bash downloader per payload."""
    stagers = render_stagers(config, client_publish_item, payload_publish_items)
    bash_downloaders = render_bash_downloaders(config, payload_publish_items)
    if len(stagers) != len(payload_publish_items):
        raise StartupError(
            "startup",
            "download_artifact_invalid_contract",
            "Python stager cardinality mismatch",
        )
    if len(bash_downloaders) != len(payload_publish_items):
        raise StartupError(
            "startup",
            "download_artifact_invalid_contract",
            "Bash downloader cardinality mismatch",
        )

    rendered = []
    for index in range(len(payload_publish_items)):
        rendered.append(stagers[index])
        rendered.append(bash_downloaders[index])

    managed_dir = generation_result["managed_dir"]
    paths = tuple(_artifact_path(managed_dir, item) for item in rendered)
    if len(set(paths)) != len(paths):
        raise StartupError(
            "startup",
            "download_artifact_invalid_contract",
            "generated payload artifact paths are not unique",
        )

    artifacts = tuple(_write_artifact(managed_dir, item) for item in rendered)
    if len(artifacts) != 2 * len(payload_publish_items):
        raise StartupError(
            "startup",
            "download_artifact_invalid_contract",
            "generated payload artifact cardinality mismatch",
        )
    return artifacts
