# -*- coding: ascii -*-
from __future__ import absolute_import, unicode_literals

import subprocess

# __TEMPLATE_SOURCE__
# __EXTRACT: _run_nslookup__
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
# __END_EXTRACT__


# __EXTRACT: _parse_nslookup_output__
def _parse_nslookup_output(output):
    lines = output.splitlines()
    server_index = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("server:"):
            server_index = index
            break
    if server_index is None:
        return []

    addresses = []
    seen_addr = False
    for line in lines[server_index + 1:]:
        stripped = line.strip()
        if not stripped:
            break
        if stripped.lower().startswith("address") and ":" in stripped:
            addr = stripped.split(":", 1)[1].strip()
            if addr:
                addresses.append(addr)
            seen_addr = True
        elif seen_addr and line[0:1] in (" ", "\t"):
            addresses.append(stripped)
    return addresses
# __END_EXTRACT__


# __EXTRACT: _load_windows_resolvers__
def _load_windows_resolvers():
    try:
        output = _run_nslookup()
    except Exception:
        return []
    return _parse_nslookup_output(output)
# __END_EXTRACT__
