# Bash Downloader

This document defines generation and runtime behavior for direct per-payload
Bash downloaders.

## Scope

Each configured payload produces one `dnsdle_<file_id>.bash.sh` file after
mapping convergence. The script downloads that payload directly; it does not
download or invoke the universal Python client. Generated source is ASCII and
contains no PSK.

The runtime target is Linux with Bash 4.0 or newer. Required commands are:
`dig`, `openssl`, `base32`, `od`, `dd`, `xxd`, `gzip`, `sha256sum`, `mktemp`,
`cat`, `rm`, `sleep`, `wc`, and `mv`.

## Generation Contract

`dnsdle/bash_downloader.py` renders one source string from validated config and
the final mapped payload item. It embeds:

- `file_id`, `publish_version`, `file_tag`, `total_slices`, `compressed_size`,
  and plaintext SHA-256
- ordered configured domains, response label, DNS label cap, and EDNS size
- final ordered `slice_tokens` after global collision promotion
- canonical crypto labels and retry/timing constants

It never receives or renders `config.psk`.

Generation fails on invalid identity fields, invalid or duplicate tokens,
token-count mismatch, unreplaced placeholders, or non-ASCII source. The common
orchestrator validates all payload artifact paths before opening files and
writes through PID-qualified temporary files in the managed directory.

The returned common record has exactly:

- `language = bash`
- `kind = downloader`
- `source_filename`
- absolute `path`

Generated source and invocation text are not returned or logged.

## CLI Contract

Required:

- `--psk secret`

Optional:

- `--resolver host[:port]` or `[ipv6]:port`
- `--out path` (`-` writes verified plaintext to stdout)
- `--verbose`
- `--help` or `-h`

Missing/empty PSK, unknown arguments, invalid resolver syntax, invalid output
directory, embedded contract failure, or missing/incompatible commands exit
`2` before DNS or final-output work.

Errors are always written to standard error. `--verbose` additionally enables
progress and success messages. Generated Bash artifacts have mode `0700`; they
can still be invoked with `bash path` when the containing filesystem is
mounted `noexec`.

Default output is `${TMPDIR:-/tmp}/dnsdle_<file_id>`.

## Startup and Temporary State

The script uses `set -u` with explicit status handling; it does not use
`set -e`. It sets `umask 077`, creates one private temporary directory, and
installs cleanup traps before deriving keys.

Before DNS it runs fixed local vectors for:

- padded RFC 4648 base32 decoding
- RFC 1952 gzip decoding
- OpenSSL HMAC-SHA256 with a hex key

Failure is a usage/runtime-capability error (`2`), not a transfer retry.

## DNS Boundary

For each missing slice, the downloader invokes `dig` for an absolute A query:

- UDP only (`+notcp`)
- one try
- bounded request timeout
- configured EDNS size, or `+noedns` at `512`
- explicit resolver when supplied, otherwise the system resolver used by `dig`

It parses header and answer presentation, requires `NOERROR` without `TC`, and
requires exactly one matching IN CNAME owner. Command failure, DNS failure,
truncation, or missing CNAME is retryable. Multiple matching CNAME records are
a fatal parse error.

The downloader rotates to the next configured domain only after retryable DNS
failure. Valid responses retain the current domain.

## Payload Decode and Crypto

After the `dig` boundary, the downloader:

1. lowercases presentation text and strips one trailing dot;
2. validates the exact response-label/domain suffix;
3. validates non-empty base32 labels against `[a-z2-7]` and
   `dns_max_label_len`;
4. uppercases joined unpadded text, rejects impossible length residues, restores
   exact padding, and decodes to a binary file;
5. validates profile `1`, flags `0`, big-endian ciphertext length, non-zero
   ciphertext, total record size, and eight-byte MAC size;
6. derives encryption and MAC keys with OpenSSL HMAC-SHA256 using exact UTF-8
   PSK bytes;
7. constructs the authenticated metadata/ciphertext message in a file;
8. compares received and expected truncated MAC hex with a fixed-length XOR
   accumulator;
9. generates deterministic HMAC keystream blocks and XORs hex byte pairs;
10. writes the decrypted slice only after MAC success.

Ciphertext, MAC messages, key input, compressed bytes, and plaintext remain in
private files. Shell variables contain only text or hex and never NUL-bearing
binary data.

## Retry and Progress

The runtime mirrors canonical client bounds:

- maximum rounds: `64`
- consecutive DNS failures: `128`
- no-progress timeout: `60` seconds
- retry delay: `100` ms plus up to `150` ms jitter
- successful-query interval: `50` ms

Progress occurs only when a new verified slice is stored. The no-progress clock
resets only on that event.

## Reconstruction and Output

Verified slices are concatenated in ascending index order. The script checks
the exact compressed size, gzip-decompresses into its private directory, and
checks plaintext SHA-256.

Only verified plaintext may reach stdout or the requested output path. File
output is copied to an adjacent `mktemp` path and renamed into place. All
failure paths remove private and adjacent temporary files.

## Exit Codes

- `0`: success
- `2`: usage, embedded contract, or command capability
- `3`: DNS/transport exhaustion
- `4`: CNAME/base32/record parse violation
- `5`: HMAC/key/decrypt violation
- `6`: compressed-size/gzip/plaintext-hash violation
- `7`: stdout or file-write failure

The script is silent by default. `--verbose` enables progress, fatal reason,
and success output on stderr without PSK, key, or payload bytes.

## Invariants

1. Runtime PSK is required and never embedded.
2. Embedded token count equals `total_slices`; tokens are final and unique.
3. DNS failures alone are retryable after the `dig` boundary.
4. MAC verification precedes decryption.
5. Binary data never enters a Bash variable.
6. Unverified plaintext never reaches stdout or the final output path.
7. Temporary state is private and removed on every exit path.

## Related Docs

- `doc/architecture/QUERY_MAPPING.md`
- `doc/architecture/DNS_MESSAGE_FORMAT.md`
- `doc/architecture/CNAME_PAYLOAD_FORMAT.md`
- `doc/architecture/CRYPTO.md`
- `doc/architecture/PUBLISH_PIPELINE.md`
- `doc/architecture/ERRORS_AND_INVARIANTS.md`
