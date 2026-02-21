# Plan: Auto-generate stager minifier rename table

## Summary

Replace the 170-entry hand-maintained rename table in `stager_minify.py` with an
auto-generated one derived from the stager source at minification time.  This
eliminates a fragile manual synchronization requirement: every function/variable
name change in any extracted module currently requires updating the table by hand.

## Problem

`stager_minify.py` contains a manually curated `_RENAME_TABLE` list of 170
`(old_name, new_name)` pairs.  This table must be kept in sync with every
identifier name across all extracted source modules (`compat.py`, `helpers.py`,
`cname_payload.py`, `dnswire.py`, `resolver_linux.py`, `resolver_windows.py`)
plus the stager template's inline code (`_STAGER_DNS_OPS`, `_STAGER_DISCOVER`,
`_STAGER_SUFFIX`).

The maintenance burden:

- Adding/renaming a function or variable in extracted code requires manually
  adding/updating a table entry.
- Names that appear inside string literals must be manually identified and
  excluded (documented in comments at lines 10-18).
- Longest-first ordering is critical for correctness (substring interference)
  and must be maintained by hand.
- The pre-compiled regex list (`_RENAME_COMPILED`) is derived from the table
  at module load time, coupling the table structure to the compilation logic.

## Goal

- The rename table is built automatically from the stager source at
  minification time.
- No manual rename entries exist in the source.
- Adding or renaming identifiers in extracted code requires zero changes to
  the minifier.
- Minification remains deterministic: same input produces same output.
- The resulting one-liners are comparable in size to the current output
  (the auto-generated table covers the same identifiers).

## Design

### Algorithm

The new rename pass replaces the static `_RENAME_TABLE` and `_RENAME_COMPILED`
with a function `_build_rename_table(source)` that runs inside `minify()` after
string extraction (step already exists):

1. **Collect identifiers** -- find all `\b[a-zA-Z_]\w*\b` tokens in the
   string-extracted source.
2. **Collect attribute names** -- find all tokens appearing after `.` in the
   source (`\.([a-zA-Z_]\w*)`).  These are external method/attribute names
   (`socket.AF_INET`, `struct.pack`, `hashlib.sha256`, etc.) that must not
   be renamed.
3. **Build skip set** -- union of:
   - Python keywords (`keyword.kwlist`)
   - Python builtins (a static set covering both Py2 and Py3 names:
     `True`, `False`, `None`, `range`, `len`, `int`, `str`, `bytes`,
     `bytearray`, `type`, `isinstance`, `getattr`, `hasattr`, `bool`,
     `open`, `Exception`, `ValueError`, `TypeError`, `NameError`,
     `UnicodeDecodeError`, `unicode`, `long`, etc.)
   - stdlib module names used in the stager (`base64`, `hashlib`, `hmac`,
     `random`, `socket`, `struct`, `subprocess`, `sys`, `time`, `zlib`,
     `os`)
   - Attribute names collected in step 2
   - String placeholder pattern names (`__S\d+__`)
4. **Select candidates** -- identifiers from step 1 that are not in the skip
   set and have `len > 2` (names of 1-2 chars save nothing or negligibly
   little).  Sort by `(-len, name)` for deterministic longest-first ordering.
5. **Generate short names** -- produce a sequence of short identifiers
   (`a`..`z`, `A`..`Z`, `aa`..`az`, `aA`..`aZ`, `ba`..etc.), skipping any
   that collide with the skip set or with identifiers already present in the
   source.  Assign one short name per candidate.
6. **Apply renames** -- compile `\b`-bounded regexes for each
   `(candidate, short_name)` pair and apply longest-first, exactly as the
   current pass does.

### What stays the same

- Pass 1 (strip comments), Pass 2 (strip blanks), Pass 4 (reduce indentation),
  Pass 5 (semicolon-join) are unchanged.
- String extraction/restoration mechanism (`_STRING_RE`, `_PLACEHOLDER_RE`)
  is unchanged.
- `_BLOCK_STARTERS` is unchanged.
- The `minify()` function signature and return contract are unchanged.
- The compile check and one-liner encoding in `stager_generator.py` are
  unchanged.

### What is removed

- `_RENAME_TABLE` (170 entries, lines 19-178)
- `_RENAME_COMPILED` (pre-compiled patterns, lines 187-190)
- The comment block explaining manual exclusions (lines 6-18)

### What is added

- `import keyword` at the top of the module.
- `_RESERVED_NAMES` -- a frozen set of Python keywords + builtins + stdlib
  module names (approximately 80 entries, static and self-documenting).
- `_generate_short_names(count, skip)` -- yields deterministic short
  identifier names, skipping any in the skip set.
- `_build_rename_table(source_without_strings)` -- implements steps 1-5
  above, returns a list of `(compiled_regex, short_name)` pairs ready for
  `re.sub`.
- Updated rename pass inside `minify()` that calls `_build_rename_table`
  on the string-extracted source, then applies the returned patterns.

## Affected Components

- `dnsdle/stager_minify.py`: replace static rename table and pre-compiled patterns with auto-generation logic; add `_RESERVED_NAMES`, `_generate_short_names`, `_build_rename_table`; update the rename pass in `minify()`
- `doc/architecture/STAGER.md`: update the Minification section to describe auto-generated rename table instead of manual "158 entries" table
