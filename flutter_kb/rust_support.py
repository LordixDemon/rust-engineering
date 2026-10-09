"""Rust lexical observations and read-only version diagnostics.

This does not replace rustc/rust-analyzer: macros, cfg and type resolution require
the real project's compiler, enabled features and target.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from .sources import KnowledgeError
from .store import open_database, source_age_hours

DECLARATION = re.compile(
    r"^[ \t]*(?:pub(?:\([^\n)]*\))?[ \t]+)?"
    r"(?:(?:async|unsafe|const|default|extern)[ \t]+)*"
    r"(?:struct|enum|trait|fn|type|mod|union|static|const|macro_rules!)[ \t]+"
    r"(?:mut[ \t]+)?(?:r#)?([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)


def mask_rust(text: str) -> str:
    """Mask nested comments and literals while preserving offsets and lifetimes."""
    chars = list(text)
    i, n = 0, len(text)

    def hide(start, end):
        for k in range(start, end):
            if chars[k] != "\n":
                chars[k] = " "

    while i < n:
        start = i
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            i += 2
            depth = 1
            while i < n and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
        else:
            raw = (
                re.match(r'(?:br|cr|r)(#{0,255})"', text[i : i + 260])
                if text[i] in "brc"
                else None
            )
            if raw:
                terminator = '"' + raw.group(1)
                end = text.find(terminator, i + raw.end())
                i = n if end < 0 else end + len(terminator)
            elif text[i] == '"' or text.startswith(('b"', 'c"'), i):
                i += 1 if text[i] == '"' else 2
                while i < n:
                    if text[i] == "\\":
                        i = min(n, i + 2)
                    elif text[i] == '"':
                        i += 1
                        break
                    else:
                        i += 1
            elif text[i] == "'" or text.startswith("b'", i):
                # A lifetime ('a, 'static) has no terminating apostrophe.
                char = re.match(
                    r"b?'(?:\\(?:u\{[0-9a-fA-F_]+\}|x[0-9a-fA-F]{2}|.)|[^'\\\n])'",
                    text[i:],
                )
                if char:
                    i += char.end()
                else:
                    i += 1
                    continue
            else:
                i += 1
                continue
        hide(start, i)
    return "".join(chars)


def crate_roots(docs: list[dict]) -> dict:
    roots = {}
    for doc in docs:
        if not doc["path"].endswith("Cargo.toml"):
            continue
        try:
            manifest = tomllib.loads(doc["content"])
        except tomllib.TOMLDecodeError:
            continue
        package = manifest.get("package", {}).get("name")
        if not isinstance(package, str):
            continue
        parent = posixpath.dirname(doc["path"])
        lib = manifest.get("lib", {}).get("path", "src/lib.rs")
        root_file = posixpath.normpath(posixpath.join(parent, lib))
        roots[(doc["source_id"], package.replace("-", "_"))] = (
            posixpath.dirname(root_file),
            root_file,
        )
    # These standard-library crates are indexed without compiler build manifests.
    for name in ("std", "core", "alloc"):
        if any(d["source_id"] == "rust-std" for d in docs):
            roots[("rust-std", name)] = (
                f"library/{name}/src",
                f"library/{name}/src/lib.rs",
            )
    return roots


def rust_file_links(doc: dict, paths: dict, roots: dict) -> list[tuple[str, str, int]]:
    """Conservative file links; use-path results are explicitly lexical candidates."""
    scan = mask_rust(doc["content"])
    sid, filename = doc["source_id"], doc["path"]
    parent = posixpath.dirname(filename)
    stem = posixpath.basename(filename).removesuffix(".rs")
    module_dir = (
        parent if stem in {"lib", "main", "mod"} else posixpath.join(parent, stem)
    )
    links = []
    for match in re.finditer(
        r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;", scan, re.MULTILINE
    ):
        before = scan[: match.start()]
        # Inline modules and macro-generated module trees need compiler context.
        if before.count("{") != before.count("}"):
            continue
        if re.search(
            r"#\s*\[\s*path\s*=",
            doc["content"][max(0, match.start() - 250) : match.start()],
        ):
            continue
        for candidate in (
            posixpath.join(module_dir, match.group(1) + ".rs"),
            posixpath.join(module_dir, match.group(1), "mod.rs"),
        ):
            if (sid, candidate) in paths:
                links.append((paths[sid, candidate], "module_file", match.start()))
                break
    local_roots = [
        (key, value)
        for key, value in roots.items()
        if key[0] == sid and filename.startswith(value[0] + "/")
    ]
    local_roots.sort(key=lambda item: len(item[1][0]), reverse=True)
    for match in re.finditer(
        r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?use\s+(?:::)?([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)",
        scan,
        re.MULTILINE,
    ):
        parts = match.group(1).split("::")
        if parts[0] == "crate":
            if not local_roots:
                continue
            target_source, root = sid, local_roots[0][1]
        elif parts[0] in {"self", "super"}:
            continue
        else:
            choices = [
                (key, value) for key, value in roots.items() if key[1] == parts[0]
            ]
            local = [item for item in choices if item[0][0] == sid]
            choices = local or choices
            if len(choices) != 1:
                continue
            target_source, root = choices[0][0][0], choices[0][1]
        rest = parts[1:]
        while rest:
            path = posixpath.join(root[0], *rest)
            found = next(
                (
                    paths[target_source, candidate]
                    for candidate in (path + ".rs", path + "/mod.rs")
                    if (target_source, candidate) in paths
                ),
                None,
            )
            if found:
                links.append((found, "use_path_candidate", match.start()))
                break
            rest.pop()
        else:
            if (target_source, root[1]) in paths:
                links.append(
                    (paths[target_source, root[1]], "use_path_candidate", match.start())
                )
    return links


def cargo_lock_packages(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        {k: p[k] for k in ("name", "version", "source") if k in p}
        for p in tomllib.loads(path.read_text()).get("package", [])
    ]


def version_tuple(value: str) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
        return None
    parts = tuple(map(int, value.split(".")))
    return parts + (0,) * (3 - len(parts))


def _command(command: list[str], cwd: Path) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
        env=dict(os.environ, RUSTUP_AUTO_INSTALL="0"),
    )
    if result.returncode:
        raise KnowledgeError(result.stderr.strip()[-1200:])
    return result.stdout.strip()


def rust_doctor(data: Path, project: Path) -> dict:
    db, manifest = open_database(data)
    db.close()
    warnings, mismatches, unverified = [], [], []
    compiler, cargo_version, active = {}, None, None
    try:
        verbose = _command(["rustc", "-Vv"], project)
        compiler = dict(
            line.split(": ", 1) for line in verbose.splitlines() if ": " in line
        )
        cargo_version = _command(["cargo", "--version"], project)
        if shutil.which("rustup"):
            active = _command(["rustup", "show", "active-toolchain"], project)
    except (KnowledgeError, OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"Cannot verify active Rust toolchain: {exc}")
    sources = {source["id"]: source for source in manifest["sources"]}
    indexed = sources.get("rust-std")
    if (
        indexed
        and compiler
        and (
            compiler.get("release") != indexed["version"]
            or compiler.get("commit-hash") != indexed["commit"]
        )
    ):
        mismatches.append(
            {
                "component": "rustc",
                "installed": compiler.get("release"),
                "indexed": indexed["version"],
            }
        )
    lock_path = next(
        (
            folder / "Cargo.lock"
            for folder in [project, *project.parents]
            if (folder / "Cargo.lock").is_file()
        ),
        None,
    )
    packages = cargo_lock_packages(lock_path) if lock_path else []
    for source in manifest["sources"]:
        name = source.get("package")
        if not name:
            continue
        matches = [p for p in packages if p["name"] == name]
        for p in matches:
            if p.get("source", "") not in {
                "registry+https://github.com/rust-lang/crates.io-index",
                "sparse+https://index.crates.io/",
            }:
                unverified.append(
                    {
                        "package": name,
                        "version": p["version"],
                        "reason": "path/git/alternative registry source; registry version alone cannot establish compatibility",
                    }
                )
            elif p["version"] != source["version"]:
                mismatches.append(
                    {
                        "component": name,
                        "installed": p["version"],
                        "indexed": source["version"],
                        "action": "Inspect which workspace dependency uses this version; do not blindly update transitive packages.",
                    }
                )
    project_manifest = project / "Cargo.toml"
    settings = {}
    if project_manifest.is_file():
        settings = tomllib.loads(project_manifest.read_text()).get("package", {})
        if not lock_path:
            warnings.append(
                "No Cargo.lock: exact dependency versions are not verified."
            )
    else:
        warnings.append(
            "No Cargo.toml here; Rust toolchain verified independently of a server project."
        )
    msrv = settings.get("rust-version")
    minimum, installed_version = (
        version_tuple(msrv),
        version_tuple(compiler.get("release")),
    )
    if minimum and installed_version:
        if installed_version < minimum:
            mismatches.append(
                {
                    "component": "project MSRV",
                    "required": msrv,
                    "installed": compiler["release"],
                }
            )
        for source in manifest["sources"]:
            dependency_msrv = version_tuple(source.get("rust_version"))
            uses_version = any(
                p["name"] == source.get("package")
                and p["version"] == source["version"]
                and p.get("source", "").startswith(
                    "registry+https://github.com/rust-lang/crates.io-index"
                )
                for p in packages
            )
            if dependency_msrv and dependency_msrv > minimum and uses_version:
                mismatches.append(
                    {
                        "component": f"MSRV contract: {source['package']}",
                        "project_msrv": msrv,
                        "dependency_msrv": source["rust_version"],
                        "action": "The newest compiler can build this while violating the project's stated minimum compiler contract.",
                    }
                )
    if isinstance(msrv, dict):
        warnings.append(
            "MSRV is inherited from a workspace; inspect workspace.package.rust-version."
        )
    warnings.append(
        "Registry latest versions are not a compatibility solution. Cargo features, target, edition, MSRV and macros require project-level checks."
    )
    indexed_names = {s["package"] for s in manifest["sources"] if s.get("package")}
    return {
        "project": str(project),
        "snapshot_id": manifest["snapshot_id"],
        "age_hours": round(source_age_hours(manifest), 2),
        "rustc": compiler,
        "cargo": cargo_version,
        "active_toolchain": active,
        "project_edition": settings.get("edition", "2015") if settings else None,
        "project_msrv": msrv,
        "lockfile": str(lock_path) if lock_path else None,
        "project_packages": packages,
        "mismatches": mismatches,
        "unverified_dependencies": unverified,
        "warnings": warnings,
        "unindexed_packages": sorted(
            {p["name"] for p in packages if p["name"] not in indexed_names}
        ),
        "sdk_verified": bool(compiler.get("release")),
        "indexed_dependency_versions_verified": any(
            p["name"] in indexed_names for p in packages
        )
        and not mismatches
        and not unverified,
        "dependency_compatibility_verified": False,
    }
