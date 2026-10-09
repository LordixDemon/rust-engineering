#!/usr/bin/env python3
"""Run the standalone Rust knowledge profile with its bundled engine."""

from pathlib import Path

from flutter_kb.cli import main

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    raise SystemExit(main(app_root=ROOT, ecosystem="rust"))
