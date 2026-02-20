from __future__ import absolute_import, unicode_literals

import os
import random
import re
import shutil
import time

from dnsdle.client_template import build_client_template
from dnsdle.compat import encode_ascii
from dnsdle.constants import ALLOWED_TARGET_OS
from dnsdle.constants import GENERATED_CLIENT_DEFAULT_MAX_CONSECUTIVE_TIMEOUTS
from dnsdle.constants import GENERATED_CLIENT_DEFAULT_MAX_ROUNDS
from dnsdle.constants import GENERATED_CLIENT_DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS
from dnsdle.constants import GENERATED_CLIENT_DEFAULT_QUERY_INTERVAL_MS
from dnsdle.constants import GENERATED_CLIENT_DEFAULT_REQUEST_TIMEOUT_SECONDS
from dnsdle.constants import GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_BASE_MS
from dnsdle.constants import GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_JITTER_MS
from dnsdle.constants import GENERATED_CLIENT_FILENAME_TEMPLATE
from dnsdle.constants import GENERATED_CLIENT_MANAGED_SUBDIR
from dnsdle.state import StartupError


_MANAGED_FILE_RE = re.compile(
    r"^dnsdl_[0-9a-f]{16}_[a-z0-9]{4,16}_(?:windows|linux)\.py$"
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


def _filename_for(file_id, file_tag, target_os):
    return GENERATED_CLIENT_FILENAME_TEMPLATE % (file_id, file_tag, target_os)


def _validate_publish_item(publish_item):
    if not publish_item.file_id or not publish_item.file_tag:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "missing required publish identity fields",
        )
    if publish_item.total_slices <= 0:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "TOTAL_SLICES must be positive",
            {"file_id": publish_item.file_id},
        )
    if len(publish_item.slice_tokens) != publish_item.total_slices:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "SLICE_TOKENS length must equal TOTAL_SLICES",
            {"file_id": publish_item.file_id},
        )
    if len(set(publish_item.slice_tokens)) != len(publish_item.slice_tokens):
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "SLICE_TOKENS contains duplicate entries",
            {"file_id": publish_item.file_id},
        )
    if not publish_item.crypto_profile or not publish_item.wire_profile:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "missing required profile fields",
            {"file_id": publish_item.file_id},
        )


def _render_client_source(config, publish_item, target_os):
    replacements = {
        "BASE_DOMAINS": tuple(config.domains),
        "FILE_TAG": publish_item.file_tag,
        "FILE_ID": publish_item.file_id,
        "PUBLISH_VERSION": publish_item.publish_version,
        "TARGET_OS": target_os,
        "TOTAL_SLICES": int(publish_item.total_slices),
        "COMPRESSED_SIZE": int(publish_item.compressed_size),
        "PLAINTEXT_SHA256_HEX": publish_item.plaintext_sha256,
        "SLICE_TOKENS": tuple(publish_item.slice_tokens),
        "CRYPTO_PROFILE": publish_item.crypto_profile,
        "WIRE_PROFILE": publish_item.wire_profile,
        "RESPONSE_LABEL": config.response_label,
        "DNS_MAX_LABEL_LEN": int(config.dns_max_label_len),
        "DNS_EDNS_SIZE": int(config.dns_edns_size),
        "SOURCE_FILENAME": publish_item.source_filename,
        "REQUEST_TIMEOUT_SECONDS": float(GENERATED_CLIENT_DEFAULT_REQUEST_TIMEOUT_SECONDS),
        "NO_PROGRESS_TIMEOUT_SECONDS": int(
            GENERATED_CLIENT_DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS
        ),
        "MAX_ROUNDS": int(GENERATED_CLIENT_DEFAULT_MAX_ROUNDS),
        "MAX_CONSECUTIVE_TIMEOUTS": int(
            GENERATED_CLIENT_DEFAULT_MAX_CONSECUTIVE_TIMEOUTS
        ),
        "RETRY_SLEEP_BASE_MS": int(GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_BASE_MS),
        "RETRY_SLEEP_JITTER_MS": int(
            GENERATED_CLIENT_DEFAULT_RETRY_SLEEP_JITTER_MS
        ),
        "QUERY_INTERVAL_MS": int(GENERATED_CLIENT_DEFAULT_QUERY_INTERVAL_MS),
    }

    source = build_client_template(target_os)
    for key, value in replacements.items():
        source = source.replace("@@%s@@" % key, repr(value))

    unreplaced = re.search(r"@@[A-Z0-9_]+@@", source)
    if unreplaced:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "unreplaced template placeholder after substitution",
            {
                "file_id": publish_item.file_id,
                "target_os": target_os,
                "placeholder": unreplaced.group(0),
            },
        )

    try:
        encode_ascii(source)
    except Exception:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "generated client source is not ASCII",
            {"file_id": publish_item.file_id, "target_os": target_os},
        )
    return source


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


