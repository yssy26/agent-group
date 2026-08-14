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
TEST_PANE
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
3. The approved task includes its exact `test_plan`; changing it invalidates approval.
4. Review cycles are bounded by `workflow.max_review_cycles`.
5. Exhausting the review limit produces `ESCALATED`; execution is blocked.
6. Lead and Reviewer are read-only with respect to production files.
7. Executor may edit production files but may not publish or destructively rewrite Git history.
8. One invocation of `orchestrate.py` runs one engineering round.
9. Structured output is validated strictly; malformed JSON may receive only bounded JSON-only repair, never semantic guessing.
10. The orchestrator never auto-resets unexpected changes.
11. After Executor returns, the orchestrator runs the sealed authoritative test plan in the dedicated Herdr test pane unless Executor reported `BLOCKED`.
12. Test-pane validation commands must be non-interactive and must not publish, rewrite Git history, escalate privileges, or perform destructive system operations.

## Separation of authority

### Lead

Owns technical planning, the exact proposed test plan, and final evidence interpretation.

### Reviewer

Owns pre-execution approval of the exact task **including the test plan**.

### Executor

Owns implementation of the sealed production-code scope. Executor may run cheap developer checks, but cannot self-certify the authoritative validation result.

### Herdr test pane

Owns no technical judgment. It is a deterministic execution surface for the sealed validation commands and records exit codes plus full logs without consuming LLM tokens.

No role may self-approve work outside its authority.

## Structured exchange

Herdr `agent prompt --wait` and `agent read --source recent-unwrapped` remain the primary agent communication channel.

For state transitions, each agent must return exactly one compact JSON payload inside:

```text
<AGENT_GROUP_JSON>
{"response_nonce":"...","...":"..."}
</AGENT_GROUP_JSON>
```

The transport protocol requires:

- RFC 8259 JSON;
- one minified JSON object;
- a per-turn `response_nonce` to prevent stale terminal history from satisfying a new turn;
- arrays of short strings instead of paragraph-length strings;
- no literal newlines inside JSON strings;
- no Markdown fences, comments, or prose around the sentinel block.

The descriptive schemas shown in prompts use `<AGENT_GROUP_SCHEMA>` rather than the response sentinel, so prompt history is not mistaken for an agent response.

If the first response is malformed but the Herdr transport completed normally, the orchestrator may request a bounded JSON-only repair. The repair instruction explicitly forbids re-analysis or changing technical conclusions. If repair still fails, the round stops with parser diagnostics instead of guessing intent.

## Review loop

A Reviewer `REVISE` result must contain at least one critical issue.

Lead's next draft must include explicit responses to prior critical issue IDs.

After the configured maximum number of review attempts, unresolved disagreement is surfaced to the user in the round directory and Executor is not called.

## Approval sealing

The approved task is serialized as canonical JSON:

- UTF-8;
- sorted keys;
- compact separators.

The SHA-256 digest of those exact bytes is stored in `APPROVAL.json`.

Immediately before execution, the orchestrator recomputes the digest. Mismatch aborts the round.

Because `test_plan` is inside the approved task, the exact test names, commands, timeouts, acceptance criteria, and scope are covered by the same approval hash.

## Dedicated test pane

`launch.py` creates a normal shell pane and stores its ID as `test_pane_id` in `.agent-runtime/session.json`.

After Executor returns, `orchestrate.py`:

1. verifies that the test pane is an idle shell;
2. runs each sealed `test_plan` command with `herdr pane run`;
3. waits for a unique completion marker with `herdr pane wait-output`;
4. captures the command exit code;
5. tees the full command output into the current round directory;
6. writes `TEST_PANE_REPORT.json` with PASS/FAIL/TIMEOUT/HARNESS_ERROR evidence;
7. optionally stops subsequent tests after the first failed authoritative test.

The test pane is intentionally not an LLM agent. Compilation, OpenFOAM solver runs, finite-difference probes, Python post-processing, and regression gates should run there whenever they can be expressed as deterministic commands.

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
- authoritative test-pane report and logs;
- unexpected scope changes;
- prior gates that may have regressed.

The final Lead verdict is one of:

- `PASS`;
- `FAIL`;
- `INCONCLUSIVE`.

The final Lead response returns a structured `updated_state`; the orchestrator renders that structure into `.agent-runtime/CURRENT_STATE.md` for the next round, avoiding fragile multiline JSON strings.

## Future extension

Multi-round unattended execution should be implemented above this single-round state machine, not by weakening any gate in the single-round implementation.
