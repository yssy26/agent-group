# Workflow policy

## State machine

The orchestrator enforces:

```text
LEAD_DRAFT
   |
   v
REVIEW
   | \
   |  \ REVISE
   |   v
   | LEAD_REVISION
   |   |
   +---+
   |
 APPROVED
   |
   v
SEAL_TASK
   |
   v
EXECUTE
   |
   v
LEAD_ASSESSMENT
   |
   v
STOP
```

## Hard rules

1. Executor is never called unless Reviewer returns `APPROVED`.
2. Reviewer approval applies only to the exact sealed task hash.
3. Any change to the approved task invalidates the approval.
4. Review cycles are bounded by `workflow.max_review_cycles`.
5. Exhausting the review limit produces `ESCALATED`; execution is blocked.
6. Lead and Reviewer are read-only with respect to production files.
7. Executor may edit production files but may not publish or destructively rewrite Git history.
8. One invocation of `orchestrate.py` runs one engineering round.
9. The orchestrator stops on malformed structured output rather than guessing intent.
10. The orchestrator never auto-resets unexpected changes.

## Separation of authority

### Lead

Owns technical planning and final evidence interpretation.

### Reviewer

Owns pre-execution plan approval only.

### Executor

Owns implementation and test execution only.

No role may self-approve work outside its authority.

## Structured exchange

Agent responses used for state transitions must contain one JSON payload inside:

```text
<AGENT_GROUP_JSON>
{ ... }
</AGENT_GROUP_JSON>
```

The orchestrator uses the last complete sentinel block in the terminal capture.

Malformed or missing JSON is an execution error.

## Review loop

A Reviewer `REVISE` result must contain at least one critical issue.

Lead's next draft must include explicit responses to prior critical issue IDs.

After the configured maximum number of review attempts, unresolved disagreement is surfaced to the user in `.agent-runtime/ESCALATION.md`.

## Approval sealing

The approved task is serialized as canonical JSON:

- UTF-8;
- sorted keys;
- compact separators.

The SHA-256 digest of those exact bytes is stored in `APPROVAL.json`.

Immediately before execution, the orchestrator recomputes the digest. Mismatch aborts the round.

## Working-tree integrity

Lead and Reviewer turns are wrapped with a Git working-tree fingerprint.

The fingerprint includes:

- tracked/staged diff against `HEAD`;
- untracked, non-ignored files outside `.agent-runtime/`.

If either read-only role changes the target working tree, orchestration aborts and preserves the evidence.

## Evidence after execution

Executor's report is not accepted as the final conclusion.

Lead must independently inspect:

- actual Git diff;
- changed filenames;
- test/build evidence;
- unexpected scope changes;
- prior gates that may have regressed.

The final Lead verdict is one of:

- `PASS`;
- `FAIL`;
- `INCONCLUSIVE`.

## Future extension

Multi-round unattended execution should be implemented above this single-round state machine, not by weakening any gate in the single-round implementation.
