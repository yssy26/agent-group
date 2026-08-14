#!/usr/bin/env python3
"""Role prompts and human-readable renderers for one engineering round."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from common import ROOT


def _bullets(values: Any) -> str:
    if not values: return "- None\n"
    if not isinstance(values,list): values=[values]
    return "".join(f"- {v}\n" for v in values)


def role_text(role: str) -> str:
    return (ROOT/"roles"/f"{role}.md").read_text(encoding="utf-8")


def workflow_text() -> str:
    return (ROOT/"policies"/"workflow.md").read_text(encoding="utf-8")


def lead_draft_prompt(runtime: Path, *, prior_task: dict[str,Any]|None, prior_review: dict[str,Any]|None) -> str:
    schema={
        "round_title":"short title",
        "current_state":["short factual statement"],
        "observed_problem":["short problem statement"],
        "hypothesis":["short hypothesis with uncertainty"],
        "evidence":["exact evidence item"],
        "proposed_investigation":["ordered step"],
        "proposed_code_change":["minimal or conditional change"],
        "allowed_files":["exact path or narrow pattern"],
        "forbidden_files":["path or area"],
        "test_plan":[{"name":"short test name","command":"one-line shell command","timeout_ms":120000}],
        "acceptance_criteria":["predeclared auditable criterion"],
        "rollback_conditions":["stop or rollback condition"],
        "reviewer_responses":[],
    }
    previous=""
    if prior_task is not None and prior_review is not None:
        previous=("\nThis is a revision cycle.\n"
                  f"Previous draft JSON:\n{json.dumps(prior_task,ensure_ascii=False,separators=(',',':'))}\n"
                  f"Reviewer JSON:\n{json.dumps(prior_review,ensure_ascii=False,separators=(',',':'))}\n"
                  "Address every critical issue ID explicitly in reviewer_responses.\n")
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
Read PROJECT_CHARTER.md, CURRENT_STATE.md, prior round evidence, and relevant code/tests.
Inspect the actual repository using read-only operations. Do not edit production files.
The test_plan becomes authoritative after Reviewer approval. Its commands are run later
by the orchestrator in Herdr's dedicated test pane. Use exact, non-interactive,
validation-only commands. Do not include destructive Git operations, publishing,
sudo, or cleanup commands in test_plan.
{previous}
Return data matching this descriptive schema:
<AGENT_GROUP_SCHEMA>
{json.dumps(schema,ensure_ascii=False,indent=2)}
</AGENT_GROUP_SCHEMA>
Replace placeholders with actual content. First-draft reviewer_responses must be [].
On revision, each response has id, disposition (ACCEPTED/REJECTED), and response as
an array of short strings. The orchestrator writes task artifacts; you remain read-only.
""".strip()


def reviewer_prompt(runtime: Path, task: dict[str,Any]) -> str:
    schema={"verdict":"APPROVED or REVISE","summary":["short audit conclusion"],"critical_issues":[{"id":"R1","issue":["blocking problem"],"why_it_matters":["technical consequence"],"required_change":["what Lead must resolve"]}],"optional_suggestions":["non-blocking suggestion"]}
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
{json.dumps(task,ensure_ascii=False,separators=(',',':'))}
Inspect repository evidence as needed, but remain read-only. Audit test_plan as part of
the hard gate: commands must be relevant, non-interactive, safe, and sufficient for
the acceptance criteria.
Return data matching this schema:
<AGENT_GROUP_SCHEMA>
{json.dumps(schema,ensure_ascii=False,indent=2)}
</AGENT_GROUP_SCHEMA>
REVISE requires non-empty critical_issues with stable IDs. APPROVED requires none.
Do not implement the plan.
""".strip()


def executor_prompt(runtime: Path, approved_task: dict[str,Any], task_hash: str) -> str:
    schema={"status":"PASS or FAIL or BLOCKED","root_cause":["what was confirmed"],"files_changed":["path"],"changes":["change summary"],"commands":["command actually run"],"developer_checks":[{"name":"quick check","result":"PASS or FAIL or NOT_RUN","evidence":["short evidence"]}],"scope_deviations":["deviation, or empty"],"remaining_issues":["remaining issue"]}
    return f"""
