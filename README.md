# dns_download_exec

Download files over DNS with generated Python and Bash clients.

The server emits one universal Python client plus, for every published file,
a Python one-line stager and a direct Bash downloader. Generated payload
artifacts require the PSK at runtime and never embed it.
