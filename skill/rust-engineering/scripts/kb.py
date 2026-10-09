#!/usr/bin/env python3
"""Run the installed Rust knowledge profile without depending on the cwd."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        skill = Path(__file__).resolve().parents[1]
        marker = skill / "installation.json"
        if os.environ.get("RKB_APP_ROOT"):
            root = Path(os.environ["RKB_APP_ROOT"]).expanduser().resolve()
        elif marker.is_file():
            root = Path(json.loads(marker.read_text())["app_root"])
        else:
            root = skill.parents[1]
        entry = root / "rkb.py"
        if not entry.is_file():
            raise OSError(
                f"Cannot find {entry}; set RKB_APP_ROOT to the rust-engineering repository"
            )
        return subprocess.run(
            [sys.executable, str(entry), *sys.argv[1:]], check=False
        ).returncode
    except (OSError, ValueError, KeyError) as exc:
        print(f"rust-engineering: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
