#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import load_config, resolve_role, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Check agent-group prerequisites.")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    problems: list[str] = []

    for exe in ("git", "herdr"):
        path = shutil.which(exe)
        print(f"{exe}: {path or 'MISSING'}")
        if not path:
            problems.append(f"Missing executable: {exe}")

    kinds = {resolve_role(config, r)["kind"] for r in ("lead", "reviewer", "executor")}
    for kind in sorted(kinds):
        path = shutil.which(kind)
        print(f"{kind}: {path or 'MISSING'}")
        if not path:
            problems.append(f"Missing agent executable: {kind}")

    if shutil.which("herdr"):
        cp = run(["herdr", "integration", "status"], check=False)
        print("\nHerdr integration status:")
        print(cp.stdout or cp.stderr)

    if args.project:
        project = args.project.expanduser().resolve()
        cp = run(["git", "rev-parse", "--show-toplevel"], cwd=project, check=False)
        ok = cp.returncode == 0
        print(f"target project git repo: {'OK' if ok else 'FAIL'} ({project})")
        if not ok:
            problems.append("Target project is not a Git repository.")

    for role in ("lead", "reviewer", "executor"):
        cfg = resolve_role(config, role)
        print(f"{role}: kind={cfg['kind']} model={cfg.get('model', '')}")

    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"- {p}")
        return 1

    print("\nDoctor: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
