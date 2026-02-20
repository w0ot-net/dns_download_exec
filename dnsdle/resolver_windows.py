# -*- coding: ascii -*-
from __future__ import absolute_import

import re
import subprocess

# __TEMPLATE_SOURCE__
_IPV4_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def _run_nslookup():
    args = ["nslookup", "google.com"]
    run_fn = getattr(subprocess, "run", None)
    if run_fn is not None:
        result = run_fn(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            universal_newlines=True,
        )
        return result.stdout or ""

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    output, _ = proc.communicate()
    return output or ""


def _parse_nslookup_output(output):
    lines = output.splitlines()
    server_index = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("server:"):
            server_index = index
            break
    if server_index is None:
        return None

    for line in lines[server_index + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("non-authoritative answer"):
            break
        match = _IPV4_RE.search(stripped)
        if match:
            return match.group(1)
    return None


def _load_windows_resolvers():
    try:
        output = _run_nslookup()
    except Exception:
        return []

    ip = _parse_nslookup_output(output)
    if not ip:
        return []
    return [ip]
