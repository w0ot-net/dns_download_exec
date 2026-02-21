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
