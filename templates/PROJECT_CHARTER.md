# Project Charter

## Final objective

Describe the final technical or scientific objective of this repository.

## Current development stage

Describe the current stage and what is explicitly out of scope.

## Non-negotiable invariants

List principles that must remain true, for example:

- mathematical identities or conservation requirements;
- discrete transpose consistency;
- serial/parallel consistency;
- unit conventions;
- benchmark behavior;
- API compatibility.

## Validation philosophy

Define what evidence is required before a change can be considered correct.

Examples:

- finite-difference versus analytical gradient closure;
- regression tests;
- deterministic repeatability;
- independent oracle comparisons;
- physical sanity checks.

## Forbidden shortcuts

Examples:

- loosening gates to produce PASS;
- deleting a failing test;
- changing both implementation and oracle in one step without independent evidence;
- accepting a large refactor when a diagnostic probe could isolate the root cause.

## Repository-specific build/test commands

Add canonical commands and important environment setup here.

## Human constraints

Document any user-owned files, dirty worktree constraints, branches that must not be touched, compute limits, or publication requirements.