You are operating under this Executor role policy:
--- EXECUTOR POLICY ---
{role_text('executor')}
--- END EXECUTOR POLICY ---
The Reviewer approved and the orchestrator sealed this exact task.
APPROVED TASK SHA256: {task_hash}
APPROVED TASK JSON:
{json.dumps(approved_task,ensure_ascii=False,separators=(',',':'))}
Runtime directory: {runtime}
Execute only the approved implementation scope. Do not commit/push or weaken tests.
If a missing design decision is required, stop and report BLOCKED. You may run cheap
developer checks, but do not spend time running the authoritative test_plan solely for
reporting: after you return, the orchestrator runs that sealed test_plan independently
in Herdr's dedicated test pane and records exit codes and full logs.
Return data matching this schema:
<AGENT_GROUP_SCHEMA>
{json.dumps(schema,ensure_ascii=False,indent=2)}
</AGENT_GROUP_SCHEMA>
Your PASS means implementation completed; it is not the authoritative test verdict.
""".strip()


def lead_assessment_prompt(runtime: Path, approved_task: dict[str,Any], executor_report: dict[str,Any], test_report: dict[str,Any], git_evidence_path: Path) -> str:
    schema={"verdict":"PASS or FAIL or INCONCLUSIVE","summary":["short independent conclusion"],"evidence":["key evidence"],"regressions":["regression or empty"],"scope_findings":["scope observation"],"next_recommendation":["next recommendation"],"updated_state":{"headline":"short state headline","facts":["confirmed fact"],"remaining_issues":["remaining issue"],"next_focus":["next-round focus"]}}
    return f"""
You are operating under this Lead role policy:
--- LEAD POLICY ---
{role_text('lead')}
--- END LEAD POLICY ---
This is post-execution assessment, not a new implementation round.
Approved task: {json.dumps(approved_task,ensure_ascii=False,separators=(',',':'))}
Executor report: {json.dumps(executor_report,ensure_ascii=False,separators=(',',':'))}
Authoritative Herdr test-pane report: {json.dumps(test_report,ensure_ascii=False,separators=(',',':'))}
Git evidence file: {git_evidence_path}
Runtime evidence directory: {runtime}
Inspect actual diff and relevant test logs using read-only operations. Do not trust the
Executor status label by itself. Interpret whether test-pane evidence satisfies the
approved acceptance criteria.
Return data matching this schema:
<AGENT_GROUP_SCHEMA>
{json.dumps(schema,ensure_ascii=False,indent=2)}
</AGENT_GROUP_SCHEMA>
""".strip()


def render_task_md(task: dict[str,Any]) -> str:
    responses=[]
    for r in task.get("reviewer_responses",[]):
        responses.append(f"{r.get('id','?')}: {r.get('disposition','')} — {'; '.join(r.get('response',[]))}")
    tests=[f"{x.get('name','test')}: `{x.get('command','')}` (timeout {x.get('timeout_ms','?')} ms)" for x in task.get("test_plan",[])]
    return f"""# Draft Task

## Round title

{task.get('round_title','')}

## Current state

{_bullets(task.get('current_state'))}
## Observed problem

{_bullets(task.get('observed_problem'))}
## Hypothesis

{_bullets(task.get('hypothesis'))}
## Evidence

{_bullets(task.get('evidence'))}
## Proposed investigation

{_bullets(task.get('proposed_investigation'))}
## Proposed code change

{_bullets(task.get('proposed_code_change'))}
## Allowed files

{_bullets(task.get('allowed_files'))}
## Forbidden files / areas

{_bullets(task.get('forbidden_files'))}
## Authoritative test plan (Herdr test pane)

{_bullets(tests)}
## Acceptance criteria

{_bullets(task.get('acceptance_criteria'))}
## Rollback / stop conditions

{_bullets(task.get('rollback_conditions'))}
## Responses to previous reviewer issues

{_bullets(responses)}
"""


def render_review_md(review: dict[str,Any]) -> str:
    lines=["# Review","",f"**VERDICT: {review.get('verdict','')}**","","## Summary",""]
    lines += [f"- {x}" for x in review.get("summary",[])] or ["- None"]
    lines += ["","## Critical issues",""]
    if not review.get("critical_issues"): lines.append("- None")
    for item in review.get("critical_issues",[]):
        lines += [f"### {item.get('id','?')}","","**Issue:**",*_bullets(item.get("issue")).rstrip().splitlines(),"","**Why it matters:**",*_bullets(item.get("why_it_matters")).rstrip().splitlines(),"","**Required change:**",*_bullets(item.get("required_change")).rstrip().splitlines(),""]
    lines += ["## Optional suggestions",""]
    lines += [f"- {x}" for x in review.get("optional_suggestions",[])] or ["- None"]
    return "\n".join(lines)+"\n"


def render_current_state(state: dict[str,Any]) -> str:
    return ("# Current State\n\n"+f"## Headline\n\n{state.get('headline','')}\n\n"+"## Confirmed facts\n\n"+_bullets(state.get("facts"))+"\n## Remaining issues\n\n"+_bullets(state.get("remaining_issues"))+"\n## Next focus\n\n"+_bullets(state.get("next_focus")))
