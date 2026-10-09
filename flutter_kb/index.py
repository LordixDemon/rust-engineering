"""Full text index and deterministic, evidence-bearing graph. No LLM inference."""

from __future__ import annotations

import json
import posixpath
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote, urlparse

from .sources import KnowledgeError

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
CREATE TABLE documents (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), path TEXT NOT NULL,
 kind TEXT NOT NULL, title TEXT NOT NULL, sha256 TEXT NOT NULL, url TEXT NOT NULL,
 version TEXT NOT NULL, content TEXT NOT NULL, line_count INTEGER NOT NULL
);
CREATE TABLE chunks (
 id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
 heading TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, body TEXT NOT NULL
);
CREATE INDEX chunks_document ON chunks(document_id);
CREATE VIRTUAL TABLE search USING fts5(title, heading, body, tokenize='porter unicode61');
CREATE TABLE nodes (
 id TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL,
 document_id TEXT REFERENCES documents(id), source_id TEXT REFERENCES sources(id), line INTEGER
);
CREATE INDEX node_label ON nodes(label COLLATE NOCASE);
CREATE TABLE edges (
 source TEXT NOT NULL REFERENCES nodes(id), target TEXT NOT NULL REFERENCES nodes(id),
 relation TEXT NOT NULL, evidence_document TEXT NOT NULL REFERENCES documents(id),
 line INTEGER NOT NULL, evidence TEXT NOT NULL,
 PRIMARY KEY (source, target, relation)
);
CREATE INDEX edge_target ON edges(target);
"""

TOPICS = {
    "ownership": r"\b(?:ownership|borrowing|lifetimes|borrow checker)\b",
    "async-concurrency": r"\b(?:tokio|backpressure|CancellationToken|JoinSet|spawn_blocking)\b",
    "rust-features": r"\b(?:MSRV|cfg|feature flags|nightly|edition)\b",
    "database": r"\b(?:sqlx|PgPool|transactions|migrations)\b",
    "error-handling": r"\b(?:thiserror|anyhow|ErrorKind|error propagation)\b",
    "unsafe-invariants": r"\b(?:unsafe|soundness|undefined behavior)\b",
    "lifecycle": r"\b(?:onLoad|onMount|onRemove|dispose|mounted)\b",
    "animation": r"\b(?:SpriteAnimation|ScaleEffect|EffectController|AnimationController|tween)\b",
    "particles": r"\b(?:ParticleSystemComponent|Particle|particles)\b",
    "shaders": r"\b(?:FragmentShader|FragmentProgram|PostProcess|shader)\b",
    "input": r"\b(?:DragCallbacks|TapCallbacks|KeyboardEvents|GestureDetector|input)\b",
    "performance": r"\b(?:profiling|frame budget|RepaintBoundary|performance)\b",
    "networking": r"\b(?:WebSocket|reconnect|networking|network)\b",
    "testing": r"\b(?:testWidgets|FlameTester|testGolden|golden|testing)\b",
    "accessibility": r"\b(?:Semantics|accessibility|screen reader)\b",
    "platforms": r"\b(?:Android|iOS|Windows|macOS|Linux)\b",
    "migration": r"\b(?:deprecated|breaking change|migration)\b",
}
DECLARATION = re.compile(
    r"^\s*(?:(?:abstract|base|final|interface|sealed)\s+)*(?:class|mixin|enum|extension(?:\s+type)?|typedef)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
# This is lexical extraction, deliberately not advertised as a Dart analyzer or
# a type-resolved call graph. Keeping newlines preserves exact evidence locations.
DART_TOKENS = re.compile(
    r"//[^\n]*|/\*[\s\S]*?\*/|r?(?:'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
)


def mask_dart(text: str, keep_strings: bool = False) -> str:
    def replace(match: re.Match) -> str:
        value = match.group()
        if keep_strings and not value.startswith(("//", "/*")):
            return value
        return re.sub(r"[^\n]", " ", value)

    return DART_TOKENS.sub(replace, text)


def title_of(doc: dict) -> str:
    if doc["kind"] == "doc":
        text = doc["content"]
        if text.startswith("---\n"):
            frontmatter = text.split("\n---", 1)[0]
            match = re.search(
                r"^title:\s*['\"]?(.+?)['\"]?\s*$", frontmatter, re.MULTILINE
            )
            if match:
                return match.group(1)
        fenced = False
        for line in text.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            match = re.match(r"^#{1,6}\s+(.+)", line) if not fenced else None
            if match:
                return match.group(1)
    return Path(doc["path"]).name


def chunk_lines(
    content: str, max_lines: int = 80, max_chars: int = 6500
) -> list[tuple]:
    lines = content.splitlines()
    chunks, start, heading, fenced = [], 0, "", False
    while start < len(lines):
        end, size, active_heading = start, 0, heading
        while end < len(lines) and end - start < max_lines:
            line = lines[end]
            if end > start and size + len(line) > max_chars:
                break
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            match = re.match(r"^(#{1,6})\s+(.+)", line) if not fenced else None
            if match:
                if end > start + 12:
                    break
                heading = match.group(2)
                active_heading = heading
            size += len(line) + 1
            end += 1
        body = "\n".join(lines[start:end])
        if body.strip():
            chunks.append((active_heading, start + 1, end, body))
        start = end
    return chunks


def _line_at(content: str, offset: int) -> tuple[int, str]:
    number = content.count("\n", 0, offset) + 1
    begin = content.rfind("\n", 0, offset) + 1
    end = content.find("\n", offset)
    return number, content[begin : end if end >= 0 else len(content)][:600]


def build_index(
    path: Path, docs: list[dict], sources: list[dict], previous: Path | None = None
) -> dict:
    old_hashes = {}
    if previous and previous.exists():
        with closing(sqlite3.connect(f"{previous.as_uri()}?mode=ro", uri=True)) as old:
            old_hashes = dict(old.execute("SELECT id, sha256 FROM documents"))
    db = sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO metadata VALUES ('schema_version', '1')")
        for source in sources:
            db.execute(
                "INSERT INTO sources VALUES (?, ?)", (source["id"], json.dumps(source))
            )
        for doc in docs:
            title = title_of(doc)
            db.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc["id"],
                    doc["source_id"],
                    doc["path"],
                    doc["kind"],
                    title,
                    doc["sha256"],
                    doc["url"],
                    doc["version"],
                    doc["content"],
                    len(doc["content"].splitlines()),
                ),
            )
            db.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?)",
                (doc["id"], title, doc["kind"], doc["id"], doc["source_id"], 1),
            )
            for heading, start, end, body in chunk_lines(doc["content"]):
                row = db.execute(
                    "INSERT INTO chunks(document_id, heading, start_line, end_line, body) VALUES (?, ?, ?, ?, ?)",
                    (doc["id"], heading, start, end, body),
                )
                db.execute(
                    "INSERT INTO search(rowid, title, heading, body) VALUES (?, ?, ?, ?)",
                    (row.lastrowid, title, heading, body),
                )
        graph_stats = build_edges(db, docs)
        db.execute("INSERT INTO search(search) VALUES ('optimize')")
        db.commit()
        if (
            db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise KnowledgeError("Index integrity check failed")
        current_ids = {doc["id"] for doc in docs}
        result = {
            "documents": len(docs),
            "chunks": db.execute("SELECT count(*) FROM chunks").fetchone()[0],
            "nodes": db.execute("SELECT count(*) FROM nodes").fetchone()[0],
            "edges": db.execute("SELECT count(*) FROM edges").fetchone()[0],
            "added": sum(doc["id"] not in old_hashes for doc in docs),
            "changed": sum(
                doc["id"] in old_hashes and old_hashes[doc["id"]] != doc["sha256"]
                for doc in docs
            ),
            "unchanged": sum(
                old_hashes.get(doc["id"]) == doc["sha256"] for doc in docs
            ),
            "removed": len(set(old_hashes) - current_ids),
            **graph_stats,
        }
        return result
    finally:
        db.close()


def build_edges(db: sqlite3.Connection, docs: list[dict]) -> dict:
    # Imported here to keep Rust support optional for the existing Dart profile.
    from .rust_support import DECLARATION as RUST_DECLARATION
    from .rust_support import crate_roots, mask_rust, rust_file_links

    rust_roots = crate_roots(docs)
    types = defaultdict(list)
    doc_by_path = {(d["source_id"], d["path"]): d["id"] for d in docs}
    doc_by_url = {d["url"]: d["id"] for d in docs}
    package_roots = {}
    ambiguous = 0

    def edge(
        origin: str, target: str, relation: str, doc: dict, line: int, evidence: str
    ):
        if origin != target:
            db.execute(
                "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?, ?)",
                (origin, target, relation, doc["id"], line, evidence),
            )

    for doc in docs:
        parts = doc["path"].split("/")
        if "lib" in parts and parts.index("lib") > 0:
            i = parts.index("lib")
            package_roots[(doc["source_id"], parts[i - 1])] = "/".join(parts[: i + 1])
        if doc["kind"] != "code":
            continue
        is_rust = doc["path"].endswith(".rs")
        cleaned = mask_rust(doc["content"]) if is_rust else mask_dart(doc["content"])
        declarations = RUST_DECLARATION if is_rust else DECLARATION
        for match in declarations.finditer(cleaned):
            name = match.group(1)
            # unnamed extensions ('extension on T') do not declare a symbol.
            if name in {"on", "type"}:
                continue
            line, evidence = _line_at(doc["content"], match.start(1))
            sid = f"{doc['id']}#symbol:{name}:{line}"
            db.execute(
                "INSERT INTO nodes VALUES (?, ?, 'symbol', ?, ?, ?)",
                (sid, name, doc["id"], doc["source_id"], line),
            )
            types[name].append((sid, doc["source_id"], doc["id"]))
            edge(doc["id"], sid, "declares", doc, line, evidence)
    for topic in TOPICS:
        db.execute(
            "INSERT INTO nodes VALUES (?, ?, 'topic', NULL, NULL, NULL)",
            ("topic:" + topic, topic),
        )

    for doc in docs:
        text = doc["content"]
        is_rust = doc["path"].endswith(".rs")
        scan = (
            mask_rust(text)
            if is_rust
            else mask_dart(text)
            if doc["kind"] == "code"
            else text
        )
        seen = set()
        identifiers = (
            r"\b[A-Za-z_][A-Za-z0-9_]*\b" if is_rust else r"\b[A-Z][A-Za-z0-9_$]*\b"
        )
        for match in re.finditer(identifiers, scan):
            name = match.group()
            if name in seen or name not in types:
                continue
            seen.add(name)
            candidates = types[name]
            local = [item for item in candidates if item[2] == doc["id"]]
            same_source = [item for item in candidates if item[1] == doc["source_id"]]
            candidates = local or same_source or candidates
            if len(candidates) != 1:
                ambiguous += 1
                continue
            target, _, definition_doc = candidates[0]
            if definition_doc == doc["id"]:
                continue
            line, evidence = _line_at(text, match.start())
            edge(doc["id"], target, "mentions_symbol", doc, line, evidence)

        import_scan = (
            mask_dart(text, keep_strings=True) if doc["path"].endswith(".dart") else ""
        )
        if is_rust:
            for target, relation, offset in rust_file_links(
                doc, doc_by_path, rust_roots
            ):
                line, evidence = _line_at(text, offset)
                edge(doc["id"], target, relation, doc, line, evidence)
        for match in re.finditer(
            r"^\s*(import|export|part)\s+['\"]([^'\"]+)['\"]", import_scan, re.MULTILINE
        ):
            relation, link = match.groups()
            path = None
            if link.startswith("package:"):
                package, _, rest = link[8:].partition("/")
                root = package_roots.get((doc["source_id"], package))
                if root:
                    path = root + "/" + rest
            elif not urlparse(link).scheme:
                path = posixpath.normpath(
                    posixpath.join(posixpath.dirname(doc["path"]), link)
                )
            target = doc_by_path.get((doc["source_id"], path))
            if target:
                line, evidence = _line_at(text, match.start(1))
                edge(doc["id"], target, relation + "s", doc, line, evidence)

        if doc["kind"] == "doc":
            links = list(
                re.finditer(r"(?<!!)\[[^\]\n]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", text)
            )
            links += list(re.finditer(r"^\s*\[[^\]\n]+\]:\s*(\S+)", text, re.MULTILINE))
            for match in links:
                link = unquote(match.group(1).strip("<>"))
                parsed = urlparse(link)
                target = doc_by_url.get(link.split("#")[0])
                if (
                    not parsed.scheme
                    and parsed.path
                    and not parsed.path.startswith("/")
                ):
                    path = posixpath.normpath(
                        posixpath.join(posixpath.dirname(doc["path"]), parsed.path)
                    )
                    for candidate in (
                        path,
                        path + ".md",
                        path + "/index.md",
                        path + "/README.md",
                    ):
                        target = doc_by_path.get((doc["source_id"], candidate))
                        if target:
                            break
                if target:
                    line, evidence = _line_at(text, match.start())
                    edge(doc["id"], target, "links_to", doc, line, evidence)
        for topic, pattern in TOPICS.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                line, evidence = _line_at(text, match.start())
                edge(doc["id"], "topic:" + topic, "keyword_tag", doc, line, evidence)
    return {
        "ambiguous_symbol_mentions_skipped": ambiguous,
        "edge_relations": dict(
            db.execute("SELECT relation, count(*) FROM edges GROUP BY relation")
        ),
    }


def export_graph(database: Path, output: Path) -> None:
    with closing(sqlite3.connect(database)) as db:
        db.row_factory = sqlite3.Row
        nodes = [dict(row) for row in db.execute("SELECT * FROM nodes ORDER BY id")]
        edges = [
            dict(row)
            for row in db.execute(
                "SELECT e.*, d.url || '#L' || e.line AS evidence_url FROM edges e JOIN documents d ON d.id=e.evidence_document ORDER BY source, target, relation"
            )
        ]
        graph = {
            "schema_version": 1,
            "directed": True,
            "extraction": "deterministic lexical and explicit links; not semantic inference or a type-resolved call graph",
            "nodes": nodes,
            "edges": edges,
        }
        output.write_text(
            json.dumps(graph, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )


def write_report(path: Path, manifest: dict) -> None:
    lines = [
        "# Knowledge snapshot",
        "",
        f"Built: {manifest['built_at']}",
        f"Snapshot: `{manifest['snapshot_id']}`",
        "",
        "## Coverage",
        "",
        "| Source | Version / channel | Documents | Commit |",
        "|---|---|---:|---|",
    ]
    for source in manifest["sources"]:
        lines.append(
            f"| {source['id']} | {source['version']} | {source['documents']} | `{source['commit']}` |"
        )
    lines += [
        "",
        "## Index and graph",
        "",
        "```json",
        json.dumps(manifest["stats"], indent=2),
        "```",
        "",
        "Edges contain the source document, line and excerpt. `mentions_symbol` is an unambiguous lexical name match, not type resolution. `keyword_tag` is a keyword match, not an inferred architectural relationship.",
        "",
        "LLM extraction: 0 input tokens, 0 output tokens. Embeddings: none. Search: SQLite FTS5/BM25 plus graph traversal.",
        "",
        "## Boundaries",
        "",
        "This is the configured official text/source corpus, not all information on the internet. Binary assets, video, issue discussions, platform SDK manuals and unconfigured third-party packages are outside coverage. See each source's skip counts and license URL in manifest.json.",
        "",
        "Rolling documentation may describe APIs newer than the installed SDK. Compare versions before implementation. Rebuilding offline does not refresh source check timestamps.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
