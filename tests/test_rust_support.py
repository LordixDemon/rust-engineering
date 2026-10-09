from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flutter_kb.index import build_index, title_of
from flutter_kb.retrieval import context, search
from flutter_kb.rust_support import cargo_lock_packages, mask_rust, rust_doctor
from flutter_kb.sources import (
    KnowledgeError,
    collect_files,
    prepare_sources,
    resolve_ref,
)


def document(path, text):
    return {
        "id": f"demo:{path}",
        "source_id": "demo",
        "path": path,
        "kind": "code"
        if path.endswith(".rs")
        else "config"
        if path.endswith(".toml")
        else "doc",
        "content": text,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "url": f"https://github.com/example/demo/blob/{'a' * 40}/{path}",
        "version": "1.2.0",
    }


class RustIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta = {
            "id": "demo",
            "version": "1.2.0",
            "commit": "a" * 40,
            "checked_at": "2026-09-11T00:00:00+00:00",
            "version_policy": "test",
        }
        self.docs = [
            document("Cargo.toml", '[package]\nname="demo"\nversion="1.2.0"\n'),
            document(
                "src/lib.rs",
                "pub mod queue;\nuse crate::queue::Worker;\npub async fn launch() {}\n"
                'const TEXT: &str = r##"\npub struct Fake {}\n"##;\n'
                "/* outer /* pub struct Nested {} */ pub struct Hidden {} */\n"
                "pub trait Service {\n    fn handle(&self);\n}\n",
            ),
            document(
                "src/queue.rs",
                "pub struct Worker<'a> {\n    label: &'a str,\n}\n"
                "pub enum Command { Move }\npub fn enqueue<'a>(value: &'a str) {}\n",
            ),
            document(
                "doc/guide.md",
                "# Bounded queue\nUse Worker for backpressure and bounded commands.\n",
            ),
        ]
        db_file = self.root / "index.sqlite"
        build_index(db_file, self.docs, [self.meta])
        self.db = sqlite3.connect(db_file)
        self.db.row_factory = sqlite3.Row

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_raw_literals_nested_comments_and_lifetimes(self):
        labels = {
            r[0] for r in self.db.execute("SELECT label FROM nodes WHERE kind='symbol'")
        }
        self.assertTrue(
            {"Worker", "Command", "Service", "handle", "enqueue", "launch", "queue"}
            <= labels
        )
        self.assertTrue({"Fake", "Nested", "Hidden"}.isdisjoint(labels))
        original = "pub fn borrow<'a>(v: &'a str) {}\nlet c='x';\nlet r=br###\"/* comment */\"###;"
        masked = mask_rust(original)
        self.assertEqual(len(original), len(masked))
        self.assertIn("borrow<'a>(v: &'a str)", masked)
        self.assertNotIn("comment", masked)

    def test_module_and_use_links_have_original_evidence(self):
        targets = list(
            self.db.execute(
                "SELECT relation,target FROM edges WHERE source='demo:src/lib.rs'"
            )
        )
        self.assertIn(("module_file", "demo:src/queue.rs"), [tuple(r) for r in targets])
        self.assertIn(
            ("use_path_candidate", "demo:src/queue.rs"), [tuple(r) for r in targets]
        )
        for edge in self.db.execute("SELECT * FROM edges"):
            doc = next(d for d in self.docs if d["id"] == edge["evidence_document"])
            self.assertEqual(
                edge["evidence"], doc["content"].splitlines()[edge["line"] - 1][:600]
            )

    def test_rust_functions_are_searchable_and_context_is_labeled(self):
        hits = search(self.db, "enqueue", source="demo")
        self.assertEqual(hits[0]["document_id"], "demo:src/queue.rs")
        packet = context(
            self.db,
            {"ecosystem": "rust", "sources": [self.meta], "snapshot_id": "fixture"},
            "Worker",
            2000,
        )
        self.assertTrue(any("MSRV" in w for w in packet["warnings"]))
        self.assertFalse(any("Flutter" in w for w in packet["warnings"]))

    def test_standalone_manifest_and_readme_are_collected(self):
        (self.root / "README.md").write_text("# Demo")
        (self.root / "Cargo.toml").write_text('[package]\nname="demo"\nversion="1.2.0"')
        source = {
            "id": "demo",
            "paths": ["README.md", "Cargo.toml"],
            "suffixes": [".md", ".toml"],
        }
        docs, _ = collect_files(
            source,
            {
                **self.meta,
                "checkout": str(self.root),
                "repository": "https://github.com/example/demo",
            },
            10000,
        )
        self.assertEqual({d["kind"] for d in docs}, {"doc", "config"})

    def test_rustdoc_hidden_code_is_not_a_document_heading(self):
        doc = document(
            "routing.md",
            "An API example.\n```rust\n# async fn main() {}\n```\n## Routing\nDetails.",
        )
        self.assertEqual(title_of(doc), "Routing")


