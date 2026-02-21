# Plan: IPv6 resolver support

## Summary

Fix Windows resolver discovery so it stops returning the wrong IP when the
system DNS server is IPv6-only, and add end-to-end IPv6 DNS resolver support
to both the stager and client.

## Problem

`_parse_nslookup_output` uses `_IPV4_RE` to find the DNS server address in
`nslookup google.com` output.  When the server only has an IPv6 address the
regex never matches, but the loop skips empty lines (`continue`) instead of
breaking on them, so it bleeds past the server section into the answer section
and returns one of Google's A-record IPs as the resolver.  Observed:

```
C:\> nslookup google.com
Server:  UnKnown
Address:  2001:db8:1::100        <-- IPv6, regex misses

Non-authoritative answer:        <-- should stop here
Name:    google.com
Addresses:  2607:f8b0:4008:80c::200e
          172.217.3.78           <-- regex grabs this instead
```

Even after fixing the parser, all socket code is hardcoded to `AF_INET`, so
IPv6 resolvers cannot be used at all.

## Goal

1. nslookup parser extracts the DNS server address from the server section
   only (IPv4 or IPv6), never bleeds into the answer section.
2. Stager and client can send DNS queries over IPv6 when the resolver is IPv6.
3. `--resolver [host]:port` bracket notation works end-to-end for IPv6.
4. `_IPV4_RE` and `import re` removed from the stager (saves minified size);
   `import re` kept in client (still used by `_TOKEN_RE` / `_LABEL_RE`).

## Design

### Why not `ipaddress` or `inet_pton`

`ipaddress` is not in the Python 2.7 stdlib (required by this project).
`socket.inet_pton` is unavailable on Windows Python 2.7.  Neither is needed:
the downstream `socket.getaddrinfo` call in `_discover_resolver` /
`_discover_system_resolver` already validates the address and raises on
garbage.  Structural parsing of the nslookup output plus downstream
getaddrinfo validation is sufficient and keeps the code minimal.

### 1. Fix nslookup parser (`resolver_windows.py`, `client_runtime.py`)

Replace the regex-based scan with structural extraction of the server section:

- Find the `Server:` line.
- Scan subsequent lines; **break on the first empty line** (end of server
  section) instead of continuing past it.
- Collect addresses from lines starting with `Address` (covers both
  `Address:` and `Addresses:`).  Extract the value after the first colon.
- Handle indented continuation lines (multi-address servers) by accepting
  lines starting with whitespace, but **only after** at least one `Address`
  line has been seen (prevents misinterpreting non-address lines between
  `Server:` and `Address:` in localized builds).
- Return a **list** of address strings.  Always return `[]` (never `None`)
  when no addresses are found, since callers iterate the result directly.
  This is a breaking change from the current single-string-or-None return.

Remove `_IPV4_RE` from both files.  Remove `import re` from
`resolver_windows.py` (no longer used).  Keep `import re` in
`client_runtime.py` (`_TOKEN_RE`, `_LABEL_RE` still need it).

Update `_load_windows_resolvers` (resolver_windows.py) and the Windows branch
of `_load_system_resolvers` (client_runtime.py) to return the list directly
instead of wrapping a single result.

### 2. IPv6 socket support

**`_send_query`** (stager_template.py `_STAGER_PRE_RESOLVER`):
Determine AF from the address: `AF_INET6 if ":" in addr[0] else AF_INET`.
Python's `sendto()` accepts `(host, port)` for both AF_INET and AF_INET6.

**`_send_dns_query`** (client_runtime.py):
Same AF detection.

**`_discover_resolver`** (stager_template.py `_STAGER_DISCOVER`):
Try `AF_INET` first, then `AF_INET6` for each host, returning the first
successful `getaddrinfo` result.  Normalize the sockaddr to a plain
`(host, port)` 2-tuple via `_ai[0][4][:2]` so downstream code never sees
the 4-tuple that `getaddrinfo` returns for AF_INET6.  (Using `AF_UNSPEC`
instead of the explicit dual-AF loop would be fewer lines, but defers
address-family preference to the OS, which may prefer IPv6 on some
configurations; explicit `AF_INET`-first is more predictable.)

