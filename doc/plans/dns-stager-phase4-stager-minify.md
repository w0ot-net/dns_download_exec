# Plan: Phase 4 -- Stager Minifier

## Summary

Create a custom deterministic minifier tailored to the stager template's
disciplined coding style. No AST, no tokenizer, no external dependencies
-- just mechanical text passes that are correct by construction given the
template constraints established in Phase 3.

## Prerequisites

- Phase 3 (stager template) must be complete. The minifier is designed for
  and tested against the template's coding discipline.

## Goal

After implementation:

- `dnsdle/stager_minify.py` exports `minify(source) -> str`.
- The minifier is deterministic: same input always produces same output.
- Minified output `compile()`s successfully.
- Round-tripping the stager template through minification preserves all
  function names and embedded constants.
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
   template-local variable to a single-character name.
4. **Reduce indentation.** Replace 4 spaces per indent level with 1 space.
5. **Semicolon-join.** Consecutive lines at the same indent level that are
   not control-flow openers (`if`, `for`, `while`, `try`, `except`,
   `else`, `elif`, `finally`, `def`, `return`, `with`, `break`,
   `continue`) get joined with `;`.

### 2. Variable rename table

The rename table is a module-level constant (list of `(old_name, new_name)`
pairs). It maps every template-local variable and function name to a
short replacement. The table is ordered longest-name-first so that
`re.sub` on longer names runs before shorter names, preventing substring
interference.

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

### 4. Indentation reduction

Each line's leading whitespace is measured. Every 4-space indent level is
replaced with 1 space. Lines with indentation that is not a multiple of 4
spaces are left unchanged (this should never happen given the template's
coding discipline, but handling it defensively costs nothing).

## Affected Components

- `dnsdle/stager_minify.py` (NEW): deterministic minifier. Exports
  `minify()`. Contains the variable rename table as a module-level
  constant.
