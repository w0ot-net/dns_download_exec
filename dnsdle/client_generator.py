from __future__ import absolute_import, unicode_literals

import os

from dnsdle.client_standalone import build_client_source
from dnsdle.client_standalone import _UNIVERSAL_CLIENT_FILENAME
from dnsdle.compat import encode_ascii
from dnsdle.constants import GENERATED_CLIENT_MANAGED_SUBDIR
from dnsdle.state import StartupError


def _norm_abs(path_value):
    return os.path.abspath(os.path.normpath(path_value))


def _safe_mkdir(path_value, reason_code):
    try:
        if os.path.isdir(path_value):
            return
        if os.path.exists(path_value):
            raise StartupError(
                "startup",
                reason_code,
                "path exists but is not a directory",
                {"path": path_value},
            )
        os.makedirs(path_value)
    except StartupError:
        raise
    except Exception as exc:
        raise StartupError(
            "startup",
            reason_code,
            "failed to create directory: %s" % exc,
            {"path": path_value},
        )


def _remove_stale_managed_files(managed_dir, keep_name):
    try:
        names = sorted(os.listdir(managed_dir))
    except Exception as exc:
        raise StartupError(
            "startup",
            "generator_write_failed",
            "failed to list managed output directory: %s" % exc,
            {"managed_dir": managed_dir},
        )
    for name in names:
        if name == keep_name:
            continue
        if not name.startswith("dnsdl"):
            continue
        if not name.endswith(".py"):
            continue
        path = os.path.join(managed_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
        except Exception as exc:
            raise StartupError(
                "startup",
                "generator_write_failed",
                "failed to remove stale managed file: %s" % exc,
                {"path": path},
            )


def generate_client_artifacts(config):
    if not config.client_out_dir:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "client_out_dir is empty",
        )

    base_output_dir = _norm_abs(config.client_out_dir)
    _safe_mkdir(base_output_dir, "generator_write_failed")

    managed_dir = _norm_abs(os.path.join(base_output_dir, GENERATED_CLIENT_MANAGED_SUBDIR))
    _safe_mkdir(managed_dir, "generator_write_failed")

    source_text = build_client_source()
    filename = _UNIVERSAL_CLIENT_FILENAME
    final_path = os.path.join(managed_dir, filename)
    temp_path = final_path + ".tmp-%d" % os.getpid()
    try:
        with open(temp_path, "wb") as handle:
            handle.write(encode_ascii(source_text))
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(temp_path, final_path)
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        raise StartupError(
            "startup",
            "generator_write_failed",
            "failed to write generated client artifact: %s" % exc,
            {"filename": filename},
        )

    _remove_stale_managed_files(managed_dir, filename)

    return {
        "managed_dir": managed_dir,
        "artifact_count": 1,
        "filename": filename,
        "source": source_text,
        "path": final_path,
    }
