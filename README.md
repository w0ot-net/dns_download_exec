# dns_download_exec

Download files over DNS with generated Python and Bash clients.

The server emits one universal Python client plus, for every published file,
a Python one-line stager and a direct Bash downloader. Generated payload
artifacts require the PSK at runtime and never embed it.

Run either per-file artifact with Bash and append the runtime arguments. For a
server listening on the local DNS port, use:

```bash
PSK='the same secret passed to the server'
bash generated_clients/dnsdle_v1/dnsdle_<file_id>.bash.sh --psk "$PSK" --resolver 127.0.0.1 --verbose
bash generated_clients/dnsdle_v1/dnsdle_<file_id>.python.1-liner.txt --psk "$PSK" --resolver 127.0.0.1 --verbose
```

The Bash downloader is generated with owner-execute permission, but invoking
it through `bash` also works from filesystems mounted with `noexec`. Omitting
`--psk` is an error; generated artifacts report that error on standard error.
