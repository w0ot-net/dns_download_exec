- Git workflow: always commit + push after code/doc changes; never `git add .`
  or `git add -A`; stage explicit paths; commit only touched files; ignore
  unrelated changes.
- Compatibility: python 2.7/3.x; ASCII-only code/scripts (Unicode allowed in
  .md); Windows + Linux support; standard library only
- Libraries/mentions: never mention claude/anthropic or use emojis.
- Breaking changes: prefer clean breaks over compatibility shims; update all
  call sites in the same change.
- Invariants: always prefer invariants to fallbacks; be certain about behavior
  and fail fast when expectations are violated.
- Reviews: answer your own questions when possible; otherwise propose best
  options grounded in facts; ignore tests unless explicitly asked; ignore
  doc/completed_plans and doc/abandoned_plans.
- Plans: drafting a <plan>.md must list affected components; evaluating a plan
  requires full code review of all affected components; executing a plan adds
  execution notes and moves it to doc/completed_plans with YYYYMMDD_ prefix;
  do not modify code under ./tests
- don't write or modify test unless you are asked to do so
- Coding: minimize code and complexity while maximizing performance,
  readability, logging, and correctness; optimize for the least code and
  complexity with the highest performance while maintaining readability,
  logging, and correctness.
- Avoid over-preserving API compatibility when a breaking change would be
  cleaner/better long term; update call sites instead.
