#!/usr/bin/env python3
"""Structured-response transport controlled by Herdr prompt/wait/read.

Herdr remains the control plane: it launches agents, delivers prompts, waits for
lifecycle completion, and captures terminal output for audit. Structured payloads
use a per-turn JSON file under the target project's ignored runtime directory so
full-screen TUI line wrapping cannot corrupt JSON strings.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from common import WorkflowError, run

READY_RE = re.compile(r"<AG_READY:([0-9a-f]{16})>")


def structured_delivery_protocol(nonce: str, response_file: Path) -> str:
    return f"""
STRICT STRUCTURED DELIVERY PROTOCOL
The orchestrator requires one JSON payload for this turn.
Do NOT print that JSON payload in the terminal.
Write the complete UTF-8 JSON object to this exact response file:
{response_file}

Hard requirements for the response file:
- RFC 8259 JSON object only; no Markdown fences or comments.
- Add top-level field \"response_nonce\": \"{nonce}\" exactly.
- Preserve exact paths, commands, identifiers, and quantitative evidence.
- Use the schema requested in the task prompt; do not add unrelated fields.
- Finish writing the whole file before signaling completion.
- This response file is inside .agent-runtime and is workflow metadata, not a
  production-file edit. Lead/Reviewer may write only this orchestrator-specified
  response file and must remain read-only everywhere else.

After the file is completely written, your final terminal response must be exactly:
<AG_READY:{nonce}>
Do not print the JSON in the terminal and do not add prose after the ACK.
""".strip()


def repair_prompt(nonce: str, response_file: Path, error: str) -> str:
    return f"""
