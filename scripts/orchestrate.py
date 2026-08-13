#!/usr/bin/env python3
"""Run one Lead -> Reviewer gate -> Executor -> Lead assessment round."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from common import (
    ROOT,
    WorkflowError,
    ensure_git_repo,
    git_fingerprint,
    load_config,
    read_json,
    require_same_fingerprint,
    run,
    runtime_dir,
    sha256_json,
    write_json,
)

SENTINEL_RE = re.compile(
    r"<AGENT_GROUP_JSON>\s*(\{.*?\})\s*</AGENT_GROUP_JSON>",
    re.DOTALL,
)


def prompt_and_capture(
    agent: str,
    prompt: str,
    *,
    timeout_ms: int,
    read_lines: int,
) -> tuple[dict[str, Any], str]:
    cmd = [
        "herdr",
        "agent",
        "prompt",
        agent,
        prompt,
        "--wait",
        "--until",
        "idle",
        "--until",
        "done",
        "--until",
        "blocked",
        "--timeout",
        str(timeout_ms),
    ]
    cp = run(cmd, check=False)
    if cp.returncode != 0:
        raise WorkflowError(
            f"Agent prompt failed for {agent}:\n{cp.stdout}\n{cp.stderr}"
        )

    read_cp = run(
        [
            "herdr",
            "agent",
            "read",
            agent,
            "--source",
            "recent-unwrapped",
            "--lines",
            str(read_lines),
        ],
        check=False,
    )
    if read_cp.returncode != 0:
        raise WorkflowError(
            f"Agent read failed for {agent}:\n{read_cp.stdout}\n{read_cp.stderr}"
        )

    raw = read_cp.stdout
    matches = SENTINEL_RE.findall(raw)
    if not matches:
        raise WorkflowError(
            f"Agent {agent} did not return a complete AGENT_GROUP_JSON block."
        )

    # The prompt itself can appear in terminal history. The last sentinel is the
    # agent's most recent structured answer by protocol.
    for candidate in reversed(matches):
        try:
            return json.loads(candidate), raw
        except json.JSONDecodeError:
            continue
    raise WorkflowError(f"Agent {agent} returned malformed sentinel JSON.")


def render_task_md(task: dict[str, Any]) -> str:
    def bullets(values: Any) -> str:
        if not values:
            return "- None\n"
        if not isinstance(values, list):
            values = [values]
        return "".join(f"- {v}\n" for v in values)

    responses = task.get("reviewer_responses", [])
    response_text = bullets(
        [
            f"{r.get('id','?')}: {r.get('disposition','')} — {r.get('response','')}"
            for r in responses
        ]
    )

    return f"""# Draft Task

## Round title

{task.get('round_title', '')}

## Current state

{task.get('current_state', '')}

## Observed problem

{task.get('observed_problem', '')}

## Hypothesis

{task.get('hypothesis', '')}

## Evidence

{bullets(task.get('evidence'))}
## Proposed investigation

{bullets(task.get('proposed_investigation'))}
## Proposed code change

{bullets(task.get('proposed_code_change'))}
## Allowed files

{bullets(task.get('allowed_files'))}
## Forbidden files / areas

{bullets(task.get('forbidden_files'))}
## Required tests

{bullets(task.get('required_tests'))}
## Acceptance criteria

{bullets(task.get('acceptance_criteria'))}
## Rollback / stop conditions

{bullets(task.get('rollback_conditions'))}
## Responses to previous reviewer issues

