# Plan: Protect string literals from minifier renames

## Summary

The stager minifier's rename pass applies `\b`-bounded regex replacements
globally, including inside string literals.  This corrupts CLI flag names in
the `sys.argv` construction (e.g. `"--total-slices"` becomes `"--total-ad"`
because `slices` is renamed to `ad`).  The fix wraps Pass 3 with a
string-literal extraction/restoration step so renames can never touch content
inside quotes.

## Problem

`stager_minify.py` Pass 3 iterates a rename table and runs
`pattern.sub(new, src)` on the full source.  Word-boundary anchors (`\b`)
do not distinguish identifiers from words inside string literals because `-`
and `"` are non-word characters, creating word boundaries around substrings
like `slices` in `"--total-slices"`.

Three `sys.argv` flag names are currently corrupted:

| Rename entry       | String affected            | Result             |
|--------------------|----------------------------|--------------------|
| `slices` -> `ad`   | `"--total-slices"`         | `"--total-ad"`     |
| `compressed` -> `B`| `"--compressed-size"`      | `"--B-size"`       |
| `label` -> `al`    | `"--response-label"`       | `"--response-al"`  |

Adding individual exclusions for these three words would fix today's bug but
leaves the entire class of bug open for every future rename-table addition.

## Goal

After implementation the minifier must guarantee that the content of every
string literal (single-quoted, double-quoted, with or without `b` prefix)
passes through the rename pass unchanged, regardless of what entries exist in
the rename table.

## Design

Before Pass 3, extract every string literal from `src` via a compiled regex,
replace each with a sequentially numbered placeholder (`__S0__`, `__S1__`,
...), apply all renames, then restore the original literals.

Concrete steps inside `minify()`:

1. **Extract** -- Use a pre-compiled regex matching `b?"..."` and `b?'...'`
   with proper escape handling (`(?:[^"\\]|\\.)*`).  A closure appends each
   match to a list and returns `__S<n>__`.  The placeholder format is safe
   because `_` and alphanumerics are all word-characters, so no rename
   pattern can match a substring inside it.

2. **Rename** -- Existing Pass 3 loop runs on the placeholder-bearing source,
   unchanged.

3. **Restore** -- A single `re.sub(r"__S(\d+)__", ...)` replaces each
   placeholder with the original literal from the list.

No changes to the rename table, the template, or any other file.

## Affected Components

- `dnsdle/stager_minify.py`: Add `_STRING_RE` compiled regex; wrap the
  rename loop in `minify()` with extract-before / restore-after logic
  (roughly +15 lines).
