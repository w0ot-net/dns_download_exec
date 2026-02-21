# FAQ

## What is dnsdle?

dnsdle is a DNS-based file transfer system. It publishes operator-selected
files as DNS CNAME responses and generates self-contained Python clients that
download those files by issuing ordinary DNS queries, verifying cryptographic
integrity, and reassembling the result on disk.

The operator starts a DNS server with a pre-shared key, a list of domains, and
one or more files to serve. dnsdle compresses and slices each file, derives
deterministic query tokens so that neither file names nor slice indexes appear
in the wire traffic, and encrypts every slice with HMAC-verified, per-file
keying material. It then emits two artifacts:

- A **universal client** -- a single generated Python script that can download
  any published file given the correct parameters.
- Per-file **one-liner stagers** -- minimal bootstrap scripts that first
  download the universal client over DNS, then invoke it to retrieve the target
  file.

All generated code is standard-library-only Python (2.7 and 3.x), so the
client side requires nothing beyond a working Python interpreter and DNS
connectivity.

## How does the download work?

The client downloads a file by retrieving it one slice at a time over DNS.

1. **Token derivation.** The client uses the pre-shared key, mapping seed, and
   publish version to deterministically derive a `file_tag` and per-slice
   `slice_token` values -- the same algorithm the server used when it built its
   lookup table. Neither file names nor slice indexes appear on the wire.

2. **Query.** For each missing slice the client sends a DNS A query for
   `<slice_token>.<file_tag>.<base_domain>`. It picks a system resolver (or one
   supplied via `--resolver`) and sends a single UDP packet per slice.

3. **Response.** The dnsdle server recognises the composite key
   `(file_tag, slice_token)`, looks up the corresponding slice bytes, encrypts
   and MACs them, base32-encodes the result, and returns it as a CNAME record:
   `<payload_labels>.<response_label>.<domain>`.

4. **Verify and decrypt.** The client strips the known suffix from the CNAME
   target, base32-decodes the payload labels, verifies the truncated HMAC, and
   decrypts the ciphertext with a per-file keystream derived from the PSK.

5. **Reassemble.** Once every slice has been received, the client concatenates
   them in index order, decompresses with zlib, and verifies the plaintext
   SHA-256 against the expected hash. On success it atomically writes the file
   to disk.

Retries, domain rotation, and configurable timeouts handle transient failures.
Each slice is independently verifiable and decryptable, so they can arrive in
any order and duplicate responses are safe.

## When would this tool be useful?

dnsdle is useful whenever DNS is the only outbound channel available or when
you want file delivery to blend into normal DNS traffic.

- **Restricted networks.** Environments that block direct HTTP/HTTPS but still
  allow DNS resolution (common in segmented corporate networks, locked-down
  VPNs, and air-gapped-adjacent hosts with a DNS forwarder).
- **Penetration testing and red-team engagements.** Delivering payloads or
  tooling to a compromised host that has no other outbound path. The one-liner
  stagers are designed to bootstrap from a single command.
- **Minimal-dependency hosts.** Servers or containers that have Python and DNS
  but no curl, wget, or outbound TCP. dnsdle needs only the standard library
  and UDP port 53.
- **Covert file distribution.** DNS queries are routine traffic and rarely
  inspected at the payload level, making dnsdle less visible than HTTP-based
  transfers in environments with deep packet inspection.

## How does the crypto work?

Everything is built from HMAC-SHA256 and XOR using the standard library only.
The operator supplies a pre-shared key (PSK); all other keying material is
derived deterministically from the PSK, a file identity, and a publish version.

**Key derivation.** For each published file the server derives two independent
32-byte keys:

- `enc_key = HMAC-SHA256(psk, "dnsdle-enc-v1|" + file_id + "|" + publish_version)`
- `mac_key = HMAC-SHA256(psk, "dnsdle-mac-v1|" + file_id + "|" + publish_version)`

Because the key context includes `file_id` and `publish_version`, different
files and different versions of the same file never share keys.

**Encryption.** Each slice is encrypted by XOR with a deterministic keystream.
The keystream is a concatenation of HMAC-SHA256 blocks keyed by `enc_key`:

    block[i] = HMAC-SHA256(enc_key, "dnsdle-enc-stream-v1|" + file_id
               + "|" + publish_version + "|" + slice_index + "|" + i)

The stream is truncated to the slice length. Because the nonce input is
`(file_id, publish_version, slice_index)` rather than a send-order counter,
the same slice always produces the same ciphertext regardless of retrieval
order or retries.

**Authentication.** Each slice carries an 8-byte truncated HMAC-SHA256 tag
computed with `mac_key` over the slice metadata and ciphertext:

    mac_input = file_id | publish_version | slice_index
                | total_slices | compressed_size | ciphertext

The client verifies the MAC before decrypting. Any mismatch is a hard failure
-- there are no warnings or fallbacks.

**End-to-end integrity.** After all slices are decrypted and reassembled in
index order, the client decompresses and checks the plaintext SHA-256 against
the hash embedded in the client parameters. A mismatch discards the output.
