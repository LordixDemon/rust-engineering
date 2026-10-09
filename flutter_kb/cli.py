from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import __version__
from .retrieval import context, graph_path, read_document, related, search
from .sources import KnowledgeError, atomic_json, load_config
from .store import current_snapshot, open_database, source_age_hours, update

APP_ROOT = Path(__file__).resolve().parents[1]


def bounded(low: int, high: int):
    def parse(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"Expected {low}..{high}")
        return number

    return parse


def emit(value: object):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def lock_versions(path: Path) -> dict:
    if not path.is_file():
        return {}
    result, package = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^  ([A-Za-z][A-Za-z0-9_]*):\s*$", line)
        if match:
            package = match.group(1)
        elif line and not line[0].isspace():
            package = None
        match = re.match(r'^    version:\s*[\'"]?([^\s\'"]+)', line)
        if match and package:
            result[package] = match.group(1)
    return result


def doctor(data: Path, project: Path) -> dict:
    db, manifest = open_database(data)
    db.close()
    warnings, mismatches = [], []
    installed = None
    executable = shutil.which("flutter")
    if executable:
        try:
            run = subprocess.run(
                [executable, "--version", "--machine"],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            if run.returncode:
                raise ValueError(run.stderr[-800:])
            installed = json.loads(run.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"Cannot determine installed Flutter SDK: {exc}")
    else:
        warnings.append("Flutter executable is not on PATH")
    sources = {s["id"]: s for s in manifest["sources"]}
    if installed and "flutter-sdk" in sources:
        indexed = sources["flutter-sdk"]
        if installed.get("frameworkRevision") != indexed["commit"]:
            mismatches.append(
                {
                    "component": "flutter",
                    "installed": installed.get("frameworkVersion"),
                    "indexed": indexed["version"],
                    "action": "Use a matching knowledge ref or perform the project-authorized stable SDK upgrade and validate the project.",
                }
            )
    pins = lock_versions(project / "pubspec.lock")
    if (
        "flame" in pins
        and "flame" in sources
        and pins["flame"] != sources["flame"]["version"]
    ):
        mismatches.append(
            {
                "component": "flame",
                "installed": pins["flame"],
                "indexed": sources["flame"]["version"],
                "action": "Index the matching release tag or upgrade the project dependency within the requested scope.",
            }
        )
    if not (project / "pubspec.yaml").exists():
        warnings.append(
            "This directory is not yet a Flutter project; no game dependency was installed or upgraded."
        )
    elif not pins:
        warnings.append(
            "No resolved pubspec.lock; dependency compatibility is unverified."
        )
    warnings.append(
        "Flutter/Dart guides track rolling main. Exact source commits and project analysis take precedence for API availability."
    )
    return {
        "project": str(project),
        "snapshot_id": manifest["snapshot_id"],
        "age_hours": round(source_age_hours(manifest), 2),
        "flutter": {
            key: installed.get(key)
            for key in (
                "frameworkVersion",
                "frameworkRevision",
                "channel",
                "dartSdkVersion",
            )
        }
        if installed
        else None,
        "project_packages": pins,
        "mismatches": mismatches,
        "warnings": warnings,
        "sdk_verified": bool(installed),
        "flame_project_verified": "flame" in pins,
    }


def install_skill(
    destination: Path,
    app_root: Path = APP_ROOT,
    skill_name: str = "flutter-flame-engineering",
) -> dict:
    template = app_root / "skill" / skill_name
    if not template.is_dir():
        raise KnowledgeError("Skill template missing; run from the source checkout")
    target = destination / template.name
    if target.exists():
        marker = target / "installation.json"
        if not marker.is_file() or json.loads(marker.read_text()).get(
            "app_root"
        ) != str(app_root):
            raise KnowledgeError(
                f"A different skill already exists at {target}; refusing to overwrite it"
            )
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, target, dirs_exist_ok=True)
    atomic_json(
        target / "installation.json",
        {"app_root": str(app_root), "application_version": __version__},
    )
    return {
        "installed": str(target),
        "invocation": "$" + skill_name,
        "implicit_invocation": True,
    }


