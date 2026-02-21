from __future__ import absolute_import, unicode_literals

import os
import random
import re
import shutil
import time

from dnsdle.client_standalone import build_client_source
from dnsdle.client_standalone import _UNIVERSAL_CLIENT_FILENAME
from dnsdle.compat import encode_ascii
from dnsdle.constants import GENERATED_CLIENT_MANAGED_SUBDIR
from dnsdle.state import StartupError


_MANAGED_FILE_RE = re.compile(
    r"^dnsdl(?:e)?_[a-z0-9][a-z0-9_]*\.py$"
)


def _norm_abs(path_value):
    return os.path.abspath(os.path.normpath(path_value))


def _is_within_dir(parent_dir, child_path):
    parent = _norm_abs(parent_dir)
    child = _norm_abs(child_path)
    parent_case = os.path.normcase(parent)
    child_case = os.path.normcase(child)
    if child_case == parent_case:
        return True
    if parent_case.endswith(os.sep):
        prefix = parent_case
    else:
        prefix = parent_case + os.sep
    return child_case.startswith(prefix)


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


def _build_run_dir(root_dir, prefix):
    for _ in range(32):
        name = "%s_%d_%d" % (prefix, int(time.time() * 1000), random.randint(0, 999999))
        path = os.path.join(root_dir, name)
        if not os.path.exists(path):
            return path
    raise StartupError(
        "startup",
        "generator_write_failed",
        "failed to allocate unique generator work directory",
        {"root_dir": root_dir, "prefix": prefix},
    )


def _cleanup_tree(path_value):
    if not os.path.exists(path_value):
        return
    if os.path.isdir(path_value):
        shutil.rmtree(path_value, ignore_errors=True)
    else:
        try:
            os.remove(path_value)
        except Exception:
            pass


def _write_staged_file(stage_dir, filename, source_text):
    final_path = os.path.join(stage_dir, filename)
    temp_path = final_path + ".tmp"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(encode_ascii(source_text))
        os.rename(temp_path, final_path)
    except Exception as exc:
        _cleanup_tree(temp_path)
        raise StartupError(
            "startup",
            "generator_write_failed",
            "failed to write generated client artifact: %s" % exc,
            {"filename": filename},
        )


def _collect_backup_targets(managed_dir, expected_name):
    targets = []
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
        path = os.path.join(managed_dir, name)
        if not os.path.isfile(path):
            continue
        if name == expected_name or _MANAGED_FILE_RE.match(name):
            targets.append(path)

    return tuple(targets)


def _rollback_commit(moved_pairs, placed_paths, backup_dir):
    rollback_errors = []
    for placed_path in reversed(placed_paths):
        if os.path.exists(placed_path):
            try:
                os.remove(placed_path)
            except Exception as exc:
                rollback_errors.append(
                    "failed removing placed artifact %s: %s" % (placed_path, exc)
                )

    for backup_path, original_path in reversed(moved_pairs):
        if not os.path.exists(backup_path):
            rollback_errors.append(
                "missing backup during restore %s" % backup_path
            )
            continue
        try:
            if os.path.exists(original_path):
                os.remove(original_path)
        except Exception as exc:
            rollback_errors.append(
                "failed removing original during restore %s: %s"
                % (original_path, exc)
            )
            continue
        try:
            os.rename(backup_path, original_path)
        except Exception as exc:
            rollback_errors.append(
                "failed restoring backup %s -> %s: %s"
                % (backup_path, original_path, exc)
            )

    if rollback_errors:
        raise StartupError(
            "startup",
            "generator_write_failed",
            "transaction rollback failed; backup directory preserved",
            {
                "backup_dir": backup_dir,
                "preserve_backup_dir": True,
                "rollback_error_count": len(rollback_errors),
                "rollback_first_error": rollback_errors[0],
            },
        )


def _transactional_commit(managed_dir, stage_dir, backup_dir, filename):
    targets_to_backup = _collect_backup_targets(managed_dir, filename)

    moved_pairs = []
    placed_paths = []

    failure = None
    try:
        for original_path in targets_to_backup:
            basename = os.path.basename(original_path)
            backup_path = os.path.join(backup_dir, basename)
            if not _is_within_dir(backup_dir, backup_path):
                raise StartupError(
                    "startup",
                    "generator_write_failed",
                    "backup path escapes backup_dir",
                    {"path": backup_path},
                )
            os.rename(original_path, backup_path)
            moved_pairs.append((backup_path, original_path))

        staged_path = os.path.join(stage_dir, filename)
        managed_path = os.path.join(managed_dir, filename)
        if not _is_within_dir(managed_dir, managed_path):
            raise StartupError(
                "startup",
                "generator_write_failed",
                "generated managed path escapes managed_dir",
                {"path": managed_path},
            )
        os.rename(staged_path, managed_path)
        placed_paths.append(managed_path)
    except StartupError as exc:
        failure = exc
    except Exception as exc:
        failure = StartupError(
            "startup",
            "generator_write_failed",
            "transactional commit failed: %s" % exc,
            {"managed_dir": managed_dir},
        )

    if failure is None:
        return

    try:
        _rollback_commit(moved_pairs, placed_paths, backup_dir)
    except StartupError as rollback_exc:
        rollback_context = dict(rollback_exc.context)
        rollback_context["rollback_trigger_reason_code"] = failure.reason_code
        raise StartupError(
            "startup",
            "generator_write_failed",
            "transaction rollback failed; backup directory preserved",
            rollback_context,
        )

    raise failure


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
    if not _is_within_dir(base_output_dir, managed_dir):
        raise StartupError(
            "startup",
            "generator_write_failed",
            "managed output path escapes client_out_dir",
            {"client_out_dir": base_output_dir, "managed_dir": managed_dir},
        )
    _safe_mkdir(managed_dir, "generator_write_failed")

    source_text = build_client_source()
    filename = _UNIVERSAL_CLIENT_FILENAME

    stage_dir = _build_run_dir(managed_dir, ".stage")
    backup_dir = _build_run_dir(managed_dir, ".backup")
    _safe_mkdir(stage_dir, "generator_write_failed")
    _safe_mkdir(backup_dir, "generator_write_failed")

    try:
        _write_staged_file(stage_dir, filename, source_text)
        _transactional_commit(managed_dir, stage_dir, backup_dir, filename)
    except StartupError as exc:
        _cleanup_tree(stage_dir)
        if not exc.context.get("preserve_backup_dir"):
            _cleanup_tree(backup_dir)
        raise
    except Exception as exc:
        _cleanup_tree(stage_dir)
        _cleanup_tree(backup_dir)
        raise StartupError(
            "startup",
            "generator_write_failed",
            "unexpected generator failure: %s" % exc,
            {"managed_dir": managed_dir},
        )

    _cleanup_tree(stage_dir)
    _cleanup_tree(backup_dir)

    return {
        "managed_dir": managed_dir,
        "artifact_count": 1,
        "filename": filename,
        "source": source_text,
        "path": os.path.join(managed_dir, filename),
    }
