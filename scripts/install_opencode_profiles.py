#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from common import ROOT


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install model-agnostic OpenCode role profiles."
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path.home() / ".config" / "opencode" / "agents",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    src_dir = ROOT / "opencode" / "agents"
    dst_dir = args.destination.expanduser().resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    for src in sorted(src_dir.glob("*.md")):
        dst = dst_dir / src.name
        if dst.exists() and not args.force:
            raise SystemExit(
                f"Refusing to overwrite {dst}. Re-run with --force if intended."
            )
        shutil.copy2(src, dst)
        print(f"Installed {dst}")

    print("Profiles contain no model IDs; models remain runtime-configurable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
