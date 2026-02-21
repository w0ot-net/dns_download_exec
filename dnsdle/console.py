from __future__ import absolute_import, unicode_literals

import os
import sys


_USE_COLOR = (
    hasattr(sys.stderr, "isatty")
    and sys.stderr.isatty()
    and os.name != "nt"
)

_ENABLED = True


def configure_console(enabled):
    global _ENABLED
    _ENABLED = enabled


def reset_console():
    global _ENABLED
    _ENABLED = True


def _color(code, text):
    if _USE_COLOR:
        return "\033[%sm%s\033[0m" % (code, text)
    return text


def _write(text):
    try:
        sys.stderr.write(text)
        sys.stderr.write("\n")
        sys.stderr.flush()
    except Exception:
        pass


def console_startup(config, generation_result, stagers):
    if not _ENABLED:
        return
    domains_str = ", ".join(config.domains)
    file_count = len(config.files)
    _write(
        _color("1;36", "dnsdle")
        + " serving %d file%s via [%s]"
        % (file_count, "" if file_count == 1 else "s", domains_str)
    )
    managed_dir = generation_result["managed_dir"]
    _write("  stagers:  " + _color("0;33", managed_dir + os.sep))
    for stager in stagers:
        src_base = os.path.basename(stager["source_filename"])
        stager_base = os.path.basename(stager["path"])
        _write("    %-12s -> %s" % (src_base, _color("0;33", stager_base)))
    _write(
        "  client:   "
        + _color("0;33", generation_result["path"])
    )


def console_server_start(host, port):
    if not _ENABLED:
        return
    addr = _color("1;32", "%s:%d" % (host, port))
    _write("listening on %s (ctrl-c to stop)" % addr)


def console_activity(file_tag, display_name):
    if not _ENABLED:
        return
    _write(
        _color("0;36", "<< download started: %s (tag=%s)" % (display_name, file_tag))
    )


def console_error(message):
    if not _ENABLED:
        return
    _write(_color("1;31", "error: %s" % message))


def console_shutdown(counters):
    if not _ENABLED:
        return
    _write(
        "shutdown: served=%d miss=%d faults=%d"
        % (
            counters.get("served", 0),
            counters.get("miss", 0),
            counters.get("runtime_fault", 0),
        )
    )
