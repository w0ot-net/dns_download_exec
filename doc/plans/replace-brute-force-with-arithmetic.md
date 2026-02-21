# Plan: Replace brute-force loops and over-indirected helpers with direct arithmetic

## Summary

Several sizing computations in budget.py and mapping.py use brute-force loops
that build fake DNS label tuples and iterate to find the answer, when the
result can be computed with a single arithmetic expression.  config.py has
dead-code helpers (`_SENTINEL`, `_arg_value`, `_arg_value_default`) and
duplicated overlap-checking raises that can be collapsed.  Together these
changes eliminate 3 functions, 2 iteration loops, and ~42 lines of code with
no behavioral change.

## Problem

1. `budget.py` iterates from 253 down to 0, constructing fake label tuples on
   each iteration, to find the maximum payload character count.  Two helper
   functions (`_payload_labels_for_chars`, `_response_size_estimate`) exist
   solely to support this loop.  The wire-length and response-size formulas
   are linear in character count and solvable in closed form.

2. `mapping.py:_max_token_len_for_file` iterates from `max_candidate` down to
   0, building fake label tuples to check wire length.  The wire length of a
   QNAME with a token label is a simple sum.

3. `config.py:_arg_value` uses a sentinel-object pattern to detect missing
   argparse attributes, but argparse always populates all defined attributes,
   making the sentinel branch unreachable.  `_arg_value_default` is a trivial
   alias for `getattr`.

4. `config.py:_normalize_domains` has two separate `labels_is_suffix` checks
   per domain pair with near-identical `StartupError` raises that can be one
   combined conditional.

5. `config.py:_normalize_domains` finds the longest domain with an 8-line
   manual loop that can be a one-liner `max()`.

6. `config.py:_normalize_client_out_dir` checks `if not normalized` after
   `os.path.abspath`, which always returns a non-empty string; the check is
   dead code.

7. `budget.py:_validate_query_token_len` builds fake label tuples to compute
   QNAME wire length; the result is a direct arithmetic expression.

## Goal

- Eliminate `_payload_labels_for_chars`, `_response_size_estimate`, and
  `_arg_value_default`.
- Replace both brute-force sizing loops with closed-form arithmetic.
- Collapse redundant overlap-check raises and the longest-domain loop.
- Remove dead code.
- No behavioral, contract, or output changes.  All existing invariants and
  fail-fast behavior preserved.

## Design

### 1. budget.py -- direct payload sizing arithmetic

Delete `_payload_labels_for_chars` and `_response_size_estimate`.

Add a helper that computes wire contribution of `n` chars split into labels
of at most `label_cap`:

```python
def _payload_wire_contribution(char_count, label_cap):
    if char_count <= 0:
        return 0
    return char_count + (char_count + label_cap - 1) // label_cap
```

Add an inverse helper that computes the maximum character count fitting a
wire budget (the max `n` satisfying `n + ceil(n/C) <= L`):

```python
def _max_chars_for_wire_budget(wire_budget, label_cap):
    if wire_budget <= 0:
        return 0
    k = wire_budget // (label_cap + 1)
    remaining = wire_budget - k * (label_cap + 1)
    return k * label_cap + max(remaining - 1, 0)
```

In `compute_max_ciphertext_slice_bytes`, compute the two constraints (wire
length limit and response size limit) as bounds on payload wire contribution,
take the tighter one, and solve for `max_payload_chars` directly.

The QNAME wire length for the question section is also computed directly:

```
qname_wire = 2 + query_token_len + config.file_tag_len + config.longest_domain_wire_len
```

This eliminates:

- `_payload_labels_for_chars` (8 lines)
- `_response_size_estimate` (8 lines)
- the 253-iteration loop (12 lines)

and replaces them with two small helpers and direct math (~12 lines).

### 2. budget.py -- simplify `_validate_query_token_len`

Replace the fake-label construction + `dns_name_wire_length` call with the
same direct QNAME wire formula:

```python
qname_wire = 2 + query_token_len + config.file_tag_len + config.longest_domain_wire_len
if qname_wire > MAX_DNS_NAME_WIRE_LENGTH:
```

### 3. mapping.py -- direct `_max_token_len_for_file`

Replace the reverse iteration with:

```python
def _max_token_len_for_file(config, file_tag):
    budget = MAX_DNS_NAME_WIRE_LENGTH - 2 - len(file_tag) - config.longest_domain_wire_len
    return min(max(budget, 0), config.dns_max_label_len, DIGEST_TEXT_CAPACITY)
```

Remove the import of `dns_name_wire_length` from mapping.py (no longer
needed after this change; verify no other callsite in this file).

### 4. config.py -- drop `_SENTINEL`, `_arg_value`, `_arg_value_default`

Delete `_SENTINEL`, `_arg_value`, and `_arg_value_default`.  Replace all
call sites with direct `getattr(parsed_args, "field_name")`.  If argparse
ever fails to populate an attribute, Python raises `AttributeError` --
which is the correct invariant-violation behavior per CLAUDE.md.

### 5. config.py -- collapse domain overlap checks

Merge the two `labels_is_suffix` conditionals per pair into one `or`
expression with a single `StartupError` raise.  Report the pair; order of
the two domain names in the context dict is immaterial.

### 6. config.py -- simplify longest-domain finding

Replace the 8-line manual loop with:

```python
longest_idx = max(range(len(domains)),
                  key=lambda i: dns_name_wire_length(domain_labels_by_domain[i]))
longest_domain = domains[longest_idx]
longest_domain_labels = domain_labels_by_domain[longest_idx]
longest_domain_wire_len = dns_name_wire_length(longest_domain_labels)
```

### 7. config.py -- remove dead `_normalize_client_out_dir` check

Delete the `if not normalized: raise ...` block after `os.path.abspath`.

## Affected Components

- `dnsdle/budget.py`: delete `_payload_labels_for_chars` and
  `_response_size_estimate`; add `_payload_wire_contribution` and
  `_max_chars_for_wire_budget`; rewrite `compute_max_ciphertext_slice_bytes`
  body to use direct arithmetic; simplify `_validate_query_token_len`.
- `dnsdle/mapping.py`: rewrite `_max_token_len_for_file` as direct
  arithmetic; remove `dns_name_wire_length` import if no longer used.
- `dnsdle/config.py`: delete `_SENTINEL`, `_arg_value`,
  `_arg_value_default`; replace call sites with `getattr`; collapse overlap
  checks; simplify longest-domain finding; remove dead normalization check.
