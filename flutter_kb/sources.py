"""Fetch public source repositories without running any downloaded code."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import subprocess
import tomllib
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


class KnowledgeError(Exception):
    """An actionable failure, suitable for CLI output."""


def now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("schema_version") != 1 or not cfg.get("sources"):
        raise KnowledgeError(
            "sources.json requires schema_version=1 and nonempty sources"
        )
    seen = set()
    for source in cfg["sources"]:
        sid = source.get("id", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sid) or sid in seen:
            raise KnowledgeError(f"Invalid or duplicate source id: {sid}")
        seen.add(sid)
        if not re.fullmatch(
            r"https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?", source["repository"]
        ):
            raise KnowledgeError(
                f"Only public HTTPS GitHub repositories are supported: {sid}"
            )
        for entry in source.get("paths", []) + [source.get("license_path", "LICENSE")]:
            parts = PurePosixPath(entry)
            if not entry or parts.is_absolute() or ".." in parts.parts or "\\" in entry:
                raise KnowledgeError(f"Unsafe source path: {entry}")
        if not source.get("paths") or not source.get("suffixes"):
            raise KnowledgeError(f"Source needs paths and suffixes: {sid}")
    cfg["max_file_bytes"] = int(cfg.get("max_file_bytes", 2_000_000))
    if cfg["max_file_bytes"] < 1:
        raise KnowledgeError("max_file_bytes must be positive")
    return cfg


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()


def git(repo: Path | None, *args: str, timeout: int = 300) -> str:
    cmd = ["git", "-c", "core.hooksPath=/dev/null"]
    if repo is not None:
        cmd += ["-C", str(repo)]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    try:
        result = subprocess.run(
            cmd + list(args),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KnowledgeError(f"Git failed: {exc}") from exc
    if result.returncode:
        raise KnowledgeError(
            f"Git {' '.join(args[:2])}: {result.stderr.strip()[-2000:]}"
        )
    return result.stdout.strip()


def fetch_text(url: str) -> str:
    # Respect configured CAs. Python.org macOS installs sometimes lack a default
    # CA bundle; the OS bundle is a verified fallback, never disable TLS checks.
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and not os.environ.get("SSL_CERT_FILE"):
        for candidate in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if Path(candidate).is_file():
                ctx.load_verify_locations(candidate)
                break
    request = urllib.request.Request(
        url, headers={"User-Agent": "local-engineering-knowledge/0.2"}
    )
    try:
        with urllib.request.urlopen(request, context=ctx, timeout=30) as response:
            return response.read().decode("utf-8")
    except (OSError, ValueError) as exc:
        raise KnowledgeError(
            f"Cannot fetch release metadata from {url}: {exc}"
        ) from exc


def fetch_json(url: str) -> dict:
    return json.loads(fetch_text(url))


def rust_stable_version() -> str:
    manifest = tomllib.loads(
        fetch_text("https://static.rust-lang.org/dist/channel-rust-stable.toml")
    )
    version = manifest["pkg"]["rust"]["version"].split()[0]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise KnowledgeError(f"Invalid stable Rust version: {version}")
    return version


def prepare_sources(sources: list[dict]) -> list[dict]:
    """Freeze the stable Rust release once per update, across all its books."""
    if not any(s["ref"].startswith("@rust-stable") for s in sources):
        return sources
    version = rust_stable_version()
    return [
        dict(s, _rust_release=version) if s["ref"].startswith("@rust-stable") else s
        for s in sources
    ]


def resolve_ref(source: dict) -> tuple[str, str]:
    ref = source["ref"]
    if ref == "@rust-stable" or ref.startswith("@rust-stable:"):
        version = source.get("_rust_release") or rust_stable_version()
        if ref == "@rust-stable":
            if (
                source["repository"].removesuffix(".git")
                != "https://github.com/rust-lang/rust"
            ):
                raise KnowledgeError("@rust-stable must refer to rust-lang/rust")
            return f"refs/tags/{version}", version
        submodule = ref.split(":", 1)[1]
        if not re.fullmatch(r"src/(?:doc|tools)/[a-z0-9-]+", submodule):
            raise KnowledgeError(f"Unsupported Rust submodule path: {submodule}")
        record = fetch_json(
            f"https://api.github.com/repos/rust-lang/rust/contents/{submodule}?ref={version}"
        )
        if record.get("submodule_git_url", "").removesuffix(".git") != source[
            "repository"
        ].removesuffix(".git"):
            raise KnowledgeError(
                f"Rust release submodule does not match configured repository: {submodule}"
            )
        commit = record.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise KnowledgeError(f"Invalid release submodule commit: {submodule}")
        return commit, version
    if ref.startswith("@crates-stable:"):
        package = ref.split(":", 1)[1]
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", package):
            raise KnowledgeError("Invalid crate name")
        record = fetch_json(f"https://crates.io/api/v1/crates/{package}")
        versions = [
            v["num"]
            for v in record.get("versions", [])
            if not v.get("yanked") and re.fullmatch(r"\d+\.\d+\.\d+", v["num"])
        ]
        if not versions:
            raise KnowledgeError(f"No non-yanked stable version for crate {package}")
        version = max(versions, key=lambda v: tuple(map(int, v.split("."))))
        tag = source["tag_template"].format(version=version)
        if not re.fullmatch(r"[\w./-]+", tag) or ".." in tag:
            raise KnowledgeError(f"Invalid crate tag: {tag}")
        available = git(
            None,
            "ls-remote",
            "--tags",
            "--refs",
            source["repository"],
            f"refs/tags/{tag}",
        )
        if not available:
            raise KnowledgeError(
                f"No exact release tag {tag} for {package} {version}; refusing to substitute main"
            )
        return f"refs/tags/{tag}", version
    if ref.startswith("@pub-stable:"):
        package = ref.split(":", 1)[1]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", package):
            raise KnowledgeError("Invalid pub.dev package name")
        version = fetch_json(f"https://pub.dev/api/packages/{package}")["latest"][
            "version"
        ]
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:\+[\w.]+)?", version):
            raise KnowledgeError(f"Release is not stable: {version}")
        tags = git(
            None, "ls-remote", "--tags", "--refs", source["repository"], f"*{version}"
        )
        available = {line.split()[1] for line in tags.splitlines() if line.strip()}
        for name in (f"{package}-v{version}", f"v{version}", version):
            if f"refs/tags/{name}" in available:
                return f"refs/tags/{name}", version
        raise KnowledgeError(
            f"No Git tag for {package} {version}; refusing to substitute main"
        )
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", ref) or ".." in ref:
        raise KnowledgeError(f"Invalid Git ref: {ref}")
    release = re.fullmatch(
        r"(?:refs/tags/)?(?:flame-)?v?(\d+\.\d+\.\d+(?:\+[\w.]+)?)", ref
    )
    return ref, release.group(1) if release else ref


def sync_source(source: dict, cache: Path, offline: bool = False) -> dict:
    folder = cache / source["id"]
    repo = folder / "repo"
    meta_path = folder / "source.json"
    if offline:
        if not meta_path.is_file() or not (repo / ".git").is_dir():
            raise KnowledgeError(
                f"No complete cached checkout for {source['id']}; run update"
            )
        meta = json.loads(meta_path.read_text())
        if (
            meta["repository"] != source["repository"]
            or meta["requested_ref"] != source["ref"]
        ):
            raise KnowledgeError(
                f"Cached ref differs for {source['id']}; online update required"
            )
        if git(repo, "rev-parse", "HEAD") != meta["commit"]:
            raise KnowledgeError(
                f"Incomplete checkout for {source['id']}; online update required"
            )
        if git(repo, "status", "--porcelain", "--untracked-files=no"):
            raise KnowledgeError(
                f"Cached tracked files were modified in {source['id']}; run an online update to restore exact source evidence"
            )
        return dict(meta, checkout=str(repo))
    folder.mkdir(parents=True, exist_ok=True)
    ref, version = resolve_ref(source)
    if not (repo / ".git").is_dir():
        repo.mkdir(exist_ok=True)
        git(repo, "init", "-q")
        git(repo, "remote", "add", "origin", source["repository"])
        git(repo, "config", "remote.origin.promisor", "true")
        git(repo, "config", "remote.origin.partialclonefilter", "blob:none")
    elif git(repo, "remote", "get-url", "origin") != source["repository"]:
        raise KnowledgeError(f"Cached repository URL changed for {source['id']}")
    # Cone mode automatically includes top-level files. File paths in the
    # manifest select their parent directory without expanding a root README to
    # a checkout of the entire repository.
    sparse = set()
    for path in source["paths"]:
        parent = str(PurePosixPath(path).parent) if PurePosixPath(path).suffix else path
        if parent != ".":
            sparse.add(parent)
    git(repo, "sparse-checkout", "set", "--cone", *sorted(sparse))
    git(repo, "fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", ref)
    commit = git(repo, "rev-parse", "FETCH_HEAD^{commit}")
    git(repo, "checkout", "--quiet", "--detach", "--force", commit)
    if source["id"] == "flutter-sdk" and source["ref"] == "stable":
        # A branch name alone isn't a release version. Match an official tag to
        # its exact commit; if no tag exists, retain the honest stable@commit label.
        tags = git(
            None,
            "ls-remote",
            "--tags",
            "--refs",
            source["repository"],
            "refs/tags/[0-9]*",
        )
        matches = [
            line.split()[1].removeprefix("refs/tags/")
            for line in tags.splitlines()
            if line.split()[0] == commit
            and re.fullmatch(r"refs/tags/\d+\.\d+\.\d+", line.split()[1])
        ]
        version = (
            max(matches, key=lambda x: tuple(map(int, x.split("."))))
            if matches
            else f"stable@{commit[:12]}"
        )
    meta = {
        "id": source["id"],
        "repository": source["repository"],
        "requested_ref": source["ref"],
        "resolved_ref": ref,
        "version": version,
        "commit": commit,
        "version_policy": source.get("version_policy", "explicit ref"),
        "checked_at": now(),
        "license_url": f"{source['repository'].removesuffix('.git')}/blob/{commit}/{source.get('license_path', 'LICENSE')}",
    }
    if source.get("package"):
        manifest_path = source.get("manifest_path", "Cargo.toml")
        candidate = PurePosixPath(manifest_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise KnowledgeError("Invalid crate manifest path")
        package_manifest = tomllib.loads((repo / manifest_path).read_text())
        package_data = package_manifest["package"]
        workspace = (
            tomllib.loads((repo / "Cargo.toml").read_text())
            .get("workspace", {})
            .get("package", {})
        )

        def inherited(key):
            value = package_data.get(key)
            return (
                workspace.get(key)
                if isinstance(value, dict) and value.get("workspace")
                else value
            )

        if (
            package_data.get("name") != source["package"]
            or inherited("version") != version
        ):
            raise KnowledgeError(
                f"Release tag manifest does not match {source['package']} {version}"
            )
        meta.update(
            package=source["package"],
            rust_version=inherited("rust-version"),
            edition=inherited("edition"),
            manifest_path=manifest_path,
        )
    atomic_json(meta_path, meta)
    return dict(meta, checkout=str(repo))


def collect_files(source: dict, meta: dict, max_bytes: int) -> tuple[list[dict], dict]:
    root = Path(meta["checkout"])
    excluded = {
        ".git",
        ".dart_tool",
        "node_modules",
        "build",
        "assets",
        "images",
        "media",
        "test",
        "test_data",
    }
    skipped = {
        "unsupported": 0,
        "excluded": 0,
        "too_large": 0,
        "symlink": 0,
        "non_utf8": 0,
        "empty": 0,
        "untracked": 0,
    }
    tracked = (
        set(git(root, "ls-files", "-z").split("\0"))
        if (root / ".git").is_dir()
        else None
    )
    docs, seen = [], set()
    base_url = meta["repository"].removesuffix(".git") + "/blob/" + meta["commit"]
    for prefix in source["paths"]:
        start = root / prefix
        if not start.exists():
            raise KnowledgeError(
                f"Configured path disappeared: {source['id']}:{prefix}"
            )
        entries = (
            os.walk(start, followlinks=False)
            if start.is_dir()
            else [(str(start.parent), [], [start.name])]
        )
        for directory, dirs, names in entries:
            skipped["excluded"] += sum(name in excluded for name in dirs)
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in excluded and not (Path(directory) / name).is_symlink()
            )
            for name in sorted(names):
                file = Path(directory) / name
                relative = file.relative_to(root).as_posix()
                if relative in seen:
                    continue
                seen.add(relative)
                if tracked is not None and relative not in tracked:
                    skipped["untracked"] += 1
                    continue
                if file.is_symlink():
                    skipped["symlink"] += 1
                    continue
                if file.suffix not in source["suffixes"]:
                    skipped["unsupported"] += 1
                    continue
                if file.stat().st_size > max_bytes:
                    skipped["too_large"] += 1
                    continue
                raw = file.read_bytes()
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped["non_utf8"] += 1
                    continue
                if not content.strip():
                    skipped["empty"] += 1
                    continue
                kind = (
                    "code"
                    if file.suffix in {".dart", ".rs"}
                    else "config"
                    if file.suffix in {".yaml", ".toml"}
                    else "doc"
                )
                docs.append(
                    {
                        "id": f"{source['id']}:{relative}",
                        "source_id": source["id"],
                        "path": relative,
                        "kind": kind,
                        "content": content,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "url": base_url + "/" + urllib.parse.quote(relative),
                        "version": meta["version"],
                    }
                )
    if not docs:
        raise KnowledgeError(
            f"Source {source['id']} yielded no documents; previous snapshot preserved"
        )
    return docs, skipped
