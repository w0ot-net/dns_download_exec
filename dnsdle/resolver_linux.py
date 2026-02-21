# -*- coding: ascii -*-
from __future__ import absolute_import, unicode_literals

# __EXTRACT: _load_unix_resolvers__
def _load_unix_resolvers():
    resolvers = []
    try:
        with open("/etc/resolv.conf", "r") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "#" in line:
                    line = line.split("#", 1)[0].strip()
                    if not line:
                        continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                if parts[0].lower() != "nameserver":
                    continue
                host = parts[1].strip()
                if not host:
                    continue
                if host not in resolvers:
                    resolvers.append(host)
    except Exception:
        return []
    return resolvers
# __END_EXTRACT__