Your immediately previous technical answer is already semantically complete.
Do NOT re-analyze the repository and do NOT change any technical conclusion.
The orchestrator could not load your structured response file.
Diagnostic: {error}
Rewrite the SAME structured information as valid JSON to:
{response_file}
Include response_nonce={nonce!r} exactly.
After the file is fully written, reply exactly <AG_READY:{nonce}>.
Do not print the JSON itself in the terminal.
""".strip()


def _read_agent(agent: str, read_lines: int) -> str:
    cp = run(
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
    if cp.returncode != 0:
        raise WorkflowError(
            f"Agent read failed for {agent}:\nstdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
        )
    return cp.stdout


def load_structured_file(
    response_file: Path, nonce: str
) -> tuple[dict[str, Any] | None, str]:
    if not response_file.exists():
        return None, f"response file was not created: {response_file}"
    try:
        raw = response_file.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read response file {response_file}: {exc}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        start = max(0, exc.pos - 160)
        end = min(len(raw), exc.pos + 160)
        context = raw[start:end].replace("\n", "\\n")
        return (
            None,
            f"JSONDecodeError line={exc.lineno} column={exc.colno}: "
            f"{exc.msg}; context={context!r}",
        )
    if not isinstance(value, dict):
        return None, "structured response file is not a JSON object"
    if value.get("response_nonce") != nonce:
        return None, "response_nonce is missing or does not match this turn"
    value = dict(value)
    value.pop("response_nonce", None)
    return value, ""


def has_ready_ack(raw: str, nonce: str) -> bool:
    return any(match == nonce for match in READY_RE.findall(raw))


def _prompt_once(
    agent: str,
    prompt: str,
    *,
    timeout_ms: int,
    read_lines: int,
) -> tuple[str, str]:
    cp = run(
        [
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
        ],
        check=False,
    )
    raw = _read_agent(agent, read_lines)
    transport = ""
    if cp.returncode != 0:
        transport = (
            f"Herdr prompt returned {cp.returncode}; "
            f"stdout={cp.stdout.strip()!r}; stderr={cp.stderr.strip()!r}"
        )
    return raw, transport


def prompt_and_capture(
    agent: str,
    prompt: str,
    *,
    response_file: Path,
    timeout_ms: int,
    read_lines: int,
    repair_attempts: int,
) -> tuple[dict[str, Any], str]:
    """Prompt one agent through Herdr and load its nonce-bound response file.

    Terminal text remains audit/control evidence only. A valid nonce-bound response
    file is authoritative. This intentionally avoids reconstructing JSON from TUI
    output, which can contain renderer-inserted hard line breaks.
    """
    nonce = uuid.uuid4().hex[:16]
    response_file.parent.mkdir(parents=True, exist_ok=True)
    response_file.unlink(missing_ok=True)

    raw_attempts: list[str] = []
    prompt_text = prompt.rstrip() + "\n\n" + structured_delivery_protocol(
        nonce, response_file
    )
    raw, transport = _prompt_once(
        agent,
        prompt_text,
        timeout_ms=timeout_ms,
        read_lines=read_lines,
    )
    raw_attempts.append(raw)
    value, error = load_structured_file(response_file, nonce)
    if value is not None:
        return value, raw

    if transport:
        raise WorkflowError(
            f"Agent {agent} transport failed and no valid structured response file "
            f"was available. {transport}. Structured diagnostic: {error}"
        )

    for _ in range(repair_attempts):
        response_file.unlink(missing_ok=True)
        raw, transport = _prompt_once(
            agent,
            repair_prompt(nonce, response_file, error),
            timeout_ms=timeout_ms,
            read_lines=read_lines,
        )
        raw_attempts.append(raw)
        value, error = load_structured_file(response_file, nonce)
        if value is not None:
            combined = "\n\n".join(
                f"===== STRUCTURED ATTEMPT {i + 1} =====\n{x}"
                for i, x in enumerate(raw_attempts)
            )
            return value, combined
        if transport:
            raise WorkflowError(
                f"Agent {agent} structured repair transport failed. {transport}. "
                f"Structured diagnostic: {error}"
            )

    ack_note = (
        "matching terminal ACK observed"
        if has_ready_ack(raw_attempts[-1], nonce)
        else "matching terminal ACK not observed"
    )
    raise WorkflowError(
        f"Agent {agent} did not produce a valid structured response file after "
        f"{1 + repair_attempts} attempt(s); {ack_note}. Last diagnostic: {error}"
    )


def require_string_list(obj: dict[str, Any], field: str) -> None:
    value = obj.get(field)
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise WorkflowError(f"Field {field!r} must be an array of strings.")


FORBIDDEN_TEST_COMMAND_PATTERNS = (
    r"\bsudo\b",
    r"\bgit\s+(commit|push|merge|rebase|cherry-pick|clean)\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\b(?:shutdown|reboot|poweroff|mkfs(?:\.[A-Za-z0-9_-]+)?)\b",
    r"\brm\s+-rf\s+/(?:\s|$)",
)


def validate_task(task: dict[str, Any]) -> None:
    required = (
        "round_title",
        "current_state",
        "observed_problem",
        "hypothesis",
        "evidence",
        "proposed_investigation",
        "proposed_code_change",
        "allowed_files",
        "forbidden_files",
        "test_plan",
        "acceptance_criteria",
        "rollback_conditions",
        "reviewer_responses",
    )
    missing = [k for k in required if k not in task]
    if missing:
        raise WorkflowError(f"Lead task is missing fields: {missing}")
    if not isinstance(task["round_title"], str) or not task["round_title"].strip():
        raise WorkflowError("Lead round_title must be a non-empty string.")
    for field in (
        "current_state",
        "observed_problem",
        "hypothesis",
        "evidence",
        "proposed_investigation",
        "proposed_code_change",
        "allowed_files",
        "forbidden_files",
        "acceptance_criteria",
        "rollback_conditions",
    ):
        require_string_list(task, field)
    if not isinstance(task["test_plan"], list):
        raise WorkflowError("Lead test_plan must be an array.")
    for i, item in enumerate(task["test_plan"], 1):
        if not isinstance(item, dict):
            raise WorkflowError(f"test_plan[{i}] must be an object.")
        name = item.get("name")
        command = item.get("command")
        timeout = item.get("timeout_ms")
        if not isinstance(name, str) or not name.strip():
            raise WorkflowError(f"test_plan[{i}].name must be non-empty.")
        if not isinstance(command, str) or not command.strip() or "\n" in command:
            raise WorkflowError(
                f"test_plan[{i}].command must be a non-empty one-line shell command."
            )
        if not isinstance(timeout, int) or not 1000 <= timeout <= 86_400_000:
            raise WorkflowError(
                f"test_plan[{i}].timeout_ms must be 1000..86400000."
            )
        for pattern in FORBIDDEN_TEST_COMMAND_PATTERNS:
            if re.search(pattern, command, flags=re.IGNORECASE):
                raise WorkflowError(
                    f"test_plan[{i}].command contains forbidden validation operation "
                    f"matching {pattern!r}."
                )
    responses = task["reviewer_responses"]
    if not isinstance(responses, list):
        raise WorkflowError("reviewer_responses must be an array.")
    for i, item in enumerate(responses, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise WorkflowError(f"reviewer_responses[{i}] is malformed.")
        if item.get("disposition") not in {"ACCEPTED", "REJECTED"}:
            raise WorkflowError(
                f"reviewer_responses[{i}].disposition must be ACCEPTED or REJECTED."
            )
        if not isinstance(item.get("response"), list) or any(
            not isinstance(x, str) for x in item.get("response", [])
        ):
            raise WorkflowError(
                f"reviewer_responses[{i}].response must be an array of strings."
            )


def validate_review(review: dict[str, Any]) -> None:
    verdict = review.get("verdict")
    if verdict not in {"APPROVED", "REVISE"}:
        raise WorkflowError(f"Invalid reviewer verdict: {verdict!r}")
    require_string_list(review, "summary")
    require_string_list(review, "optional_suggestions")
    issues = review.get("critical_issues")
    if not isinstance(issues, list):
        raise WorkflowError("critical_issues must be an array.")
    if verdict == "REVISE" and not issues:
        raise WorkflowError("REVISE requires at least one critical issue.")
    if verdict == "APPROVED" and issues:
        raise WorkflowError("APPROVED requires critical_issues to be empty.")
    for i, item in enumerate(issues, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise WorkflowError(f"critical_issues[{i}] is malformed.")
        for field in ("issue", "why_it_matters", "required_change"):
            value = item.get(field)
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                raise WorkflowError(
                    f"critical_issues[{i}].{field} must be an array of strings."
                )


def validate_executor_report(report: dict[str, Any]) -> None:
    if report.get("status") not in {"PASS", "FAIL", "BLOCKED"}:
        raise WorkflowError(f"Invalid Executor status: {report.get('status')!r}")
    for field in (
        "root_cause",
        "files_changed",
        "changes",
        "commands",
        "scope_deviations",
        "remaining_issues",
    ):
        require_string_list(report, field)
    if not isinstance(report.get("developer_checks"), list):
        raise WorkflowError("developer_checks must be an array.")


def validate_assessment(value: dict[str, Any]) -> None:
    if value.get("verdict") not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        raise WorkflowError(f"Invalid final Lead verdict: {value.get('verdict')!r}")
    for field in (
        "summary",
        "evidence",
        "regressions",
        "scope_findings",
        "next_recommendation",
    ):
        require_string_list(value, field)
    state = value.get("updated_state")
    if not isinstance(state, dict) or not isinstance(state.get("headline"), str):
        raise WorkflowError("Lead assessment updated_state is malformed.")
    for field in ("facts", "remaining_issues", "next_focus"):
        if not isinstance(state.get(field), list) or any(
            not isinstance(x, str) for x in state.get(field, [])
        ):
            raise WorkflowError(
                f"updated_state.{field} must be an array of strings."
            )