{response_text}
"""


def render_review_md(review: dict[str, Any]) -> str:
    lines = [
        "# Review",
        "",
        f"**VERDICT: {review.get('verdict', '')}**",
        "",
        review.get("summary", ""),
        "",
        "## Critical issues",
        "",
    ]
    issues = review.get("critical_issues", [])
    if not issues:
        lines.append("- None")
    else:
        for item in issues:
            lines += [
                f"### {item.get('id','?')}",
                "",
                f"**Issue:** {item.get('issue','')}",
                "",
                f"**Why it matters:** {item.get('why_it_matters','')}",
                "",
                f"**Required change:** {item.get('required_change','')}",
                "",
            ]
    lines += ["## Optional suggestions", ""]
    opts = review.get("optional_suggestions", [])
    if opts:
        lines += [f"- {x}" for x in opts]
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def validate_task(task: dict[str, Any]) -> None:
    required = [
        "round_title",
        "current_state",
        "observed_problem",
        "hypothesis",
        "evidence",
        "proposed_investigation",
        "proposed_code_change",
        "allowed_files",
        "forbidden_files",
        "required_tests",
        "acceptance_criteria",
        "rollback_conditions",
        "reviewer_responses",
    ]
    missing = [k for k in required if k not in task]
    if missing:
        raise WorkflowError(f"Lead task is missing fields: {missing}")


def validate_review(review: dict[str, Any]) -> None:
    verdict = review.get("verdict")
    if verdict not in {"APPROVED", "REVISE"}:
        raise WorkflowError(f"Invalid reviewer verdict: {verdict!r}")
    if verdict == "REVISE" and not review.get("critical_issues"):
        raise WorkflowError("REVISE requires at least one critical issue.")


def role_text(role: str) -> str:
    return (ROOT / "roles" / f"{role}.md").read_text(encoding="utf-8")


def workflow_text() -> str:
    return (ROOT / "policies" / "workflow.md").read_text(encoding="utf-8")


def lead_draft_prompt(
    runtime: Path,
    *,
    prior_task: dict[str, Any] | None,
    prior_review: dict[str, Any] | None,
) -> str:
    schema = {
        "round_title": "short title",
        "current_state": "concise factual state",
        "observed_problem": "problem statement",
        "hypothesis": "current hypothesis, with uncertainty",
        "evidence": ["evidence item"],
        "proposed_investigation": ["ordered step"],
        "proposed_code_change": ["conditional/minimal code change"],
        "allowed_files": ["path or narrow pattern"],
        "forbidden_files": ["path/area"],
        "required_tests": ["test/gate"],
        "acceptance_criteria": ["predeclared criterion"],
        "rollback_conditions": ["condition"],
        "reviewer_responses": [
            {
                "id": "R1",
                "disposition": "ACCEPTED or REJECTED",
                "response": "how the issue was addressed, or evidence for rejection",
            }
        ],
    }
    previous = ""
    if prior_task is not None and prior_review is not None:
        previous = (
            "\nThis is a revision cycle.\n"
            f"Previous draft JSON:\n{json.dumps(prior_task, ensure_ascii=False, indent=2)}\n"
            f"Reviewer JSON:\n{json.dumps(prior_review, ensure_ascii=False, indent=2)}\n"
            "Address every critical issue ID explicitly in reviewer_responses.\n"
        )

    return f"""
You are operating under this Lead role policy:

--- LEAD POLICY ---
{role_text('lead')}
--- END LEAD POLICY ---

Workflow policy:

--- WORKFLOW POLICY ---
{workflow_text()}
--- END WORKFLOW POLICY ---

Target project runtime directory: {runtime}

Read:
- {runtime / 'PROJECT_CHARTER.md'}
- {runtime / 'CURRENT_STATE.md'}
- any prior execution/assessment evidence in {runtime}

Inspect the actual repository and relevant source/tests using read-only operations.
Do not edit production files.

{previous}

Return exactly one machine-readable answer using this protocol:

<AGENT_GROUP_JSON>
{json.dumps(schema, ensure_ascii=False, indent=2)}
</AGENT_GROUP_JSON>

Replace every placeholder with actual content. Do not put another JSON object outside
the sentinel block. The orchestrator, not you, writes the task file.
""".strip()


def reviewer_prompt(runtime: Path, task: dict[str, Any]) -> str:
    schema = {
        "verdict": "APPROVED or REVISE",
        "summary": "concise audit conclusion",
        "critical_issues": [
            {
                "id": "R1",
                "issue": "blocking problem",
                "why_it_matters": "technical consequence",
                "required_change": "what Lead must resolve",
            }
        ],
        "optional_suggestions": ["non-blocking suggestion"],
    }
    return f"""
You are operating under this Reviewer role policy:

--- REVIEWER POLICY ---
{role_text('reviewer')}
--- END REVIEWER POLICY ---

Workflow policy:

--- WORKFLOW POLICY ---
{workflow_text()}
--- END WORKFLOW POLICY ---

Target runtime directory: {runtime}

Audit this exact Lead draft:
{json.dumps(task, ensure_ascii=False, indent=2)}

Inspect repository evidence as needed, but remain read-only.

Return exactly one machine-readable answer:

<AGENT_GROUP_JSON>
{json.dumps(schema, ensure_ascii=False, indent=2)}
</AGENT_GROUP_JSON>

If verdict is REVISE, critical_issues must be non-empty and use stable IDs.
If verdict is APPROVED, critical_issues should be empty.
Do not implement the plan.
""".strip()


def executor_prompt(
    runtime: Path,
    approved_task: dict[str, Any],
    task_hash: str,
) -> str:
    schema = {
        "status": "PASS or FAIL or BLOCKED",
        "root_cause": "what was confirmed, if any",
        "files_changed": ["path"],
        "changes": ["change summary"],
        "commands": ["command actually run"],
        "test_results": [
            {
                "name": "test/gate",
                "result": "PASS/FAIL/NOT_RUN",
                "evidence": "key quantitative or textual result",
            }
        ],
        "scope_deviations": ["deviation, or empty"],
        "remaining_issues": ["remaining issue"],
    }
    return f"""
