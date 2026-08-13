#!/usr/bin/env python3
"""Shared helpers for agent-group scripts. Standard-library only."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError as exc:  # Python < 3.11
    raise SystemExit("Python 3.11+ is required (tomllib is used).") from exc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "agents.toml"


class WorkflowError(RuntimeError):
    pass


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = (path or DEFAULT_CONFIG).resolve()
    with config_path.open("rb") as fh:
        return tomllib.load(fh)


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=capture,
        text=text,
    )


def run_json(cmd: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    cp = run(cmd, cwd=cwd)
    stdout = cp.stdout.strip()
    if not stdout:
        raise WorkflowError(f"Command returned no JSON: {shlex.join(cmd)}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Some CLIs may emit harmless text before a final JSON line.
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
        raise WorkflowError(
            f"Could not parse JSON from command: {shlex.join(cmd)}\n{stdout}"
        )


def ensure_git_repo(project: Path) -> None:
    cp = run(["git", "rev-parse", "--show-toplevel"], cwd=project, check=False)
    if cp.returncode != 0:
        raise WorkflowError(f"Not a Git repository: {project}")


def runtime_dir(project: Path, config: dict[str, Any]) -> Path:
    name = config.get("workflow", {}).get("runtime_dir", ".agent-runtime")
    return project / name


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_role(
    config: dict[str, Any],
    role: str,
    *,
    cli_kind: str | None = None,
    cli_model: str | None = None,
) -> dict[str, Any]:
    raw = dict(config["roles"][role])
    env_prefix = f"AG_{role.upper()}_"
    raw["kind"] = (
        cli_kind
        or os.environ.get(env_prefix + "KIND")
        or raw.get("kind", "")
    )
    raw["model"] = (
        cli_model
        or os.environ.get(env_prefix + "MODEL")
        or raw.get("model", "")
    )
    if not raw["kind"]:
        raise WorkflowError(f"Role {role!r} has no agent kind.")
    return raw


def agent_launch_args(role_cfg: dict[str, Any]) -> list[str]:
    args: list[str] = []
    model = str(role_cfg.get("model", "")).strip()
    model_flag = str(role_cfg.get("model_flag", "-m")).strip()
    if model and model_flag:
        args.extend([model_flag, model])

    if role_cfg.get("kind") == "opencode":
        profile = str(role_cfg.get("profile", "")).strip()
        if profile:
            args.extend(["--agent", profile])

    extra = role_cfg.get("launch_args", [])
    if not isinstance(extra, list):
        raise WorkflowError("launch_args must be a TOML array.")
    args.extend(str(x) for x in extra)
    return args


def git_fingerprint(project: Path, runtime_name: str = ".agent-runtime") -> str:
    """Fingerprint tracked/staged diff plus non-ignored untracked files."""
    head = run(["git", "rev-parse", "--verify", "HEAD"], cwd=project, check=False)
    if head.returncode != 0:
        raise WorkflowError("Target project must have a HEAD commit.")

    tracked = run(
        ["git", "diff", "--binary", "HEAD", "--"],
        cwd=project,
        text=False,
    ).stdout

    untracked_cp = run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=project,
        text=False,
    )
    names = [n for n in untracked_cp.stdout.split(b"\0") if n]

    h = hashlib.sha256()
    h.update(tracked)
    for name_b in sorted(names):
        name = os.fsdecode(name_b)
        if name == runtime_name or name.startswith(runtime_name + os.sep):
            continue
        path = project / name
        h.update(name_b)
        h.update(b"\0")
        if path.is_file():
            try:
                h.update(path.read_bytes())
            except OSError:
                h.update(b"<UNREADABLE>")
        else:
            h.update(b"<NONFILE>")
        h.update(b"\0")
    return h.hexdigest()


def require_same_fingerprint(
    before: str,
    after: str,
    *,
    role: str,
) -> None:
    if before != after:
        raise WorkflowError(
            f"Read-only role {role!r} changed the target working tree. "
            "Workflow stopped; no automatic reset was performed."
        )


def print_err(msg: str) -> None:
    print(msg, file=sys.stderr)