def parser(
    app_root: Path = APP_ROOT, ecosystem: str = "flutter"
) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=f"Versioned {ecosystem} knowledge: update, search and evidence graph"
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--data-dir",
        type=Path,
        default=Path(
            os.environ.get(
                "RKB_DATA_DIR" if ecosystem == "rust" else "FFKB_DATA_DIR",
                app_root / ".data",
            )
        ),
    )
    p.add_argument("--config", type=Path, default=app_root / "sources.json")
    sub = p.add_subparsers(dest="command", required=True)
    up = sub.add_parser(
        "update", help="Fetch configured sources and atomically publish a new snapshot"
    )
    up.add_argument("--if-older", type=float, metavar="HOURS")
    up.add_argument("--workers", type=bounded(1, 8), default=4)
    sub.add_parser(
        "rebuild", help="Build a new snapshot from cached checkouts; no network"
    )
    sub.add_parser(
        "status", help="Coverage, versions, freshness and last update outcome"
    )
    sub.add_parser("sources", help="Configured official sources")
    for command in ("search", "context"):
        q = sub.add_parser(command)
        q.add_argument("query")
        q.add_argument("--source")
        if command == "search":
            q.add_argument("--limit", type=bounded(1, 50), default=8)
            q.add_argument("--kind", choices=("doc", "code", "config"))
        else:
            q.add_argument("--max-chars", type=bounded(1000, 60000), default=14000)
    read = sub.add_parser(
        "read", help="Read immutable source text with exact line ranges"
    )
    read.add_argument("document_id")
    read.add_argument("--start", type=bounded(1, 1000000), default=1)
    read.add_argument("--lines", type=bounded(1, 400), default=120)
    rel = sub.add_parser("related", help="Bounded evidence graph neighborhood")
    rel.add_argument("node")
    rel.add_argument("--source")
    rel.add_argument("--depth", type=bounded(1, 3), default=1)
    rel.add_argument("--limit", type=bounded(1, 250), default=40)
    route = sub.add_parser("path", help="Find an evidence path, excluding keyword hubs")
    route.add_argument("start")
    route.add_argument("end")
    route.add_argument("--max-depth", type=bounded(1, 8), default=5)
    sub.add_parser("graph", help="Paths to the full graph and coverage report")
    check = sub.add_parser(
        "doctor",
        help="Compare indexed versions with the active toolchain and project lockfile; never upgrades anything",
    )
    check.add_argument("--project", type=Path, default=Path.cwd())
    check.add_argument("--strict", action="store_true")
    install = sub.add_parser(
        "install-skill", help="Install the companion skill for automatic discovery"
    )
    install.add_argument(
        "--destination",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills",
    )
    return p


def main(
    argv: list[str] | None = None,
    *,
    app_root: Path = APP_ROOT,
    ecosystem: str = "flutter",
) -> int:
    args = parser(app_root, ecosystem).parse_args(argv)
    data = args.data_dir.expanduser().resolve()
    try:
        if args.command == "sources":
            emit(load_config(args.config))
        elif args.command in {"update", "rebuild"}:
            if getattr(args, "if_older", None) is not None and args.if_older < 0:
                raise KnowledgeError("--if-older must be nonnegative")
            emit(
                update(
                    data,
                    load_config(args.config),
                    offline=args.command == "rebuild",
                    if_older=getattr(args, "if_older", None),
                    workers=getattr(args, "workers", 4),
                )
            )
        elif args.command == "install-skill":
            emit(
                install_skill(
                    args.destination.expanduser().resolve(),
                    app_root,
                    "rust-engineering"
                    if ecosystem == "rust"
                    else "flutter-flame-engineering",
                )
            )
        elif args.command == "doctor":
            if ecosystem == "rust":
                from .rust_support import rust_doctor

                result = rust_doctor(data, args.project.expanduser().resolve())
            else:
                result = doctor(data, args.project.expanduser().resolve())
            emit(result)
            if args.strict and (
                result["mismatches"]
                or not result["sdk_verified"]
                or result.get("unverified_dependencies")
            ):
                return 3
        elif args.command == "graph":
            folder = current_snapshot(data)
            emit(
                {
                    "graph": str(folder / "graph.json"),
                    "report": str(folder / "REPORT.md"),
                    "manifest": str(folder / "manifest.json"),
                }
            )
        else:
            db, manifest = open_database(data)
            try:
                if args.command == "status":
                    last = data / "last_update.json"
                    emit(
                        {
                            "snapshot_id": manifest["snapshot_id"],
                            "built_at": manifest["built_at"],
                            "age_hours": round(source_age_hours(manifest), 2),
                            "stats": manifest["stats"],
                            "sources": manifest["sources"],
                            "last_update": json.loads(last.read_text())
                            if last.exists()
                            else None,
                        }
                    )
                elif args.command == "search":
                    emit(search(db, args.query, args.limit, args.source, args.kind))
                elif args.command == "context":
                    emit(context(db, manifest, args.query, args.max_chars, args.source))
                elif args.command == "read":
                    emit(read_document(db, args.document_id, args.start, args.lines))
                elif args.command == "related":
                    emit(related(db, args.node, args.depth, args.limit, args.source))
                elif args.command == "path":
                    emit(graph_path(db, args.start, args.end, args.max_depth))
            finally:
                db.close()
        return 0
    except (KnowledgeError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"{ecosystem}-kb: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ffkb: interrupted; published snapshots remain intact", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
