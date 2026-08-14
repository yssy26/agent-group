#!/usr/bin/env python3
"""Run sealed validation commands in Herdr's dedicated non-agent test pane."""
from __future__ import annotations
import re
import shlex
import uuid
from pathlib import Path
from typing import Any
from common import WorkflowError, run, run_json


def _result(payload: dict[str,Any]) -> dict[str,Any]:
    return payload.get("result",payload)


def _slug(text: str) -> str:
    value=re.sub(r"[^A-Za-z0-9._-]+","-",text.strip()).strip("-")
    return value[:48] or "test"


def _split_shell_literal(value: str) -> str:
    # Prevent wait-output from matching the echoed command before completion.
    middle=max(1,len(value)//2)
    left=value[:middle].replace("'","'\"'\"'")
    right=value[middle:].replace("'","'\"'\"'")
    return f"'{left}''{right}'"


def ensure_test_pane_ready(pane_id: str) -> None:
    try:
        payload=run_json(["herdr","pane","process-info","--pane",pane_id])
    except Exception as exc:
        raise WorkflowError(f"Could not inspect test pane {pane_id}: {exc}") from exc
    processes=_result(payload).get("process_info",{}).get("foreground_processes",[])
    shells={"bash","sh","zsh","fish"}
    if not processes or not all(p.get("name") in shells for p in processes):
        raise WorkflowError(f"Test pane {pane_id} is not an idle shell; foreground={[p.get('name') for p in processes]}.")


def run_test_plan(*, project: Path, round_dir: Path, pane_id: str, test_plan: list[dict[str,Any]], output_tail_lines: int, stop_on_failure: bool) -> dict[str,Any]:
    if not test_plan:
        return {"overall_status":"NO_TESTS","pane_id":pane_id,"tests":[]}
    ensure_test_pane_ready(pane_id)
    results=[]; stopped=False
    for index,item in enumerate(test_plan,1):
        if stopped:
            results.append({"name":item["name"],"command":item["command"],"result":"NOT_RUN","exit_code":None,"timeout_ms":item["timeout_ms"],"log_file":None,"output_tail":[]})
            continue
        token=uuid.uuid4().hex
        begin=f"__AG_BEGIN_{token}__"; end=f"__AG_END_{token}__"
        log_path=round_dir/f"TEST_{index:02d}_{_slug(item['name'])}.log"
        script=(f"AG_BEG={_split_shell_literal(begin)}; AG_END={_split_shell_literal(end)}; "
                "printf '%s\\n' \"$AG_BEG\"; set +e; "
                f"( {item['command']} ) 2>&1 | tee {shlex.quote(str(log_path))}; "
                "rc=${PIPESTATUS[0]}; printf '%s:%s\\n' \"$AG_END\" \"$rc\"")
        pane_command="bash -c "+shlex.quote(script)
        submit=run(["herdr","pane","run",pane_id,pane_command],check=False)
        if submit.returncode != 0:
            results.append({"name":item["name"],"command":item["command"],"result":"HARNESS_ERROR","exit_code":None,"timeout_ms":item["timeout_ms"],"log_file":str(log_path),"output_tail":[],"harness_error":(submit.stdout+submit.stderr).strip()})
            stopped=stop_on_failure; continue
        wait=run(["herdr","pane","wait-output",pane_id,"--regex",rf"^{re.escape(end)}:[0-9]+$","--source","recent-unwrapped","--lines","200","--timeout",str(item["timeout_ms"])],check=False)
        if wait.returncode != 0:
            run(["herdr","pane","send-keys",pane_id,"ctrl+c"],check=False)
            output=log_path.read_text(encoding="utf-8",errors="replace") if log_path.exists() else ""
            results.append({"name":item["name"],"command":item["command"],"result":"TIMEOUT","exit_code":None,"timeout_ms":item["timeout_ms"],"log_file":str(log_path),"output_tail":output.splitlines()[-output_tail_lines:],"harness_error":(wait.stdout+wait.stderr).strip()})
            stopped=stop_on_failure; continue
        match=re.search(re.escape(end)+r":([0-9]+)",wait.stdout)
        if not match:
            pane_read=run(["herdr","pane","read",pane_id,"--source","recent-unwrapped","--lines","200"],check=False).stdout
            match=re.search(re.escape(end)+r":([0-9]+)",pane_read)
        exit_code=int(match.group(1)) if match else None
        result="PASS" if exit_code==0 else ("FAIL" if exit_code is not None else "HARNESS_ERROR")
        output=log_path.read_text(encoding="utf-8",errors="replace") if log_path.exists() else ""
        results.append({"name":item["name"],"command":item["command"],"result":result,"exit_code":exit_code,"timeout_ms":item["timeout_ms"],"log_file":str(log_path),"output_tail":output.splitlines()[-output_tail_lines:]})
        if result!="PASS" and stop_on_failure: stopped=True
    statuses=[x["result"] for x in results]
    overall="PASS" if statuses and all(x=="PASS" for x in statuses) else ("FAIL" if any(x in {"FAIL","TIMEOUT","HARNESS_ERROR"} for x in statuses) else "INCOMPLETE")
    return {"overall_status":overall,"pane_id":pane_id,"tests":results}
