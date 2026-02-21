from __future__ import absolute_import, unicode_literals

import keyword
import re

# Runtime builtins for the current Python version.
try:
    _rt_builtins = set(dir(__import__('builtins')))
except ImportError:
    _rt_builtins = set(dir(__import__('__builtin__')))

# Names present in one Python version's builtins but not another's.
# Ensures the rename table is identical regardless of build Python version.
_CROSS_VERSION_BUILTINS = frozenset((
    # Py2 builtins absent from Py3
    "apply", "basestring", "buffer", "cmp", "coerce", "execfile", "file",
    "intern", "long", "raw_input", "reduce", "reload", "StandardError",
    "unicode", "unichr", "xrange",
    # Py3.8+ builtins absent from Py2 and older Py3
    "breakpoint",
    # Py3.10+ builtins
    "aiter", "anext",
    # Py3.11+ exception types
    "BaseExceptionGroup", "ExceptionGroup",
    # Platform-specific (Windows only)
    "WindowsError",
))

# Stdlib modules imported by the stager.
_STAGER_STDLIB = frozenset((
    "base64", "hashlib", "hmac", "random", "socket", "struct",
    "subprocess", "sys", "time", "zlib",
))

_RESERVED_NAMES = frozenset(_rt_builtins | _CROSS_VERSION_BUILTINS | _STAGER_STDLIB)

_BLOCK_STARTERS = frozenset((
    "if", "for", "while", "try", "except", "else",
    "elif", "finally", "def", "return", "with",
    "break", "continue",
))

# Match string literals (single/double quoted, optional b prefix) so the
# rename pass never touches content inside quotes.
_STRING_RE = re.compile(r'''b?(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')''')
_PLACEHOLDER_RE = re.compile(r"__S(\d+)__")
_IDENT_RE = re.compile(r"\b[a-zA-Z_]\w*\b")
_ATTR_RE = re.compile(r"\.([a-zA-Z_]\w*)")
_PLACEHOLDER_NAME_RE = re.compile(r"^__S\d+__$")


def _generate_short_names(count, skip):
    """Yield *count* deterministic short identifier names, skipping *skip*."""
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    produced = 0
    for ch in chars:
        if produced >= count:
            return
        if ch not in skip:
            yield ch
            produced += 1
    for c1 in chars:
        for c2 in chars:
            if produced >= count:
                return
            name = c1 + c2
            if name not in skip:
                yield name
                produced += 1


def _build_rename_table(source):
    """Build rename table from string-extracted source.

    Returns a list of (compiled_regex, short_name) pairs sorted longest-first,
    ready for sequential re.sub application.
    """
    all_idents = set(_IDENT_RE.findall(source))
    attr_names = set(_ATTR_RE.findall(source))
    skip = set(keyword.kwlist) | _RESERVED_NAMES | attr_names
    skip.update(n for n in all_idents if _PLACEHOLDER_NAME_RE.match(n))
    candidates = sorted(
        (n for n in all_idents if n not in skip and len(n) > 2),
        key=lambda n: (-len(n), n),
    )
    if not candidates:
        return []
    short_names = list(_generate_short_names(len(candidates), skip | all_idents))
    return [
        (re.compile(r"\b" + re.escape(old) + r"\b"), new)
        for old, new in zip(candidates, short_names)
    ]


def minify(source):
    """Minify stager source. Deterministic: same input -> same output."""
    lines = source.split("\n")
    # Pass 1: strip comment lines.
    lines = [ln for ln in lines if not ln.strip().startswith("#")]
    # Pass 2: strip blank lines.
    lines = [ln for ln in lines if ln.strip()]
    src = "\n".join(lines)
    # Extract string literals before renaming so renames cannot corrupt
    # content inside quotes.
    saved = []
    def _extract(m):
        saved.append(m.group(0))
        return "__S%d__" % (len(saved) - 1)
    src = _STRING_RE.sub(_extract, src)
    # Pass 3: rename variables (longest names first, auto-generated).
    for pattern, new in _build_rename_table(src):
        src = pattern.sub(new, src)
    # Restore original string literals.
    src = _PLACEHOLDER_RE.sub(lambda m: saved[int(m.group(1))], src)
    lines = src.split("\n")
    # Pass 4: reduce indentation (4 spaces -> 1 space per level).
    reduced = []
    for ln in lines:
        stripped = ln.lstrip(" ")
        spaces = len(ln) - len(stripped)
        level = spaces // 4
        remainder = spaces % 4
        if remainder:
            reduced.append(ln)
        else:
            reduced.append(" " * level + stripped)
    lines = reduced
    # Pass 5: semicolon-join consecutive same-indent non-block lines.
    # Skip joining inside multiline parenthesized expressions.
    result = []
    for ln in lines:
        stripped = ln.lstrip(" ")
        indent = len(ln) - len(stripped)
        token = stripped.split(None, 1)[0].rstrip(":") if stripped else ""
        first_char = stripped[0] if stripped else ""
        if result:
            prev = result[-1]
            prev_stripped = prev.lstrip(" ")
            prev_indent = len(prev) - len(prev_stripped)
            if (indent == prev_indent
                    and token not in _BLOCK_STARTERS
                    and not prev.rstrip().endswith(",")
                    and not prev.rstrip().endswith("(")
                    and first_char not in "+-*/|&^%~)"):
                result[-1] = prev + ";" + stripped
                continue
        result.append(ln)
    return "\n".join(result)