def _build_artifacts(runtime_state):
    config = runtime_state.config
    if not config.domains:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "BASE_DOMAINS must be non-empty",
        )
    if not config.response_label:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "RESPONSE_LABEL must be non-empty",
        )
    for os_value in config.target_os:
        if os_value not in ALLOWED_TARGET_OS:
            raise StartupError(
                "startup",
                "generator_invalid_contract",
                "unsupported target_os for generator",
                {"target_os": os_value},
            )

    artifacts = []
    seen_names = set()

    for publish_item in runtime_state.publish_items:
        _validate_publish_item(publish_item)
        for target_os in config.target_os:
            filename = _filename_for(
                publish_item.file_id,
                publish_item.file_tag,
                target_os,
            )
            if filename in seen_names:
                raise StartupError(
                    "startup",
                    "generator_invalid_contract",
                    "deterministic generated filename collision",
                    {
                        "file_id": publish_item.file_id,
                        "file_tag": publish_item.file_tag,
                        "target_os": target_os,
                        "filename": filename,
                    },
                )
            seen_names.add(filename)
            source_text = _render_client_source(config, publish_item, target_os)
            artifacts.append(
                {
                    "file_id": publish_item.file_id,
                    "file_tag": publish_item.file_tag,
                    "target_os": target_os,
                    "filename": filename,
                    "source": source_text,
                    "publish_version": publish_item.publish_version,
                }
            )

    expected_count = len(runtime_state.publish_items) * len(config.target_os)
    if len(artifacts) != expected_count:
        raise StartupError(
            "startup",
            "generator_invalid_contract",
            "artifact count mismatch",
            {
                "expected": expected_count,
                "realized": len(artifacts),
            },
        )

    return tuple(artifacts)


def _collect_backup_targets(managed_dir, expected_names):
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
        if name in expected_names or _MANAGED_FILE_RE.match(name):
            targets.append(path)

    return tuple(sorted(targets))


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


def _transactional_commit(managed_dir, stage_dir, backup_dir, artifacts):
    expected_names = tuple(sorted(item["filename"] for item in artifacts))
    targets_to_backup = _collect_backup_targets(managed_dir, expected_names)

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

        for filename in expected_names:
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


def generate_client_artifacts(runtime_state):
    config = runtime_state.config
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

    artifacts = _build_artifacts(runtime_state)

    stage_dir = _build_run_dir(managed_dir, ".stage")
    backup_dir = _build_run_dir(managed_dir, ".backup")
    _safe_mkdir(stage_dir, "generator_write_failed")
    _safe_mkdir(backup_dir, "generator_write_failed")

    try:
        for artifact in artifacts:
            _write_staged_file(stage_dir, artifact["filename"], artifact["source"])

        _transactional_commit(managed_dir, stage_dir, backup_dir, artifacts)
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

    generated = []
    for artifact in artifacts:
        generated.append(
            {
                "file_id": artifact["file_id"],
                "file_tag": artifact["file_tag"],
                "target_os": artifact["target_os"],
                "publish_version": artifact["publish_version"],
                "path": os.path.join(managed_dir, artifact["filename"]),
                "source": artifact["source"],
                "filename": artifact["filename"],
            }
        )

    return {
        "managed_dir": managed_dir,
        "artifact_count": len(generated),
        "target_os": tuple(config.target_os),
        "artifacts": tuple(generated),
    }