You are operating under this Executor role policy:

--- EXECUTOR POLICY ---
{role_text('executor')}
--- END EXECUTOR POLICY ---

The Reviewer approved and the orchestrator sealed this exact task.

APPROVED TASK SHA256: {task_hash}

APPROVED TASK JSON:
{json.dumps(approved_task, ensure_ascii=False, indent=2)}

Runtime directory: {runtime}

Execute only the approved scope. Do not commit or push. Do not weaken tests.
If a missing design decision is required, stop safely and report BLOCKED.

After implementation/testing, return exactly one machine-readable answer:

<AGENT_GROUP_JSON>
{json.dumps(schema, ensure_ascii=False, indent=2)}
</AGENT_GROUP_JSON>

Report observed evidence faithfully. Your PASS status means the requested execution
completed successfully; it is not the final scientific/algorithmic verdict.
""".strip()


def lead_assessment_prompt(
    runtime: Path,
    approved_task: dict[str, Any],
    executor_report: dict[str, Any],
) -> str:
    schema = {
        "verdict": "PASS or FAIL or INCONCLUSIVE",
        "summary": "independent evidence-based conclusion",
        "evidence": ["key evidence"],
        "regressions": ["regression or empty"],
        "scope_findings": ["scope observation"],
        "next_recommendation": "what should be considered next; not an execution order",
        "updated_state_markdown": "# Current State\\n\\n... concise state for the next round ...",
    }
    return f"""
You are operating under this Lead role policy:

--- LEAD POLICY ---
{role_text('lead')}
--- END LEAD POLICY ---

This is post-execution assessment, not a new implementation round.

Approved task:
{json.dumps(approved_task, ensure_ascii=False, indent=2)}

Executor report:
{json.dumps(executor_report, ensure_ascii=False, indent=2)}

Runtime evidence is in: {runtime}
Inspect the actual repository diff and relevant test/log evidence yourself using
read-only operations. Do not rely solely on Executor's status label.

Return exactly:

<AGENT_GROUP_JSON>
{json.dumps(schema, ensure_ascii=False, indent=2)}
</AGENT_GROUP_JSON>

