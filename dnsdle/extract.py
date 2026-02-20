from __future__ import absolute_import, unicode_literals

import os
import re

from dnsdle.state import StartupError


_EXTRACT_START_RE = re.compile(r"^# __EXTRACT:\s+(\S+)__\s*$")
_EXTRACT_END_RE = re.compile(r"^# __END_EXTRACT__\s*$")


def _read_module_source(module_filename):
    source_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(source_dir, module_filename)
    try:
        with open(filepath, "r") as handle:
            return handle.read()
    except Exception as exc:
        raise StartupError(
            "startup",
            "extract_read_failed",
            "cannot read module source: %s" % exc,
            {"filename": module_filename},
        )


def extract_functions(module_filename, names):
    source = _read_module_source(module_filename)
    lines = source.split("\n")

    blocks = {}
    current_name = None
    current_lines = []

    for line in lines:
        start_match = _EXTRACT_START_RE.match(line)
        if start_match:
            if current_name is not None:
                raise StartupError(
                    "startup",
                    "extract_marker_error",
                    "nested extract markers",
                    {"filename": module_filename, "name": start_match.group(1)},
                )
            current_name = start_match.group(1)
            current_lines = []
            continue

        end_match = _EXTRACT_END_RE.match(line)
        if end_match:
            if current_name is None:
                raise StartupError(
                    "startup",
                    "extract_marker_error",
                    "end marker without start",
                    {"filename": module_filename},
                )
            blocks[current_name] = "\n".join(current_lines)
            current_name = None
            current_lines = []
            continue

        if current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        raise StartupError(
            "startup",
            "extract_marker_error",
            "unterminated extract block",
            {"filename": module_filename, "name": current_name},
        )

    missing = set(names) - set(blocks.keys())
    if missing:
        raise StartupError(
            "startup",
            "extract_marker_error",
            "requested extract names not found",
            {"filename": module_filename, "missing": sorted(missing)},
        )

    return [blocks[name] for name in names]


def apply_renames(source, rename_table):
    for old_name, new_name in rename_table:
        source = re.sub(r"\b" + re.escape(old_name) + r"\b", new_name, source)
    return source
