#!/usr/bin/env python3
from __future__ import annotations
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from common import WorkflowError  # noqa: E402
from structured_protocol import parse_latest_structured, validate_task  # noqa: E402
from test_pane import _split_shell_literal  # noqa: E402

class StructuredProtocolTests(unittest.TestCase):
    def test_parses_compact_json_with_matching_nonce(self):
        raw='noise\n<AGENT_GROUP_JSON>{"value":["ok"],"response_nonce":"n1"}</AGENT_GROUP_JSON>\n'
        value,error=parse_latest_structured(raw,"n1")
        self.assertEqual(error,""); self.assertEqual(value,{"value":["ok"]})
    def test_rejects_literal_newline_inside_string(self):
        raw='<AGENT_GROUP_JSON>{"value":"bad\nline","response_nonce":"n1"}</AGENT_GROUP_JSON>'
        value,error=parse_latest_structured(raw,"n1")
        self.assertIsNone(value); self.assertIn("JSONDecodeError",error)
    def test_rejects_stale_nonce(self):
        raw='<AGENT_GROUP_JSON>{"value":["ok"],"response_nonce":"old"}</AGENT_GROUP_JSON>'
        value,error=parse_latest_structured(raw,"new")
        self.assertIsNone(value); self.assertIn("response_nonce",error)
    def test_test_marker_not_echoed_contiguously(self):
        marker="__AG_END_abcdef123456__"; expr=_split_shell_literal(marker)
        self.assertNotIn(marker,expr)

class TaskValidationTests(unittest.TestCase):
    def valid_task(self):
        return {"round_title":"Add multiply","current_state":["Baseline passes."],"observed_problem":["multiply() is missing."],"hypothesis":["A pure function is sufficient."],"evidence":["Two unit tests pass."],"proposed_investigation":["Inspect calculator.py."],"proposed_code_change":["Add multiply(a,b)."],"allowed_files":["calculator.py","test_calculator.py"],"forbidden_files":[],"test_plan":[{"name":"unit tests","command":"python3 -m unittest -v","timeout_ms":120000}],"acceptance_criteria":["All unit tests pass."],"rollback_conditions":["Stop on unrelated failures."],"reviewer_responses":[]}
    def test_valid_task(self):
        validate_task(self.valid_task())
    def test_blocks_publish_command_in_test_pane(self):
        task=self.valid_task(); task["test_plan"][0]["command"]="git push origin main"
        with self.assertRaises(WorkflowError): validate_task(task)

if __name__=="__main__": unittest.main()