updated_state_markdown must be a concise self-contained state summary suitable for
the next Lead round.
""".strip()


def write_raw(runtime: Path, name: str, raw: str) -> None:
    (runtime / name).write_text(raw, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one controlled agent-group engineering round."
    )
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    config = load_config(args.config)
    ensure_git_repo(project)
    runtime = runtime_dir(project, config)
    session_path = runtime / "session.json"
    if not session_path.exists():
        raise WorkflowError(
            f"Missing {session_path}. Run init_project.py and launch.py first."
        )

    for required in ("PROJECT_CHARTER.md", "CURRENT_STATE.md"):
        if not (runtime / required).exists():
            raise WorkflowError(
                f"Missing runtime file {required}. Run init_project.py first."
            )

    session = read_json(session_path)
    agents = session["agents"]
    for role in ("lead", "reviewer", "executor"):
        if role not in agents or "name" not in agents[role]:
            raise WorkflowError(f"Session has no live agent mapping for {role}.")

    workflow_cfg = config.get("workflow", {})
    max_reviews = int(workflow_cfg.get("max_review_cycles", 3))
    timeout_ms = int(workflow_cfg.get("prompt_timeout_ms", 300000))
    read_lines = int(workflow_cfg.get("read_lines", 500))
    runtime_name = runtime.name

    round_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    round_dir = runtime / "rounds" / round_id
    round_dir.mkdir(parents=True, exist_ok=False)

    lead_name = agents["lead"]["name"]
    reviewer_name = agents["reviewer"]["name"]
    executor_name = agents["executor"]["name"]

    task: dict[str, Any] | None = None
    review: dict[str, Any] | None = None

    for attempt in range(1, max_reviews + 1):
        before = git_fingerprint(project, runtime_name)
        task, raw = prompt_and_capture(
            lead_name,
            lead_draft_prompt(
                runtime,
                prior_task=task,
                prior_review=review,
            ),
            timeout_ms=timeout_ms,
            read_lines=read_lines,
        )
        write_raw(round_dir, f"lead-{attempt}-raw.txt", raw)
        after = git_fingerprint(project, runtime_name)
        require_same_fingerprint(before, after, role="lead")
        validate_task(task)
        write_json(round_dir / f"DRAFT_TASK.v{attempt}.json", task)
        (round_dir / f"DRAFT_TASK.v{attempt}.md").write_text(
            render_task_md(task), encoding="utf-8"
        )

        before = git_fingerprint(project, runtime_name)
        review, raw = prompt_and_capture(
            reviewer_name,
            reviewer_prompt(runtime, task),
            timeout_ms=timeout_ms,
            read_lines=read_lines,
        )
        write_raw(round_dir, f"review-{attempt}-raw.txt", raw)
        after = git_fingerprint(project, runtime_name)
        require_same_fingerprint(before, after, role="reviewer")
        validate_review(review)
        write_json(round_dir / f"REVIEW.v{attempt}.json", review)
        (round_dir / f"REVIEW.v{attempt}.md").write_text(
            render_review_md(review), encoding="utf-8"
        )

        if review["verdict"] == "APPROVED":
            break
    else:
        escalation = (
            "# Escalation\n\n"
            f"Round `{round_id}` exhausted {max_reviews} review attempts.\n\n"
            "Executor was not called.\n\n"
            "Inspect the final draft and review in this round directory.\n"
        )
        (round_dir / "ESCALATION.md").write_text(escalation, encoding="utf-8")
        print(escalation)
        return 2

    assert task is not None and review is not None
    task_hash = sha256_json(task)
    approval = {
        "status": "APPROVED",
        "round_id": round_id,
        "review_attempt": attempt,
        "task_sha256": task_hash,
        "approved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_json(round_dir / "APPROVED_TASK.json", task)
    (round_dir / "APPROVED_TASK.md").write_text(
        render_task_md(task), encoding="utf-8"
    )
    write_json(round_dir / "APPROVAL.json", approval)

    # Re-read and re-hash the persisted task immediately before execution.
    persisted_task = read_json(round_dir / "APPROVED_TASK.json")
    persisted_hash = sha256_json(persisted_task)
    if persisted_hash != task_hash:
        raise WorkflowError(
            "Approved-task hash mismatch. Executor was not called."
        )

    pre_exec_fingerprint = git_fingerprint(project, runtime_name)
    executor_report, raw = prompt_and_capture(
        executor_name,
        executor_prompt(runtime, persisted_task, task_hash),
        timeout_ms=timeout_ms,
        read_lines=read_lines,
    )
    write_raw(round_dir, "executor-raw.txt", raw)
    write_json(round_dir / "EXECUTOR_REPORT.json", executor_report)

    # Record actual repository evidence regardless of Executor's narrative.
    post_exec_fingerprint = git_fingerprint(project, runtime_name)
    diff_names = run(
        ["git", "diff", "--name-only", "HEAD", "--"],
        cwd=project,
    ).stdout
    diff_stat = run(
        ["git", "diff", "--stat", "HEAD", "--"],
        cwd=project,
    ).stdout
    status = run(
        ["git", "status", "--short"],
        cwd=project,
    ).stdout
    git_evidence = (
        f"pre_executor_fingerprint={pre_exec_fingerprint}\n"
        f"post_executor_fingerprint={post_exec_fingerprint}\n\n"
        "## git status --short\n"
        f"{status}\n"
        "## git diff --name-only HEAD --\n"
        f"{diff_names}\n"
        "## git diff --stat HEAD --\n"
        f"{diff_stat}\n"
    )
    (round_dir / "POST_EXECUTION_GIT.txt").write_text(
        git_evidence, encoding="utf-8"
    )

    before = git_fingerprint(project, runtime_name)
    assessment, raw = prompt_and_capture(
        lead_name,
        lead_assessment_prompt(runtime, persisted_task, executor_report),
        timeout_ms=timeout_ms,
        read_lines=read_lines,
    )
    write_raw(round_dir, "lead-assessment-raw.txt", raw)
    after = git_fingerprint(project, runtime_name)
    require_same_fingerprint(before, after, role="lead")
    if assessment.get("verdict") not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        raise WorkflowError(
            f"Invalid final Lead verdict: {assessment.get('verdict')!r}"
        )
    write_json(round_dir / "LEAD_ASSESSMENT.json", assessment)

    updated_state = assessment.get("updated_state_markdown", "").strip()
    if not updated_state:
        raise WorkflowError("Lead assessment omitted updated_state_markdown.")
    (runtime / "CURRENT_STATE.md").write_text(
        updated_state + "\n", encoding="utf-8"
    )

    summary = {
        "round_id": round_id,
        "review_attempts": attempt,
        "approved_task_sha256": task_hash,
        "executor_status": executor_report.get("status"),
        "lead_verdict": assessment.get("verdict"),
        "round_dir": str(round_dir),
    }
    write_json(round_dir / "ROUND_SUMMARY.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowError as exc:
        raise SystemExit(f"ERROR: {exc}")
