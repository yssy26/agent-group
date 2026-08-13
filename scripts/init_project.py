#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import ROOT, ensure_git_repo, load_config, runtime_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize local agent-group runtime files in a target Git repo."
    )
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--force-templates",
        action="store_true",
        help="Overwrite PROJECT_CHARTER.md and CURRENT_STATE.md in runtime.",
    )
    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    config = load_config(args.config)
    ensure_git_repo(project)

    runtime = runtime_dir(project, config)
    runtime.mkdir(parents=True, exist_ok=True)

    templates = {
        "PROJECT_CHARTER.md": ROOT / "templates" / "PROJECT_CHARTER.md",
        "CURRENT_STATE.md": ROOT / "templates" / "CURRENT_STATE.md",
    }
    for name, source in templates.items():
        dest = runtime / name
        if args.force_templates or not dest.exists():
            dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    # Local exclude avoids changing tracked .gitignore.
    exclude = project / ".git" / "info" / "exclude"
    line = config.get("workflow", {}).get("runtime_dir", ".agent-runtime").rstrip("/") + "/"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if line not in {x.strip() for x in existing.splitlines()}:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(line + "\n")

    print(f"Initialized runtime: {runtime}")
    print("Edit PROJECT_CHARTER.md before relying on autonomous planning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
