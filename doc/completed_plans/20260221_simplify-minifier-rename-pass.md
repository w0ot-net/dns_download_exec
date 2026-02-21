# Plan: Simplify minifier rename pass to single-pass dict lookup

## Summary

Replace the N-compiled-regex sequential substitution in `stager_minify.py` with a
single-pass dict-based substitution using the existing `_IDENT_RE` regex.  This
eliminates per-identifier regex compilation and reduces N full-source scans to one,
while producing identical output for any input.

## Problem

`_build_rename_table` compiles a separate `\b`-bounded regex for every renameable
identifier and returns a list of `(compiled_regex, short_name)` pairs.  `minify()`
then applies each regex sequentially over the entire source string:

```python
# Current: O(N) compiled regexes, O(N) full-source passes
return [
    (re.compile(r"\b" + re.escape(old) + r"\b"), new)
    for old, new in zip(candidates, short_names)
]
...
for pattern, new in _build_rename_table(src):
    src = pattern.sub(new, src)
```

The longest-first ordering exists to prevent shorter renames from interfering with
longer names.  But `\b` treats `_` as a word character, so `\bfoo\b` never matches
inside `foo_bar` regardless of processing order.  The ordering constraint is
therefore unnecessary for correctness -- it only matters for deterministic
candidate-to-short-name assignment, which a stable sort already guarantees.

The per-identifier regex compilation and sequential application are pure overhead:
the same result can be achieved by matching all identifiers in one pass with the
existing `_IDENT_RE` and looking up replacements in a dict.

## Goal

- The rename pass compiles zero per-identifier regexes.
- The rename pass makes exactly one scan over the source string.
- Output is byte-identical for any input (determinism preserved).
- `_build_rename_table` is replaced by `_build_rename_map` returning a plain dict.
- Net reduction of ~5 lines in the module.

## Design

### Change `_build_rename_table` to `_build_rename_map`

The function keeps the same candidate selection and short-name generation logic.
Only the return value changes: instead of a list of `(compiled_regex, short_name)`
pairs, it returns a `dict` mapping `old_name -> short_name`.

```python
def _build_rename_map(source):
    # ... (identical candidate selection and short-name generation) ...
    return dict(zip(candidates, short_names))
```

The sorted candidate ordering by `(-len(n), n)` is retained for deterministic
short-name assignment (longer names get first pick of short names), but no regexes
are compiled.

### Change the rename application in `minify()`

Replace the sequential loop with a single `_IDENT_RE.sub()` call using a dict
lookup:

```python
rename_map = _build_rename_map(src)
if rename_map:
    src = _IDENT_RE.sub(lambda m: rename_map.get(m.group(0), m.group(0)), src)
```

`_IDENT_RE` already matches `\b[a-zA-Z_]\w*\b` -- exactly the word-bounded
identifiers that the per-identifier regexes were matching individually.  The
`dict.get` with a default of the original match text leaves non-candidate
identifiers unchanged.

### Why output is identical

1. Candidate selection and short-name assignment are unchanged (same sorted order,
   same `_generate_short_names` sequence).
2. `_IDENT_RE` matches the same word-bounded identifiers that the per-identifier
   `\b...\b` regexes matched.  `\b` word boundaries are identical in both cases.
3. A single-pass approach processes each identifier occurrence exactly once, left to
   right.  The sequential approach also processes each occurrence exactly once per
   regex, and because word boundaries prevent cross-identifier interference, the
   result is the same.

## Affected Components

- `dnsdle/stager_minify.py`: rename `_build_rename_table` to `_build_rename_map`,
  change return type from list-of-regex-pairs to dict, replace sequential regex loop
  in `minify()` with single `_IDENT_RE.sub` call.
- `doc/architecture/STAGER.md`: update the Minification section -- change "Each
  rename is applied via compiled `\b`-bounded regex" to describe single-pass dict
  lookup.

## Execution Notes

Implemented as designed with no deviations.

- `_build_rename_table` renamed to `_build_rename_map`; returns `dict(zip(candidates, short_names))` instead of list of `(compiled_regex, short_name)` pairs; empty case returns `{}` instead of `[]`.
- `minify()` rename pass replaced: sequential `for pattern, new in _build_rename_table(src)` loop replaced with single `_IDENT_RE.sub(lambda m: rename_map.get(m.group(0), m.group(0)), src)` call.
- STAGER.md Minification section updated to describe single-pass dict lookup.
- Net change: -3 lines in `stager_minify.py` (15 removed, 12 added).
- Implementation commit: `2941885`
