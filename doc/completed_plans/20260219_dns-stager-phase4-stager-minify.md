# Plan: Phase 4 -- Stager Minifier

## Summary

Create a custom deterministic minifier tailored to the stager template's
disciplined coding style. No AST, no tokenizer, no external dependencies
-- just mechanical text passes that are correct by construction given the
template constraints established in Phase 3. The minifier operates on
fully-substituted Python source (Phase 5 replaces `@@PLACEHOLDER@@`
markers before calling `minify()`).

## Prerequisites

- Phase 3 (stager template) must be complete. The minifier is designed for
  and tested against the template's coding discipline.

## Goal

After implementation:

- `dnsdle/stager_minify.py` exports `minify(source) -> str`.
- The minifier is deterministic: same input always produces same output.
- Minified output `compile()`s successfully.
- Round-tripping the fully-substituted stager source through minification
  preserves semantic correctness: all functions remain callable, all
  constant values are unchanged, and the download-verify-exec chain
  behaves identically.
- The minified output is substantially smaller than the readable template,
  making it suitable for zlib compression and base64 one-liner embedding.

## Design

### 1. Minifier module (`dnsdle/stager_minify.py`)

A module exporting a single function:

```python
def minify(source):
    """Minify stager source. Deterministic: same input -> same output."""
```

**Minification passes (applied in order):**

1. **Strip comment lines.** Drop any line whose `.strip()` starts with
   `#`.
2. **Strip blank lines.** Drop lines that are empty after stripping.
3. **Rename variables.** Apply a fixed rename table using word-boundary
   regex: `re.sub(r'\benc_key\b', 'e', src)`. Process longest names
   first to prevent substring interference. The rename table maps every
   template-local variable and function name to a single-character name.
   The regex operates on the entire source including string literals, so
   **no old-name in the rename table may appear as a whole word inside
   any string literal** of the post-substitution source. The Phase 3
   template satisfies this for hardcoded strings (crypto labels use
   `dnsdle-enc-v1|` style naming, never bare variable names).
   Substituted runtime values (domain labels, file identifiers) are
   generated or DNS-conformant and will not collide in practice.
4. **Reduce indentation.** Replace 4 spaces per indent level with 1 space.
5. **Semicolon-join.** Consecutive lines at the same indent level that are
   not control-flow openers (`if`, `for`, `while`, `try`, `except`,
   `else`, `elif`, `finally`, `def`, `return`, `with`, `break`,
   `continue`) get joined with `;`.

### 2. Variable rename table

The rename table is a module-level constant (list of `(old_name, new_name)`
pairs). It maps every template-local variable, function name, and
function parameter name to a short replacement. The table is ordered
longest-name-first so that `re.sub` on longer names runs before shorter
names, preventing substring interference. Because the template has no
nested functions or closures, each name is unique across the entire
source and whole-source `re.sub` renames it consistently at every use
site.

The table doubles as documentation of the variable mapping. It must be
updated whenever the stager template's local names change.

### 3. Control-flow detection

The semicolon-join pass must not join lines that begin with control-flow
keywords. Detection is based on the stripped line's first word:

```python
_BLOCK_STARTERS = frozenset((
    "if", "for", "while", "try", "except", "else",
    "elif", "finally", "def", "return", "with",
    "break", "continue",
))
```

A line is a block starter if its first whitespace-delimited token is in
this set. Block starters force a line break; they cannot be appended to
the previous line with `;`.

This set is intentionally scoped to the stager template's vocabulary,
not all Python keywords. `return`, `break`, and `continue` are included
even though `a=1;return a` is valid Python -- preventing that join is a
safety-over-size trade-off that keeps the pass trivial. Keywords like
`class`, `assert`, `raise`, and `del` are omitted because the template
never uses them.

### 4. Indentation reduction

Each line's leading whitespace is measured. Every 4-space indent level is
replaced with 1 space. Lines with indentation that is not a multiple of 4
spaces are left unchanged (this should never happen given the template's
coding discipline, but handling it defensively costs nothing).

## Affected Components

- `dnsdle/stager_minify.py` (NEW): deterministic minifier. Exports
  `minify()`. Contains the variable rename table as a module-level
  constant.

## Execution Notes

Implemented 2026-02-19.

All five minification passes implemented as specified:
1. Strip comment lines
2. Strip blank lines
3. Rename variables (106-entry table, longest-first ordering)
4. Reduce indentation (4-space to 1-space)
5. Semicolon-join same-indent non-block lines

Deviations from plan:

- The plan states "maps every template-local variable and function name
  to a single-character name." In practice 106 names are renamed; 48 get
  single-char names and 58 get two-char names (insufficient single-char
  namespace for all). This still achieves 40% size reduction.
- Six names are excluded from the rename table:
  - `mac`, `msg`, `stream`: appear as `\b`-delimited words inside
    hardcoded crypto label strings (`dnsdle-mac-v1|`, `dnsdle-mac-msg-v1|`,
    `dnsdle-enc-stream-v1|`).
  - `upper`: collides with the `.upper()` method call on the same
    variable.
  - `psk`, `resolver`: appear as whole words inside `"--psk"` and
    `"--resolver"` string literals.
- Four single-char names (`v`, `i`, `j`, `r`) are not renamed since
  there is no size benefit.
- The 10 ALL_CAPS template constants (`DOMAIN_LABELS`, `FILE_TAG`, etc.)
  are included in the rename table since they remain as variable names
  in the fully-substituted source.
- Regex patterns are pre-compiled at module load for performance.
- The semicolon-join pass strips trailing `:` from the first token
  before the block-starter check, so bare `else:` / `try:` / `finally:`
  lines are correctly detected.
