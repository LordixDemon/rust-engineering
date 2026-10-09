from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from flutter_kb.cli import doctor, install_skill, lock_versions
from flutter_kb.index import build_index, chunk_lines, export_graph
from flutter_kb.retrieval import context, graph_path, read_document, related, search
from flutter_kb.sources import KnowledgeError, collect_files, load_config, resolve_ref
from flutter_kb.store import (
    current_snapshot,
    open_database,
    source_age_hours,
    update,
    update_lock,
)


def source_meta(checkout: Path, commit="a" * 40) -> dict:
    return {
        "id": "flame",
        "repository": "https://github.com/flame-engine/flame.git",
        "requested_ref": "main",
        "resolved_ref": "main",
        "version": "1.2.3",
        "commit": commit,
        "version_policy": "test release",
        "checked_at": "2026-01-01T00:00:00+00:00",
        "license_url": "https://example.invalid/LICENSE",
        "checkout": str(checkout),
    }


def doc(path: str, body: str, source="flame") -> dict:
    return {
        "id": f"{source}:{path}",
        "source_id": source,
        "path": path,
        "kind": "code" if path.endswith(".dart") else "doc",
        "content": body,
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
        "url": f"https://github.com/flame-engine/flame/blob/{'a' * 40}/{path}",
        "version": "1.2.3",
    }


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / "index.sqlite"
        self.docs = [
            doc(
                "packages/flame/lib/scale.dart",
                "// class Phantom {}\n/* class Hidden {} */\n"
                "const text = 'class Fake {}';\nclass ScaleEffect {\n  void update() {}\n}\n",
            ),
            doc("packages/flame/lib/controller.dart", "class EffectController {}\n"),
            doc(
                "packages/flame/lib/example.dart",
                "import 'package:flame/scale.dart';\n"
                "export 'controller.dart';\nclass Demo { ScaleEffect? effect; }\n",
            ),
            doc(
                "doc/effects.md",
                "# Pillow animation\n\nUse ScaleEffect with EffectController.\n"
                "See [example](../packages/flame/lib/example.dart).\n\n## Lifecycle\nRelease resources onRemove.\n",
            ),
        ]
        self.meta = source_meta(self.root)
        self.manifest = {"snapshot_id": "fixture", "sources": [self.meta]}
        self.stats = build_index(self.database, self.docs, [self.meta])
        self.db = sqlite3.connect(self.database)
        self.db.row_factory = sqlite3.Row

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_search_and_citation_reproduce_exact_source(self):
        hits = search(self.db, "Pillow animation", source="flame")
        self.assertEqual(hits[0]["document_id"], "flame:doc/effects.md")
        hit = hits[0]
        evidence = read_document(
            self.db,
            hit["document_id"],
            hit["start_line"],
            hit["end_line"] - hit["start_line"] + 1,
        )
        self.assertIn("Use ScaleEffect", evidence["text"])
        self.assertIn("a" * 40, evidence["url"])
        self.assertEqual(evidence["url"], hit["url"])

    def test_graph_has_real_declarations_imports_and_evidence(self):
        labels = {
            row[0]
            for row in self.db.execute("SELECT label FROM nodes WHERE kind='symbol'")
        }
        self.assertEqual(labels, {"ScaleEffect", "EffectController", "Demo"})
        edges = self.db.execute(
            "SELECT * FROM edges WHERE relation='imports'"
        ).fetchall()
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["target"], "flame:packages/flame/lib/scale.dart")
        for edge in self.db.execute("SELECT * FROM edges"):
            original = next(
                d for d in self.docs if d["id"] == edge["evidence_document"]
            )
            self.assertEqual(
                original["content"].splitlines()[edge["line"] - 1][:600],
                edge["evidence"],
            )

    def test_ambiguous_symbols_do_not_create_invented_edges(self):
        more = self.docs + [
            doc("packages/flame/lib/other.dart", "class ScaleEffect {}")
        ]
        other = self.root / "ambiguous.sqlite"
        stats = build_index(other, more, [self.meta])
        with closing(sqlite3.connect(other)) as db:
            targets = db.execute(
                "SELECT n.label FROM edges e JOIN nodes n ON n.id=e.target "
                "WHERE e.source='flame:doc/effects.md' AND relation='mentions_symbol'"
            ).fetchall()
        self.assertNotIn(("ScaleEffect",), targets)
        self.assertGreater(stats["ambiguous_symbol_mentions_skipped"], 0)

    def test_changes_and_removals_do_not_leave_stale_graph_nodes(self):
        new_docs = [
            doc("doc/effects.md", "# New behavior\nOnly EffectController remains."),
            self.docs[1],
        ]
        new_db = self.root / "new.sqlite"
        stats = build_index(new_db, new_docs, [self.meta], self.database)
        self.assertEqual(
            (stats["changed"], stats["removed"], stats["unchanged"]), (1, 2, 1)
        )
        with closing(sqlite3.connect(new_db)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM nodes WHERE label='ScaleEffect'"
                ).fetchone()[0],
                0,
            )
        self.assertTrue(search(self.db, "ScaleEffect"))

    def test_queries_are_bounded_and_treat_syntax_as_data(self):
        self.assertEqual(search(self.db, "!!!"), [])
        search(self.db, '" OR 1=1; DROP TABLE documents; --')
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM documents").fetchone()[0], 4
        )
        results = search(self.db, "подушка")
        self.assertTrue(results)
        packet = context(self.db, self.manifest, "ScaleEffect", max_chars=1000)
        self.assertLessEqual(packet["evidence_characters"], 1000)
        self.assertTrue(packet["evidence"])
        self.assertTrue(
            all(
                item["url"].startswith("https://github.com/")
                for item in packet["evidence"]
            )
        )

    def test_graph_navigation_and_stable_export(self):
        graph = related(self.db, "ScaleEffect", limit=2)
        self.assertEqual(len(graph["edges"]), 2)
        self.assertTrue(graph["truncated"])
        route = graph_path(self.db, "ScaleEffect", "EffectController")
        self.assertTrue(route["found"])
        self.assertTrue(
            all(edge["relation"] != "keyword_tag" for edge in route["edges"])
        )
        a, b = self.root / "a.json", self.root / "b.json"
        export_graph(self.database, a)
        export_graph(self.database, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_chunking_keeps_every_line_and_literal_code_heading(self):
        text = "# Section\n```python\n# code, not heading\n```\n" + "\n".join(
            f"value {i}" for i in range(200)
        )
        chunks = chunk_lines(text, max_lines=20, max_chars=300)
        self.assertEqual("\n".join(chunk[3] for chunk in chunks), text)
        self.assertTrue(all(chunk[0] == "Section" for chunk in chunks))
        self.assertEqual(chunks[-1][2], len(text.splitlines()))

    def test_lockfile_parser_uses_resolved_versions(self):
        file = self.root / "pubspec.lock"
        file.write_text(
            'packages:\n  flame:\n    dependency: "direct main"\n    version: "1.38.2"\n'
            '  flame_audio:\n    version: "2.11.2"\nsdks:\n  dart: ">=3.0.0"\n'
        )
        self.assertEqual(
            lock_versions(file), {"flame": "1.38.2", "flame_audio": "2.11.2"}
        )


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.checkout = self.root / "upstream"
        (self.checkout / "doc").mkdir(parents=True)
        (self.checkout / "doc" / "guide.md").write_text(
            "# Animation\nUse ScaleEffect for pillows.\n"
        )
        self.source = {
            "id": "flame",
            "repository": "https://github.com/flame-engine/flame.git",
            "ref": "main",
            "paths": ["doc"],
            "suffixes": [".md"],
        }
        self.cfg = {
            "schema_version": 1,
            "max_file_bytes": 10000,
            "sources": [self.source],
        }
        self.data = self.root / "data"
        self.meta = source_meta(self.checkout)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, offline=False):
        with patch("flutter_kb.store.sync_source", return_value=self.meta):
            return update(self.data, self.cfg, offline=offline)

    def test_offline_rebuild_creates_new_snapshot_without_faking_freshness(self):
        first = self.build()
        old = current_snapshot(self.data)
        second = self.build(offline=True)
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertTrue(old.is_dir())
        db, manifest = open_database(self.data)
        db.close()
        self.assertEqual(manifest["sources"][0]["checked_at"], self.meta["checked_at"])
        self.assertTrue(manifest["offline_rebuild"])
        self.assertGreater(source_age_hours(manifest), 24)

    def test_failed_fetch_preserves_queryable_snapshot(self):
        self.build()
        before = (self.data / "current.json").read_bytes()
        with (
            patch(
                "flutter_kb.store.sync_source",
                side_effect=KnowledgeError("network down"),
            ),
            self.assertRaisesRegex(KnowledgeError, "previous snapshot preserved"),
        ):
            update(self.data, self.cfg)
        self.assertEqual(before, (self.data / "current.json").read_bytes())
        db, _ = open_database(self.data)
        try:
            self.assertTrue(search(db, "animation"))
        finally:
            db.close()
        self.assertEqual(
            json.loads((self.data / "last_update.json").read_text())["status"], "failed"
        )

    def test_failed_graph_export_cannot_publish_partial_snapshot(self):
        self.build()
        before = current_snapshot(self.data)
        with (
            patch("flutter_kb.store.sync_source", return_value=self.meta),
            patch("flutter_kb.store.export_graph", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            update(self.data, self.cfg)
        self.assertEqual(before, current_snapshot(self.data))
        self.assertFalse(list((self.data / "snapshots").glob(".staging-*")))

    def test_empty_source_and_missing_path_preserve_previous_data(self):
        self.build()
        before = current_snapshot(self.data)
        (self.checkout / "doc" / "guide.md").unlink()
        with self.assertRaisesRegex(KnowledgeError, "no documents"):
            self.build()
        self.assertEqual(before, current_snapshot(self.data))
        (self.checkout / "doc").rmdir()
        with self.assertRaisesRegex(KnowledgeError, "path disappeared"):
            self.build()

    def test_competing_writer_fails_but_reads_keep_working(self):
        self.build()
        with update_lock(self.data):
            with (
                self.assertRaisesRegex(KnowledgeError, "Another update"),
                update_lock(self.data),
            ):
                pass
            db, _ = open_database(self.data)
            self.assertTrue(search(db, "pillows"))
            db.close()

    def test_collection_skips_symlink_outside_corpus_and_large_files(self):
        outside = self.root / "private.md"
        outside.write_text("private")
        (self.checkout / "doc" / "escape.md").symlink_to(outside)
        (self.checkout / "doc" / "large.md").write_text("x" * 20000)
        documents, skipped = collect_files(self.source, self.meta, 10000)
        self.assertEqual(len(documents), 1)
        self.assertEqual(skipped["symlink"], 1)
        self.assertEqual(skipped["too_large"], 1)

    def test_doctor_detects_flame_mismatch_without_upgrading(self):
        self.build()
        project = self.root / "project"
        project.mkdir()
        (project / "pubspec.yaml").write_text("name: demo")
        (project / "pubspec.lock").write_text(
            'packages:\n  flame:\n    version: "0.9.0"\n'
        )
        result = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "frameworkVersion": "3.47.4",
                    "frameworkRevision": "abc",
                    "dartSdkVersion": "3.13.3",
                }
            ),
            "",
        )
        with (
            patch("flutter_kb.cli.shutil.which", return_value="flutter"),
            patch("flutter_kb.cli.subprocess.run", return_value=result) as run,
        ):
            report = doctor(self.data, project)
        self.assertEqual(report["mismatches"][0]["component"], "flame")
        self.assertEqual(run.call_args.args[0], ["flutter", "--version", "--machine"])

    def test_config_rejects_path_escape(self):
        self.cfg["sources"][0]["paths"] = ["../../outside"]
        file = self.root / "sources.json"
        file.write_text(json.dumps(self.cfg))
        with self.assertRaisesRegex(KnowledgeError, "Unsafe"):
            load_config(file)

    def test_skill_install_wrapper_and_ownership_guard(self):
        destination = self.root / "skills"
        result = install_skill(destination, skill_name="rust-engineering")
        wrapper = Path(result["installed"]) / "scripts" / "kb.py"
        process = subprocess.run(
            [sys.executable, str(wrapper), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("0.1.0", process.stdout)
        install_skill(destination, skill_name="rust-engineering")
        marker = Path(result["installed"]) / "installation.json"
        marker.write_text('{"app_root":"/different/project"}')
        with self.assertRaisesRegex(KnowledgeError, "different skill"):
            install_skill(destination, skill_name="rust-engineering")


class ReleaseTests(unittest.TestCase):
    def test_explicit_release_ref_keeps_a_comparable_package_version(self):
        ref, version = resolve_ref(
            {
                "ref": "flame-v1.38.2",
                "repository": "https://github.com/flame-engine/flame.git",
            }
        )
        self.assertEqual((ref, version), ("flame-v1.38.2", "1.38.2"))

    def test_pub_release_is_exact_tag_and_never_falls_back_to_main(self):
        source = {
            "ref": "@pub-stable:flame",
            "repository": "https://github.com/flame-engine/flame.git",
        }
        with (
            patch(
                "flutter_kb.sources.fetch_json",
                return_value={"latest": {"version": "1.38.2"}},
            ),
            patch("flutter_kb.sources.git", return_value="abc refs/tags/flame-v1.38.2"),
        ):
            self.assertEqual(resolve_ref(source), ("refs/tags/flame-v1.38.2", "1.38.2"))
        with (
            patch(
                "flutter_kb.sources.fetch_json",
                return_value={"latest": {"version": "1.38.2"}},
            ),
            patch("flutter_kb.sources.git", return_value=""),
            self.assertRaisesRegex(KnowledgeError, "refusing to substitute main"),
        ):
            resolve_ref(source)


if __name__ == "__main__":
    unittest.main()
