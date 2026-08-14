#!/usr/bin/env python3
"""Run one Lead -> Reviewer -> Executor -> test pane -> Lead round."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from common import (
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
from round_prompts import (
    executor_prompt,
    lead_assessment_prompt,
    lead_draft_prompt,
    render_current_state,
    render_review_md,
    render_task_md,
    reviewer_prompt,
)
from structured_protocol import (
    prompt_and_capture,
    validate_assessment,
    validate_executor_report,
    validate_review,
    validate_task,
)
from test_pane import run_test_plan


def write_raw(path: Path, raw: str) -> None:
    path.write_text(raw, encoding="utf-8")


def collect_git_evidence(
    project: Path,
    *,
    pre_executor_fingerprint: str,
    post_executor_fingerprint: str,
    post_test_fingerprint: str,
) -> str:
    diff_names = run(
        ["git", "diff", "--name-only", "HEAD", "--"], cwd=project
    ).stdout
    diff_stat = run(
        ["git", "diff", "--stat", "HEAD", "--"], cwd=project
    ).stdout
    status = run(["git", "status", "--short"], cwd=project).stdout
    return (
        f"pre_executor_fingerprint={pre_executor_fingerprint}\n"
        f"post_executor_fingerprint={post_executor_fingerprint}\n"
        f"post_test_fingerprint={post_test_fingerprint}\n\n"
        "## git status --short\n"
        + status
        + "\n"
        "## git diff --name-only HEAD --\n"
        + diff_names
        + "\n"
        "## git diff --stat HEAD --\n"
        + diff_stat
        + "\n"
    )


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
    test_pane_id = session.get("test_pane_id")
    if not isinstance(test_pane_id, str) or not test_pane_id:
        raise WorkflowError(
            "Session has no dedicated test_pane_id. Re-run launch.py."
        )

    wf = config.get("workflow", {})
    max_reviews = int(wf.get("max_review_cycles", 3))
    timeout_ms = int(wf.get("prompt_timeout_ms", 300000))
    read_lines = int(wf.get("read_lines", 500))
    repairs = int(wf.get("structured_repair_attempts", 1))
    test_tail = int(wf.get("test_output_tail_lines", 40))
    stop_on_failure = bool(wf.get("test_stop_on_failure", True))
    runtime_name = runtime.name

    round_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    round_dir = runtime / "rounds" / round_id
    round_dir.mkdir(parents=True, exist_ok=False)
    transport_dir = round_dir / "transport"
    transport_dir.mkdir(parents=True, exist_ok=False)

    lead = agents["lead"]["name"]
    reviewer = agents["reviewer"]["name"]
    executor = agents["executor"]["name"]
    task: dict[str, Any] | None = None
    review: dict[str, Any] | None = None

    for attempt in range(1, max_reviews + 1):
        before = git_fingerprint(project, runtime_name)
        task, raw = prompt_and_capture(
            lead,
            lead_draft_prompt(
                runtime,
                prior_task=task,
                prior_review=review,
            ),
            response_file=transport_dir / f"lead-draft-v{attempt}.json",
            timeout_ms=timeout_ms,
            read_lines=read_lines,
            repair_attempts=repairs,
        )
        write_raw(round_dir / f"lead-{attempt}-raw.txt", raw)
        require_same_fingerprint(
            before,
            git_fingerprint(project, runtime_name),
            role="lead",
        )
        validate_task(task)
        write_json(round_dir / f"DRAFT_TASK.v{attempt}.json", task)
        (round_dir / f"DRAFT_TASK.v{attempt}.md").write_text(
            render_task_md(task), encoding="utf-8"
        )

        before = git_fingerprint(project, runtime_name)
        review, raw = prompt_and_capture(
            reviewer,
            reviewer_prompt(runtime, task),
            response_file=transport_dir / f"review-v{attempt}.json",
            timeout_ms=timeout_ms,
            read_lines=read_lines,
            repair_attempts=repairs,
        )
        write_raw(round_dir / f"review-{attempt}-raw.txt", raw)
        require_same_fingerprint(
            before,
            git_fingerprint(project, runtime_name),
            role="reviewer",
        )
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
            + f"Round `{round_id}` exhausted {max_reviews} review attempts.\n\n"
            "Executor was not called.\n"
        )
        (round_dir / "ESCALATION.md").write_text(
            escalation, encoding="utf-8"
        )
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
    persisted = read_json(round_dir / "APPROVED_TASK.json")
    if sha256_json(persisted) != task_hash:
        raise WorkflowError(
            "Approved-task hash mismatch. Executor was not called."
        )

    pre_exec = git_fingerprint(project, runtime_name)
    executor_report, raw = prompt_and_capture(
        executor,
        executor_prompt(runtime, persisted, task_hash),
        response_file=transport_dir / "executor-report.json",
        timeout_ms=timeout_ms,
        read_lines=read_lines,
        repair_attempts=repairs,
    )
    write_raw(round_dir / "executor-raw.txt", raw)
    validate_executor_report(executor_report)
    write_json(round_dir / "EXECUTOR_REPORT.json", executor_report)
    post_exec = git_fingerprint(project, runtime_name)

    if executor_report["status"] == "BLOCKED":
        test_report = {
            "overall_status": "SKIPPED_EXECUTOR_BLOCKED",
            "pane_id": test_pane_id,
            "tests": [],
        }
    else:
        test_report = run_test_plan(
            project=project,
            round_dir=round_dir,
            pane_id=test_pane_id,
            test_plan=persisted.get("test_plan", []),
            output_tail_lines=test_tail,
            stop_on_failure=stop_on_failure,
        )
    write_json(round_dir / "TEST_PANE_REPORT.json", test_report)
    post_test = git_fingerprint(project, runtime_name)

    evidence_path = round_dir / "POST_EXECUTION_GIT.txt"
    evidence_path.write_text(
        collect_git_evidence(
            project,
            pre_executor_fingerprint=pre_exec,
            post_executor_fingerprint=post_exec,
            post_test_fingerprint=post_test,
        ),
        encoding="utf-8",
    )

    before = git_fingerprint(project, runtime_name)
    assessment, raw = prompt_and_capture(
        lead,
        lead_assessment_prompt(
            runtime,
            persisted,
            executor_report,
            test_report,
            evidence_path,
        ),
        response_file=transport_dir / "lead-assessment.json",
        timeout_ms=timeout_ms,
        read_lines=read_lines,
        repair_attempts=repairs,
    )
    write_raw(round_dir / "lead-assessment-raw.txt", raw)
    require_same_fingerprint(
        before,
        git_fingerprint(project, runtime_name),
        role="lead",
    )
    validate_assessment(assessment)
    write_json(round_dir / "LEAD_ASSESSMENT.json", assessment)
    (runtime / "CURRENT_STATE.md").write_text(
        render_current_state(assessment["updated_state"]),
        encoding="utf-8",
    )

    summary = {
        "round_id": round_id,
        "review_attempts": attempt,
        "approved_task_sha256": task_hash,
        "executor_status": executor_report.get("status"),
        "test_pane_status": test_report.get("overall_status"),
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
