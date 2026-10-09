"""Atomic snapshots: readers keep a complete old index until the new one is ready."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .index import build_index, export_graph, write_report
from .sources import (
    KnowledgeError,
    atomic_json,
    collect_files,
    config_hash,
    now,
    prepare_sources,
    sync_source,
)


@contextmanager
def update_lock(data: Path):
    data.mkdir(parents=True, exist_ok=True)
    with (data / ".update.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                if not lock.read(1):
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise KnowledgeError(
                "Another update is running; current snapshot is still readable"
            ) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def current_snapshot(data: Path) -> Path:
    pointer = data / "current.json"
    if not pointer.is_file():
        raise KnowledgeError("No knowledge snapshot. Run: python3 ffkb.py update")
    snapshot_id = json.loads(pointer.read_text())["snapshot_id"]
    if (
        not isinstance(snapshot_id, str)
        or Path(snapshot_id).name != snapshot_id
        or snapshot_id in {".", ".."}
    ):
        raise KnowledgeError("Invalid snapshot pointer")
    folder = data / "snapshots" / snapshot_id
    if (
        not (folder / "index.sqlite").is_file()
        or not (folder / "manifest.json").is_file()
    ):
        raise KnowledgeError(
            "Incomplete snapshot; run update or restore a previous snapshot"
        )
    return folder.resolve()


def open_database(data: Path) -> tuple[sqlite3.Connection, dict]:
    folder = current_snapshot(data)
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest.get("schema_version") != 1:
        raise KnowledgeError(
            "Unsupported index schema; rebuild with the matching application version"
        )
    db = sqlite3.connect(f"{(folder / 'index.sqlite').as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db, manifest


def source_age_hours(manifest: dict) -> float:
    checked = [datetime.fromisoformat(s["checked_at"]) for s in manifest["sources"]]
    return max(0.0, (datetime.now(UTC) - min(checked)).total_seconds() / 3600)


def update(
    data: Path,
    cfg: dict,
    offline: bool = False,
    if_older: float | None = None,
    workers: int = 4,
) -> dict:
    with update_lock(data):
        old_folder = (
            current_snapshot(data) if (data / "current.json").exists() else None
        )
        if old_folder and if_older is not None and not offline:
            old = json.loads((old_folder / "manifest.json").read_text())
            if source_age_hours(old) < if_older and old.get(
                "config_hash"
            ) == config_hash(cfg):
                return {
                    "status": "fresh",
                    "snapshot_id": old["snapshot_id"],
                    "age_hours": round(source_age_hours(old), 2),
                }
        sid = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        snapshots = data / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        staging = snapshots / (".staging-" + sid)
        staging.mkdir()
        started = now()
        try:
            metas, errors = {}, []
            run_sources = cfg["sources"] if offline else prepare_sources(cfg["sources"])
            with ThreadPoolExecutor(max_workers=workers) as pool:
                tasks = {
                    pool.submit(sync_source, source, data / "cache", offline): source[
                        "id"
                    ]
                    for source in run_sources
                }
                for future in as_completed(tasks):
                    name = tasks[future]
                    try:
                        metas[name] = future.result()
                        print(
                            f"{name}: {metas[name]['version']} @ {metas[name]['commit'][:12]}",
                            file=sys.stderr,
                            flush=True,
                        )
                    except (
                        KnowledgeError,
                        OSError,
                        ValueError,
                        KeyError,
                        TypeError,
                    ) as exc:
                        errors.append(f"{name}: {exc}")
            if errors:
                raise KnowledgeError(
                    "Update aborted; previous snapshot preserved.\n" + "\n".join(errors)
                )
            docs, sources = [], []
            for source in cfg["sources"]:
                items, skipped = collect_files(
                    source, metas[source["id"]], cfg["max_file_bytes"]
                )
                docs.extend(items)
                meta = {
                    key: value
                    for key, value in metas[source["id"]].items()
                    if key != "checkout"
                }
                meta.update(
                    documents=len(items),
                    skipped=skipped,
                    included_paths=source["paths"],
                )
                sources.append(meta)
                print(
                    f"Indexing {source['id']}: {len(items)} documents",
                    file=sys.stderr,
                    flush=True,
                )
            docs.sort(key=lambda doc: doc["id"])
            previous = old_folder / "index.sqlite" if old_folder else None
            stats = build_index(staging / "index.sqlite", docs, sources, previous)
            manifest = {
                "schema_version": 1,
                "ecosystem": cfg.get("ecosystem", "flutter"),
                "snapshot_id": sid,
                "built_at": now(),
                "offline_rebuild": offline,
                "config_hash": config_hash(cfg),
                "sources": sources,
                "stats": stats,
                "extraction": "deterministic lexical links",
                "llm_input_tokens": 0,
                "llm_output_tokens": 0,
            }
            atomic_json(staging / "manifest.json", manifest)
            export_graph(staging / "index.sqlite", staging / "graph.json")
            write_report(staging / "REPORT.md", manifest)
            destination = snapshots / sid
            os.replace(staging, destination)
            atomic_json(data / "current.json", {"snapshot_id": sid})
            atomic_json(
                data / "last_update.json",
                {
                    "status": "ok",
                    "started_at": started,
                    "finished_at": now(),
                    "snapshot_id": sid,
                },
            )
            return {
                "status": "updated",
                "snapshot_id": sid,
                "path": str(destination.resolve()),
                **stats,
            }
        except BaseException as exc:
            atomic_json(
                data / "last_update.json",
                {
                    "status": "failed",
                    "started_at": started,
                    "finished_at": now(),
                    "error": str(exc),
                },
            )
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
