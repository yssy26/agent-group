#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
from pathlib import Path
from typing import Any

from common import (
    WorkflowError,
    agent_launch_args,
    ensure_git_repo,
    load_config,
    resolve_role,
    run,
    run_json,
    runtime_dir,
    write_json,
)


ROLES = ("lead", "reviewer", "executor")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Create a Herdr workspace and launch configurable role agents."
    )
    p.add_argument("--project", required=True, type=Path)
    p.add_argument("--config", type=Path)
    p.add_argument("--dry-run", action="store_true")
    for role in ROLES:
        p.add_argument(f"--{role}-kind")
        p.add_argument(f"--{role}-model")
    return p


def result_obj(payload: dict[str, Any]) -> dict[str, Any]:
    # Herdr currently wraps successful output under result.
    return payload.get("result", payload)


def main() -> int:
    args = build_parser().parse_args()
    project = args.project.expanduser().resolve()
    config = load_config(args.config)
    ensure_git_repo(project)
    runtime = runtime_dir(project, config)
    runtime.mkdir(parents=True, exist_ok=True)

    resolved: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        resolved[role] = resolve_role(
            config,
            role,
            cli_kind=getattr(args, f"{role}_kind"),
            cli_model=getattr(args, f"{role}_model"),
        )

    if args.dry_run:
        for role in ROLES:
            cfg = resolved[role]
            print(
                f"{role}: kind={cfg['kind']} model={cfg.get('model','')} "
                f"args={shlex.join(agent_launch_args(cfg))}"
            )
        return 0

    label = config.get("workflow", {}).get("workspace_label", "agent-group")
    created = run_json(
        [
            "herdr",
            "workspace",
            "create",
            "--cwd",
            str(project),
            "--label",
            str(label),
            "--no-focus",
        ]
    )
    root = result_obj(created)["root_pane"]["pane_id"]

    reviewer_split = run_json(
        ["herdr", "pane", "split", root, "--direction", "right", "--no-focus"]
    )
    reviewer_pane = result_obj(reviewer_split)["pane"]["pane_id"]

    executor_split = run_json(
        [
            "herdr",
            "pane",
            "split",
            reviewer_pane,
            "--direction",
            "down",
            "--no-focus",
        ]
    )
    executor_pane = result_obj(executor_split)["pane"]["pane_id"]

    test_split = run_json(
        ["herdr", "pane", "split", root, "--direction", "down", "--no-focus"]
    )
    test_pane = result_obj(test_split)["pane"]["pane_id"]

    pane_for = {
        "lead": root,
        "reviewer": reviewer_pane,
        "executor": executor_pane,
    }

    stamp = dt.datetime.now().strftime("%y%m%d%H%M%S")
    agents: dict[str, Any] = {}

    for role in ROLES:
        cfg = resolved[role]
        # Herdr live names must be short, lower-case, and unique.
        name = f"ag{stamp}-{role}"
        cmd = [
            "herdr",
            "agent",
            "start",
            name,
            "--kind",
            str(cfg["kind"]),
            "--pane",
            pane_for[role],
            "--timeout",
            "120000",
        ]
        passthrough = agent_launch_args(cfg)
        if passthrough:
            cmd += ["--", *passthrough]

        print(f"Starting {role}: {shlex.join(cmd)}")
        run(cmd, capture=True)

        agents[role] = {
            "name": name,
            "pane_id": pane_for[role],
            "kind": cfg["kind"],
            "model": cfg.get("model", ""),
            "profile": cfg.get("profile", ""),
            "launch_args": passthrough,
        }

    session = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "project": str(project),
        "agents": agents,
        "test_pane_id": test_pane,
    }
    write_json(runtime / "session.json", session)

    print(json.dumps(session, ensure_ascii=False, indent=2))
    print(f"Session written to {runtime / 'session.json'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowError as exc:
        raise SystemExit(f"ERROR: {exc}")