class RustReleaseTests(unittest.TestCase):
    def test_crate_latest_ignores_prerelease_and_yanked(self):
        record = {
            "versions": [
                {"num": "2.0.0-alpha.1", "yanked": False},
                {"num": "1.3.0", "yanked": True},
                {"num": "1.2.0", "yanked": False},
            ]
        }
        source = {
            "ref": "@crates-stable:demo",
            "repository": "https://github.com/example/demo.git",
            "tag_template": "demo-v{version}",
        }
        with (
            patch("flutter_kb.sources.fetch_json", return_value=record),
            patch("flutter_kb.sources.git", return_value="abc refs/tags/demo-v1.2.0"),
        ):
            self.assertEqual(resolve_ref(source), ("refs/tags/demo-v1.2.0", "1.2.0"))
        with (
            patch("flutter_kb.sources.fetch_json", return_value=record),
            patch("flutter_kb.sources.git", return_value=""),
            self.assertRaisesRegex(KnowledgeError, "refusing to substitute main"),
        ):
            resolve_ref(source)

    def test_books_use_release_submodule_and_validate_repository(self):
        source = {
            "ref": "@rust-stable:src/doc/book",
            "repository": "https://github.com/rust-lang/book.git",
            "_rust_release": "1.98.1",
        }
        record = {"sha": "b" * 40, "submodule_git_url": source["repository"]}
        with patch("flutter_kb.sources.fetch_json", return_value=record):
            self.assertEqual(resolve_ref(source), ("b" * 40, "1.98.1"))
        record["submodule_git_url"] = "https://github.com/other/book.git"
        with (
            patch("flutter_kb.sources.fetch_json", return_value=record),
            self.assertRaisesRegex(KnowledgeError, "does not match"),
        ):
            resolve_ref(source)

    def test_one_stable_release_is_frozen_per_update(self):
        sources = [
            {"ref": "@rust-stable"},
            {"ref": "@rust-stable:src/doc/book"},
            {"ref": "main"},
        ]
        with patch(
            "flutter_kb.sources.rust_stable_version", return_value="1.98.1"
        ) as fetch:
            prepared = prepare_sources(sources)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(prepared[0]["_rust_release"], prepared[1]["_rust_release"])
        self.assertNotIn("_rust_release", sources[0])


class RustDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        folder = self.data / "snapshots" / "test"
        folder.mkdir(parents=True)
        db = sqlite3.connect(folder / "index.sqlite")
        db.close()
        self.sources = [
            {
                "id": "rust-std",
                "version": "1.98.1",
                "commit": "a" * 40,
                "checked_at": "2026-09-11T00:00:00+00:00",
            },
            {
                "id": "tokio",
                "package": "tokio",
                "version": "1.53.1",
                "rust_version": "1.71",
                "checked_at": "2026-09-11T00:00:00+00:00",
            },
            {
                "id": "sqlx",
                "package": "sqlx",
                "version": "0.9.0",
                "rust_version": "1.94",
                "checked_at": "2026-09-11T00:00:00+00:00",
            },
        ]
        (folder / "manifest.json").write_text(
            json.dumps(
                {"schema_version": 1, "sources": self.sources, "snapshot_id": "test"}
            )
        )
        (self.data / "current.json").write_text('{"snapshot_id":"test"}')
        (self.root / "Cargo.toml").write_text(
            '[package]\nname="probe"\nversion="0.1.0"\nedition="2024"\nrust-version="1.90"\n'
        )

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        def command(args, cwd):
            if args[0] == "rustc":
                return (
                    "rustc 1.98.1\nrelease: 1.98.1\ncommit-hash: "
                    + "a" * 40
                    + "\nhost: aarch64-apple-darwin"
                )
            return "stable" if args[0] == "rustup" else "cargo 1.98.1"

        with patch("flutter_kb.rust_support._command", side_effect=command):
            return rust_doctor(self.data, self.root)

    def test_lockfile_keeps_multiple_versions(self):
        lock = self.root / "Cargo.lock"
        lock.write_text(
            'version=4\n[[package]]\nname="tokio"\nversion="1.50.0"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n'
            '[[package]]\nname="tokio"\nversion="1.53.1"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n'
        )
        self.assertEqual(len(cargo_lock_packages(lock)), 2)
        mismatches = self.report()["mismatches"]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["installed"], "1.50.0")

    def test_latest_compiler_does_not_prove_msrv_contract(self):
        (self.root / "Cargo.lock").write_text(
            'version=4\n[[package]]\nname="sqlx"\nversion="0.9.0"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n'
        )
        result = self.report()
        self.assertTrue(
            any(m["component"] == "MSRV contract: sqlx" for m in result["mismatches"])
        )
        self.assertFalse(result["dependency_compatibility_verified"])

    def test_path_or_git_dependency_is_not_verified_by_version(self):
        (self.root / "Cargo.lock").write_text(
            'version=4\n[[package]]\nname="tokio"\nversion="1.53.1"\nsource="git+https://github.com/custom/tokio#abc"\n'
        )
        result = self.report()
        self.assertTrue(result["unverified_dependencies"])
        self.assertFalse(result["indexed_dependency_versions_verified"])


if __name__ == "__main__":
    unittest.main()