**`_resolve_udp_address`** (client_runtime.py):
Same dual-AF loop (replaces the current `AF_INET`-only call).  Already
normalizes to `(host, port)` 2-tuple, so no shape change needed.

### 3. Stager resolver string formatting (`_STAGER_SUFFIX`)

Since `_discover_resolver` now always returns a `(host, port)` 2-tuple,
format the resolver string directly: `"[%s]:%d"` when `":" in host`
(IPv6), otherwise `"%s:%d"` (IPv4).

For the `--resolver` manual parse path, add bracket-aware parsing (matching
the client's existing `_parse_resolver_arg` logic): check for leading `[`,
otherwise split on `:` only when `count(":") == 1`.

### 4. Client log line (client_runtime.py)

Update the `"start file_id=... resolver=..."` log to use bracket notation for
IPv6 addresses.

### 5. Stager minifier (`stager_minify.py`)

Remove `("_IPV4_RE", "cu")` from `_RENAME_TABLE` and remove
`("match", "db")` (the variable was only used by the regex match call).
Remove `import re` from the stager import list in `_STAGER_PRE_RESOLVER`.

Add rename entries for new identifiers introduced by the structural parser
and dual-AF loop.  Expected new names (verify against final implementation):
`addresses` (address list accumulator in `_parse_nslookup_output`),
`seen_addr` (state guard for continuation lines), `_af` (AF loop variable
in `_discover_resolver`).

## Affected Components

- `dnsdle/resolver_windows.py`: rewrite `_parse_nslookup_output` (structural
  parse, return list), simplify `_load_windows_resolvers`, delete `_IPV4_RE`
  and `import re`
- `dnsdle/stager_template.py`: remove `import re` from `_STAGER_PRE_RESOLVER`;
  IPv6 AF detection in `_send_query`; dual-AF loop with 2-tuple normalization
  in `_STAGER_DISCOVER`; bracket-formatted resolver string and bracket-aware
  `--resolver` parsing in `_STAGER_SUFFIX`
- `dnsdle/client_runtime.py`: same parser rewrite in `_parse_nslookup_output`
  and `_load_system_resolvers` Windows branch; delete `_IPV4_RE`; dual-AF in
  `_resolve_udp_address`; IPv6 AF detection in `_send_dns_query`; bracket
  notation in start log line
- `dnsdle/stager_minify.py`: remove `_IPV4_RE` and `match` entries from
  `_RENAME_TABLE`

## Execution Notes

Implemented as planned with no deviations.  All five design sections
executed:

1. **Parser rewrite** -- identical structural parser in both
   `resolver_windows.py` and `client_runtime.py`.  Returns `[]` on no match.
   Uses `seen_addr` state guard for continuation lines.  Uses `addr` (already
   in rename table) instead of introducing `value` to avoid a new minifier
   entry.

2. **IPv6 socket support** -- `_send_query` and `_send_dns_query` detect AF
   via `":" in addr[0]`.  `_discover_resolver` dual-AF loop with
   `_ai[0][4][:2]` normalization.  `_resolve_udp_address` dual-AF loop,
   already returns 2-tuple.

3. **Stager suffix** -- bracket formatting for auto-discovered IPv6; bracket-
   aware `--resolver` parsing with `startswith("[")` / `count(":") == 1`.

4. **Client log line** -- `_resolver_fmt` selects bracket or plain format.

5. **Minifier** -- replaced `_IPV4_RE`/`match` codes (`cu`/`db`) with
   `addresses`/`seen_addr`.  Added `_rest` (`do`), `_end` (`dp`), `_af`
   (`dq`).

Commit: `152cc17`
