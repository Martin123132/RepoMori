"""Core RepoMori pack format.

The pack is a SQLite database with compressed chunks and small, queryable
machine summaries. Exact source is still recoverable through the chunk map.
"""

from __future__ import annotations

import ast
import base64
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import zlib
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "repomori.pack.v1"
DEFAULT_CHUNK_SIZE = 256 * 1024
DEFAULT_EVAL_QUESTIONS = (
    "Where is the command-line interface defined?",
    "How are files stored, compressed, or restored?",
    "What tests cover the project behavior?",
)
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_SERVER_VERSION = "0.2.0"

SCHEMA_DEFINITIONS = (
    {
        "schema_version": SCHEMA_VERSION,
        "kind": "pack",
        "title": "RepoMori pack metadata",
        "producer": "build_pack",
        "required_fields": ["schema_version", "repo_path", "pack_path", "chunk_size"],
    },
    {
        "schema_version": "repomori.context.v1",
        "kind": "report",
        "title": "Source-backed agent context bundle",
        "producer": "build_context_bundle",
        "required_fields": ["schema_version", "question", "pack", "selection", "sources", "source_manifest"],
    },
    {
        "schema_version": "repomori.diff_context.v1",
        "kind": "report",
        "title": "Source-backed changed-files context bundle",
        "producer": "build_diff_context_bundle",
        "required_fields": ["schema_version", "question", "base_pack", "target_pack", "summary", "selection", "sources", "source_manifest"],
    },
    {
        "schema_version": "repomori.capsule.v1",
        "kind": "report",
        "title": "Dense machine-readable pack capsule",
        "producer": "build_capsule",
        "required_fields": ["schema_version", "key", "pack", "selection", "files", "dictionary", "manifest"],
    },
    {
        "schema_version": "repomori.handoff.v1",
        "kind": "manifest",
        "title": "Agent handoff package manifest",
        "producer": "build_handoff_package",
        "required_fields": ["schema_version", "status", "question", "out_dir", "artifacts", "verification"],
    },
    {
        "schema_version": "repomori.memory.v1",
        "kind": "report",
        "title": "Snapshot memory cycle report",
        "producer": "run_memory_cycle",
        "required_fields": ["schema_version", "status", "repo_path", "out_dir", "settings", "summary", "snapshot", "doctor", "prune", "timeline"],
    },
    {
        "schema_version": "repomori.doctor.v1",
        "kind": "report",
        "title": "Snapshot directory doctor report",
        "producer": "doctor_snapshot_dir",
        "required_fields": ["schema_version", "status", "error_count", "warning_count", "summary", "errors", "warnings"],
    },
    {
        "schema_version": "repomori.prune.v1",
        "kind": "report",
        "title": "Safe snapshot prune plan or result",
        "producer": "prune_snapshots",
        "required_fields": ["schema_version", "applied", "keep", "retained", "candidates", "deleted", "skipped", "errors"],
    },
    {
        "schema_version": "repomori.timeline.v1",
        "kind": "report",
        "title": "Snapshot timeline report",
        "producer": "read_snapshot_timeline",
        "required_fields": ["schema_version", "out_dir", "snapshot_count", "returned_count", "latest", "summary", "snapshots"],
    },
    {
        "schema_version": "repomori.stats.v1",
        "kind": "report",
        "title": "Snapshot incremental savings report",
        "producer": "read_snapshot_stats",
        "required_fields": ["schema_version", "out_dir", "snapshot_count", "returned_count", "summary", "latest", "snapshots", "top_reuse"],
    },
    {
        "schema_version": "repomori.snapshot.v1",
        "kind": "report",
        "title": "Single snapshot build report",
        "producer": "snapshot_repo",
        "required_fields": ["schema_version", "status", "repo_path", "out_dir", "summary", "artifacts", "verify"],
    },
    {
        "schema_version": "repomori.agent.response.v1",
        "kind": "protocol",
        "title": "JSON-lines agent bridge response envelope",
        "producer": "run_agent_bridge",
        "required_fields": ["schema_version", "jsonrpc", "id", "ok"],
    },
    {
        "schema_version": "repomori.agent.help.v1",
        "kind": "protocol",
        "title": "Agent bridge help payload",
        "producer": "agent.help",
        "required_fields": ["schema_version", "protocol", "request", "response", "methods"],
    },
    {
        "schema_version": "repomori.agent.query.v1",
        "kind": "protocol",
        "title": "Agent bridge query wrapper",
        "producer": "query.run",
        "required_fields": ["schema_version", "results"],
    },
    {
        "schema_version": "repomori.agent.file.v1",
        "kind": "protocol",
        "title": "Agent bridge exact file payload",
        "producer": "file.get",
        "required_fields": ["schema_version", "path", "size", "sha256", "is_text", "text", "base64"],
    },
    {
        "schema_version": "repomori.schema.catalog.v1",
        "kind": "catalog",
        "title": "Schema catalog payload",
        "producer": "schema_catalog",
        "required_fields": ["schema_version", "schemas", "agent_methods"],
    },
    {
        "schema_version": "repomori.mcp.tools.v1",
        "kind": "protocol",
        "title": "MCP tool listing payload",
        "producer": "tools/list",
        "required_fields": ["schema_version", "tools"],
    },
    {
        "schema_version": "repomori.demo.v1",
        "kind": "report",
        "title": "Local quickstart demo report",
        "producer": "run_demo",
        "required_fields": ["schema_version", "status", "out_dir", "repo_path", "summary", "artifacts"],
    },
    {
        "schema_version": "repomori.scan.v1",
        "kind": "report",
        "title": "Public safety repository scan",
        "producer": "scan_repository",
        "required_fields": ["schema_version", "status", "repo_path", "settings", "summary", "findings"],
    },
    {
        "schema_version": "repomori.scan.baseline.v1",
        "kind": "config",
        "title": "Public safety scan baseline",
        "producer": "scan_baseline_from_report",
        "required_fields": ["schema_version", "source_schema_version", "repo_path", "created_at", "ignore"],
    },
    {
        "schema_version": "repomori.release_check.v1",
        "kind": "report",
        "title": "Local release readiness check",
        "producer": "run_release_check",
        "required_fields": ["schema_version", "status", "repo_path", "settings", "summary", "checks"],
    },
    {
        "schema_version": "repomori.config.v1",
        "kind": "config",
        "title": "RepoMori TOML config",
        "producer": "init_config",
        "required_fields": ["schema_version", "default_profile", "profiles"],
    },
)

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}

EXCLUDED_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".repomori",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db-wal",
    ".db-shm",
}

SCAN_DEFAULT_MAX_FILE_BYTES = 1024 * 1024
SCAN_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}
SCAN_SECRET_PATTERNS = (
    (
        "private_key",
        "high",
        re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |)?PRIVATE KEY-----"),
        "Private key material appears in source.",
    ),
    (
        "openai_api_key",
        "high",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
        "OpenAI-style API key appears in source.",
    ),
    (
        "github_token",
        "high",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
        "GitHub-style token appears in source.",
    ),
    (
        "aws_access_key",
        "high",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "AWS access key id appears in source.",
    ),
    (
        "generic_secret_assignment",
        "medium",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret|secret|token|password|passwd)\b"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9][A-Za-z0-9_.:/+=@-]{7,})"
        ),
        "Secret-like assignment appears in source.",
    ),
)
SCAN_RISKY_FILENAMES = {
    ".env",
    ".env.development",
    ".env.local",
    ".env.production",
    ".env.test",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
SCAN_GENERATED_DIR_NAMES = {
    "bench",
    "benchmark",
    "benchmarks",
    "handoff",
    "handoffs",
    "pack",
    "packs",
}
SCAN_NOISE_DIR_NAMES = {
    ".cache",
    ".gradle",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".parcel-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".turbo",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
SCAN_LICENSE_NAMES = {"licence", "licence.md", "license", "license.md", "license.txt"}
SCAN_PUBLIC_REQUIRED_FILES = (
    "LICENSE.md",
    "NOTICE.md",
    "COMMERCIAL-LICENSE.md",
    "CONTRIBUTING.md",
    "PUBLIC_RELEASE_CHECKLIST.md",
)
SCAN_BINARY_EXTENSIONS = {
    ".7z",
    ".bmp",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".tar",
    ".webp",
    ".zip",
}
SCAN_PERSONAL_PATH_PATTERNS = (
    (
        "windows_user_path",
        "low",
        re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"'<>|]+"),
        "Local Windows user path appears in source.",
    ),
    (
        "onedrive_path",
        "low",
        re.compile(r"(?i)(?:\\|/|^)OneDrive(?:\\|/)"),
        "OneDrive path appears in source.",
    ),
    (
        "temp_drive_path",
        "low",
        re.compile(r"(?i)\bD:\\Temp\\[^\\\s\"'<>|]*"),
        "D-drive temp path appears in source.",
    ),
)

LANG_BY_EXT = {
    ".bat": "batch",
    ".c": "c",
    ".cc": "cpp",
    ".cfg": "config",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".ini": "config",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".md": "markdown",
    ".mjs": "javascript",
    ".ps1": "powershell",
    ".py": "python",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "but",
    "can",
    "class",
    "const",
    "def",
    "else",
    "for",
    "from",
    "function",
    "has",
    "have",
    "import",
    "into",
    "let",
    "not",
    "return",
    "that",
    "the",
    "this",
    "true",
    "var",
    "was",
    "with",
    "you",
}


@dataclass(frozen=True)
class BuildOptions:
    chunk_size: int = DEFAULT_CHUNK_SIZE
    force: bool = False
    exclude_paths: tuple[Path | str, ...] = ()
    base_pack: Path | str | None = None


def build_pack(repo: Path | str, output: Path | str, options: BuildOptions | None = None) -> dict[str, Any]:
    """Build a `.repomori` pack from a repository folder."""

    opts = options or BuildOptions()
    repo_path = Path(repo).resolve()
    output_path = Path(output).resolve()
    base_pack_path = Path(opts.base_pack).resolve() if opts.base_pack is not None else None
    if not repo_path.is_dir():
        raise ValueError(f"Repository folder not found: {repo_path}")
    if opts.chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if base_pack_path is not None:
        if not base_pack_path.exists():
            raise FileNotFoundError(f"Base pack not found: {base_pack_path}")
        if base_pack_path == output_path:
            raise ValueError("base_pack must not be the same path as output")
    if output_path.exists():
        if not opts.force:
            raise FileExistsError(f"Pack already exists: {output_path}")
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    stats = {
        "schema_version": SCHEMA_VERSION,
        "repo_path": str(repo_path),
        "pack_path": str(output_path),
        "chunk_size": opts.chunk_size,
        "file_count": 0,
        "text_file_count": 0,
        "binary_file_count": 0,
        "logical_bytes": 0,
        "unique_chunks": 0,
        "unique_chunk_raw_bytes": 0,
        "compressed_chunk_bytes": 0,
        "symbol_count": 0,
        "import_count": 0,
        "incremental": base_pack_path is not None,
        "base_pack_path": str(base_pack_path) if base_pack_path is not None else None,
        "base_pack_schema_version": None,
        "base_file_count": 0,
        "reused_file_count": 0,
        "rebuilt_file_count": 0,
        "reused_chunk_count": 0,
    }

    with closing(sqlite3.connect(output_path)) as conn:
        _init_db(conn)
        _put_metadata(
            conn,
            {
                "schema_version": SCHEMA_VERSION,
                "repo_path": str(repo_path),
                "created_at": int(started),
                "chunk_size": opts.chunk_size,
                "base_pack_path": str(base_pack_path) if base_pack_path is not None else None,
            },
        )
        base_conn = _open_pack(base_pack_path) if base_pack_path is not None else None
        try:
            base_files: dict[str, sqlite3.Row] = {}
            if base_conn is not None:
                base_metadata = _metadata(base_conn)
                stats["base_pack_schema_version"] = base_metadata.get("schema_version")
                if stats["base_pack_schema_version"] != SCHEMA_VERSION:
                    raise ValueError(
                        f"Unexpected base pack schema: {stats['base_pack_schema_version']}"
                    )
                base_files = _pack_file_index(base_conn)
                stats["base_file_count"] = len(base_files)

            for path in _iter_repo_files(repo_path, output_path, opts.exclude_paths):
                rel = _normalize_repo_path(path.relative_to(repo_path).as_posix())
                base_row = base_files.get(rel)
                if base_conn is not None and base_row is not None:
                    current_sha256, current_size = _hash_file(path)
                    if current_sha256 == base_row["sha256"] and current_size == base_row["size"]:
                        file_stats = _copy_file_from_base(conn, base_conn, base_row, path)
                        stats["reused_file_count"] += 1
                        stats["reused_chunk_count"] += file_stats.pop("reused_chunk_count", 0)
                    else:
                        file_stats = _ingest_file(conn, repo_path, path, opts.chunk_size)
                        stats["rebuilt_file_count"] += 1
                else:
                    file_stats = _ingest_file(conn, repo_path, path, opts.chunk_size)
                    stats["rebuilt_file_count"] += 1
                for key, value in file_stats.items():
                    stats[key] += value
        finally:
            if base_conn is not None:
                base_conn.close()
        chunk_row = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(raw_size),0) AS raw, COALESCE(SUM(compressed_size),0) AS compressed FROM chunks"
        ).fetchone()
        stats["unique_chunks"] = int(chunk_row[0])
        stats["unique_chunk_raw_bytes"] = int(chunk_row[1])
        stats["compressed_chunk_bytes"] = int(chunk_row[2])
        elapsed = time.time() - started
        stats["elapsed_seconds"] = round(elapsed, 4)
        _put_metadata(conn, {"build_summary": stats})
        if base_pack_path is not None:
            _put_metadata(
                conn,
                {
                    "incremental_base": {
                        "pack_path": str(base_pack_path),
                        "schema_version": stats["base_pack_schema_version"],
                        "file_count": stats["base_file_count"],
                        "reused_file_count": stats["reused_file_count"],
                        "rebuilt_file_count": stats["rebuilt_file_count"],
                    }
                },
            )
        conn.commit()
    stats["pack_bytes"] = output_path.stat().st_size
    return stats


def scan_repository(
    repo: Path | str,
    *,
    max_file_bytes: int = SCAN_DEFAULT_MAX_FILE_BYTES,
    include_hidden: bool = False,
    public_release: bool = False,
    ignore_codes: Iterable[str] = (),
    baseline: Path | str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan a repository for public-release and packing risks without network calls."""

    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository folder not found: {repo_path}")
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be greater than zero")
    ignored_codes = sorted({str(code).strip() for code in ignore_codes if str(code).strip()})
    baseline_entries, baseline_path = _scan_baseline_entries(baseline)

    started = time.time()
    findings: list[dict[str, Any]] = []
    files_scanned = 0
    dirs_checked = 0
    bytes_scanned = 0
    binary_by_dir: Counter[str] = Counter()
    total_by_dir: Counter[str] = Counter()

    _scan_license_posture(repo_path, findings, public_release=public_release)

    for root, dirs, files in os.walk(repo_path):
        root_path = Path(root)
        dirs_checked += 1
        kept_dirs = []
        for dirname in sorted(dirs):
            dir_path = root_path / dirname
            rel_dir = _scan_relpath(repo_path, dir_path)
            lower_name = dirname.lower()
            if lower_name in {".git", "__pycache__"}:
                continue
            if lower_name in SCAN_GENERATED_DIR_NAMES:
                _scan_add(
                    findings,
                    "medium",
                    "generated_artifact_dir",
                    rel_dir,
                    "Generated RepoMori-style artifact directory is present.",
                )
            if lower_name in SCAN_NOISE_DIR_NAMES:
                _scan_add(
                    findings,
                    "low",
                    "dependency_or_build_noise",
                    rel_dir,
                    "Dependency, build, or cache directory is present.",
                )
            if not include_hidden and dirname.startswith(".") and dirname != ".github":
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs

        for filename in sorted(files):
            path = root_path / filename
            if not path.is_file():
                continue
            if not include_hidden and filename.startswith(".") and filename.lower() not in SCAN_RISKY_FILENAMES:
                continue
            rel = _scan_relpath(repo_path, path)
            parent_rel = _scan_relpath(repo_path, path.parent)
            lower_name = filename.lower()

            try:
                size = path.stat().st_size
            except OSError as exc:
                _scan_add(findings, "low", "unreadable_file", rel, f"Could not stat file: {exc}")
                continue

            files_scanned += 1
            total_by_dir[parent_rel] += 1

            if path.suffix.lower() == ".repomori":
                _scan_add(
                    findings,
                    "medium",
                    "repomori_pack_artifact",
                    rel,
                    "Generated .repomori pack is present in the repository tree.",
                    size=size,
                )
            if lower_name in SCAN_RISKY_FILENAMES or lower_name.endswith((".pem", ".key")):
                _scan_add(
                    findings,
                    "high" if lower_name in {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"} else "medium",
                    "risky_secret_filename",
                    rel,
                    "Secret-bearing filename is present in the repository tree.",
                    size=size,
                )
            if size > max_file_bytes:
                _scan_add(
                    findings,
                    "medium",
                    "large_file",
                    rel,
                    f"File is larger than the scan threshold ({max_file_bytes} bytes).",
                    size=size,
                )

            data = _scan_read_prefix(path, max_file_bytes)
            if data is None:
                _scan_add(findings, "low", "unreadable_file", rel, "Could not read file bytes.", size=size)
                continue
            bytes_scanned += len(data)
            text = _decode_text(data)
            if text is None:
                binary_by_dir[parent_rel] += 1
                if path.suffix.lower() in SCAN_BINARY_EXTENSIONS:
                    _scan_add(
                        findings,
                        "info",
                        "binary_file",
                        rel,
                        "Binary file is present; confirm it belongs in a public source repo.",
                        size=size,
                    )
                continue

            _scan_text_for_secrets(rel, text, findings)
            _scan_text_for_personal_paths(rel, text, findings)

    _scan_binary_heavy_dirs(binary_by_dir, total_by_dir, findings)
    public_report = _scan_public_release_report(repo_path, findings, enabled=public_release)
    active_findings, ignored_findings = _scan_filter_findings(
        findings,
        ignore_codes=ignored_codes,
        baseline_entries=baseline_entries,
    )
    summary = _scan_summary(active_findings)
    summary.update(
        {
            "files_scanned": files_scanned,
            "dirs_checked": dirs_checked,
            "bytes_scanned": bytes_scanned,
            "raw_findings": len(findings),
            "ignored_findings": len(ignored_findings),
            "generated_artifacts": sum(1 for item in active_findings if item["code"] in {"generated_artifact_dir", "repomori_pack_artifact"}),
            "secret_findings": sum(1 for item in active_findings if item["code"] in {"private_key", "openai_api_key", "github_token", "aws_access_key", "generic_secret_assignment", "risky_secret_filename"}),
            "license_findings": sum(1 for item in active_findings if item["code"] in {"missing_license", "private_license_metadata", "missing_public_release_file"}),
            "elapsed_seconds": round(time.time() - started, 4),
        }
    )
    status = "fail" if summary["high"] else "warn" if summary["findings"] else "pass"
    report = {
        "schema_version": "repomori.scan.v1",
        "status": status,
        "repo_path": str(repo_path),
        "settings": {
            "max_file_bytes": max_file_bytes,
            "include_hidden": include_hidden,
            "public_release": public_release,
            "ignore_codes": ignored_codes,
            "baseline_path": baseline_path,
        },
        "summary": summary,
        "findings": active_findings,
        "ignored_findings": ignored_findings,
    }
    if public_report is not None:
        report["public_release"] = public_report
    return report


def run_release_check(
    repo: Path | str,
    *,
    baseline: Path | str | None = None,
    fail_on: str = "low",
    public_release: bool = True,
    run_tests: bool = True,
    run_demo_smoke: bool = True,
    demo_out: Path | str | None = None,
    keep_demo: bool = False,
    tests_dir: Path | str = "tests",
) -> dict[str, Any]:
    """Run the local pre-release readiness loop."""

    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository folder not found: {repo_path}")
    if fail_on not in SCAN_SEVERITY_ORDER:
        raise ValueError("fail_on must be one of: info, low, medium, high")

    started = time.time()
    baseline_path = Path(baseline).resolve() if baseline is not None else repo_path / ".repomori-scan-baseline.json"
    baseline_arg: Path | None = baseline_path if baseline_path.exists() else None

    schema_check = _release_schema_check()
    scan = scan_repository(
        repo_path,
        public_release=public_release,
        baseline=baseline_arg,
    )
    scan_ok = not _scan_has_severity_threshold(scan, fail_on)
    tests_check = _release_tests_check(repo_path, tests_dir) if run_tests else _release_skipped_check("tests")
    demo_check = (
        _release_demo_check(repo_path, demo_out=demo_out, keep_demo=keep_demo)
        if run_demo_smoke
        else _release_skipped_check("demo")
    )

    checks = {
        "schema": schema_check,
        "scan": {
            "name": "scan",
            "ok": scan_ok,
            "status": "pass" if scan_ok else "fail",
            "fail_on": fail_on,
            "baseline_path": str(baseline_arg) if baseline_arg else None,
            "summary": scan.get("summary", {}),
            "report": scan,
        },
        "tests": tests_check,
        "demo": demo_check,
    }
    failed = [name for name, check in checks.items() if not check.get("ok")]
    status = "pass" if not failed else "fail"
    elapsed = round(time.time() - started, 4)
    return {
        "schema_version": "repomori.release_check.v1",
        "status": status,
        "repo_path": str(repo_path),
        "settings": {
            "baseline": str(baseline_path) if baseline_path.exists() else None,
            "fail_on": fail_on,
            "public_release": public_release,
            "run_tests": run_tests,
            "run_demo_smoke": run_demo_smoke,
            "demo_out": str(Path(demo_out).resolve()) if demo_out is not None else None,
            "keep_demo": keep_demo,
            "tests_dir": str(tests_dir),
        },
        "summary": {
            "elapsed_seconds": elapsed,
            "failed_checks": failed,
            "scan_findings": scan.get("summary", {}).get("findings"),
            "scan_ignored_findings": scan.get("summary", {}).get("ignored_findings"),
            "tests_returncode": tests_check.get("returncode"),
            "demo_status": demo_check.get("demo_status"),
        },
        "checks": checks,
    }


def info_pack(pack: Path | str) -> dict[str, Any]:
    """Return compact metadata about a RepoMori pack."""

    pack_path = Path(pack)
    with closing(_open_pack(pack_path)) as conn:
        metadata = _metadata(conn)
        counts = {
            "files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "symbols": conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
            "imports": conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0],
            "index_rows": conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0],
        }
        file_row = conn.execute(
            "SELECT COALESCE(SUM(size),0), COALESCE(SUM(is_text),0), COALESCE(SUM(1-is_text),0) FROM files"
        ).fetchone()
        chunk_row = conn.execute(
            "SELECT COALESCE(SUM(raw_size),0), COALESCE(SUM(compressed_size),0) FROM chunks"
        ).fetchone()
    logical = int(file_row[0])
    pack_bytes = pack_path.stat().st_size
    unique_raw = int(chunk_row[0])
    compressed = int(chunk_row[1])
    return {
        "schema_version": metadata.get("schema_version"),
        "repo_path": metadata.get("repo_path"),
        "created_at": metadata.get("created_at"),
        "pack_path": str(pack_path.resolve()),
        "pack_bytes": pack_bytes,
        "logical_bytes": logical,
        "unique_chunk_raw_bytes": unique_raw,
        "compressed_chunk_bytes": compressed,
        "logical_to_pack_ratio": round(logical / pack_bytes, 3) if pack_bytes else None,
        "dedupe_ratio": round(logical / unique_raw, 3) if unique_raw else None,
        "counts": counts,
        "text_files": int(file_row[1]),
        "binary_files": int(file_row[2]),
    }


def tree_pack(pack: Path | str, limit: int = 200) -> list[dict[str, Any]]:
    """Return file rows from the pack."""

    with closing(_open_pack(pack)) as conn:
        rows = conn.execute(
            """
            SELECT path, language, size, sha256, is_text, line_count, token_count
            FROM files
            ORDER BY path
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_dict(row) for row in rows]


def verify_pack(pack: Path | str) -> dict[str, Any]:
    """Verify compressed chunks, file hashes, and source recovery for a pack."""

    pack_path = Path(pack)
    started = time.time()
    errors: list[dict[str, Any]] = []
    checked_file_bytes = 0
    checked_chunk_raw_bytes = 0

    with closing(_open_pack(pack_path)) as conn:
        metadata = _metadata(conn)
        pack_schema = metadata.get("schema_version")
        if pack_schema != SCHEMA_VERSION:
            _add_verify_error(
                errors,
                "metadata",
                None,
                "Unexpected pack schema version.",
                expected=SCHEMA_VERSION,
                actual=pack_schema,
            )

        chunk_rows = conn.execute(
            "SELECT id, compressor, raw_size, compressed_size, data FROM chunks ORDER BY id"
        ).fetchall()
        for row in chunk_rows:
            chunk_id = row["id"]
            data = row["data"]
            if len(data) != row["compressed_size"]:
                _add_verify_error(
                    errors,
                    "chunk",
                    chunk_id,
                    "Compressed chunk size does not match stored metadata.",
                    expected=row["compressed_size"],
                    actual=len(data),
                )
            try:
                block = _decompress_chunk(row["compressor"], data)
            except (ValueError, zlib.error) as exc:
                _add_verify_error(errors, "chunk", chunk_id, f"Chunk decompression failed: {exc}")
                continue
            checked_chunk_raw_bytes += len(block)
            if len(block) != row["raw_size"]:
                _add_verify_error(
                    errors,
                    "chunk",
                    chunk_id,
                    "Raw chunk size does not match stored metadata.",
                    expected=row["raw_size"],
                    actual=len(block),
                )
            actual_chunk_id = hashlib.sha256(block).hexdigest()
            if actual_chunk_id != chunk_id:
                _add_verify_error(
                    errors,
                    "chunk",
                    chunk_id,
                    "Chunk id does not match decompressed bytes.",
                    expected=chunk_id,
                    actual=actual_chunk_id,
                )

        file_rows = conn.execute(
            "SELECT path, size, sha256, chunk_count FROM files ORDER BY path"
        ).fetchall()
        for row in file_rows:
            path = row["path"]
            chunk_links = conn.execute(
                """
                SELECT
                    fc.chunk_index,
                    fc.chunk_id,
                    fc.raw_size AS file_raw_size,
                    fc.sha256 AS file_sha256,
                    c.compressor,
                    c.data
                FROM file_chunks fc
                LEFT JOIN chunks c ON c.id = fc.chunk_id
                WHERE fc.path=?
                ORDER BY fc.chunk_index
                """,
                (path,),
            ).fetchall()
            if len(chunk_links) != row["chunk_count"]:
                _add_verify_error(
                    errors,
                    "file",
                    path,
                    "File chunk count does not match stored metadata.",
                    expected=row["chunk_count"],
                    actual=len(chunk_links),
                )

            parts = []
            for chunk in chunk_links:
                if chunk["data"] is None:
                    _add_verify_error(errors, "file", path, "File references a missing chunk.")
                    continue
                try:
                    block = _decompress_chunk(chunk["compressor"], chunk["data"])
                except (ValueError, zlib.error) as exc:
                    _add_verify_error(errors, "file", path, f"File chunk decompression failed: {exc}")
                    continue
                block_hash = hashlib.sha256(block).hexdigest()
                if len(block) != chunk["file_raw_size"]:
                    _add_verify_error(
                        errors,
                        "file",
                        path,
                        "File chunk raw size does not match link metadata.",
                        expected=chunk["file_raw_size"],
                        actual=len(block),
                    )
                if block_hash != chunk["file_sha256"]:
                    _add_verify_error(
                        errors,
                        "file",
                        path,
                        "File chunk hash does not match link metadata.",
                        expected=chunk["file_sha256"],
                        actual=block_hash,
                    )
                if block_hash != chunk["chunk_id"]:
                    _add_verify_error(
                        errors,
                        "file",
                        path,
                        "File chunk hash does not match referenced chunk id.",
                        expected=chunk["chunk_id"],
                        actual=block_hash,
                    )
                parts.append(block)

            file_data = b"".join(parts)
            checked_file_bytes += len(file_data)
            if len(file_data) != row["size"]:
                _add_verify_error(
                    errors,
                    "file",
                    path,
                    "Restored file size does not match stored metadata.",
                    expected=row["size"],
                    actual=len(file_data),
                )
            actual_file_hash = hashlib.sha256(file_data).hexdigest()
            if actual_file_hash != row["sha256"]:
                _add_verify_error(
                    errors,
                    "file",
                    path,
                    "Restored file hash does not match stored metadata.",
                    expected=row["sha256"],
                    actual=actual_file_hash,
                )

    elapsed = time.time() - started
    return {
        "schema_version": "repomori.verify.v1",
        "pack_path": str(pack_path.resolve()),
        "pack_schema_version": pack_schema,
        "verified": not errors,
        "error_count": len(errors),
        "checked_files": len(file_rows),
        "checked_chunks": len(chunk_rows),
        "checked_file_bytes": checked_file_bytes,
        "checked_chunk_raw_bytes": checked_chunk_raw_bytes,
        "elapsed_seconds": round(elapsed, 4),
        "errors": errors,
    }


def query_pack(pack: Path | str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Query pack indexes and return scored matching files."""

    scored = _score_pack_query(pack, query)
    return _ranked_query_results(scored, limit)


def diagnose_query(
    pack: Path | str,
    question: str,
    limit: int = 8,
    snippet_lines: int = 12,
    max_bytes: int | None = None,
    snippets_per_file: int = 2,
) -> dict[str, Any]:
    """Explain query ranking and snippet anchor selection for a pack."""

    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if snippet_lines <= 0:
        raise ValueError("snippet_lines must be greater than zero")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be zero or greater")
    if snippets_per_file < 0:
        raise ValueError("snippets_per_file must be zero or greater")

    started = time.time()
    pack_info = info_pack(pack)
    scored = _score_pack_query(pack, question)
    selected = _ranked_query_results(scored, limit)
    sources = []
    remaining_bytes = max_bytes
    source_bytes = 0

    for rank, result in enumerate(selected, start=1):
        snippets, snippet_status, used_bytes = _snippets_for_result(
            pack,
            question,
            result,
            snippet_lines,
            snippets_per_file,
            remaining_bytes,
            True,
        )
        source_bytes += used_bytes
        if remaining_bytes is not None:
            remaining_bytes = max(0, remaining_bytes - used_bytes)
        anchors, anchor_status = _diagnose_snippet_anchors(pack, question, result)
        path = result["path"]
        matched = set(scored["matched_tokens"].get(path, set()))
        missed = [token for token in scored["tokens"] if token not in matched]
        effective_snippet_status = anchor_status if snippet_status == "no_snippet" else snippet_status
        sources.append(
            {
                "rank": rank,
                "path": path,
                "language": result.get("language"),
                "size": result.get("size"),
                "sha256": result.get("sha256"),
                "score": result.get("score"),
                "why": result.get("why", []),
                "matched_tokens": sorted(matched),
                "missed_tokens": missed,
                "summary": result.get("summary", {}),
                "score_breakdown": scored["breakdown"].get(path, []),
                "snippet_status": effective_snippet_status,
                "snippet_count": len(snippets),
                "source_bytes": used_bytes,
                "snippet_anchors": anchors,
                "snippets": snippets,
            }
        )

    return {
        "schema_version": "repomori.diagnose.v1",
        "question": question,
        "pack": {
            "schema_version": pack_info.get("schema_version"),
            "repo_path": pack_info.get("repo_path"),
            "pack_path": pack_info.get("pack_path"),
            "created_at": pack_info.get("created_at"),
            "logical_bytes": pack_info.get("logical_bytes"),
            "pack_bytes": pack_info.get("pack_bytes"),
            "counts": pack_info.get("counts", {}),
        },
        "query": {
            "tokens": scored["tokens"],
            "phrases": scored["phrases"],
            "limit": limit,
        },
        "settings": {
            "snippet_lines": snippet_lines,
            "max_bytes": max_bytes,
            "snippets_per_file": snippets_per_file,
        },
        "summary": {
            "selected_count": len(sources),
            "source_bytes": source_bytes,
            "elapsed_seconds": round(time.time() - started, 4),
        },
        "selected_files": sources,
        "ranking_notes": _diagnose_ranking_notes(sources),
        "suggestions": _diagnose_suggestions(scored["tokens"], sources),
    }


def _score_pack_query(pack: Path | str, query: str) -> dict[str, Any]:
    tokens = _query_tokens(query)
    phrases = _query_phrases(query, tokens)
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, set[str]] = defaultdict(set)
    matched_tokens: dict[str, set[str]] = defaultdict(set)
    breakdown: dict[str, list[dict[str, Any]]] = defaultdict(list)

    if not tokens:
        return {
            "tokens": [],
            "phrases": [],
            "scores": scores,
            "reasons": reasons,
            "matched_tokens": matched_tokens,
            "breakdown": breakdown,
            "file_rows": [],
        }

    with closing(_open_pack(pack)) as conn:
        files = conn.execute(
            "SELECT path, language, size, sha256, summary_json FROM files"
        ).fetchall()
        for row in files:
            path = row["path"]
            _score_query_value(
                path,
                "path",
                path,
                tokens,
                phrases,
                _field_weight("path"),
                scores,
                reasons,
                matched_tokens,
                breakdown,
            )
            _score_query_value(
                path,
                "basename",
                Path(path).stem,
                tokens,
                phrases,
                _field_weight("basename"),
                scores,
                reasons,
                matched_tokens,
                breakdown,
            )
            if row["language"]:
                _score_query_value(
                    path,
                    "language",
                    str(row["language"]),
                    tokens,
                    phrases,
                    _field_weight("language"),
                    scores,
                    reasons,
                    matched_tokens,
                    breakdown,
                )

        index_rows = conn.execute("SELECT path, field, value FROM search_index").fetchall()
        for row in index_rows:
            field = str(row["field"])
            _score_query_value(
                row["path"],
                field,
                str(row["value"] or ""),
                tokens,
                phrases,
                _field_weight(field),
                scores,
                reasons,
                matched_tokens,
                breakdown,
            )

        for path, matches in matched_tokens.items():
            coverage = len(matches) / len(tokens)
            added = coverage * 3.0
            scores[path] += added
            reason = "all-query-terms" if coverage == 1.0 else "partial-query-terms"
            reasons[path].add(reason)
            breakdown[path].append(
                {
                    "field": "coverage",
                    "kind": reason,
                    "matched_tokens": sorted(matches),
                    "weight": round(added, 2),
                }
            )

        file_rows = []
        if scores:
            placeholders = ",".join("?" for _ in scores)
            file_rows = conn.execute(
                f"SELECT path, language, size, sha256, summary_json FROM files WHERE path IN ({placeholders})",
                tuple(scores),
            ).fetchall()

    return {
        "tokens": tokens,
        "phrases": phrases,
        "scores": scores,
        "reasons": reasons,
        "matched_tokens": matched_tokens,
        "breakdown": breakdown,
        "file_rows": file_rows,
    }


def _ranked_query_results(scored: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    results = []
    scores = scored["scores"]
    reasons = scored["reasons"]
    for row in scored["file_rows"]:
        summary = _safe_json(row["summary_json"], {})
        path = row["path"]
        results.append(
            {
                "path": path,
                "score": round(scores[path], 2),
                "why": sorted(reasons[path]),
                "language": row["language"],
                "size": row["size"],
                "sha256": row["sha256"],
                "summary": _compact_summary(summary),
            }
        )
    return sorted(results, key=lambda item: (-item["score"], item["path"]))[:limit]


def _diagnose_snippet_anchors(
    pack: Path | str,
    question: str,
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    data = get_file_bytes(pack, str(result["path"]))
    text = _decode_text(data)
    if text is None:
        return [], "binary_or_undecodable"
    lines = text.splitlines()
    if not lines:
        return [], "empty_text"
    anchors = []
    for line_no, matched in _snippet_anchors(question, result, lines)[:8]:
        anchors.append(
            {
                "line": line_no,
                "matched": matched,
                "preview": lines[line_no - 1].strip(),
            }
        )
    return anchors, "text" if anchors else "no_snippet"


def _diagnose_ranking_notes(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notes = []
    for higher, lower in zip(sources, sources[1:]):
        higher_reasons = set(higher.get("why", []))
        lower_reasons = set(lower.get("why", []))
        notes.append(
            {
                "higher": higher.get("path"),
                "lower": lower.get("path"),
                "score_delta": round(float(higher.get("score") or 0) - float(lower.get("score") or 0), 2),
                "higher_unique_reasons": sorted(higher_reasons - lower_reasons),
                "lower_unique_reasons": sorted(lower_reasons - higher_reasons),
                "higher_matched_tokens": higher.get("matched_tokens", []),
                "lower_matched_tokens": lower.get("matched_tokens", []),
            }
        )
    return notes


def _diagnose_suggestions(tokens: list[str], sources: list[dict[str, Any]]) -> list[str]:
    suggestions = []
    if not sources:
        return ["No files matched. Try a path, symbol, import, heading, or repository-specific term."]
    if sources and float(sources[0].get("score") or 0) < 4.0:
        suggestions.append("Top match is weak. Add a repository-specific path, symbol, import, or heading term.")
    missed = sorted({token for source in sources for token in source.get("missed_tokens", [])})
    if missed and tokens:
        suggestions.append("Some selected files missed query terms: " + ", ".join(missed[:8]) + ".")
    if any(source.get("snippet_status") == "budget_exhausted" for source in sources):
        suggestions.append("Snippet source budget was exhausted. Increase --max-bytes or lower --max-files.")
    if any(source.get("snippet_count") == 0 and source.get("snippet_status") == "text" for source in sources):
        suggestions.append("Text files were selected but no snippets were emitted. Increase --snippets-per-file.")
    if any("symbol" not in source.get("why", []) for source in sources):
        suggestions.append("At least one selected file had no symbol match. Add a class or function name when you need code-specific ranking.")
    return _unique_items(suggestions)


def build_context_bundle(
    pack: Path | str,
    question: str,
    limit: int = 8,
    snippet_lines: int = 12,
    max_bytes: int | None = None,
    snippets_per_file: int = 2,
    include_source: bool = True,
) -> dict[str, Any]:
    """Build a compact source-backed context bundle for an AI agent."""

    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if snippet_lines <= 0:
        raise ValueError("snippet_lines must be greater than zero")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be zero or greater")
    if snippets_per_file < 0:
        raise ValueError("snippets_per_file must be zero or greater")

    pack_info = info_pack(pack)
    selected = query_pack(pack, question, limit=limit)
    sources = []
    remaining_bytes = max_bytes
    source_bytes = 0
    for result in selected:
        snippets, status, used_bytes = _snippets_for_result(
            pack,
            question,
            result,
            snippet_lines,
            snippets_per_file,
            remaining_bytes,
            include_source,
        )
        source_bytes += used_bytes
        if remaining_bytes is not None:
            remaining_bytes = max(0, remaining_bytes - used_bytes)
        source = {
            "path": result["path"],
            "language": result.get("language"),
            "size": result.get("size"),
            "sha256": result.get("sha256"),
            "score": result.get("score"),
            "why": result.get("why", []),
            "summary": result.get("summary", {}),
            "snippet_status": status,
            "source_bytes": used_bytes,
            "snippets": snippets,
        }
        sources.append(source)

    return {
        "schema_version": "repomori.context.v1",
        "question": question,
        "pack": {
            "schema_version": pack_info.get("schema_version"),
            "repo_path": pack_info.get("repo_path"),
            "pack_path": pack_info.get("pack_path"),
            "created_at": pack_info.get("created_at"),
            "logical_bytes": pack_info.get("logical_bytes"),
            "pack_bytes": pack_info.get("pack_bytes"),
            "counts": pack_info.get("counts", {}),
        },
        "selection": {
            "limit": limit,
            "snippet_lines": snippet_lines,
            "max_bytes": max_bytes,
            "snippets_per_file": snippets_per_file,
            "include_source": include_source,
            "selected_count": len(sources),
            "source_bytes": source_bytes,
        },
        "sources": sources,
        "source_manifest": [
            {
                "path": source["path"],
                "sha256": source["sha256"],
                "size": source["size"],
                "snippet_count": len(source["snippets"]),
                "snippet_status": source["snippet_status"],
                "source_bytes": source["source_bytes"],
            }
            for source in sources
        ],
    }


def format_context_markdown(bundle: dict[str, Any]) -> str:
    """Render a context bundle as source-backed Markdown."""

    pack = bundle.get("pack", {})
    selection = bundle.get("selection", {})
    sources = bundle.get("sources", [])
    lines = [
        "# RepoMori Agent Context",
        "",
        f"Question: {bundle.get('question', '')}",
        "",
        "## Pack",
        "",
        f"- Schema: `{pack.get('schema_version')}`",
        f"- Repository: `{pack.get('repo_path')}`",
        f"- Pack: `{pack.get('pack_path')}`",
        f"- Logical bytes: `{pack.get('logical_bytes')}`",
        f"- Pack bytes: `{pack.get('pack_bytes')}`",
        f"- Selected sources: `{selection.get('selected_count', len(sources))}`",
        f"- Source bytes: `{selection.get('source_bytes', 0)}`",
        "",
        "## Selected Sources",
        "",
    ]
    if not sources:
        lines.extend(["No matching sources were found.", ""])
    for source in sources:
        summary = source.get("summary", {})
        lines.extend(
            [
                f"### {source.get('path')}",
                "",
                f"- Score: `{source.get('score')}`",
                f"- Language: `{source.get('language') or 'unknown'}`",
                f"- Size: `{source.get('size')}`",
                f"- SHA-256: `{source.get('sha256')}`",
                f"- Match reasons: `{', '.join(source.get('why', [])) or 'none'}`",
                f"- Snippet status: `{source.get('snippet_status')}`",
                f"- Source bytes: `{source.get('source_bytes', 0)}`",
                "",
            ]
        )
        top_terms = summary.get("top_terms") or []
        if top_terms:
            lines.extend([f"- Top terms: `{', '.join(top_terms)}`", ""])
        snippets = source.get("snippets", [])
        if not snippets:
            lines.extend(["No text snippets available.", ""])
            continue
        for snippet in snippets:
            language = source.get("language") or ""
            lines.extend(
                [
                    f"Lines {snippet['start_line']}-{snippet['end_line']} ({snippet['matched']}):",
                    "",
                    f"```{_markdown_fence_language(language)}",
                    snippet["text"],
                    "```",
                    "",
                ]
            )

    lines.extend(["## Source Manifest", ""])
    manifest = bundle.get("source_manifest", [])
    if not manifest:
        lines.extend(["No sources selected.", ""])
    else:
        for item in manifest:
            lines.append(
                f"- `{item.get('path')}` sha256=`{item.get('sha256')}` "
                f"size=`{item.get('size')}` snippets=`{item.get('snippet_count')}` "
                f"source_bytes=`{item.get('source_bytes', 0)}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_diff_context_bundle(
    base_pack: Path | str,
    target_pack: Path | str,
    question: str = "what changed?",
    *,
    limit: int = 8,
    snippet_lines: int = 12,
    max_bytes: int | None = None,
    snippets_per_file: int = 2,
    include_source: bool = True,
) -> dict[str, Any]:
    """Build a source-backed context bundle for files changed between two packs."""

    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if snippet_lines <= 0:
        raise ValueError("snippet_lines must be greater than zero")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be zero or greater")
    if snippets_per_file < 0:
        raise ValueError("snippets_per_file must be zero or greater")
    if not question.strip():
        raise ValueError("question must not be empty")

    started = time.time()
    base_info = info_pack(base_pack)
    target_info = info_pack(target_pack)
    base_records = _pack_file_records(base_pack)
    target_records = _pack_file_records(target_pack)
    base_paths = set(base_records)
    target_paths = set(target_records)
    added_paths = sorted(target_paths - base_paths)
    removed_paths = sorted(base_paths - target_paths)
    shared_paths = sorted(base_paths & target_paths)
    changed_paths = [
        path
        for path in shared_paths
        if _compare_record_reasons(base_records[path], target_records[path])
    ]
    unchanged_count = len(shared_paths) - len(changed_paths)

    candidates = []
    for path in added_paths:
        record = target_records[path]
        candidates.append(
            _diff_context_candidate(
                "added",
                record,
                question,
                change_reasons=["added"],
                before=None,
                after=record,
            )
        )
    for path in changed_paths:
        before = base_records[path]
        after = target_records[path]
        reasons = _compare_record_reasons(before, after)
        candidates.append(
            _diff_context_candidate(
                "changed",
                after,
                question,
                change_reasons=reasons,
                before=before,
                after=after,
                summary_delta=_summary_delta(before.get("_summary", {}), after.get("_summary", {})),
            )
        )
    for path in removed_paths:
        record = base_records[path]
        candidates.append(
            _diff_context_candidate(
                "removed",
                record,
                question,
                change_reasons=["removed"],
                before=record,
                after=None,
            )
        )

    candidates.sort(key=lambda item: (-float(item["score"]), _diff_context_type_order(item["change_type"]), item["path"]))
    selected = candidates[:limit]
    remaining_bytes = max_bytes
    source_bytes = 0
    sources = []
    for item in selected:
        source_pack = target_pack if item["source_pack"] == "target" else base_pack
        peer_pack = base_pack if item["change_type"] == "changed" else None
        snippets, status, used_bytes = _diff_context_snippets(
            source_pack,
            question,
            item,
            snippet_lines,
            snippets_per_file,
            remaining_bytes,
            include_source,
            peer_pack=peer_pack,
        )
        source_bytes += used_bytes
        if remaining_bytes is not None:
            remaining_bytes = max(0, remaining_bytes - used_bytes)
        source = dict(item)
        source.update(
            {
                "snippet_status": status,
                "source_bytes": used_bytes,
                "snippets": snippets,
            }
        )
        sources.append(source)

    return {
        "schema_version": "repomori.diff_context.v1",
        "question": question,
        "base_pack": _pack_identity(base_info),
        "target_pack": _pack_identity(target_info),
        "settings": {
            "limit": limit,
            "snippet_lines": snippet_lines,
            "max_bytes": max_bytes,
            "snippets_per_file": snippets_per_file,
            "include_source": include_source,
        },
        "summary": {
            "added_count": len(added_paths),
            "removed_count": len(removed_paths),
            "changed_count": len(changed_paths),
            "unchanged_count": unchanged_count,
            "selected_count": len(sources),
            "source_bytes": source_bytes,
            "elapsed_seconds": round(time.time() - started, 4),
        },
        "selection": {
            "limit": limit,
            "candidate_count": len(candidates),
            "selected_count": len(sources),
            "truncated": len(candidates) > limit,
        },
        "sources": sources,
        "source_manifest": [
            {
                "path": source["path"],
                "change_type": source["change_type"],
                "source_pack": source["source_pack"],
                "sha256": source["sha256"],
                "size": source["size"],
                "snippet_count": len(source["snippets"]),
                "snippet_status": source["snippet_status"],
                "source_bytes": source["source_bytes"],
            }
            for source in sources
        ],
        "comparison": compare_packs(base_pack, target_pack, limit=limit),
    }


def format_diff_context_markdown(bundle: dict[str, Any]) -> str:
    """Render a diff context bundle as source-backed Markdown."""

    summary = bundle.get("summary", {})
    settings = bundle.get("settings", {})
    lines = [
        "# RepoMori Diff Context",
        "",
        f"Question: {bundle.get('question', '')}",
        "",
        "## Packs",
        "",
        f"- Base: `{bundle.get('base_pack', {}).get('pack_path')}`",
        f"- Target: `{bundle.get('target_pack', {}).get('pack_path')}`",
        "",
        "## Summary",
        "",
        f"- Added: `{summary.get('added_count')}`",
        f"- Removed: `{summary.get('removed_count')}`",
        f"- Changed: `{summary.get('changed_count')}`",
        f"- Unchanged: `{summary.get('unchanged_count')}`",
        f"- Selected: `{summary.get('selected_count')}`",
        f"- Source bytes: `{summary.get('source_bytes')}`",
        f"- Max bytes: `{settings.get('max_bytes')}`",
        "",
        "## Changed Context",
        "",
    ]
    sources = bundle.get("sources", [])
    if not sources:
        lines.extend(["No added, changed, or removed files were selected.", ""])
    for source in sources:
        lines.extend(
            [
                f"### {source.get('path')}",
                "",
                f"- Change type: `{source.get('change_type')}`",
                f"- Source pack: `{source.get('source_pack')}`",
                f"- Score: `{source.get('score')}`",
                f"- Language: `{source.get('language') or 'unknown'}`",
                f"- Size: `{source.get('size')}`",
                f"- SHA-256: `{source.get('sha256')}`",
                f"- Change reasons: `{', '.join(source.get('change_reasons', [])) or 'none'}`",
                f"- Snippet status: `{source.get('snippet_status')}`",
                f"- Source bytes: `{source.get('source_bytes', 0)}`",
                "",
            ]
        )
        delta = source.get("summary_delta", {})
        for key in ("added_symbols", "removed_symbols", "added_imports", "removed_imports", "added_headings", "removed_headings"):
            values = delta.get(key, [])
            if values:
                lines.append(f"- {key}: " + ", ".join(f"`{value}`" for value in values[:8]))
        if delta:
            lines.append("")
        snippets = source.get("snippets", [])
        if not snippets:
            lines.extend(["No text snippets available.", ""])
            continue
        for snippet in snippets:
            language = source.get("language") or ""
            lines.extend(
                [
                    f"Lines {snippet['start_line']}-{snippet['end_line']} ({snippet['matched']}):",
                    "",
                    f"```{_markdown_fence_language(language)}",
                    snippet["text"],
                    "```",
                    "",
                ]
            )

    lines.extend(["## Source Manifest", ""])
    manifest = bundle.get("source_manifest", [])
    if not manifest:
        lines.extend(["No sources selected.", ""])
    else:
        for item in manifest:
            lines.append(
                f"- `{item.get('path')}` change=`{item.get('change_type')}` "
                f"pack=`{item.get('source_pack')}` sha256=`{item.get('sha256')}` "
                f"size=`{item.get('size')}` snippets=`{item.get('snippet_count')}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def evaluate_pack(
    pack: Path | str,
    questions: Iterable[str] | None = None,
    limit: int = 5,
    snippet_lines: int = 10,
    max_bytes: int | None = 4096,
    snippets_per_file: int = 2,
    include_source: bool = True,
) -> dict[str, Any]:
    """Evaluate whether a pack can build useful agent context."""

    question_list = [question.strip() for question in (questions or DEFAULT_EVAL_QUESTIONS) if question.strip()]
    if not question_list:
        raise ValueError("at least one eval question is required")

    started = time.time()
    pack_info = info_pack(pack)
    evaluations = []
    unique_sources: dict[str, int] = {}
    total_source_bytes = 0
    total_snippets = 0
    top_scores = []

    for question in question_list:
        bundle = build_context_bundle(
            pack,
            question,
            limit=limit,
            snippet_lines=snippet_lines,
            max_bytes=max_bytes,
            snippets_per_file=snippets_per_file,
            include_source=include_source,
        )
        sources = bundle["sources"]
        selected_count = len(sources)
        source_bytes = int(bundle["selection"]["source_bytes"])
        snippet_count = sum(len(source["snippets"]) for source in sources)
        top_score = max((float(source["score"] or 0) for source in sources), default=0.0)
        weak_signals = _eval_weak_signals(sources, selected_count, snippet_count, top_score)
        suggestions = _eval_suggestions(weak_signals)

        total_source_bytes += source_bytes
        total_snippets += snippet_count
        if selected_count:
            top_scores.append(top_score)
        for source in sources:
            unique_sources.setdefault(str(source["path"]), int(source["size"] or 0))

        evaluations.append(
            {
                "question": question,
                "status": "pass" if not weak_signals else "weak",
                "selected_count": selected_count,
                "snippet_count": snippet_count,
                "source_bytes": source_bytes,
                "top_score": round(top_score, 2),
                "weak_signals": weak_signals,
                "suggestions": suggestions,
                "selected_sources": [_eval_source_summary(source) for source in sources],
            }
        )

    aggregate_suggestions = _unique_items(
        suggestion
        for evaluation in evaluations
        for suggestion in evaluation["suggestions"]
    )
    pack_file_count = int(pack_info.get("counts", {}).get("files", 0) or 0)
    logical_bytes = int(pack_info.get("logical_bytes", 0) or 0)
    unique_source_bytes = sum(unique_sources.values())
    elapsed = time.time() - started

    return {
        "schema_version": "repomori.eval.v1",
        "pack": {
            "schema_version": pack_info.get("schema_version"),
            "repo_path": pack_info.get("repo_path"),
            "pack_path": pack_info.get("pack_path"),
            "logical_bytes": logical_bytes,
            "pack_bytes": pack_info.get("pack_bytes"),
            "counts": pack_info.get("counts", {}),
        },
        "settings": {
            "limit": limit,
            "snippet_lines": snippet_lines,
            "max_bytes": max_bytes,
            "snippets_per_file": snippets_per_file,
            "include_source": include_source,
        },
        "summary": {
            "question_count": len(evaluations),
            "passed_questions": sum(1 for evaluation in evaluations if evaluation["status"] == "pass"),
            "weak_questions": sum(1 for evaluation in evaluations if evaluation["status"] == "weak"),
            "total_source_bytes": total_source_bytes,
            "total_snippets": total_snippets,
            "average_top_score": round(sum(top_scores) / len(top_scores), 2) if top_scores else 0.0,
            "elapsed_seconds": round(elapsed, 4),
        },
        "coverage": {
            "unique_files": sorted(unique_sources),
            "unique_file_count": len(unique_sources),
            "pack_file_count": pack_file_count,
            "unique_file_percent": _percent(len(unique_sources), pack_file_count),
            "unique_source_bytes": unique_source_bytes,
            "logical_bytes": logical_bytes,
            "unique_source_byte_percent": _percent(unique_source_bytes, logical_bytes),
        },
        "questions": evaluations,
        "suggested_improvements": aggregate_suggestions,
    }


def format_eval_markdown(report: dict[str, Any]) -> str:
    """Render an eval report as Markdown."""

    pack = report.get("pack", {})
    settings = report.get("settings", {})
    summary = report.get("summary", {})
    coverage = report.get("coverage", {})
    lines = [
        "# RepoMori Evaluation",
        "",
        "## Pack",
        "",
        f"- Repository: `{pack.get('repo_path')}`",
        f"- Pack: `{pack.get('pack_path')}`",
        f"- Schema: `{pack.get('schema_version')}`",
        f"- Files: `{coverage.get('pack_file_count', 0)}`",
        "",
        "## Settings",
        "",
        f"- Limit: `{settings.get('limit')}`",
        f"- Snippet lines: `{settings.get('snippet_lines')}`",
        f"- Max bytes per question: `{settings.get('max_bytes')}`",
        f"- Snippets per file: `{settings.get('snippets_per_file')}`",
        f"- Include source: `{settings.get('include_source')}`",
        "",
        "## Summary",
        "",
        f"- Questions: `{summary.get('question_count', 0)}`",
        f"- Passed: `{summary.get('passed_questions', 0)}`",
        f"- Weak: `{summary.get('weak_questions', 0)}`",
        f"- Total source bytes: `{summary.get('total_source_bytes', 0)}`",
        f"- Total snippets: `{summary.get('total_snippets', 0)}`",
        f"- Average top score: `{summary.get('average_top_score', 0)}`",
        "",
        "## Coverage",
        "",
        f"- Unique selected files: `{coverage.get('unique_file_count', 0)}`",
        f"- File coverage: `{coverage.get('unique_file_percent', 0)}%`",
        f"- Unique selected bytes: `{coverage.get('unique_source_bytes', 0)}`",
        f"- Byte coverage: `{coverage.get('unique_source_byte_percent', 0)}%`",
        "",
        "## Questions",
        "",
    ]

    for index, evaluation in enumerate(report.get("questions", []), start=1):
        lines.extend(
            [
                f"### {index}. {evaluation.get('question', '')}",
                "",
                f"- Status: `{evaluation.get('status')}`",
                f"- Selected files: `{evaluation.get('selected_count', 0)}`",
                f"- Snippets: `{evaluation.get('snippet_count', 0)}`",
                f"- Source bytes: `{evaluation.get('source_bytes', 0)}`",
                f"- Top score: `{evaluation.get('top_score', 0)}`",
                f"- Weak signals: `{', '.join(evaluation.get('weak_signals', [])) or 'none'}`",
                "",
            ]
        )
        sources = evaluation.get("selected_sources", [])
        if sources:
            lines.append("Selected sources:")
            for source in sources:
                lines.append(
                    f"- `{source.get('path')}` score=`{source.get('score')}` "
                    f"snippets=`{source.get('snippet_count')}` "
                    f"bytes=`{source.get('source_bytes')}` "
                    f"status=`{source.get('snippet_status')}`"
                )
            lines.append("")
        else:
            lines.extend(["No sources selected.", ""])
        suggestions = evaluation.get("suggestions", [])
        if suggestions:
            lines.append("Suggestions:")
            for suggestion in suggestions:
                lines.append(f"- {suggestion}")
            lines.append("")

    lines.extend(["## Suggested Improvements", ""])
    suggestions = report.get("suggested_improvements", [])
    if suggestions:
        for suggestion in suggestions:
            lines.append(f"- {suggestion}")
    else:
        lines.append("No immediate eval weaknesses detected.")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compare_packs(
    base_pack: Path | str,
    target_pack: Path | str,
    *,
    limit: int = 50,
    include_unchanged: bool = False,
) -> dict[str, Any]:
    """Compare two packs and return a machine-readable delta."""

    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    started = time.time()
    base_info = info_pack(base_pack)
    target_info = info_pack(target_pack)
    base_records = _pack_file_records(base_pack)
    target_records = _pack_file_records(target_pack)

    base_paths = set(base_records)
    target_paths = set(target_records)
    added_paths = sorted(target_paths - base_paths)
    removed_paths = sorted(base_paths - target_paths)
    shared_paths = sorted(base_paths & target_paths)
    changed_paths = [
        path
        for path in shared_paths
        if _compare_record_reasons(base_records[path], target_records[path])
    ]
    changed_path_set = set(changed_paths)
    unchanged_paths = [path for path in shared_paths if path not in changed_path_set]

    files: dict[str, Any] = {
        "added": [_visible_file_record(target_records[path]) for path in added_paths[:limit]],
        "removed": [_visible_file_record(base_records[path]) for path in removed_paths[:limit]],
        "changed": [
            _changed_file_record(path, base_records[path], target_records[path])
            for path in changed_paths[:limit]
        ],
    }
    if include_unchanged:
        files["unchanged"] = [_visible_file_record(target_records[path]) for path in unchanged_paths[:limit]]

    base_bytes = sum(int(record["size"]) for record in base_records.values())
    target_bytes = sum(int(record["size"]) for record in target_records.values())
    return {
        "schema_version": "repomori.compare.v1",
        "base_pack": _pack_identity(base_info),
        "target_pack": _pack_identity(target_info),
        "settings": {
            "limit": limit,
            "include_unchanged": include_unchanged,
        },
        "summary": {
            "added_count": len(added_paths),
            "removed_count": len(removed_paths),
            "changed_count": len(changed_paths),
            "unchanged_count": len(unchanged_paths),
            "file_count_delta": len(target_records) - len(base_records),
            "byte_delta": target_bytes - base_bytes,
            "logical_bytes_delta": int(target_info.get("logical_bytes", 0) or 0)
            - int(base_info.get("logical_bytes", 0) or 0),
            "elapsed_seconds": round(time.time() - started, 4),
        },
        "language_delta": _language_delta(base_records, target_records),
        "files": files,
        "truncated": {
            "added": len(added_paths) > limit,
            "removed": len(removed_paths) > limit,
            "changed": len(changed_paths) > limit,
            "unchanged": include_unchanged and len(unchanged_paths) > limit,
        },
    }


def format_compare_markdown(report: dict[str, Any]) -> str:
    """Render a pack comparison report as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Pack Compare",
        "",
        f"Base: `{report.get('base_pack', {}).get('pack_path')}`",
        f"Target: `{report.get('target_pack', {}).get('pack_path')}`",
        "",
        "## Summary",
        "",
        f"- Added: {summary.get('added_count', 0)}",
        f"- Removed: {summary.get('removed_count', 0)}",
        f"- Changed: {summary.get('changed_count', 0)}",
        f"- Unchanged: {summary.get('unchanged_count', 0)}",
        f"- File count delta: {summary.get('file_count_delta', 0)}",
        f"- Byte delta: {summary.get('byte_delta', 0)}",
        "",
    ]
    language_delta = report.get("language_delta", [])
    if language_delta:
        lines.extend(["## Language Delta", ""])
        for item in language_delta:
            lines.append(
                f"- `{item.get('language')}` base=`{item.get('base_count')}` "
                f"target=`{item.get('target_count')}` delta=`{item.get('delta')}`"
            )
        lines.append("")

    files = report.get("files", {})
    _append_compare_file_section(lines, "Added Files", files.get("added", []))
    _append_compare_file_section(lines, "Removed Files", files.get("removed", []))

    changed = files.get("changed", [])
    lines.extend(["## Changed Files", ""])
    if not changed:
        lines.extend(["No changed files.", ""])
    else:
        for item in changed:
            before = item.get("before", {})
            after = item.get("after", {})
            reasons = ", ".join(item.get("change_reasons", []))
            lines.append(
                f"- `{item.get('path')}` reasons=`{reasons}` "
                f"bytes `{before.get('size')}` -> `{after.get('size')}` "
                f"sha `{before.get('sha256')}` -> `{after.get('sha256')}`"
            )
            detail = item.get("summary_delta", {})
            for key in ("added_symbols", "removed_symbols", "added_imports", "removed_imports"):
                values = detail.get(key, [])
                if values:
                    lines.append(f"  - {key}: " + ", ".join(f"`{value}`" for value in values[:8]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_repo_brief(
    pack: Path | str,
    *,
    max_files: int = 12,
    top_terms: int = 40,
    top_symbols: int = 40,
) -> dict[str, Any]:
    """Build a question-free orientation brief from a pack."""

    if max_files <= 0:
        raise ValueError("max_files must be greater than zero")
    if top_terms < 0:
        raise ValueError("top_terms must be zero or greater")
    if top_symbols < 0:
        raise ValueError("top_symbols must be zero or greater")

    pack_info = info_pack(pack)
    records = _pack_file_records(pack)
    language_counts = Counter(str(record.get("language") or "unknown") for record in records.values())
    term_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    import_counts: Counter[str] = Counter()
    heading_counts: Counter[str] = Counter()
    symbol_paths: dict[str, set[str]] = defaultdict(set)

    for record in records.values():
        summary = record["_summary"]
        terms = [str(term) for term in summary.get("top_terms", [])]
        term_counts.update(terms)
        for symbol in summary.get("symbols", []):
            name = str(symbol.get("name", "")).strip()
            if not name:
                continue
            key = f"{symbol.get('kind', 'symbol')}:{name}"
            symbol_counts[key] += 1
            symbol_paths[key].add(str(record["path"]))
        for item in summary.get("imports", []):
            target = str(item.get("target", "")).strip()
            if target:
                import_counts[target] += 1
        for item in summary.get("headings", []):
            text = str(item.get("text", "")).strip()
            if text:
                heading_counts[text] += 1

    ranked_files = sorted(
        records.values(),
        key=lambda record: (
            -_brief_file_score(record),
            -int(record.get("token_count") or 0),
            str(record.get("path")),
        ),
    )
    key_files = [_visible_file_record(record) for record in ranked_files[:max_files]]
    entrypoints = [
        _visible_file_record(record)
        for record in ranked_files
        if _brief_file_score(record) >= 70
    ][:max_files]
    largest_files = [
        _visible_file_record(record)
        for record in sorted(records.values(), key=lambda record: (-int(record["size"]), str(record["path"])))[:max_files]
    ]
    top_symbol_rows = [
        {
            "symbol": symbol,
            "count": count,
            "paths": sorted(symbol_paths[symbol])[:8],
        }
        for symbol, count in symbol_counts.most_common(top_symbols)
    ]

    return {
        "schema_version": "repomori.brief.v1",
        "pack": _pack_identity(pack_info),
        "settings": {
            "max_files": max_files,
            "top_terms": top_terms,
            "top_symbols": top_symbols,
        },
        "summary": {
            "file_count": len(records),
            "text_files": pack_info.get("text_files"),
            "binary_files": pack_info.get("binary_files"),
            "logical_bytes": pack_info.get("logical_bytes"),
            "pack_bytes": pack_info.get("pack_bytes"),
            "language_counts": [
                {"language": language, "count": count}
                for language, count in sorted(language_counts.items())
            ],
        },
        "orientation": {
            "entrypoints": entrypoints,
            "key_files": key_files,
            "largest_files": largest_files,
        },
        "vocabulary": {
            "top_terms": [[term, count] for term, count in term_counts.most_common(top_terms)],
            "top_symbols": top_symbol_rows,
            "top_imports": [[target, count] for target, count in import_counts.most_common(top_terms)],
            "top_headings": [[heading, count] for heading, count in heading_counts.most_common(top_terms)],
        },
        "source_manifest": [
            {
                "path": record["path"],
                "sha256": record["sha256"],
                "size": record["size"],
            }
            for record in key_files
        ],
        "suggestions": _brief_suggestions(records, key_files, top_symbol_rows),
    }


def format_brief_markdown(brief: dict[str, Any]) -> str:
    """Render a repo brief as Markdown."""

    summary = brief.get("summary", {})
    lines = [
        "# RepoMori Repo Brief",
        "",
        f"Pack: `{brief.get('pack', {}).get('pack_path')}`",
        "",
        "## Summary",
        "",
        f"- Files: {summary.get('file_count', 0)}",
        f"- Text files: {summary.get('text_files', 0)}",
        f"- Binary files: {summary.get('binary_files', 0)}",
        f"- Logical bytes: {summary.get('logical_bytes', 0)}",
        f"- Pack bytes: {summary.get('pack_bytes', 0)}",
        "",
    ]
    language_counts = summary.get("language_counts", [])
    if language_counts:
        lines.extend(["## Languages", ""])
        for item in language_counts:
            lines.append(f"- `{item.get('language')}`: {item.get('count')}")
        lines.append("")

    orientation = brief.get("orientation", {})
    _append_brief_file_section(lines, "Entrypoints", orientation.get("entrypoints", []))
    _append_brief_file_section(lines, "Key Files", orientation.get("key_files", []))

    vocabulary = brief.get("vocabulary", {})
    lines.extend(["## Vocabulary", ""])
    terms = vocabulary.get("top_terms", [])
    if terms:
        lines.append("Top terms: " + ", ".join(f"`{term}`({count})" for term, count in terms[:20]))
    symbols = vocabulary.get("top_symbols", [])
    if symbols:
        lines.append("Top symbols: " + ", ".join(f"`{item.get('symbol')}`({item.get('count')})" for item in symbols[:20]))
    imports = vocabulary.get("top_imports", [])
    if imports:
        lines.append("Top imports: " + ", ".join(f"`{target}`({count})" for target, count in imports[:20]))
    if not terms and not symbols and not imports:
        lines.append("No text vocabulary extracted.")
    lines.append("")

    suggestions = brief.get("suggestions", [])
    if suggestions:
        lines.extend(["## Suggestions", ""])
        for suggestion in suggestions:
            lines.append(f"- {suggestion}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_capsule(
    pack: Path | str,
    max_files: int | None = None,
    top_terms: int = 128,
) -> dict[str, Any]:
    """Build a dense machine-readable capsule from pack summaries."""

    if max_files is not None and max_files <= 0:
        raise ValueError("max_files must be greater than zero")
    if top_terms < 0:
        raise ValueError("top_terms must be zero or greater")

    pack_info = info_pack(pack)
    with closing(_open_pack(pack)) as conn:
        rows = conn.execute(
            """
            SELECT path, language, size, sha256, is_text, line_count, token_count, summary_json
            FROM files
            ORDER BY path
            """
        ).fetchall()

    total_files = len(rows)
    selected_rows = rows[:max_files] if max_files is not None else rows
    language_counts: Counter[str] = Counter()
    term_counts: Counter[str] = Counter()
    files = []
    graph_symbols = []
    graph_imports = []
    graph_headings = []
    manifest = []

    for row in selected_rows:
        path = row["path"]
        language = row["language"] or "unknown"
        summary = _safe_json(row["summary_json"], {})
        terms = [str(term) for term in summary.get("top_terms", [])[:16]]
        symbols = _capsule_symbols(summary.get("symbols", []), limit=32)
        imports = _capsule_imports(summary.get("imports", []), limit=32)
        headings = _capsule_headings(summary.get("headings", []), limit=24)

        language_counts[language] += 1
        term_counts.update(terms)
        graph_symbols.extend([[path, *symbol] for symbol in symbols])
        graph_imports.extend([[path, *item] for item in imports])
        graph_headings.extend([[path, *heading] for heading in headings])

        record: dict[str, Any] = {
            "p": path,
            "l": row["language"],
            "b": row["size"],
            "h": row["sha256"],
            "x": bool(row["is_text"]),
            "lc": row["line_count"],
            "tc": row["token_count"],
            "k": summary.get("kind"),
        }
        if terms:
            record["tt"] = terms
        if symbols:
            record["s"] = symbols
        if imports:
            record["i"] = imports
        if headings:
            record["hd"] = headings
        files.append(record)
        manifest.append({"path": path, "sha256": row["sha256"], "size": row["size"]})

    return {
        "schema_version": "repomori.capsule.v1",
        "pack": {
            "schema_version": pack_info.get("schema_version"),
            "repo_path": pack_info.get("repo_path"),
            "pack_path": pack_info.get("pack_path"),
            "created_at": pack_info.get("created_at"),
            "logical_bytes": pack_info.get("logical_bytes"),
            "pack_bytes": pack_info.get("pack_bytes"),
            "counts": pack_info.get("counts", {}),
        },
        "selection": {
            "max_files": max_files,
            "included_files": len(files),
            "total_files": total_files,
            "truncated": len(files) < total_files,
            "top_terms": top_terms,
        },
        "dictionary": {
            "languages": [[language, count] for language, count in sorted(language_counts.items())],
            "terms": [[term, count] for term, count in term_counts.most_common(top_terms)],
        },
        "files": files,
        "graph": {
            "symbols": graph_symbols,
            "imports": graph_imports,
            "headings": graph_headings,
        },
        "manifest": manifest,
        "key": {
            "p": "path",
            "l": "language",
            "b": "bytes",
            "h": "sha256",
            "x": "is_text",
            "lc": "line_count",
            "tc": "token_count",
            "k": "kind",
            "tt": "top_terms",
            "s": "symbols[kind,name,line]",
            "i": "imports[target,line]",
            "hd": "headings[level,text,line]",
        },
    }


def build_handoff_package(
    pack: Path | str,
    question: str,
    out_dir: Path | str,
    *,
    base_pack: Path | str | None = None,
    force: bool = False,
    copy_pack: bool = False,
    allow_unverified: bool = False,
    max_files: int = 8,
    max_bytes: int | None = None,
    snippet_lines: int = 12,
    snippets_per_file: int = 2,
    capsule_max_files: int | None = None,
    top_terms: int = 128,
    eval_questions: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a portable directory of source-backed handoff artifacts."""

    if not question.strip():
        raise ValueError("question must not be empty")
    if max_files <= 0:
        raise ValueError("max_files must be greater than zero")

    pack_path = Path(pack).resolve()
    base_pack_path = Path(base_pack).resolve() if base_pack is not None else None
    out_path = Path(out_dir).resolve()
    _prepare_handoff_dir(out_path, force)

    artifacts: list[dict[str, Any]] = []
    verify_report = verify_pack(pack_path)
    verify_path = out_path / "verify.json"
    _write_json(verify_path, verify_report)
    artifacts.append(_artifact_record(out_path, verify_path, "verify_json"))

    pack_info = info_pack(pack_path)
    status = "complete_unverified" if not verify_report["verified"] else "complete"
    if not verify_report["verified"] and not allow_unverified:
        status = "verification_failed"
        manifest = _handoff_manifest(
            question,
            out_path,
            pack_info,
            verify_report,
            artifacts,
            status,
            {
                "base_pack": str(base_pack_path) if base_pack_path is not None else None,
                "force": force,
                "copy_pack": copy_pack,
                "allow_unverified": allow_unverified,
                "max_files": max_files,
                "max_bytes": max_bytes,
                "snippet_lines": snippet_lines,
                "snippets_per_file": snippets_per_file,
                "capsule_max_files": capsule_max_files,
                "top_terms": top_terms,
            },
            info_pack(base_pack_path) if base_pack_path is not None else None,
        )
        _write_json(out_path / "manifest.json", manifest)
        return manifest

    context = build_context_bundle(
        pack_path,
        question,
        limit=max_files,
        snippet_lines=snippet_lines,
        max_bytes=max_bytes,
        snippets_per_file=snippets_per_file,
    )
    context_json = out_path / "context.json"
    context_md = out_path / "context.md"
    _write_json(context_json, context)
    context_md.write_text(format_context_markdown(context), encoding="utf-8")
    artifacts.append(_artifact_record(out_path, context_json, "context_json"))
    artifacts.append(_artifact_record(out_path, context_md, "context_markdown"))

    brief = build_repo_brief(pack_path, max_files=max_files, top_terms=top_terms, top_symbols=top_terms)
    brief_json = out_path / "brief.json"
    brief_md = out_path / "brief.md"
    _write_json(brief_json, brief)
    brief_md.write_text(format_brief_markdown(brief), encoding="utf-8")
    artifacts.append(_artifact_record(out_path, brief_json, "brief_json"))
    artifacts.append(_artifact_record(out_path, brief_md, "brief_markdown"))

    base_pack_info = None
    if base_pack_path is not None:
        base_pack_info = info_pack(base_pack_path)
        comparison = compare_packs(base_pack_path, pack_path)
        compare_json = out_path / "compare.json"
        compare_md = out_path / "compare.md"
        _write_json(compare_json, comparison)
        compare_md.write_text(format_compare_markdown(comparison), encoding="utf-8")
        artifacts.append(_artifact_record(out_path, compare_json, "compare_json"))
        artifacts.append(_artifact_record(out_path, compare_md, "compare_markdown"))

    capsule = build_capsule(pack_path, max_files=capsule_max_files, top_terms=top_terms)
    capsule_path = out_path / "capsule.json"
    _write_json(capsule_path, capsule, compact=True)
    artifacts.append(_artifact_record(out_path, capsule_path, "capsule_json"))

    eval_question_list = _handoff_eval_questions(question, eval_questions)
    eval_report = evaluate_pack(
        pack_path,
        questions=eval_question_list,
        limit=max_files,
        snippet_lines=snippet_lines,
        max_bytes=max_bytes,
        snippets_per_file=snippets_per_file,
    )
    eval_json = out_path / "eval.json"
    eval_md = out_path / "eval.md"
    _write_json(eval_json, eval_report)
    eval_md.write_text(format_eval_markdown(eval_report), encoding="utf-8")
    artifacts.append(_artifact_record(out_path, eval_json, "eval_json"))
    artifacts.append(_artifact_record(out_path, eval_md, "eval_markdown"))

    if copy_pack:
        pack_copy = out_path / pack_path.name
        if pack_copy.resolve() != pack_path:
            shutil.copy2(pack_path, pack_copy)
        artifacts.append(_artifact_record(out_path, pack_copy, "pack_copy"))

    readme_path = out_path / "README.md"
    readme_path.write_text(_handoff_readme(question, copy_pack, base_pack_path is not None), encoding="utf-8")
    artifacts.append(_artifact_record(out_path, readme_path, "handoff_readme"))

    manifest = _handoff_manifest(
        question,
        out_path,
        pack_info,
        verify_report,
        artifacts,
        status,
        {
            "base_pack": str(base_pack_path) if base_pack_path is not None else None,
            "force": force,
            "copy_pack": copy_pack,
            "allow_unverified": allow_unverified,
            "max_files": max_files,
            "max_bytes": max_bytes,
            "snippet_lines": snippet_lines,
            "snippets_per_file": snippets_per_file,
            "capsule_max_files": capsule_max_files,
            "top_terms": top_terms,
        },
        base_pack_info,
    )
    _write_json(out_path / "manifest.json", manifest)
    return manifest


def check_handoff_package(handoff_dir: Path | str) -> dict[str, Any]:
    """Validate a RepoMori handoff directory and its artifact manifest."""

    root = Path(handoff_dir).resolve()
    errors: list[dict[str, Any]] = []
    artifact_results = []
    json_results = []
    copied_pack_result = None
    manifest = None
    started = time.time()

    manifest_path = root / "manifest.json"
    if not root.exists() or not root.is_dir():
        _add_check_error(errors, "handoff", "", "Handoff directory not found.")
    elif not manifest_path.exists():
        _add_check_error(errors, "manifest", "manifest.json", "Manifest file not found.")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _add_check_error(errors, "manifest", "manifest.json", f"Manifest JSON is invalid: {exc}")

    if isinstance(manifest, dict):
        if manifest.get("schema_version") != "repomori.handoff.v1":
            _add_check_error(
                errors,
                "manifest",
                "manifest.json",
                "Unexpected handoff schema version.",
                expected="repomori.handoff.v1",
                actual=manifest.get("schema_version"),
            )
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            _add_check_error(errors, "manifest", "manifest.json", "Manifest artifacts must be a list.")
            artifacts = []
        for artifact in artifacts:
            artifact_results.append(_check_handoff_artifact(root, artifact, errors))

        json_names = ["context.json", "capsule.json", "eval.json", "verify.json"]
        has_brief = (root / "brief.json").exists() or any(
            isinstance(artifact, dict) and artifact.get("path") == "brief.json"
            for artifact in artifacts
        )
        if has_brief:
            json_names.insert(1, "brief.json")
        has_compare = (root / "compare.json").exists() or any(
            isinstance(artifact, dict) and artifact.get("path") == "compare.json"
            for artifact in artifacts
        )
        if has_compare:
            json_names.insert(-1, "compare.json")
        for name in json_names:
            json_results.append(_check_handoff_json(root, name, errors))

        pack_artifact = next(
            (
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict) and artifact.get("kind") == "pack_copy"
            ),
            None,
        )
        if pack_artifact:
            pack_path = root / str(pack_artifact.get("path", ""))
            copied_pack_result = _check_handoff_pack_copy(pack_path, errors)

    elapsed = time.time() - started
    return {
        "schema_version": "repomori.handoff.check.v1",
        "handoff_dir": str(root),
        "valid": not errors,
        "error_count": len(errors),
        "checked_artifacts": len(artifact_results),
        "checked_json": len(json_results),
        "copied_pack": copied_pack_result,
        "elapsed_seconds": round(elapsed, 4),
        "artifacts": artifact_results,
        "json_files": json_results,
        "errors": errors,
    }


def benchmark_repo(
    repo: Path | str,
    out_dir: Path | str,
    *,
    question: str = "How should an agent understand and continue this repository?",
    force: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_files: int = 8,
    max_bytes: int | None = 4096,
    snippet_lines: int = 12,
    snippets_per_file: int = 2,
    capsule_max_files: int | None = None,
    top_terms: int = 128,
    eval_questions: Iterable[str] | None = None,
    copy_pack: bool = False,
) -> dict[str, Any]:
    """Run an end-to-end RepoMori benchmark for a repository."""

    repo_path = Path(repo).resolve()
    out_path = Path(out_dir).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository folder not found: {repo_path}")
    _prepare_handoff_dir(out_path, force)

    started = time.time()
    pack_path = out_path / f"{repo_path.name}.repomori"
    handoff_path = out_path / "handoff"

    build = build_pack(repo_path, pack_path, BuildOptions(chunk_size=chunk_size, force=True))
    verify = verify_pack(pack_path)
    eval_question_list = _handoff_eval_questions(question, eval_questions)
    eval_report = evaluate_pack(
        pack_path,
        questions=eval_question_list,
        limit=max_files,
        snippet_lines=snippet_lines,
        max_bytes=max_bytes,
        snippets_per_file=snippets_per_file,
    )
    brief = build_repo_brief(pack_path, max_files=max_files, top_terms=top_terms, top_symbols=top_terms)
    brief_json = out_path / "brief.json"
    brief_md = out_path / "brief.md"
    _write_json(brief_json, brief)
    brief_md.write_text(format_brief_markdown(brief), encoding="utf-8")
    handoff = build_handoff_package(
        pack_path,
        question,
        handoff_path,
        force=True,
        copy_pack=copy_pack,
        max_files=max_files,
        max_bytes=max_bytes,
        snippet_lines=snippet_lines,
        snippets_per_file=snippets_per_file,
        capsule_max_files=capsule_max_files,
        top_terms=top_terms,
        eval_questions=eval_questions,
    )
    handoff_check = check_handoff_package(handoff_path)
    elapsed = time.time() - started
    status = "pass" if verify["verified"] and handoff_check["valid"] else "fail"

    report = {
        "schema_version": "repomori.bench.v1",
        "status": status,
        "repo_path": str(repo_path),
        "out_dir": str(out_path),
        "question": question,
        "settings": {
            "chunk_size": chunk_size,
            "max_files": max_files,
            "max_bytes": max_bytes,
            "snippet_lines": snippet_lines,
            "snippets_per_file": snippets_per_file,
            "capsule_max_files": capsule_max_files,
            "top_terms": top_terms,
            "copy_pack": copy_pack,
        },
        "summary": {
            "elapsed_seconds": round(elapsed, 4),
            "pack_path": str(pack_path),
            "handoff_dir": str(handoff_path),
            "pack_bytes": build.get("pack_bytes"),
            "logical_bytes": build.get("logical_bytes"),
            "logical_to_pack_ratio": _ratio(build.get("logical_bytes"), build.get("pack_bytes")),
            "file_count": build.get("file_count"),
            "text_file_count": build.get("text_file_count"),
            "binary_file_count": build.get("binary_file_count"),
            "compressed_chunk_bytes": build.get("compressed_chunk_bytes"),
            "verify_passed": verify.get("verified"),
            "handoff_passed": handoff_check.get("valid"),
            "eval_weak_questions": eval_report.get("summary", {}).get("weak_questions"),
            "eval_total_source_bytes": eval_report.get("summary", {}).get("total_source_bytes"),
            "eval_total_snippets": eval_report.get("summary", {}).get("total_snippets"),
            "eval_average_top_score": eval_report.get("summary", {}).get("average_top_score"),
            "brief_key_files": len(brief.get("orientation", {}).get("key_files", [])),
        },
        "artifacts": {
            "pack": pack_path.name,
            "brief_json": brief_json.name,
            "brief_markdown": brief_md.name,
            "handoff": handoff_path.name,
            "bench_json": "bench.json",
            "bench_markdown": "bench.md",
        },
        "build": build,
        "verify": verify,
        "brief": brief,
        "eval": eval_report,
        "handoff": handoff,
        "handoff_check": handoff_check,
    }
    _write_json(out_path / "bench.json", report)
    (out_path / "bench.md").write_text(format_benchmark_markdown(report), encoding="utf-8")
    return report


def run_demo(
    out_dir: Path | str,
    *,
    force: bool = False,
    question: str = "sqlite connect Store",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, Any]:
    """Create and run a complete local RepoMori quickstart demo."""

    if not question.strip():
        raise ValueError("question must not be empty")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    started = time.time()
    out_path = Path(out_dir).resolve()
    _prepare_demo_dir(out_path, force)

    repo_path = out_path / "demo-repo"
    pack_path = out_path / "demo.repomori"
    context_path = out_path / "context.md"
    config_path = out_path / "repomori.toml"
    packs_path = out_path / "packs"
    readme_path = out_path / "README.md"
    demo_json_path = out_path / "demo.json"

    _write_demo_repo(repo_path)
    build = build_pack(repo_path, pack_path, BuildOptions(chunk_size=chunk_size, force=True))
    verify = verify_pack(pack_path)
    query = query_pack(pack_path, question, limit=3)
    context = build_context_bundle(pack_path, question, limit=2, max_bytes=1200)
    context_path.write_text(format_context_markdown(context), encoding="utf-8")
    config = init_config(repo_path, packs_path, config_path=config_path, no_handoff=True, force=True)
    memory = run_memory_cycle(repo_path, packs_path, no_handoff=True, timeline_limit=3, chunk_size=chunk_size)
    mcp_tools = handle_mcp_request({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
    mcp_context = handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": "context",
            "method": "tools/call",
            "params": {
                "name": "repomori_context_build",
                "arguments": {"question": question, "max_files": 1, "max_bytes": 800},
            },
        },
        config_path=config_path,
    )

    mcp_tool_names = [
        item.get("name")
        for item in (mcp_tools or {}).get("result", {}).get("tools", [])
        if isinstance(item, dict)
    ]
    mcp_context_result = (mcp_context or {}).get("result", {})
    mcp_ok = isinstance(mcp_context_result, dict) and not mcp_context_result.get("isError")
    status = "pass" if verify.get("verified") and query and context.get("sources") and memory.get("status") != "fail" and mcp_ok else "fail"
    elapsed = time.time() - started
    summary = {
        "elapsed_seconds": round(elapsed, 4),
        "pack_path": str(pack_path),
        "config_path": str(config_path),
        "memory_out_dir": str(packs_path),
        "pack_bytes": build.get("pack_bytes"),
        "logical_bytes": build.get("logical_bytes"),
        "file_count": build.get("file_count"),
        "query_top_path": query[0].get("path") if query else None,
        "context_source_count": len(context.get("sources", [])),
        "memory_status": memory.get("status"),
        "mcp_tool_count": len(mcp_tool_names),
        "mcp_context_schema": mcp_context_result.get("structuredContent", {}).get("schema_version") if isinstance(mcp_context_result, dict) else None,
    }
    artifacts = {
        "demo_repo": repo_path.name,
        "pack": pack_path.name,
        "context_markdown": context_path.name,
        "config": config_path.name,
        "memory_out_dir": packs_path.name,
        "demo_json": demo_json_path.name,
        "readme": readme_path.name,
    }
    report = {
        "schema_version": "repomori.demo.v1",
        "status": status,
        "out_dir": str(out_path),
        "repo_path": str(repo_path),
        "question": question,
        "settings": {"chunk_size": chunk_size, "force": force},
        "summary": summary,
        "artifacts": artifacts,
        "build": build,
        "verify": verify,
        "query": query,
        "context": context,
        "config": config,
        "memory": memory,
        "mcp": {
            "tools": mcp_tools,
            "context_call": mcp_context,
            "tool_names": mcp_tool_names,
        },
    }
    readme_path.write_text(_demo_output_readme(report), encoding="utf-8")
    _write_json(demo_json_path, report)
    return report


def snapshot_repo(
    repo: Path | str,
    out_dir: Path | str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    incremental: bool = True,
    compare: bool = True,
    compare_limit: int = 50,
    handoff_question: str | None = None,
    handoff_out_dir: Path | str | None = None,
    handoff_force: bool = False,
    diff_context: bool = False,
    diff_context_question: str = "what changed?",
    diff_context_limit: int = 8,
    diff_context_snippet_lines: int = 12,
    diff_context_snippets_per_file: int = 2,
    diff_context_max_bytes: int | None = 8192,
    diff_context_include_source: bool = True,
) -> dict[str, Any]:
    """Build a timestamped pack snapshot and compare it with the previous latest pack."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if compare_limit <= 0:
        raise ValueError("compare_limit must be greater than zero")
    if handoff_question is not None and not handoff_question.strip():
        raise ValueError("handoff_question must not be empty")
    if diff_context:
        if not diff_context_question.strip():
            raise ValueError("diff_context_question must not be empty")
        if diff_context_limit <= 0:
            raise ValueError("diff_context_limit must be greater than zero")
        if diff_context_snippet_lines <= 0:
            raise ValueError("diff_context_snippet_lines must be greater than zero")
        if diff_context_snippets_per_file < 0:
            raise ValueError("diff_context_snippets_per_file must be zero or greater")
        if diff_context_max_bytes is not None and diff_context_max_bytes < 0:
            raise ValueError("diff_context_max_bytes must be zero or greater")

    started = time.time()
    repo_path = Path(repo).resolve()
    out_path = Path(out_dir).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository folder not found: {repo_path}")
    out_path.mkdir(parents=True, exist_ok=True)

    stamp = _snapshot_stamp(started)
    pack_path = _unique_snapshot_pack_path(out_path, repo_path.name, stamp)
    latest_path = out_path / "latest.repomori"
    previous_latest = latest_path if latest_path.exists() else None
    previous_pack = _snapshot_previous_pack(out_path) or previous_latest
    incremental_base = previous_pack if incremental and previous_pack is not None else None

    build = build_pack(
        repo_path,
        pack_path,
        BuildOptions(
            chunk_size=chunk_size,
            force=False,
            exclude_paths=_snapshot_exclude_paths(repo_path, out_path),
            base_pack=incremental_base,
        ),
    )
    verify = verify_pack(pack_path)

    comparison = None
    compare_json = None
    compare_md = None
    if compare and previous_pack is not None:
        comparison = compare_packs(previous_pack, pack_path, limit=compare_limit)
        compare_json = out_path / f"{pack_path.stem}.compare.json"
        compare_md = out_path / f"{pack_path.stem}.compare.md"
        _write_json(compare_json, comparison)
        compare_md.write_text(format_compare_markdown(comparison), encoding="utf-8")

    handoff = None
    handoff_check = None
    handoff_path = None
    if handoff_question is not None:
        handoff_path = Path(handoff_out_dir).resolve() if handoff_out_dir is not None else out_path / f"{pack_path.stem}.handoff"
        handoff = build_handoff_package(
            pack_path,
            handoff_question,
            handoff_path,
            base_pack=previous_pack if compare and previous_pack is not None else None,
            force=handoff_force,
        )
        handoff_check = check_handoff_package(handoff_path)

    diff_context_bundle = None
    diff_context_json = None
    diff_context_md = None
    diff_context_status = "disabled"
    if diff_context:
        if previous_pack is None:
            diff_context_status = "skipped_no_previous_pack"
        else:
            diff_context_bundle = build_diff_context_bundle(
                previous_pack,
                pack_path,
                diff_context_question,
                limit=diff_context_limit,
                snippet_lines=diff_context_snippet_lines,
                max_bytes=diff_context_max_bytes,
                snippets_per_file=diff_context_snippets_per_file,
                include_source=diff_context_include_source,
            )
            diff_context_json = out_path / f"{pack_path.stem}.diff-context.json"
            diff_context_md = out_path / f"{pack_path.stem}.diff-context.md"
            _write_json(diff_context_json, diff_context_bundle)
            diff_context_md.write_text(format_diff_context_markdown(diff_context_bundle), encoding="utf-8")
            diff_context_status = "written"

    if latest_path.resolve() != pack_path.resolve():
        shutil.copy2(pack_path, latest_path)

    elapsed = time.time() - started
    snapshot_json = out_path / f"{pack_path.stem}.snapshot.json"
    snapshot_md = out_path / f"{pack_path.stem}.snapshot.md"
    report = {
        "schema_version": "repomori.snapshot.v1",
        "status": "pass" if verify.get("verified") else "fail",
        "repo_path": str(repo_path),
        "out_dir": str(out_path),
        "created_at": int(started),
        "settings": {
            "chunk_size": chunk_size,
            "incremental": incremental,
            "compare": compare,
            "compare_limit": compare_limit,
            "diff_context": diff_context,
            "diff_context_question": diff_context_question if diff_context else None,
            "diff_context_limit": diff_context_limit,
            "diff_context_snippet_lines": diff_context_snippet_lines,
            "diff_context_snippets_per_file": diff_context_snippets_per_file,
            "diff_context_max_bytes": diff_context_max_bytes,
            "diff_context_include_source": diff_context_include_source,
        },
        "summary": {
            "elapsed_seconds": round(elapsed, 4),
            "pack_path": str(pack_path),
            "latest_pack": str(latest_path),
            "previous_latest_pack": str(previous_pack) if previous_pack is not None else None,
            "pack_bytes": build.get("pack_bytes"),
            "logical_bytes": build.get("logical_bytes"),
            "file_count": build.get("file_count"),
            "text_file_count": build.get("text_file_count"),
            "binary_file_count": build.get("binary_file_count"),
            "incremental": build.get("incremental"),
            "incremental_base_pack": build.get("base_pack_path"),
            "reused_file_count": build.get("reused_file_count"),
            "rebuilt_file_count": build.get("rebuilt_file_count"),
            "reused_chunk_count": build.get("reused_chunk_count"),
            "verify_passed": verify.get("verified"),
            "compared_with_previous": comparison is not None,
            "changed_count": comparison.get("summary", {}).get("changed_count") if comparison else None,
            "added_count": comparison.get("summary", {}).get("added_count") if comparison else None,
            "removed_count": comparison.get("summary", {}).get("removed_count") if comparison else None,
            "handoff_dir": str(handoff_path) if handoff_path is not None else None,
            "handoff_passed": handoff_check.get("valid") if handoff_check else None,
            "diff_context_status": diff_context_status,
            "diff_context_json": str(diff_context_json) if diff_context_json is not None else None,
            "diff_context_markdown": str(diff_context_md) if diff_context_md is not None else None,
            "diff_context_selected_count": diff_context_bundle.get("summary", {}).get("selected_count") if diff_context_bundle else None,
            "diff_context_added_count": diff_context_bundle.get("summary", {}).get("added_count") if diff_context_bundle else None,
            "diff_context_changed_count": diff_context_bundle.get("summary", {}).get("changed_count") if diff_context_bundle else None,
            "diff_context_removed_count": diff_context_bundle.get("summary", {}).get("removed_count") if diff_context_bundle else None,
        },
        "artifacts": {
            "pack": pack_path.name,
            "latest_pack": latest_path.name,
            "snapshot_json": snapshot_json.name,
            "snapshot_markdown": snapshot_md.name,
            "snapshot_index": "snapshots.json",
        },
        "build": build,
        "verify": verify,
        "comparison": comparison,
        "handoff": handoff,
        "handoff_check": handoff_check,
        "diff_context": diff_context_bundle,
    }
    if compare_json is not None and compare_md is not None:
        report["artifacts"]["compare_json"] = compare_json.name
        report["artifacts"]["compare_markdown"] = compare_md.name
    if handoff_path is not None:
        report["artifacts"]["handoff"] = handoff_path.name
    if diff_context_json is not None and diff_context_md is not None:
        report["artifacts"]["diff_context_json"] = diff_context_json.name
        report["artifacts"]["diff_context_markdown"] = diff_context_md.name

    _write_json(snapshot_json, report)
    snapshot_md.write_text(format_snapshot_markdown(report), encoding="utf-8")
    _update_snapshot_index(out_path, report)
    return report


def format_benchmark_markdown(report: dict[str, Any]) -> str:
    """Render a benchmark report as Markdown."""

    summary = report.get("summary", {})
    settings = report.get("settings", {})
    eval_report = report.get("eval", {})
    coverage = eval_report.get("coverage", {})
    lines = [
        "# RepoMori Benchmark",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Repository: `{report.get('repo_path')}`",
        f"- Output: `{report.get('out_dir')}`",
        f"- Question: {report.get('question')}",
        "",
        "## Settings",
        "",
        f"- Chunk size: `{settings.get('chunk_size')}`",
        f"- Max files: `{settings.get('max_files')}`",
        f"- Max bytes: `{settings.get('max_bytes')}`",
        f"- Snippet lines: `{settings.get('snippet_lines')}`",
        f"- Snippets per file: `{settings.get('snippets_per_file')}`",
        f"- Copy pack in handoff: `{settings.get('copy_pack')}`",
        "",
        "## Results",
        "",
        f"- Files: `{summary.get('file_count')}`",
        f"- Text files: `{summary.get('text_file_count')}`",
        f"- Binary files: `{summary.get('binary_file_count')}`",
        f"- Logical bytes: `{summary.get('logical_bytes')}`",
        f"- Pack bytes: `{summary.get('pack_bytes')}`",
        f"- Logical/pack ratio: `{summary.get('logical_to_pack_ratio')}`",
        f"- Verify passed: `{summary.get('verify_passed')}`",
        f"- Handoff check passed: `{summary.get('handoff_passed')}`",
        f"- Eval weak questions: `{summary.get('eval_weak_questions')}`",
        f"- Eval source bytes: `{summary.get('eval_total_source_bytes')}`",
        f"- Eval snippets: `{summary.get('eval_total_snippets')}`",
        f"- Eval average top score: `{summary.get('eval_average_top_score')}`",
        f"- Brief key files: `{summary.get('brief_key_files')}`",
        f"- Elapsed seconds: `{summary.get('elapsed_seconds')}`",
        "",
        "## Coverage",
        "",
        f"- Unique selected files: `{coverage.get('unique_file_count', 0)}`",
        f"- File coverage: `{coverage.get('unique_file_percent', 0)}%`",
        f"- Unique selected bytes: `{coverage.get('unique_source_bytes', 0)}`",
        f"- Byte coverage: `{coverage.get('unique_source_byte_percent', 0)}%`",
        "",
        "## Artifacts",
        "",
    ]
    for label, path in report.get("artifacts", {}).items():
        lines.append(f"- {label}: `{path}`")
    suggestions = eval_report.get("suggested_improvements", [])
    lines.extend(["", "## Suggested Improvements", ""])
    if suggestions:
        for suggestion in suggestions:
            lines.append(f"- {suggestion}")
    else:
        lines.append("No immediate eval weaknesses detected.")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_snapshot_markdown(report: dict[str, Any]) -> str:
    """Render a snapshot report as Markdown."""

    summary = report.get("summary", {})
    comparison = report.get("comparison")
    lines = [
        "# RepoMori Snapshot",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Repository: `{report.get('repo_path')}`",
        f"- Output: `{report.get('out_dir')}`",
        "",
        "## Snapshot",
        "",
        f"- Pack: `{summary.get('pack_path')}`",
        f"- Latest pack: `{summary.get('latest_pack')}`",
        f"- Previous latest: `{summary.get('previous_latest_pack')}`",
        f"- Files: `{summary.get('file_count')}`",
        f"- Text files: `{summary.get('text_file_count')}`",
        f"- Binary files: `{summary.get('binary_file_count')}`",
        f"- Logical bytes: `{summary.get('logical_bytes')}`",
        f"- Pack bytes: `{summary.get('pack_bytes')}`",
        f"- Incremental: `{summary.get('incremental')}`",
        f"- Incremental base: `{summary.get('incremental_base_pack')}`",
        f"- Reused files: `{summary.get('reused_file_count')}`",
        f"- Rebuilt files: `{summary.get('rebuilt_file_count')}`",
        f"- Reused chunks: `{summary.get('reused_chunk_count')}`",
        f"- Verify passed: `{summary.get('verify_passed')}`",
        f"- Elapsed seconds: `{summary.get('elapsed_seconds')}`",
        "",
        "## Comparison",
        "",
    ]
    if comparison:
        compare_summary = comparison.get("summary", {})
        lines.extend(
            [
                f"- Added: `{compare_summary.get('added_count')}`",
                f"- Removed: `{compare_summary.get('removed_count')}`",
                f"- Changed: `{compare_summary.get('changed_count')}`",
                f"- Unchanged: `{compare_summary.get('unchanged_count')}`",
                f"- Byte delta: `{compare_summary.get('byte_delta')}`",
                "",
            ]
        )
    elif report.get("settings", {}).get("compare"):
        lines.extend(["No previous `latest.repomori` snapshot was available to compare.", ""])
    else:
        lines.extend(["Comparison disabled for this snapshot.", ""])

    handoff = report.get("handoff")
    lines.extend(["## Handoff", ""])
    if handoff:
        summary = report.get("summary", {})
        lines.extend(
            [
                f"- Directory: `{summary.get('handoff_dir')}`",
                f"- Check passed: `{summary.get('handoff_passed')}`",
                f"- Status: `{handoff.get('status')}`",
                "",
            ]
        )
    else:
        lines.extend(["No handoff package was requested.", ""])

    lines.extend(["## Diff Context", ""])
    diff_status = summary.get("diff_context_status")
    if diff_status == "written":
        lines.extend(
            [
                f"- Status: `{diff_status}`",
                f"- Selected files: `{summary.get('diff_context_selected_count')}`",
                f"- Added: `{summary.get('diff_context_added_count')}`",
                f"- Changed: `{summary.get('diff_context_changed_count')}`",
                f"- Removed: `{summary.get('diff_context_removed_count')}`",
                f"- JSON: `{summary.get('diff_context_json')}`",
                f"- Markdown: `{summary.get('diff_context_markdown')}`",
                "",
            ]
        )
    else:
        lines.extend([f"Diff context status: `{diff_status or 'disabled'}`", ""])

    lines.extend(["## Artifacts", ""])
    for label, path in report.get("artifacts", {}).items():
        lines.append(f"- {label}: `{path}`")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def read_snapshot_timeline(out_dir: Path | str, *, limit: int | None = None) -> dict[str, Any]:
    """Read a snapshot index and return recent snapshot history."""

    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero")
    out_path = Path(out_dir).resolve()
    index = _read_snapshot_index(out_path / "snapshots.json", out_path)
    snapshots = list(index.get("snapshots", []))
    latest = index.get("latest")
    recent = []
    if isinstance(latest, dict):
        recent.append(latest)
    recent.extend(
        item
        for item in reversed(snapshots)
        if not isinstance(latest, dict) or item.get("pack_path") != latest.get("pack_path")
    )
    if limit is not None:
        recent = recent[:limit]
    return {
        "schema_version": "repomori.timeline.v1",
        "out_dir": str(out_path),
        "snapshot_count": len(snapshots),
        "returned_count": len(recent),
        "latest": index.get("latest"),
        "summary": {
            "total_added": _sum_snapshot_field(snapshots, "added_count"),
            "total_removed": _sum_snapshot_field(snapshots, "removed_count"),
            "total_changed": _sum_snapshot_field(snapshots, "changed_count"),
            "verified_count": sum(1 for item in snapshots if item.get("verify_passed")),
            "handoff_count": sum(1 for item in snapshots if item.get("handoff_dir")),
            "incremental_snapshot_count": sum(1 for item in snapshots if item.get("incremental")),
            "total_reused_files": _sum_snapshot_field(snapshots, "reused_file_count"),
            "total_rebuilt_files": _sum_snapshot_field(snapshots, "rebuilt_file_count"),
            "total_reused_chunks": _sum_snapshot_field(snapshots, "reused_chunk_count"),
        },
        "snapshots": recent,
    }


def read_snapshot_stats(out_dir: Path | str, *, limit: int | None = 10) -> dict[str, Any]:
    """Summarize snapshot reuse, rebuild, and storage trends."""

    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero")
    out_path = Path(out_dir).resolve()
    index = _read_snapshot_index(out_path / "snapshots.json", out_path)
    snapshots = sorted(
        list(index.get("snapshots", [])),
        key=lambda item: _snapshot_entry_sort_key(out_path, item),
    )
    latest = index.get("latest")
    recent = list(reversed(snapshots))
    if limit is not None:
        recent = recent[:limit]

    total_reused_files = _sum_snapshot_field(snapshots, "reused_file_count")
    total_rebuilt_files = _sum_snapshot_field(snapshots, "rebuilt_file_count")
    total_reused_chunks = _sum_snapshot_field(snapshots, "reused_chunk_count")
    total_file_decisions = total_reused_files + total_rebuilt_files
    total_pack_bytes = _sum_snapshot_field(snapshots, "pack_bytes")
    total_logical_bytes = _sum_snapshot_field(snapshots, "logical_bytes")
    incremental_snapshots = [item for item in snapshots if item.get("incremental")]
    top_reuse = sorted(
        (_snapshot_stats_entry(item) for item in snapshots),
        key=lambda item: (item.get("reused_file_count") or 0, item.get("reused_chunk_count") or 0),
        reverse=True,
    )
    if limit is not None:
        top_reuse = top_reuse[:limit]

    latest_stats = _snapshot_stats_entry(latest) if isinstance(latest, dict) else None
    return {
        "schema_version": "repomori.stats.v1",
        "out_dir": str(out_path),
        "snapshot_count": len(snapshots),
        "returned_count": len(recent),
        "summary": {
            "incremental_snapshot_count": len(incremental_snapshots),
            "full_snapshot_count": len(snapshots) - len(incremental_snapshots),
            "total_reused_files": total_reused_files,
            "total_rebuilt_files": total_rebuilt_files,
            "total_reused_chunks": total_reused_chunks,
            "reuse_percent": _percent(total_reused_files, total_file_decisions),
            "total_file_decisions": total_file_decisions,
            "total_pack_bytes": total_pack_bytes,
            "total_logical_bytes": total_logical_bytes,
            "logical_to_pack_ratio": _ratio(total_logical_bytes, total_pack_bytes),
            "total_added": _sum_snapshot_field(snapshots, "added_count"),
            "total_removed": _sum_snapshot_field(snapshots, "removed_count"),
            "total_changed": _sum_snapshot_field(snapshots, "changed_count"),
            "verified_count": sum(1 for item in snapshots if item.get("verify_passed")),
            "handoff_count": sum(1 for item in snapshots if item.get("handoff_dir")),
        },
        "latest": latest_stats,
        "snapshots": [_snapshot_stats_entry(item) for item in recent],
        "top_reuse": top_reuse,
    }


def doctor_snapshot_dir(out_dir: Path | str, *, verify_packs: bool = False) -> dict[str, Any]:
    """Check snapshot index, pack hashes, generated reports, and handoff health."""

    out_path = Path(out_dir).resolve()
    index_path = out_path / "snapshots.json"
    latest_path = out_path / "latest.repomori"
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "out_dir": str(out_path),
        "index_path": str(index_path),
        "snapshot_count": 0,
        "checked_packs": 0,
        "verified_packs": 0,
        "checked_artifacts": 0,
        "checked_handoffs": 0,
        "latest_repomori": str(latest_path),
        "verify_packs": verify_packs,
    }

    index: dict[str, Any] | None = None
    snapshots: list[dict[str, Any]] = []
    if not out_path.exists():
        _add_doctor_issue(errors, "out_dir", str(out_path), "Snapshot directory does not exist.")
    elif not out_path.is_dir():
        _add_doctor_issue(errors, "out_dir", str(out_path), "Snapshot path is not a directory.")
    elif not index_path.exists():
        _add_doctor_issue(errors, "index", str(index_path), "snapshots.json was not found.")
    else:
        try:
            index = _read_snapshot_index(index_path, out_path)
        except json.JSONDecodeError as exc:
            _add_doctor_issue(errors, "index", str(index_path), f"snapshots.json is invalid JSON: {exc}")
        except ValueError as exc:
            _add_doctor_issue(errors, "index", str(index_path), str(exc))

    if index is not None:
        raw_snapshots = index.get("snapshots", [])
        if isinstance(raw_snapshots, list):
            for offset, item in enumerate(raw_snapshots):
                if isinstance(item, dict):
                    snapshots.append(item)
                else:
                    _add_doctor_issue(
                        errors,
                        "index",
                        str(index_path),
                        "Snapshot index entry is not an object.",
                        index=offset,
                    )
        else:
            _add_doctor_issue(errors, "index", str(index_path), "Snapshot index snapshots must be a list.")

        summary["snapshot_count"] = len(snapshots)
        summary["index_snapshot_count"] = index.get("snapshot_count")
        if index.get("snapshot_count") != len(snapshots):
            _add_doctor_issue(
                warnings,
                "index",
                str(index_path),
                "snapshot_count does not match the number of indexed snapshots.",
                expected=len(snapshots),
                actual=index.get("snapshot_count"),
            )

        latest = index.get("latest")
        if latest is None:
            if snapshots:
                _add_doctor_issue(errors, "latest", str(index_path), "Snapshot index latest entry is missing.")
        elif not isinstance(latest, dict):
            _add_doctor_issue(errors, "latest", str(index_path), "Snapshot index latest entry is not an object.")
        else:
            latest_pack = _recorded_snapshot_path(out_path, latest.get("pack_path"))
            summary["latest_index_pack"] = str(latest_pack) if latest_pack is not None else None
            if latest_pack is None:
                _add_doctor_issue(errors, "latest", str(index_path), "Snapshot index latest pack_path is missing.")
            elif not latest_pack.exists():
                _add_doctor_issue(errors, "latest", str(latest_pack), "Snapshot index latest pack does not exist.")

        if not latest_path.exists():
            _add_doctor_issue(errors, "latest", str(latest_path), "latest.repomori does not exist.")
        elif not latest_path.is_file():
            _add_doctor_issue(errors, "latest", str(latest_path), "latest.repomori is not a file.")

        latest_pack_keys = {str(_recorded_snapshot_path(out_path, item.get("pack_path"))) for item in snapshots}
        if isinstance(index.get("latest"), dict):
            latest_key = str(_recorded_snapshot_path(out_path, index["latest"].get("pack_path")))
            if snapshots and latest_key not in latest_pack_keys:
                _add_doctor_issue(
                    warnings,
                    "latest",
                    str(index_path),
                    "Snapshot index latest entry is not present in snapshots.",
                )

        for offset, snapshot in enumerate(snapshots):
            _doctor_check_snapshot(out_path, snapshot, offset, verify_packs, summary, errors, warnings)

    status = "fail" if errors else "warn" if warnings else "pass"
    return {
        "schema_version": "repomori.doctor.v1",
        "status": status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }


def prune_snapshots(out_dir: Path | str, *, keep: int = 20, apply: bool = False) -> dict[str, Any]:
    """Plan or apply safe cleanup of generated snapshot artifacts."""

    if keep < 0:
        raise ValueError("keep must be zero or greater")

    out_path = Path(out_dir).resolve()
    index_path = out_path / "snapshots.json"
    latest_path = out_path / "latest.repomori"
    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    if not out_path.exists():
        _add_prune_error(errors, "out_dir", str(out_path), "Snapshot directory does not exist.")
        snapshots: list[dict[str, Any]] = []
        index: dict[str, Any] | None = None
    elif not out_path.is_dir():
        _add_prune_error(errors, "out_dir", str(out_path), "Snapshot path is not a directory.")
        snapshots = []
        index = None
    elif not index_path.exists():
        _add_prune_error(errors, "index", str(index_path), "snapshots.json was not found.")
        snapshots = []
        index = None
    else:
        try:
            index = _read_snapshot_index(index_path, out_path)
            snapshots = [item for item in index.get("snapshots", []) if isinstance(item, dict)]
        except json.JSONDecodeError as exc:
            _add_prune_error(errors, "index", str(index_path), f"snapshots.json is invalid JSON: {exc}")
            snapshots = []
            index = None
        except ValueError as exc:
            _add_prune_error(errors, "index", str(index_path), str(exc))
            snapshots = []
            index = None

    retained_keys: set[str] = set()
    latest_entry = index.get("latest") if isinstance(index, dict) and isinstance(index.get("latest"), dict) else None
    if latest_entry is not None:
        retained_keys.add(_snapshot_pack_key(out_path, latest_entry))
    ordered_snapshots = sorted(snapshots, key=lambda item: _snapshot_entry_sort_key(out_path, item))
    for offset, snapshot in enumerate(reversed(ordered_snapshots)):
        if offset >= keep:
            break
        retained_keys.add(_snapshot_pack_key(out_path, snapshot))
    retained_keys = {key for key in retained_keys if key}

    seen_targets: set[str] = set()
    for snapshot in snapshots:
        key = _snapshot_pack_key(out_path, snapshot)
        record = _snapshot_prune_record(snapshot)
        if key in retained_keys:
            retained.append(record)
            continue
        targets, target_skips = _snapshot_prune_targets(out_path, snapshot)
        candidates.append({**record, "artifacts": targets, "skipped": target_skips})
        skipped.extend(target_skips)
        for target in targets:
            target_path = Path(str(target["path"])).resolve()
            target_key = str(target_path)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            if target_path in {index_path, latest_path}:
                skipped.append({**target, "reason": "protected"})
                continue
            if not _is_within_path(out_path, target_path):
                skipped.append({**target, "reason": "skipped_external"})
                continue
            if target_path == out_path:
                skipped.append({**target, "reason": "protected"})
                continue
            if not target_path.exists():
                skipped.append({**target, "reason": "missing"})
                continue
            if not apply:
                continue
            try:
                if target_path.is_dir():
                    shutil.rmtree(target_path)
                else:
                    target_path.unlink()
            except OSError as exc:
                _add_prune_error(errors, "delete", str(target_path), f"Could not delete snapshot artifact: {exc}")
                continue
            deleted.append({**target, "deleted": True})

    if apply and index is not None and not errors:
        retained_snapshots = [snapshot for snapshot in snapshots if _snapshot_pack_key(out_path, snapshot) in retained_keys]
        latest = index.get("latest")
        if isinstance(latest, dict) and _snapshot_pack_key(out_path, latest) not in retained_keys:
            retained_snapshots.append(latest)
        updated = {
            "schema_version": "repomori.snapshots.v1",
            "out_dir": str(out_path),
            "updated_at": int(time.time()),
            "snapshot_count": len(retained_snapshots),
            "latest": index.get("latest"),
            "snapshots": retained_snapshots,
        }
        _write_json(index_path, updated)

    return {
        "schema_version": "repomori.prune.v1",
        "applied": apply,
        "keep": keep,
        "out_dir": str(out_path),
        "retained": retained,
        "candidates": candidates,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
    }


def run_memory_cycle(
    repo: Path | str,
    out_dir: Path | str,
    *,
    handoff_question: str = "continue this repo",
    no_handoff: bool = False,
    keep: int = 20,
    prune_apply: bool = False,
    verify_packs: bool = False,
    timeline_limit: int = 5,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    incremental: bool = True,
    compare: bool = True,
    compare_limit: int = 50,
    diff_context: bool = False,
    diff_context_question: str = "what changed?",
    diff_context_limit: int = 8,
    diff_context_snippet_lines: int = 12,
    diff_context_snippets_per_file: int = 2,
    diff_context_max_bytes: int | None = 8192,
    diff_context_include_source: bool = True,
) -> dict[str, Any]:
    """Run the full offline snapshot memory loop for a repository."""

    if timeline_limit <= 0:
        raise ValueError("timeline_limit must be greater than zero")
    if no_handoff:
        snapshot_handoff_question = None
    else:
        if not handoff_question.strip():
            raise ValueError("handoff_question must not be empty")
        snapshot_handoff_question = handoff_question

    started = time.time()
    repo_path = Path(repo).resolve()
    out_path = Path(out_dir).resolve()
    snapshot = snapshot_repo(
        repo_path,
        out_path,
        chunk_size=chunk_size,
        incremental=incremental,
        compare=compare,
        compare_limit=compare_limit,
        handoff_question=snapshot_handoff_question,
        diff_context=diff_context,
        diff_context_question=diff_context_question,
        diff_context_limit=diff_context_limit,
        diff_context_snippet_lines=diff_context_snippet_lines,
        diff_context_snippets_per_file=diff_context_snippets_per_file,
        diff_context_max_bytes=diff_context_max_bytes,
        diff_context_include_source=diff_context_include_source,
    )
    doctor = doctor_snapshot_dir(out_path, verify_packs=verify_packs)
    prune = prune_snapshots(out_path, keep=keep, apply=prune_apply)
    timeline = read_snapshot_timeline(out_path, limit=timeline_limit)

    status = "pass"
    if snapshot.get("status") != "pass" or doctor.get("status") == "fail" or prune.get("errors"):
        status = "fail"
    elif doctor.get("status") == "warn":
        status = "warn"

    snapshot_summary = snapshot.get("summary", {})
    artifacts = {
        "pack": snapshot_summary.get("pack_path"),
        "latest_pack": snapshot_summary.get("latest_pack"),
        "snapshot_json": snapshot.get("artifacts", {}).get("snapshot_json"),
        "snapshot_markdown": snapshot.get("artifacts", {}).get("snapshot_markdown"),
        "snapshot_index": snapshot.get("artifacts", {}).get("snapshot_index"),
    }
    if snapshot_summary.get("handoff_dir"):
        artifacts["handoff"] = snapshot_summary.get("handoff_dir")
    if snapshot.get("artifacts", {}).get("compare_json"):
        artifacts["compare_json"] = snapshot["artifacts"]["compare_json"]
    if snapshot.get("artifacts", {}).get("compare_markdown"):
        artifacts["compare_markdown"] = snapshot["artifacts"]["compare_markdown"]
    if snapshot.get("artifacts", {}).get("diff_context_json"):
        artifacts["diff_context_json"] = snapshot["artifacts"]["diff_context_json"]
    if snapshot.get("artifacts", {}).get("diff_context_markdown"):
        artifacts["diff_context_markdown"] = snapshot["artifacts"]["diff_context_markdown"]

    return {
        "schema_version": "repomori.memory.v1",
        "status": status,
        "repo_path": str(repo_path),
        "out_dir": str(out_path),
        "created_at": int(started),
        "settings": {
            "handoff_question": None if no_handoff else handoff_question,
            "no_handoff": no_handoff,
            "keep": keep,
            "prune_apply": prune_apply,
            "verify_packs": verify_packs,
            "timeline_limit": timeline_limit,
            "chunk_size": chunk_size,
            "incremental": incremental,
            "compare": compare,
            "compare_limit": compare_limit,
            "diff_context": diff_context,
            "diff_context_question": diff_context_question if diff_context else None,
            "diff_context_limit": diff_context_limit,
            "diff_context_snippet_lines": diff_context_snippet_lines,
            "diff_context_snippets_per_file": diff_context_snippets_per_file,
            "diff_context_max_bytes": diff_context_max_bytes,
            "diff_context_include_source": diff_context_include_source,
        },
        "summary": {
            "elapsed_seconds": round(time.time() - started, 4),
            "snapshot_status": snapshot.get("status"),
            "doctor_status": doctor.get("status"),
            "doctor_errors": doctor.get("error_count"),
            "doctor_warnings": doctor.get("warning_count"),
            "prune_applied": prune.get("applied"),
            "prune_candidates": len(prune.get("candidates", [])),
            "prune_deleted": len(prune.get("deleted", [])),
            "timeline_snapshot_count": timeline.get("snapshot_count"),
            "timeline_returned_count": timeline.get("returned_count"),
            "pack_path": snapshot_summary.get("pack_path"),
            "latest_pack": snapshot_summary.get("latest_pack"),
            "incremental": snapshot_summary.get("incremental"),
            "incremental_base_pack": snapshot_summary.get("incremental_base_pack"),
            "reused_file_count": snapshot_summary.get("reused_file_count"),
            "rebuilt_file_count": snapshot_summary.get("rebuilt_file_count"),
            "handoff_dir": snapshot_summary.get("handoff_dir"),
            "handoff_passed": snapshot_summary.get("handoff_passed"),
            "diff_context_status": snapshot_summary.get("diff_context_status"),
            "diff_context_json": snapshot_summary.get("diff_context_json"),
            "diff_context_markdown": snapshot_summary.get("diff_context_markdown"),
            "diff_context_selected_count": snapshot_summary.get("diff_context_selected_count"),
            "diff_context_added_count": snapshot_summary.get("diff_context_added_count"),
            "diff_context_changed_count": snapshot_summary.get("diff_context_changed_count"),
            "diff_context_removed_count": snapshot_summary.get("diff_context_removed_count"),
        },
        "artifacts": artifacts,
        "snapshot": snapshot,
        "doctor": doctor,
        "prune": prune,
        "timeline": timeline,
        "diff_context": snapshot.get("diff_context"),
    }


def init_config(
    repo: Path | str,
    out_dir: Path | str,
    *,
    config_path: Path | str | None = None,
    profile: str = "default",
    force: bool = False,
    handoff_question: str = "continue this repo",
    no_handoff: bool = False,
    keep: int = 20,
    prune_apply: bool = False,
    verify_packs: bool = False,
    timeline_limit: int = 5,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    incremental: bool = True,
    compare: bool = True,
    compare_limit: int = 50,
    diff_context: bool = False,
    diff_context_question: str = "what changed?",
    diff_context_limit: int = 8,
    diff_context_snippet_lines: int = 12,
    diff_context_snippets_per_file: int = 2,
    diff_context_max_bytes: int = 8192,
    diff_context_include_source: bool = True,
) -> dict[str, Any]:
    """Write a local RepoMori config file for memory runs."""

    _validate_config_profile(profile)
    repo_path = Path(repo).resolve()
    out_path = Path(out_dir).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository folder not found: {repo_path}")
    path = Path(config_path).resolve() if config_path is not None else repo_path / "repomori.toml"
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}")
    if keep < 0:
        raise ValueError("keep must be zero or greater")
    if timeline_limit <= 0:
        raise ValueError("timeline_limit must be greater than zero")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if compare_limit <= 0:
        raise ValueError("compare_limit must be greater than zero")
    if diff_context_limit <= 0:
        raise ValueError("diff_context_limit must be greater than zero")
    if diff_context_snippet_lines <= 0:
        raise ValueError("diff_context_snippet_lines must be greater than zero")
    if diff_context_snippets_per_file < 0:
        raise ValueError("diff_context_snippets_per_file must be zero or greater")
    if diff_context_max_bytes < 0:
        raise ValueError("diff_context_max_bytes must be zero or greater")
    if not no_handoff and not handoff_question.strip():
        raise ValueError("handoff_question must not be empty")
    if diff_context and not diff_context_question.strip():
        raise ValueError("diff_context_question must not be empty")

    settings = {
        "repo": str(repo_path),
        "out_dir": str(out_path),
        "handoff_question": handoff_question,
        "no_handoff": no_handoff,
        "keep": keep,
        "prune_apply": prune_apply,
        "verify_packs": verify_packs,
        "timeline_limit": timeline_limit,
        "chunk_size": chunk_size,
        "incremental": incremental,
        "compare": compare,
        "compare_limit": compare_limit,
        "diff_context": diff_context,
        "diff_context_question": diff_context_question,
        "diff_context_limit": diff_context_limit,
        "diff_context_snippet_lines": diff_context_snippet_lines,
        "diff_context_snippets_per_file": diff_context_snippets_per_file,
        "diff_context_max_bytes": diff_context_max_bytes,
        "diff_context_include_source": diff_context_include_source,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_format_memory_config(profile, settings), encoding="utf-8")
    return {
        "schema_version": "repomori.config.init.v1",
        "config_schema_version": "repomori.config.v1",
        "config_path": str(path),
        "profile": profile,
        "settings": settings,
    }


def load_memory_config(
    config_path: Path | str | None = None,
    *,
    start_dir: Path | str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Load a RepoMori memory profile from repomori.toml."""

    path = Path(config_path).resolve() if config_path is not None else _find_config_path(start_dir)
    if path is None:
        raise FileNotFoundError("RepoMori config not found. Run `python -m repomori init ...` first.")
    raw = _read_memory_config(path)
    if raw.get("schema_version") != "repomori.config.v1":
        raise ValueError(f"Unexpected RepoMori config schema: {path}")
    selected = profile or str(raw.get("default_profile") or "default")
    _validate_config_profile(selected)
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict) or selected not in profiles:
        raise ValueError(f"RepoMori config profile not found: {selected}")
    settings = _normalize_memory_config_settings(path, profiles[selected])
    return {
        "schema_version": "repomori.config.v1",
        "config_path": str(path),
        "profile": selected,
        "settings": settings,
    }


def schema_catalog(schema_version: str | None = None) -> dict[str, Any]:
    """Return supported RepoMori schema and agent method metadata."""

    schemas = [dict(item) for item in SCHEMA_DEFINITIONS]
    if schema_version is not None:
        matches = [item for item in schemas if item["schema_version"] == schema_version]
        if not matches:
            raise ValueError(f"Unknown RepoMori schema: {schema_version}")
        return {
            "schema_version": "repomori.schema.catalog.v1",
            "selected": schema_version,
            "schema_count": 1,
            "schemas": matches,
            "schema": matches[0],
            "agent_methods": list(AGENT_METHODS),
            "mcp_tools": [tool["name"] for tool in MCP_TOOLS],
        }
    return {
        "schema_version": "repomori.schema.catalog.v1",
        "schema_count": len(schemas),
        "schemas": schemas,
        "agent_methods": list(AGENT_METHODS),
        "mcp_tools": [tool["name"] for tool in MCP_TOOLS],
    }


def handle_agent_request(
    request: dict[str, Any],
    *,
    config_path: Path | str | None = None,
    profile: str | None = None,
    start_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Handle one JSON-RPC-style RepoMori agent bridge request."""

    request_id = request.get("id") if isinstance(request, dict) else None
    try:
        if not isinstance(request, dict):
            raise ValueError("Agent request must be a JSON object.")
        method = request.get("method")
        if not isinstance(method, str) or not method:
            raise ValueError("Agent request must include a string method.")
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("Agent request params must be an object.")
        result = _agent_dispatch(method, params, config_path=config_path, profile=profile, start_dir=start_dir)
        return _agent_response(request_id, result)
    except Exception as exc:
        code = "method_not_found" if isinstance(exc, NotImplementedError) else "execution_error"
        if isinstance(exc, ValueError):
            code = "invalid_request"
        return _agent_error_response(request_id, code, str(exc))


def run_agent_bridge(
    input_stream,
    output_stream,
    *,
    config_path: Path | str | None = None,
    profile: str | None = None,
    start_dir: Path | str | None = None,
) -> int:
    """Run the RepoMori JSON-lines agent bridge on stdio-like streams."""

    for raw_line in input_stream:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _agent_error_response(None, "invalid_json", f"Invalid JSON request: {exc}")
        else:
            response = handle_agent_request(
                request,
                config_path=config_path,
                profile=profile,
                start_dir=start_dir,
            )
        output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
        output_stream.flush()
    return 0


def handle_mcp_request(
    request: dict[str, Any],
    *,
    config_path: Path | str | None = None,
    profile: str | None = None,
    start_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """Handle one minimal MCP JSON-RPC request."""

    request_id = request.get("id") if isinstance(request, dict) else None
    try:
        if not isinstance(request, dict):
            return _mcp_error_response(None, -32600, "Invalid Request", "MCP request must be a JSON object.")
        method = request.get("method")
        if not isinstance(method, str) or not method:
            return _mcp_error_response(request_id, -32600, "Invalid Request", "MCP request must include a string method.")
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _mcp_error_response(request_id, -32602, "Invalid params", "MCP request params must be an object.")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return _mcp_response(request_id, _mcp_initialize_result(params))
        if method == "ping":
            return _mcp_response(request_id, {})
        if method == "tools/list":
            return _mcp_response(
                request_id,
                {"schema_version": "repomori.mcp.tools.v1", "tools": _mcp_tool_definitions()},
            )
        if method == "tools/call":
            try:
                result = _mcp_call_tool(
                    params,
                    config_path=config_path,
                    profile=profile,
                    start_dir=start_dir,
                )
            except ValueError as exc:
                return _mcp_error_response(request_id, -32602, "Invalid params", str(exc))
            return _mcp_response(request_id, result)
        return _mcp_error_response(request_id, -32601, "Method not found", f"Unknown MCP method: {method}")
    except Exception as exc:
        return _mcp_error_response(request_id, -32603, "Internal error", str(exc))


def run_mcp_bridge(
    input_stream,
    output_stream,
    *,
    config_path: Path | str | None = None,
    profile: str | None = None,
    start_dir: Path | str | None = None,
) -> int:
    """Run the dependency-free RepoMori MCP stdio bridge."""

    for raw_line in input_stream:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _mcp_error_response(None, -32700, "Parse error", f"Invalid JSON request: {exc}")
        else:
            response = handle_mcp_request(
                request,
                config_path=config_path,
                profile=profile,
                start_dir=start_dir,
            )
        if response is None:
            continue
        output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
        output_stream.flush()
    return 0


def format_timeline_markdown(timeline: dict[str, Any]) -> str:
    """Render snapshot timeline history as Markdown."""

    summary = timeline.get("summary", {})
    lines = [
        "# RepoMori Snapshot Timeline",
        "",
        f"- Output: `{timeline.get('out_dir')}`",
        f"- Snapshots: `{timeline.get('snapshot_count')}`",
        f"- Returned: `{timeline.get('returned_count')}`",
        f"- Verified snapshots: `{summary.get('verified_count')}`",
        f"- Handoffs: `{summary.get('handoff_count')}`",
        f"- Total added: `{summary.get('total_added')}`",
        f"- Total removed: `{summary.get('total_removed')}`",
        f"- Total changed: `{summary.get('total_changed')}`",
        f"- Incremental snapshots: `{summary.get('incremental_snapshot_count')}`",
        f"- Reused files: `{summary.get('total_reused_files')}`",
        f"- Rebuilt files: `{summary.get('total_rebuilt_files')}`",
        f"- Reused chunks: `{summary.get('total_reused_chunks')}`",
        "",
        "## Recent Snapshots",
        "",
    ]
    snapshots = timeline.get("snapshots", [])
    if not snapshots:
        lines.extend(["No snapshots recorded.", ""])
    else:
        for item in snapshots:
            lines.append(
                f"- `{item.get('pack_name')}` status=`{item.get('status')}` "
                f"files=`{item.get('file_count')}` "
                f"reused=`{item.get('reused_file_count')}` rebuilt=`{item.get('rebuilt_file_count')}` "
                f"added=`{item.get('added_count')}` removed=`{item.get('removed_count')}` "
                f"changed=`{item.get('changed_count')}`"
            )
            if item.get("handoff_dir"):
                lines.append(f"  - handoff: `{item.get('handoff_dir')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_stats_markdown(report: dict[str, Any]) -> str:
    """Render snapshot incremental savings as Markdown."""

    summary = report.get("summary", {})
    latest = report.get("latest") or {}
    lines = [
        "# RepoMori Snapshot Stats",
        "",
        f"- Output: `{report.get('out_dir')}`",
        f"- Snapshots: `{report.get('snapshot_count')}`",
        f"- Incremental snapshots: `{summary.get('incremental_snapshot_count')}`",
        f"- Full snapshots: `{summary.get('full_snapshot_count')}`",
        f"- Reused files: `{summary.get('total_reused_files')}`",
        f"- Rebuilt files: `{summary.get('total_rebuilt_files')}`",
        f"- Reuse percent: `{summary.get('reuse_percent')}`",
        f"- Reused chunks: `{summary.get('total_reused_chunks')}`",
        f"- Total pack bytes: `{summary.get('total_pack_bytes')}`",
        f"- Total logical bytes: `{summary.get('total_logical_bytes')}`",
        f"- Logical/pack ratio: `{summary.get('logical_to_pack_ratio')}`",
        "",
        "## Latest",
        "",
    ]
    if latest:
        lines.extend(
            [
                f"- Pack: `{latest.get('pack_name')}`",
                f"- Incremental: `{latest.get('incremental')}`",
                f"- Incremental base: `{latest.get('incremental_base_pack')}`",
                f"- Reused files: `{latest.get('reused_file_count')}`",
                f"- Rebuilt files: `{latest.get('rebuilt_file_count')}`",
                f"- Reuse percent: `{latest.get('reuse_percent')}`",
                "",
            ]
        )
    else:
        lines.extend(["No latest snapshot recorded.", ""])

    lines.extend(["## Top Reuse", ""])
    top_reuse = report.get("top_reuse", [])
    if not top_reuse:
        lines.extend(["No snapshots recorded.", ""])
    else:
        for item in top_reuse:
            lines.append(
                f"- `{item.get('pack_name')}` reused=`{item.get('reused_file_count')}` "
                f"rebuilt=`{item.get('rebuilt_file_count')}` reuse=`{item.get('reuse_percent')}`"
            )
        lines.append("")

    lines.extend(["## Recent Snapshots", ""])
    snapshots = report.get("snapshots", [])
    if not snapshots:
        lines.extend(["No snapshots recorded.", ""])
    else:
        for item in snapshots:
            lines.append(
                f"- `{item.get('pack_name')}` incremental=`{item.get('incremental')}` "
                f"reused=`{item.get('reused_file_count')}` rebuilt=`{item.get('rebuilt_file_count')}` "
                f"changed=`{item.get('changed_count')}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def get_file_bytes(pack: Path | str, repo_path: str) -> bytes:
    """Restore one file from a pack and return its bytes."""

    normalized = _normalize_repo_path(repo_path)
    with closing(_open_pack(pack)) as conn:
        return _read_file_bytes(conn, normalized)


def _read_file_bytes(conn: sqlite3.Connection, normalized: str) -> bytes:
    file_row = conn.execute("SELECT path FROM files WHERE path=?", (normalized,)).fetchone()
    if not file_row:
        raise KeyError(f"File not found in pack: {normalized}")
    chunks = conn.execute(
        """
        SELECT c.compressor, c.data
        FROM file_chunks fc
        JOIN chunks c ON c.id = fc.chunk_id
        WHERE fc.path=?
        ORDER BY fc.chunk_index
        """,
        (normalized,),
    ).fetchall()
    return b"".join(_decompress_chunk(row["compressor"], row["data"]) for row in chunks)


def _decompress_chunk(compressor: str, data: bytes) -> bytes:
    if compressor != "zlib":
        raise ValueError(f"Unsupported compressor: {compressor}")
    return zlib.decompress(data)


def _snippets_for_result(
    pack: Path | str,
    question: str,
    result: dict[str, Any],
    snippet_lines: int,
    snippets_per_file: int,
    max_bytes: int | None,
    include_source: bool,
) -> tuple[list[dict[str, Any]], str, int]:
    if not include_source:
        return [], "source_omitted", 0
    if snippets_per_file == 0:
        return [], "snippet_limit_zero", 0
    if max_bytes is not None and max_bytes <= 0:
        return [], "budget_exhausted", 0

    data = get_file_bytes(pack, str(result["path"]))
    text = _decode_text(data)
    if text is None:
        return [], "binary_or_undecodable", 0
    lines = text.splitlines()
    if not lines:
        return [], "empty_text", 0

    anchors = _snippet_anchors(question, result, lines)
    snippets = []
    used_bytes = 0
    remaining_bytes = max_bytes
    seen_ranges: set[tuple[int, int]] = set()
    for line_no, matched in anchors:
        snippet = _make_snippet(lines, line_no, matched, snippet_lines, remaining_bytes)
        if snippet is None:
            continue
        start = snippet["start_line"]
        end = snippet["end_line"]
        if (start, end) in seen_ranges:
            continue
        seen_ranges.add((start, end))
        snippets.append(snippet)
        snippet_bytes = int(snippet["byte_count"])
        used_bytes += snippet_bytes
        if remaining_bytes is not None:
            remaining_bytes = max(0, remaining_bytes - snippet_bytes)
        if len(snippets) >= snippets_per_file:
            break
    status = "text" if snippets else ("budget_exhausted" if max_bytes is not None else "no_snippet")
    return snippets, status, used_bytes


def _snippet_anchors(
    question: str,
    result: dict[str, Any],
    lines: list[str],
) -> list[tuple[int, str]]:
    anchors: list[tuple[int, str]] = []
    tokens = _query_tokens(question)
    for index, line in enumerate(lines, start=1):
        lowered = line.lower()
        for token in tokens:
            if token in lowered:
                anchors.append((index, f"query:{token}"))
                break

    summary = result.get("summary", {})
    for field in ("symbols", "headings", "imports"):
        for item in summary.get(field, []):
            line = int(item.get("line", 0) or 0)
            if line > 0:
                label = item.get("name") or item.get("text") or item.get("target") or field
                anchors.append((line, f"{field}:{label}"))

    if not anchors:
        for index, line in enumerate(lines, start=1):
            if line.strip():
                anchors.append((index, "fallback:first-useful-line"))
                break
    return _dedupe_anchors(anchors, len(lines))


def _dedupe_anchors(anchors: list[tuple[int, str]], line_count: int) -> list[tuple[int, str]]:
    seen = set()
    deduped = []
    for line, matched in anchors:
        safe_line = min(max(1, line), line_count)
        key = (safe_line, matched)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((safe_line, matched))
    return deduped


def _snippet_range(anchor_line: int, line_count: int, snippet_lines: int) -> tuple[int, int]:
    half = max(0, snippet_lines // 2)
    start = max(1, anchor_line - half)
    end = min(line_count, start + snippet_lines - 1)
    start = max(1, end - snippet_lines + 1)
    return start, end


def _make_snippet(
    lines: list[str],
    anchor_line: int,
    matched: str,
    snippet_lines: int,
    max_bytes: int | None,
) -> dict[str, Any] | None:
    widths = [snippet_lines]
    if max_bytes is not None:
        widths.extend(range(min(snippet_lines - 1, len(lines)), 0, -1))

    seen_widths = set()
    for width in widths:
        if width in seen_widths:
            continue
        seen_widths.add(width)
        start, end = _snippet_range(anchor_line, len(lines), width)
        text = "\n".join(lines[start - 1 : end])
        byte_count = len(text.encode("utf-8"))
        if max_bytes is not None and byte_count > max_bytes:
            continue
        return {
            "start_line": start,
            "end_line": end,
            "matched": matched,
            "byte_count": byte_count,
            "text": text,
        }
    return None


def _diff_context_candidate(
    change_type: str,
    record: dict[str, Any],
    question: str,
    *,
    change_reasons: list[str],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    summary_delta: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    source_pack = "base" if change_type == "removed" else "target"
    summary = record.get("summary", {})
    delta = summary_delta or {}
    return {
        "path": record["path"],
        "change_type": change_type,
        "source_pack": source_pack,
        "language": record.get("language"),
        "size": record.get("size"),
        "sha256": record.get("sha256"),
        "score": _diff_context_score(change_type, record, question, change_reasons, delta),
        "why": [f"change:{change_type}", *[f"reason:{reason}" for reason in change_reasons]],
        "change_reasons": change_reasons,
        "summary": summary,
        "summary_delta": delta,
        "before": _visible_file_record(before) if before is not None else None,
        "after": _visible_file_record(after) if after is not None else None,
    }


def _diff_context_score(
    change_type: str,
    record: dict[str, Any],
    question: str,
    change_reasons: list[str],
    summary_delta: dict[str, list[str]],
) -> float:
    score = {"added": 100.0, "changed": 90.0, "removed": 70.0}.get(change_type, 50.0)
    score += len(change_reasons) * 3.0
    score += min(float(record.get("token_count") or 0) / 100.0, 10.0)
    score += min(sum(len(values) for values in summary_delta.values()) * 1.5, 12.0)
    haystack = _diff_context_haystack(record)
    for token in _query_tokens(question):
        if token in haystack:
            score += 5.0
    return round(score, 2)


def _diff_context_haystack(record: dict[str, Any]) -> str:
    summary = record.get("_summary") or record.get("summary", {})
    values = [str(record.get("path", "")), str(record.get("language") or "")]
    values.extend(str(term) for term in summary.get("top_terms", []))
    for field, key in (("symbols", "name"), ("imports", "target"), ("headings", "text")):
        for item in summary.get(field, []):
            values.append(str(item.get(key, "")))
    return " ".join(values).lower()


def _diff_context_type_order(change_type: str) -> int:
    return {"added": 0, "changed": 1, "removed": 2}.get(change_type, 3)


def _diff_context_snippets(
    source_pack: Path | str,
    question: str,
    result: dict[str, Any],
    snippet_lines: int,
    snippets_per_file: int,
    max_bytes: int | None,
    include_source: bool,
    *,
    peer_pack: Path | str | None = None,
) -> tuple[list[dict[str, Any]], str, int]:
    if not include_source:
        return [], "source_omitted", 0
    if snippets_per_file == 0:
        return [], "snippet_limit_zero", 0
    if max_bytes is not None and max_bytes <= 0:
        return [], "budget_exhausted", 0

    data = get_file_bytes(source_pack, str(result["path"]))
    text = _decode_text(data)
    if text is None:
        return [], "binary_or_undecodable", 0
    lines = text.splitlines()
    if not lines:
        return [], "empty_text", 0

    anchors: list[tuple[int, str]] = []
    if result.get("change_type") == "changed" and peer_pack is not None:
        anchors.extend(_diff_context_line_anchors(peer_pack, source_pack, str(result["path"])))
    if result.get("change_type") in {"added", "removed"}:
        anchors.extend(_first_useful_anchor(lines, f"diff:{result.get('change_type')}"))
    anchors.extend(_snippet_anchors(question, result, lines))
    anchors = _dedupe_anchors(anchors, len(lines))

    snippets = []
    used_bytes = 0
    remaining_bytes = max_bytes
    seen_ranges: set[tuple[int, int]] = set()
    for line_no, matched in anchors:
        snippet = _make_snippet(lines, line_no, matched, snippet_lines, remaining_bytes)
        if snippet is None:
            continue
        start = snippet["start_line"]
        end = snippet["end_line"]
        if (start, end) in seen_ranges:
            continue
        seen_ranges.add((start, end))
        snippets.append(snippet)
        snippet_bytes = int(snippet["byte_count"])
        used_bytes += snippet_bytes
        if remaining_bytes is not None:
            remaining_bytes = max(0, remaining_bytes - snippet_bytes)
        if len(snippets) >= snippets_per_file:
            break
    status = "text" if snippets else ("budget_exhausted" if max_bytes is not None else "no_snippet")
    return snippets, status, used_bytes


def _diff_context_line_anchors(
    base_pack: Path | str,
    target_pack: Path | str,
    path: str,
) -> list[tuple[int, str]]:
    try:
        before_text = _decode_text(get_file_bytes(base_pack, path))
        after_text = _decode_text(get_file_bytes(target_pack, path))
    except KeyError:
        return []
    if before_text is None or after_text is None:
        return []
    before_lines = before_text.splitlines()
    after_lines = after_text.splitlines()
    if not after_lines:
        return []
    anchors = []
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if j1 < len(after_lines):
            line = j1 + 1
        elif j2 > 0:
            line = j2
        else:
            line = 1
        anchors.append((line, f"diff:{tag}"))
    return anchors


def _first_useful_anchor(lines: list[str], label: str) -> list[tuple[int, str]]:
    for index, line in enumerate(lines, start=1):
        if line.strip():
            return [(index, label)]
    return []


def _eval_source_summary(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": source.get("path"),
        "language": source.get("language"),
        "size": source.get("size"),
        "sha256": source.get("sha256"),
        "score": source.get("score"),
        "why": source.get("why", []),
        "snippet_status": source.get("snippet_status"),
        "snippet_count": len(source.get("snippets", [])),
        "source_bytes": source.get("source_bytes", 0),
    }


def _capsule_symbols(items: list[dict[str, Any]], limit: int) -> list[list[Any]]:
    return [
        [str(item.get("kind", "")), str(item.get("name", "")), int(item.get("line", 0) or 0)]
        for item in items[:limit]
    ]


def _capsule_imports(items: list[dict[str, Any]], limit: int) -> list[list[Any]]:
    return [
        [str(item.get("target", "")), int(item.get("line", 0) or 0)]
        for item in items[:limit]
    ]


def _capsule_headings(items: list[dict[str, Any]], limit: int) -> list[list[Any]]:
    return [
        [
            int(item.get("level", 0) or 0),
            str(item.get("text", "")),
            int(item.get("line", 0) or 0),
        ]
        for item in items[:limit]
    ]


def _prepare_handoff_dir(out_path: Path, force: bool) -> None:
    if out_path.exists():
        if not force:
            raise FileExistsError(f"Handoff output already exists: {out_path}")
        if out_path.is_dir():
            shutil.rmtree(out_path)
        else:
            out_path.unlink()
    out_path.mkdir(parents=True, exist_ok=False)


def _prepare_demo_dir(out_path: Path, force: bool) -> None:
    if out_path.exists():
        if not force:
            raise FileExistsError(f"Demo output already exists: {out_path}")
        if out_path.is_dir():
            shutil.rmtree(out_path)
        else:
            out_path.unlink()
    out_path.mkdir(parents=True, exist_ok=False)


def _write_demo_repo(repo_path: Path) -> None:
    (repo_path / "docs").mkdir(parents=True, exist_ok=True)
    (repo_path / "tests").mkdir(parents=True, exist_ok=True)
    (repo_path / "README.md").write_text(
        "# Demo Store\n\n"
        "A tiny repository used to prove RepoMori packs, context bundles, memory runs, and MCP tools.\n\n"
        "The important code is the sqlite-backed `Store` class in `app.py`.\n",
        encoding="utf-8",
    )
    (repo_path / "app.py").write_text(
        "import sqlite3\n"
        "from pathlib import Path\n\n\n"
        "class Store:\n"
        "    def __init__(self, path='notes.sqlite'):\n"
        "        self.path = Path(path)\n\n"
        "    def connect(self):\n"
        "        return sqlite3.connect(str(self.path))\n\n"
        "    def setup(self):\n"
        "        with self.connect() as conn:\n"
        "            conn.execute('create table if not exists notes (title text, body text)')\n\n"
        "    def save_note(self, title, body):\n"
        "        self.setup()\n"
        "        with self.connect() as conn:\n"
        "            conn.execute('insert into notes values (?, ?)', (title, body))\n\n"
        "    def list_titles(self):\n"
        "        self.setup()\n"
        "        with self.connect() as conn:\n"
        "            rows = conn.execute('select title from notes order by title').fetchall()\n"
        "        return [row[0] for row in rows]\n\n\n"
        "def main():\n"
        "    store = Store()\n"
        "    store.save_note('repomori', 'machine-readable repository memory')\n"
        "    return store.list_titles()\n",
        encoding="utf-8",
    )
    (repo_path / "docs" / "architecture.md").write_text(
        "# Architecture\n\n"
        "The demo has one storage boundary. `Store.connect` owns sqlite connection creation, "
        "while `save_note` and `list_titles` use that boundary instead of opening their own database handles.\n",
        encoding="utf-8",
    )
    (repo_path / "tests" / "test_store.py").write_text(
        "from app import Store\n\n\n"
        "def test_store_lists_saved_titles(tmp_path):\n"
        "    store = Store(tmp_path / 'notes.sqlite')\n"
        "    store.save_note('alpha', 'first')\n"
        "    store.save_note('beta', 'second')\n"
        "    assert store.list_titles() == ['alpha', 'beta']\n",
        encoding="utf-8",
    )
    (repo_path / ".gitignore").write_text("__pycache__/\n*.pyc\n*.sqlite\n", encoding="utf-8")


def _demo_output_readme(report: dict[str, Any]) -> str:
    out_dir = report.get("out_dir")
    pack = report.get("summary", {}).get("pack_path")
    config = report.get("summary", {}).get("config_path")
    question = report.get("question")
    return (
        "# RepoMori Demo Output\n\n"
        f"- Status: `{report.get('status')}`\n"
        f"- Demo repo: `{report.get('repo_path')}`\n"
        f"- Pack: `{pack}`\n"
        f"- Config: `{config}`\n"
        f"- Question: `{question}`\n\n"
        "Try these next:\n\n"
        "```powershell\n"
        f"python -m repomori query {pack} \"{question}\" --json\n"
        f"python -m repomori context {pack} \"{question}\" --format markdown --out {out_dir}\\context.md\n"
        f"python -m repomori memory --config {config} --json\n"
        f"python -m repomori mcp --config {config}\n"
        "```\n"
    )


def _write_json(path: Path, payload: dict[str, Any], *, compact: bool = False) -> None:
    if compact:
        text = json.dumps(payload, separators=(",", ":"))
    else:
        text = json.dumps(payload, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def _artifact_record(root: Path, path: Path, kind: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _handoff_eval_questions(question: str, extra_questions: Iterable[str] | None) -> list[str]:
    return _unique_items(
        item.strip()
        for item in (question, *DEFAULT_EVAL_QUESTIONS, *(extra_questions or ()))
        if item.strip()
    )


def _handoff_manifest(
    question: str,
    out_path: Path,
    pack_info: dict[str, Any],
    verify_report: dict[str, Any],
    artifacts: list[dict[str, Any]],
    status: str,
    settings: dict[str, Any],
    base_pack_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": "repomori.handoff.v1",
        "status": status,
        "created_at": int(time.time()),
        "question": question,
        "out_dir": str(out_path),
        "pack": _pack_identity(pack_info),
        "verification": {
            "verified": verify_report.get("verified"),
            "error_count": verify_report.get("error_count"),
            "artifact": "verify.json",
        },
        "settings": settings,
        "artifacts": artifacts,
    }
    if base_pack_info is not None:
        manifest["base_pack"] = _pack_identity(base_pack_info)
    return manifest


def _handoff_readme(question: str, copied_pack: bool, has_compare: bool = False) -> str:
    pack_note = (
        "The `.repomori` pack is included in this directory.\n"
        if copied_pack
        else "The original `.repomori` pack is referenced in `manifest.json` but not copied here.\n"
    )
    compare_note = (
        "8. `compare.md` / `compare.json` - delta from the base pack.\n"
        if has_compare
        else ""
    )
    return (
        "# RepoMori Agent Handoff\n\n"
        f"Question: {question}\n\n"
        "Use these files in order:\n\n"
        "1. `manifest.json` - artifact list, hashes, settings, and verification status.\n"
        "2. `brief.md` - question-free repository orientation.\n"
        "3. `context.md` - compact source-backed context for quick reading.\n"
        "4. `context.json` - raw context bundle for tools.\n"
        "5. `capsule.json` - dense machine-readable repository state.\n"
        "6. `eval.md` / `eval.json` - context quality report.\n"
        "7. `verify.json` - pack integrity report.\n"
        f"{compare_note}\n"
        f"{pack_note}"
        "No AI provider, API key, or network call is required to consume this handoff.\n"
    )


def _check_handoff_artifact(
    root: Path,
    artifact: Any,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        _add_check_error(errors, "artifact", "", "Artifact entry must be an object.")
        return {"path": None, "exists": False, "valid": False}
    relative = str(artifact.get("path", ""))
    result = {
        "path": relative,
        "kind": artifact.get("kind"),
        "exists": False,
        "valid": False,
        "size": None,
        "sha256": None,
    }
    if not relative:
        _add_check_error(errors, "artifact", "", "Artifact path is missing.")
        return result
    try:
        path = _safe_child_path(root, relative)
    except ValueError as exc:
        _add_check_error(errors, "artifact", relative, str(exc))
        return result
    if not path.exists() or not path.is_file():
        _add_check_error(errors, "artifact", relative, "Artifact file not found.")
        return result

    data = path.read_bytes()
    actual_size = len(data)
    actual_hash = hashlib.sha256(data).hexdigest()
    result.update({"exists": True, "size": actual_size, "sha256": actual_hash})
    if actual_size != artifact.get("size"):
        _add_check_error(
            errors,
            "artifact",
            relative,
            "Artifact size mismatch.",
            expected=artifact.get("size"),
            actual=actual_size,
        )
    if actual_hash != artifact.get("sha256"):
        _add_check_error(
            errors,
            "artifact",
            relative,
            "Artifact SHA-256 mismatch.",
            expected=artifact.get("sha256"),
            actual=actual_hash,
        )
    result["valid"] = actual_size == artifact.get("size") and actual_hash == artifact.get("sha256")
    return result


def _check_handoff_json(root: Path, relative: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"path": relative, "exists": False, "valid_json": False}
    try:
        path = _safe_child_path(root, relative)
    except ValueError as exc:
        _add_check_error(errors, "json", relative, str(exc))
        return result
    if not path.exists():
        _add_check_error(errors, "json", relative, "Expected JSON artifact not found.")
        return result
    result["exists"] = True
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _add_check_error(errors, "json", relative, f"JSON artifact is invalid: {exc}")
        return result
    result["valid_json"] = True
    return result


def _check_handoff_pack_copy(pack_path: Path, errors: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"path": pack_path.name, "verified": False, "error_count": None}
    if not pack_path.exists():
        _add_check_error(errors, "pack_copy", pack_path.name, "Copied pack file not found.")
        return result
    try:
        verify_report = verify_pack(pack_path)
    except (FileNotFoundError, sqlite3.DatabaseError, ValueError, zlib.error) as exc:
        _add_check_error(errors, "pack_copy", pack_path.name, f"Copied pack verification failed: {exc}")
        return result
    result["verified"] = bool(verify_report.get("verified"))
    result["error_count"] = verify_report.get("error_count")
    if not result["verified"]:
        _add_check_error(
            errors,
            "pack_copy",
            pack_path.name,
            "Copied pack did not verify.",
            actual=verify_report.get("error_count"),
        )
    return result


def _safe_child_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise ValueError("Path escapes handoff directory.")
    return path


def _add_check_error(
    errors: list[dict[str, Any]],
    scope: str,
    path: str,
    message: str,
    *,
    expected: Any = None,
    actual: Any = None,
) -> None:
    error = {"scope": scope, "path": path, "message": message}
    if expected is not None:
        error["expected"] = expected
    if actual is not None:
        error["actual"] = actual
    errors.append(error)


def _eval_weak_signals(
    sources: list[dict[str, Any]],
    selected_count: int,
    snippet_count: int,
    top_score: float,
) -> list[str]:
    signals = []
    if selected_count == 0:
        signals.append("no_sources")
    if selected_count > 0 and snippet_count == 0:
        signals.append("no_snippets")
    if selected_count > 0 and top_score < 4.0:
        signals.append("low_top_score")
    statuses = {str(source.get("snippet_status")) for source in sources}
    if "budget_exhausted" in statuses:
        signals.append("budget_exhausted")
    if selected_count > 0 and statuses == {"binary_or_undecodable"}:
        signals.append("binary_only")
    return signals


def _eval_suggestions(weak_signals: list[str]) -> list[str]:
    suggestion_map = {
        "no_sources": "Add phrase matching, synonyms, or stronger path/symbol matching for questions with no selected files.",
        "no_snippets": "Improve snippet anchors or increase the source budget so selected files produce usable evidence.",
        "low_top_score": "Tune ranking weights for exact phrase hits, symbols, imports, and path matches.",
        "budget_exhausted": "Increase --max-bytes or reduce --max-files / --snippets-per-file for tighter bundles.",
        "binary_only": "Add binary-aware metadata or type-specific extractors for non-text assets.",
    }
    return [suggestion_map[signal] for signal in weak_signals if signal in suggestion_map]


def _unique_items(items: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _pack_identity(pack_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": pack_info.get("schema_version"),
        "repo_path": pack_info.get("repo_path"),
        "pack_path": pack_info.get("pack_path"),
        "created_at": pack_info.get("created_at"),
        "logical_bytes": pack_info.get("logical_bytes"),
        "pack_bytes": pack_info.get("pack_bytes"),
        "counts": pack_info.get("counts", {}),
    }


def _pack_file_records(pack: Path | str) -> dict[str, dict[str, Any]]:
    with closing(_open_pack(pack)) as conn:
        rows = conn.execute(
            """
            SELECT path, language, size, sha256, is_text, line_count, token_count, summary_json
            FROM files
            ORDER BY path
            """
        ).fetchall()
    records = {}
    for row in rows:
        summary = _safe_json(row["summary_json"], {})
        path = row["path"]
        records[path] = {
            "path": path,
            "language": row["language"],
            "size": row["size"],
            "sha256": row["sha256"],
            "is_text": bool(row["is_text"]),
            "line_count": row["line_count"],
            "token_count": row["token_count"],
            "summary": _compact_summary(summary),
            "_summary": summary,
        }
    return records


def _visible_file_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _compare_record_reasons(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    reasons = []
    for key in ("sha256", "size", "language", "is_text", "line_count", "token_count"):
        if before.get(key) != after.get(key):
            reasons.append(key)
    before_summary = before.get("_summary", {})
    after_summary = after.get("_summary", {})
    if before_summary.get("kind") != after_summary.get("kind"):
        reasons.append("summary_kind")
    for field in ("symbols", "imports", "headings", "top_terms"):
        if _summary_identity_set(before_summary, field) != _summary_identity_set(after_summary, field):
            reasons.append(field)
    return reasons


def _changed_file_record(path: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": path,
        "change_reasons": _compare_record_reasons(before, after),
        "before": _visible_file_record(before),
        "after": _visible_file_record(after),
        "summary_delta": _summary_delta(before.get("_summary", {}), after.get("_summary", {})),
    }


def _summary_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    delta = {}
    for field, added_key, removed_key in (
        ("symbols", "added_symbols", "removed_symbols"),
        ("imports", "added_imports", "removed_imports"),
        ("headings", "added_headings", "removed_headings"),
        ("top_terms", "added_terms", "removed_terms"),
    ):
        before_values = _summary_identity_set(before, field)
        after_values = _summary_identity_set(after, field)
        added = sorted(after_values - before_values)
        removed = sorted(before_values - after_values)
        if added:
            delta[added_key] = added[:16]
        if removed:
            delta[removed_key] = removed[:16]
    return delta


def _summary_identity_set(summary: dict[str, Any], field: str) -> set[str]:
    if field == "top_terms":
        return {str(item) for item in summary.get("top_terms", []) if str(item)}
    values = set()
    for item in summary.get(field, []):
        if field == "symbols":
            name = str(item.get("name", "")).strip()
            if name:
                values.add(f"{item.get('kind', 'symbol')}:{name}")
        elif field == "imports":
            target = str(item.get("target", "")).strip()
            if target:
                values.add(target)
        elif field == "headings":
            text = str(item.get("text", "")).strip()
            if text:
                values.add(f"{item.get('level', '')}:{text}")
    return values


def _language_delta(
    base_records: dict[str, dict[str, Any]],
    target_records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    base_counts = Counter(str(record.get("language") or "unknown") for record in base_records.values())
    target_counts = Counter(str(record.get("language") or "unknown") for record in target_records.values())
    rows = []
    for language in sorted(set(base_counts) | set(target_counts)):
        base_count = base_counts.get(language, 0)
        target_count = target_counts.get(language, 0)
        delta = target_count - base_count
        if delta:
            rows.append(
                {
                    "language": language,
                    "base_count": base_count,
                    "target_count": target_count,
                    "delta": delta,
                }
            )
    return rows


def _append_compare_file_section(lines: list[str], title: str, files: list[dict[str, Any]]) -> None:
    lines.extend([f"## {title}", ""])
    if not files:
        lines.extend([f"No {title.lower()}.", ""])
        return
    for item in files:
        lines.append(
            f"- `{item.get('path')}` [{item.get('language') or 'unknown'}] "
            f"{item.get('size', 0)} bytes sha=`{item.get('sha256')}`"
        )
    lines.append("")


def _brief_file_score(record: dict[str, Any]) -> float:
    path = str(record.get("path", ""))
    lowered = path.lower()
    name = Path(path).name.lower()
    score = 0.0
    if name.startswith("readme"):
        score = max(score, 100.0)
    if name in {"pyproject.toml", "package.json", "setup.py", "cargo.toml", "go.mod", "pom.xml"}:
        score = max(score, 95.0)
    if name in {"cli.py", "main.py", "app.py", "server.py", "index.js", "index.ts"} or lowered.endswith("/__main__.py"):
        score = max(score, 85.0)
    if lowered.startswith("tests/") or "/tests/" in lowered:
        score = max(score, 55.0)
    if record.get("is_text"):
        score += min(float(record.get("token_count") or 0) / 100.0, 20.0)
    summary = record.get("_summary", {})
    if summary.get("symbols"):
        score += 10.0
    if summary.get("headings"):
        score += 5.0
    return score


def _brief_suggestions(
    records: dict[str, dict[str, Any]],
    key_files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
) -> list[str]:
    suggestions = []
    if not key_files:
        suggestions.append("No key files were identified; add README or package metadata for stronger orientation.")
    if not symbols and any(record.get("is_text") for record in records.values()):
        suggestions.append("No symbols were extracted; add language-specific structure extractors if this repo is mostly code.")
    if any(not record.get("is_text") for record in records.values()):
        suggestions.append("Binary files are represented by metadata only; add type-specific extractors if they matter to agents.")
    return suggestions


def _append_brief_file_section(lines: list[str], title: str, files: list[dict[str, Any]]) -> None:
    lines.extend([f"## {title}", ""])
    if not files:
        lines.extend([f"No {title.lower()} identified.", ""])
        return
    for item in files:
        summary = item.get("summary", {})
        bits = []
        symbols = summary.get("symbols", [])
        if symbols:
            bits.append("symbols=" + ",".join(str(symbol.get("name", "")) for symbol in symbols[:5]))
        headings = summary.get("headings", [])
        if headings:
            bits.append("headings=" + ",".join(str(heading.get("text", "")) for heading in headings[:3]))
        suffix = " " + " ".join(bits) if bits else ""
        lines.append(
            f"- `{item.get('path')}` [{item.get('language') or 'unknown'}] "
            f"{item.get('size', 0)} bytes sha=`{item.get('sha256')}`{suffix}"
        )
    lines.append("")


def _snapshot_stamp(timestamp: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(timestamp))


def _unique_snapshot_pack_path(out_path: Path, repo_name: str, stamp: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo_name).strip(".-") or "repo"
    base = out_path / f"{safe_name}-{stamp}.repomori"
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = out_path / f"{safe_name}-{stamp}-{index}.repomori"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not allocate a snapshot pack name in {out_path}")


def _snapshot_previous_pack(out_path: Path) -> Path | None:
    index_path = out_path / "snapshots.json"
    if not index_path.exists():
        return None
    try:
        index = _read_snapshot_index(index_path, out_path)
    except (json.JSONDecodeError, ValueError):
        return None
    latest = index.get("latest")
    if not isinstance(latest, dict):
        return None
    pack_path = latest.get("pack_path")
    if not pack_path:
        return None
    path = Path(str(pack_path))
    return path if path.exists() else None


def _snapshot_exclude_paths(repo_path: Path, out_path: Path) -> tuple[Path, ...]:
    if out_path == repo_path:
        return ()
    try:
        out_path.relative_to(repo_path)
    except ValueError:
        return ()
    return (out_path,)


def _update_snapshot_index(out_path: Path, report: dict[str, Any]) -> dict[str, Any]:
    index_path = out_path / "snapshots.json"
    index = _read_snapshot_index(index_path, out_path)
    entry = _snapshot_index_entry(report)
    snapshots = [
        snapshot
        for snapshot in index.get("snapshots", [])
        if snapshot.get("pack_path") != entry["pack_path"]
    ]
    snapshots.append(entry)
    snapshots.sort(key=lambda item: _snapshot_entry_sort_key(out_path, item))
    updated = {
        "schema_version": "repomori.snapshots.v1",
        "out_dir": str(out_path),
        "updated_at": int(time.time()),
        "snapshot_count": len(snapshots),
        "latest": entry,
        "snapshots": snapshots,
    }
    _write_json(index_path, updated)
    return updated


def _read_snapshot_index(index_path: Path, out_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        return {
            "schema_version": "repomori.snapshots.v1",
            "out_dir": str(out_path),
            "updated_at": None,
            "snapshot_count": 0,
            "latest": None,
            "snapshots": [],
        }
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != "repomori.snapshots.v1":
        raise ValueError(f"Unexpected snapshot index schema: {index_path}")
    if not isinstance(data.get("snapshots"), list):
        raise ValueError(f"Snapshot index snapshots must be a list: {index_path}")
    return data


def _snapshot_index_entry(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary", {})
    artifacts = report.get("artifacts", {})
    pack_path = Path(str(summary.get("pack_path", "")))
    comparison = report.get("comparison")
    compare_summary = comparison.get("summary", {}) if isinstance(comparison, dict) else {}
    return {
        "created_at": report.get("created_at"),
        "status": report.get("status"),
        "repo_path": report.get("repo_path"),
        "pack_path": str(pack_path),
        "pack_name": artifacts.get("pack"),
        "pack_sha256": _path_sha256(pack_path),
        "latest_pack": summary.get("latest_pack"),
        "previous_latest_pack": summary.get("previous_latest_pack"),
        "snapshot_json": artifacts.get("snapshot_json"),
        "snapshot_markdown": artifacts.get("snapshot_markdown"),
        "compare_json": artifacts.get("compare_json"),
        "compare_markdown": artifacts.get("compare_markdown"),
        "diff_context_json": artifacts.get("diff_context_json"),
        "diff_context_markdown": artifacts.get("diff_context_markdown"),
        "file_count": summary.get("file_count"),
        "logical_bytes": summary.get("logical_bytes"),
        "pack_bytes": summary.get("pack_bytes"),
        "incremental": summary.get("incremental"),
        "incremental_base_pack": summary.get("incremental_base_pack"),
        "reused_file_count": summary.get("reused_file_count"),
        "rebuilt_file_count": summary.get("rebuilt_file_count"),
        "reused_chunk_count": summary.get("reused_chunk_count"),
        "verify_passed": summary.get("verify_passed"),
        "compared_with_previous": summary.get("compared_with_previous"),
        "handoff_dir": summary.get("handoff_dir"),
        "handoff_passed": summary.get("handoff_passed"),
        "diff_context_status": summary.get("diff_context_status"),
        "diff_context_selected_count": summary.get("diff_context_selected_count"),
        "diff_context_added_count": summary.get("diff_context_added_count"),
        "diff_context_changed_count": summary.get("diff_context_changed_count"),
        "diff_context_removed_count": summary.get("diff_context_removed_count"),
        "added_count": compare_summary.get("added_count"),
        "removed_count": compare_summary.get("removed_count"),
        "changed_count": compare_summary.get("changed_count"),
        "unchanged_count": compare_summary.get("unchanged_count"),
        "byte_delta": compare_summary.get("byte_delta"),
    }


def _snapshot_stats_entry(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    item = snapshot if isinstance(snapshot, dict) else {}
    reused_files = _snapshot_int(item, "reused_file_count")
    rebuilt_files = _snapshot_int(item, "rebuilt_file_count")
    file_decisions = reused_files + rebuilt_files
    pack_bytes = _snapshot_int(item, "pack_bytes")
    logical_bytes = _snapshot_int(item, "logical_bytes")
    return {
        "created_at": item.get("created_at"),
        "status": item.get("status"),
        "pack_name": item.get("pack_name"),
        "pack_path": item.get("pack_path"),
        "pack_sha256": item.get("pack_sha256"),
        "incremental": bool(item.get("incremental")),
        "incremental_base_pack": item.get("incremental_base_pack"),
        "file_count": _snapshot_int(item, "file_count"),
        "logical_bytes": logical_bytes,
        "pack_bytes": pack_bytes,
        "logical_to_pack_ratio": _ratio(logical_bytes, pack_bytes),
        "reused_file_count": reused_files,
        "rebuilt_file_count": rebuilt_files,
        "reused_chunk_count": _snapshot_int(item, "reused_chunk_count"),
        "reuse_percent": _percent(reused_files, file_decisions),
        "added_count": _snapshot_int(item, "added_count"),
        "removed_count": _snapshot_int(item, "removed_count"),
        "changed_count": _snapshot_int(item, "changed_count"),
        "verify_passed": bool(item.get("verify_passed")),
        "handoff_dir": item.get("handoff_dir"),
    }


def _snapshot_int(snapshot: dict[str, Any], field: str) -> int:
    value = snapshot.get(field)
    return value if isinstance(value, int) else 0


def _doctor_check_snapshot(
    out_path: Path,
    snapshot: dict[str, Any],
    index: int,
    verify_packs: bool,
    summary: dict[str, Any],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    pack_path = _recorded_snapshot_path(out_path, snapshot.get("pack_path"))
    pack_label = str(pack_path) if pack_path is not None else str(snapshot.get("pack_path"))
    if pack_path is None:
        _add_doctor_issue(errors, "pack", pack_label, "Snapshot pack_path is missing.", index=index)
    elif not pack_path.exists():
        _add_doctor_issue(errors, "pack", str(pack_path), "Indexed snapshot pack does not exist.", index=index)
    elif not pack_path.is_file():
        _add_doctor_issue(errors, "pack", str(pack_path), "Indexed snapshot pack is not a file.", index=index)
    else:
        summary["checked_packs"] += 1
        expected_hash = snapshot.get("pack_sha256")
        actual_hash = _path_sha256(pack_path)
        if expected_hash and actual_hash != expected_hash:
            _add_doctor_issue(
                errors,
                "pack",
                str(pack_path),
                "Indexed snapshot pack SHA-256 does not match.",
                index=index,
                expected=expected_hash,
                actual=actual_hash,
            )
        elif not expected_hash:
            _add_doctor_issue(
                warnings,
                "pack",
                str(pack_path),
                "Indexed snapshot pack has no recorded SHA-256.",
                index=index,
            )

        if verify_packs:
            try:
                verify = verify_pack(pack_path)
            except (FileNotFoundError, sqlite3.DatabaseError, ValueError, zlib.error) as exc:
                _add_doctor_issue(
                    errors,
                    "verify",
                    str(pack_path),
                    f"Pack verification could not run: {exc}",
                    index=index,
                )
            else:
                if verify.get("verified"):
                    summary["verified_packs"] += 1
                else:
                    _add_doctor_issue(
                        errors,
                        "verify",
                        str(pack_path),
                        "Pack verification failed.",
                        index=index,
                        actual=verify.get("error_count"),
                    )

    for field in ("snapshot_json", "snapshot_markdown"):
        _doctor_check_snapshot_artifact(out_path, snapshot, index, field, errors, summary)
    for field in ("compare_json", "compare_markdown"):
        if snapshot.get(field):
            _doctor_check_snapshot_artifact(out_path, snapshot, index, field, errors, summary)
    for field in ("diff_context_json", "diff_context_markdown"):
        if snapshot.get(field):
            _doctor_check_snapshot_artifact(out_path, snapshot, index, field, errors, summary)

    handoff_value = snapshot.get("handoff_dir")
    if not handoff_value:
        return
    handoff_path = _recorded_snapshot_path(out_path, handoff_value)
    if handoff_path is None:
        _add_doctor_issue(errors, "handoff", str(handoff_value), "Recorded handoff path is empty.", index=index)
        return
    if not _is_within_path(out_path, handoff_path):
        if not handoff_path.exists():
            _add_doctor_issue(
                warnings,
                "handoff",
                str(handoff_path),
                "External handoff path is recorded but does not exist.",
                index=index,
            )
        else:
            _add_doctor_issue(
                warnings,
                "handoff",
                str(handoff_path),
                "External handoff path was not validated by snapshot doctor.",
                index=index,
            )
        return
    if not handoff_path.exists():
        _add_doctor_issue(errors, "handoff", str(handoff_path), "Recorded in-dir handoff does not exist.", index=index)
        return
    if not handoff_path.is_dir():
        _add_doctor_issue(errors, "handoff", str(handoff_path), "Recorded in-dir handoff is not a directory.", index=index)
        return
    summary["checked_handoffs"] += 1
    check = check_handoff_package(handoff_path)
    if not check.get("valid"):
        _add_doctor_issue(
            errors,
            "handoff",
            str(handoff_path),
            "Recorded in-dir handoff failed validation.",
            index=index,
            actual=check.get("error_count"),
        )


def _doctor_check_snapshot_artifact(
    out_path: Path,
    snapshot: dict[str, Any],
    index: int,
    field: str,
    errors: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    value = snapshot.get(field)
    path = _recorded_snapshot_path(out_path, value)
    label = str(path) if path is not None else str(value)
    summary["checked_artifacts"] += 1
    if path is None:
        _add_doctor_issue(errors, field, label, f"Recorded {field} path is missing.", index=index)
        return
    if not path.exists():
        _add_doctor_issue(errors, field, str(path), f"Recorded {field} path does not exist.", index=index)
        return
    if not path.is_file():
        _add_doctor_issue(errors, field, str(path), f"Recorded {field} path is not a file.", index=index)


def _add_doctor_issue(
    issues: list[dict[str, Any]],
    scope: str,
    path: str,
    message: str,
    *,
    index: int | None = None,
    expected: Any = None,
    actual: Any = None,
) -> None:
    issue: dict[str, Any] = {"scope": scope, "path": path, "message": message}
    if index is not None:
        issue["index"] = index
    if expected is not None:
        issue["expected"] = expected
    if actual is not None:
        issue["actual"] = actual
    issues.append(issue)


def _recorded_snapshot_path(out_path: Path, value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = out_path / path
    return path.resolve()


def _is_within_path(root: Path, path: Path) -> bool:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    return path_resolved == root_resolved or root_resolved in path_resolved.parents


def _snapshot_pack_key(out_path: Path, snapshot: dict[str, Any]) -> str:
    pack_path = _recorded_snapshot_path(out_path, snapshot.get("pack_path"))
    return str(pack_path) if pack_path is not None else ""


def _snapshot_entry_sort_key(out_path: Path, snapshot: dict[str, Any]) -> tuple[int, int, str]:
    pack_path = _recorded_snapshot_path(out_path, snapshot.get("pack_path"))
    pack_name = str(snapshot.get("pack_name") or (pack_path.name if pack_path is not None else ""))
    stem = Path(pack_name).stem
    suffix_order = 0
    match = re.search(r"\d{8}-\d{6}(?:-(\d+))?$", stem)
    if match and match.group(1):
        suffix_order = int(match.group(1))
    return (int(snapshot.get("created_at", 0) or 0), suffix_order, str(pack_path or ""))


def _snapshot_prune_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "created_at": snapshot.get("created_at"),
        "pack_name": snapshot.get("pack_name"),
        "pack_path": snapshot.get("pack_path"),
        "pack_sha256": snapshot.get("pack_sha256"),
        "snapshot_json": snapshot.get("snapshot_json"),
        "snapshot_markdown": snapshot.get("snapshot_markdown"),
        "compare_json": snapshot.get("compare_json"),
        "compare_markdown": snapshot.get("compare_markdown"),
        "diff_context_json": snapshot.get("diff_context_json"),
        "diff_context_markdown": snapshot.get("diff_context_markdown"),
        "handoff_dir": snapshot.get("handoff_dir"),
    }


def _snapshot_prune_targets(out_path: Path, snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    targets: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for kind, field in (
        ("pack", "pack_path"),
        ("snapshot_json", "snapshot_json"),
        ("snapshot_markdown", "snapshot_markdown"),
        ("compare_json", "compare_json"),
        ("compare_markdown", "compare_markdown"),
        ("diff_context_json", "diff_context_json"),
        ("diff_context_markdown", "diff_context_markdown"),
    ):
        path = _recorded_snapshot_path(out_path, snapshot.get(field))
        if path is None:
            continue
        targets.append(
            {
                "kind": kind,
                "path": str(path),
                "exists": path.exists(),
                "directory": path.is_dir(),
            }
        )

    handoff_path = _recorded_snapshot_path(out_path, snapshot.get("handoff_dir"))
    if handoff_path is None:
        return targets, skipped
    handoff_target = {
        "kind": "handoff_dir",
        "path": str(handoff_path),
        "exists": handoff_path.exists(),
        "directory": True,
    }
    if _is_within_path(out_path, handoff_path):
        targets.append(handoff_target)
    else:
        skipped.append({**handoff_target, "reason": "skipped_external"})
    return targets, skipped


def _add_prune_error(errors: list[dict[str, Any]], scope: str, path: str, message: str) -> None:
    errors.append({"scope": scope, "path": path, "message": message})


def _format_memory_config(profile: str, settings: dict[str, Any]) -> str:
    lines = [
        'schema_version = "repomori.config.v1"',
        f"default_profile = {_toml_value(profile)}",
        "",
        f"[profiles.{profile}]",
    ]
    for key in (
        "repo",
        "out_dir",
        "handoff_question",
        "no_handoff",
        "keep",
        "prune_apply",
        "verify_packs",
        "timeline_limit",
        "chunk_size",
        "incremental",
        "compare",
        "compare_limit",
        "diff_context",
        "diff_context_question",
        "diff_context_limit",
        "diff_context_snippet_lines",
        "diff_context_snippets_per_file",
        "diff_context_max_bytes",
        "diff_context_include_source",
    ):
        lines.append(f"{key} = {_toml_value(settings[key])}")
    return "\n".join(lines).rstrip() + "\n"


def _read_memory_config(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {"profiles": {}}
    current: dict[str, Any] = data
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section.startswith("profiles."):
                raise ValueError(f"Unsupported config section at {path}:{line_number}")
            profile = section[len("profiles.") :].strip()
            _validate_config_profile(profile)
            profiles = data.setdefault("profiles", {})
            current = profiles.setdefault(profile, {})
            continue
        if "=" not in line:
            raise ValueError(f"Invalid config line at {path}:{line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]+", key):
            raise ValueError(f"Invalid config key at {path}:{line_number}")
        current[key] = _parse_toml_value(value.strip(), path, line_number)
    return data


def _find_config_path(start_dir: Path | str | None) -> Path | None:
    start = Path(start_dir or Path.cwd()).resolve()
    if start.is_file():
        start = start.parent
    for path in (start, *start.parents):
        candidate = path / "repomori.toml"
        if candidate.exists():
            return candidate
    return None


def _normalize_memory_config_settings(path: Path, settings: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "handoff_question": "continue this repo",
        "no_handoff": False,
        "keep": 20,
        "prune_apply": False,
        "verify_packs": False,
        "timeline_limit": 5,
        "chunk_size": DEFAULT_CHUNK_SIZE,
        "incremental": True,
        "compare": True,
        "compare_limit": 50,
        "diff_context": False,
        "diff_context_question": "what changed?",
        "diff_context_limit": 8,
        "diff_context_snippet_lines": 12,
        "diff_context_snippets_per_file": 2,
        "diff_context_max_bytes": 8192,
        "diff_context_include_source": True,
    }
    normalized = {**defaults, **settings}
    for key in ("repo", "out_dir"):
        value = normalized.get(key)
        if value is None or not str(value).strip():
            raise ValueError(f"RepoMori config missing required key `{key}`: {path}")
        config_path = Path(str(value))
        if not config_path.is_absolute():
            config_path = path.parent / config_path
        normalized[key] = str(config_path.resolve())
    for key in ("no_handoff", "prune_apply", "verify_packs", "incremental", "compare", "diff_context", "diff_context_include_source"):
        normalized[key] = _coerce_config_bool(path, key, normalized[key])
    for key in ("keep", "timeline_limit", "chunk_size", "compare_limit", "diff_context_limit", "diff_context_snippet_lines", "diff_context_snippets_per_file", "diff_context_max_bytes"):
        normalized[key] = _coerce_config_int(path, key, normalized[key])
    normalized["handoff_question"] = str(normalized.get("handoff_question") or "")
    normalized["diff_context_question"] = str(normalized.get("diff_context_question") or "")
    return normalized


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))


def _parse_toml_value(raw: str, path: Path, line_number: int) -> Any:
    if raw in {"true", "false"}:
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid string value at {path}:{line_number}: {exc}") from exc
    raise ValueError(f"Unsupported config value at {path}:{line_number}")


def _validate_config_profile(profile: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
        raise ValueError("profile must contain only letters, numbers, underscores, or hyphens")


def _coerce_config_bool(path: Path, key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"RepoMori config key `{key}` must be true or false: {path}")


def _coerce_config_int(path: Path, key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"RepoMori config key `{key}` must be an integer: {path}")
    return value


AGENT_METHODS = (
    "agent.help",
    "ping",
    "memory.run",
    "timeline.read",
    "stats.read",
    "doctor.run",
    "query.run",
    "context.build",
    "diff_context.build",
    "handoff.build",
    "capsule.build",
    "file.get",
    "schema.list",
)

MCP_TOOLS = (
    {
        "name": "repomori_help",
        "title": "RepoMori Help",
        "description": "List RepoMori bridge methods and protocol metadata.",
        "agent_method": "agent.help",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_memory_run",
        "title": "RepoMori Memory Run",
        "description": "Run the configured offline memory cycle: snapshot, handoff, doctor, prune, and timeline.",
        "agent_method": "memory.run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "out_dir": {"type": "string"},
                "handoff_question": {"type": "string"},
                "no_handoff": {"type": "boolean"},
                "keep": {"type": "integer"},
                "prune_apply": {"type": "boolean"},
                "verify_packs": {"type": "boolean"},
                "timeline_limit": {"type": "integer"},
                "chunk_size": {"type": "integer"},
                "incremental": {"type": "boolean"},
                "compare": {"type": "boolean"},
                "compare_limit": {"type": "integer"},
                "diff_context": {"type": "boolean"},
                "diff_context_question": {"type": "string"},
                "diff_context_limit": {"type": "integer"},
                "diff_context_snippet_lines": {"type": "integer"},
                "diff_context_snippets_per_file": {"type": "integer"},
                "diff_context_max_bytes": {"type": "integer"},
                "diff_context_include_source": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "repomori_timeline_read",
        "title": "RepoMori Timeline Read",
        "description": "Read the configured snapshot timeline.",
        "agent_method": "timeline.read",
        "inputSchema": {
            "type": "object",
            "properties": {"out_dir": {"type": "string"}, "limit": {"type": ["integer", "null"]}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_stats_read",
        "title": "RepoMori Stats Read",
        "description": "Read snapshot incremental reuse and storage statistics.",
        "agent_method": "stats.read",
        "inputSchema": {
            "type": "object",
            "properties": {"out_dir": {"type": "string"}, "limit": {"type": ["integer", "null"]}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_doctor_run",
        "title": "RepoMori Doctor Run",
        "description": "Check snapshot directory health.",
        "agent_method": "doctor.run",
        "inputSchema": {
            "type": "object",
            "properties": {"out_dir": {"type": "string"}, "verify_packs": {"type": "boolean"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_query_run",
        "title": "RepoMori Query",
        "description": "Search a RepoMori pack or the latest configured snapshot pack.",
        "agent_method": "query.run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack": {"type": "string"},
                "out_dir": {"type": "string"},
                "text": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_context_build",
        "title": "RepoMori Context Build",
        "description": "Build a compact source-backed context bundle for a question.",
        "agent_method": "context.build",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack": {"type": "string"},
                "out_dir": {"type": "string"},
                "question": {"type": "string"},
                "limit": {"type": "integer"},
                "max_files": {"type": "integer"},
                "snippet_lines": {"type": "integer"},
                "snippets_per_file": {"type": "integer"},
                "max_bytes": {"type": ["integer", "null"]},
                "include_source": {"type": "boolean"},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_diff_context_build",
        "title": "RepoMori Diff Context Build",
        "description": "Build source-backed context for added, changed, and removed files between two packs.",
        "agent_method": "diff_context.build",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_pack": {"type": "string"},
                "target_pack": {"type": "string"},
                "out_dir": {"type": "string"},
                "question": {"type": "string"},
                "limit": {"type": "integer"},
                "max_files": {"type": "integer"},
                "snippet_lines": {"type": "integer"},
                "snippets_per_file": {"type": "integer"},
                "max_bytes": {"type": ["integer", "null"]},
                "include_source": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_handoff_build",
        "title": "RepoMori Handoff Build",
        "description": "Write a handoff package directory for another local agent.",
        "agent_method": "handoff.build",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack": {"type": "string"},
                "out_dir": {"type": "string"},
                "out": {"type": "string"},
                "question": {"type": "string"},
                "base_pack": {"type": "string"},
                "force": {"type": "boolean"},
                "copy_pack": {"type": "boolean"},
                "allow_unverified": {"type": "boolean"},
                "max_files": {"type": "integer"},
                "max_bytes": {"type": ["integer", "null"]},
                "snippet_lines": {"type": "integer"},
                "snippets_per_file": {"type": "integer"},
                "capsule_max_files": {"type": ["integer", "null"]},
                "top_terms": {"type": "integer"},
                "eval_questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "repomori_capsule_build",
        "title": "RepoMori Capsule Build",
        "description": "Export a dense machine-readable capsule for a pack.",
        "agent_method": "capsule.build",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack": {"type": "string"},
                "out_dir": {"type": "string"},
                "max_files": {"type": ["integer", "null"]},
                "top_terms": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_file_get",
        "title": "RepoMori File Get",
        "description": "Retrieve exact file bytes from a pack as text when possible plus base64.",
        "agent_method": "file.get",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack": {"type": "string"},
                "out_dir": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_schema_list",
        "title": "RepoMori Schema List",
        "description": "List supported RepoMori schemas, agent methods, and MCP tool names.",
        "agent_method": "schema.list",
        "inputSchema": {
            "type": "object",
            "properties": {"schema_version": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
)

MCP_TOOL_METHODS = {tool["name"]: tool["agent_method"] for tool in MCP_TOOLS}


def _agent_dispatch(
    method: str,
    params: dict[str, Any],
    *,
    config_path: Path | str | None,
    profile: str | None,
    start_dir: Path | str | None,
) -> dict[str, Any]:
    settings = _agent_config_settings(config_path, profile, start_dir)
    if method == "agent.help":
        return {
            "schema_version": "repomori.agent.help.v1",
            "protocol": "json-lines",
            "request": {"id": "optional", "method": "string", "params": "object"},
            "response": {"id": "mirrored", "ok": "boolean", "result": "object"},
            "methods": list(AGENT_METHODS),
        }
    if method == "ping":
        return {"schema_version": "repomori.agent.ping.v1", "status": "ok"}
    if method == "schema.list":
        schema_version = params.get("schema_version")
        if schema_version is not None and not isinstance(schema_version, str):
            raise ValueError("params.schema_version must be a string when supplied.")
        return schema_catalog(schema_version)
    if method == "memory.run":
        return run_memory_cycle(**_agent_memory_kwargs(params, settings))
    if method == "timeline.read":
        return read_snapshot_timeline(
            _agent_out_dir(params, settings),
            limit=_agent_optional_int(params, "limit", None),
        )
    if method == "stats.read":
        return read_snapshot_stats(
            _agent_out_dir(params, settings),
            limit=_agent_optional_int(params, "limit", 10),
        )
    if method == "doctor.run":
        return doctor_snapshot_dir(
            _agent_out_dir(params, settings),
            verify_packs=bool(params.get("verify_packs", settings.get("verify_packs", False))),
        )
    if method == "query.run":
        return {
            "schema_version": "repomori.agent.query.v1",
            "results": query_pack(
                _agent_pack(params, settings),
                _agent_required_str(params, "text"),
                limit=_agent_int(params, "limit", 10),
            ),
        }
    if method == "context.build":
        limit = _agent_int(params, "max_files", _agent_int(params, "limit", 8))
        return build_context_bundle(
            _agent_pack(params, settings),
            _agent_required_str(params, "question"),
            limit=limit,
            snippet_lines=_agent_int(params, "snippet_lines", 12),
            max_bytes=_agent_optional_int(params, "max_bytes", None),
            snippets_per_file=_agent_int(params, "snippets_per_file", 2),
            include_source=bool(params.get("include_source", True)),
        )
    if method == "diff_context.build":
        base_pack, target_pack = _agent_pack_pair(params, settings)
        limit = _agent_int(params, "max_files", _agent_int(params, "limit", 8))
        question = str(params.get("question", "what changed?"))
        return build_diff_context_bundle(
            base_pack,
            target_pack,
            question,
            limit=limit,
            snippet_lines=_agent_int(params, "snippet_lines", 12),
            max_bytes=_agent_optional_int(params, "max_bytes", None),
            snippets_per_file=_agent_int(params, "snippets_per_file", 2),
            include_source=bool(params.get("include_source", True)),
        )
    if method == "handoff.build":
        out = params.get("out") or params.get("out_dir")
        if not isinstance(out, str) or not out.strip():
            raise ValueError("handoff.build requires params.out or params.out_dir.")
        return build_handoff_package(
            _agent_pack(params, settings),
            _agent_required_str(params, "question"),
            out,
            base_pack=params.get("base_pack"),
            force=bool(params.get("force", False)),
            copy_pack=bool(params.get("copy_pack", False)),
            allow_unverified=bool(params.get("allow_unverified", False)),
            max_files=_agent_int(params, "max_files", 8),
            max_bytes=_agent_optional_int(params, "max_bytes", None),
            snippet_lines=_agent_int(params, "snippet_lines", 12),
            snippets_per_file=_agent_int(params, "snippets_per_file", 2),
            capsule_max_files=_agent_optional_int(params, "capsule_max_files", None),
            top_terms=_agent_int(params, "top_terms", 128),
            eval_questions=params.get("eval_questions"),
        )
    if method == "capsule.build":
        return build_capsule(
            _agent_pack(params, settings),
            max_files=_agent_optional_int(params, "max_files", None),
            top_terms=_agent_int(params, "top_terms", 128),
        )
    if method == "file.get":
        file_path = _agent_required_str(params, "path")
        data = get_file_bytes(_agent_pack(params, settings), file_path)
        text = _decode_text(data)
        return {
            "schema_version": "repomori.agent.file.v1",
            "path": file_path,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "is_text": text is not None,
            "text": text,
            "base64": base64.b64encode(data).decode("ascii"),
        }
    raise NotImplementedError(f"Unknown agent method: {method}")


def _agent_config_settings(
    config_path: Path | str | None,
    profile: str | None,
    start_dir: Path | str | None,
) -> dict[str, Any]:
    if config_path is not None or profile is not None:
        return load_memory_config(config_path, start_dir=start_dir, profile=profile).get("settings", {})
    try:
        return load_memory_config(None, start_dir=start_dir, profile=None).get("settings", {})
    except FileNotFoundError:
        return {}


def _agent_memory_kwargs(params: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    repo = params.get("repo", settings.get("repo"))
    out_dir = params.get("out_dir", settings.get("out_dir"))
    if not repo:
        raise ValueError("memory.run requires params.repo or config repo.")
    if not out_dir:
        raise ValueError("memory.run requires params.out_dir or config out_dir.")
    return {
        "repo": repo,
        "out_dir": out_dir,
        "handoff_question": str(params.get("handoff_question", settings.get("handoff_question", "continue this repo"))),
        "no_handoff": bool(params.get("no_handoff", settings.get("no_handoff", False))),
        "keep": _agent_int(params, "keep", int(settings.get("keep", 20))),
        "prune_apply": bool(params.get("prune_apply", settings.get("prune_apply", False))),
        "verify_packs": bool(params.get("verify_packs", settings.get("verify_packs", False))),
        "timeline_limit": _agent_int(params, "timeline_limit", int(settings.get("timeline_limit", 5))),
        "chunk_size": _agent_int(params, "chunk_size", int(settings.get("chunk_size", DEFAULT_CHUNK_SIZE))),
        "incremental": bool(params.get("incremental", settings.get("incremental", True))),
        "compare": bool(params.get("compare", settings.get("compare", True))),
        "compare_limit": _agent_int(params, "compare_limit", int(settings.get("compare_limit", 50))),
        "diff_context": bool(params.get("diff_context", settings.get("diff_context", False))),
        "diff_context_question": str(params.get("diff_context_question", settings.get("diff_context_question", "what changed?"))),
        "diff_context_limit": _agent_int(params, "diff_context_limit", int(settings.get("diff_context_limit", 8))),
        "diff_context_snippet_lines": _agent_int(params, "diff_context_snippet_lines", int(settings.get("diff_context_snippet_lines", 12))),
        "diff_context_snippets_per_file": _agent_int(params, "diff_context_snippets_per_file", int(settings.get("diff_context_snippets_per_file", 2))),
        "diff_context_max_bytes": _agent_int(params, "diff_context_max_bytes", int(settings.get("diff_context_max_bytes", 8192))),
        "diff_context_include_source": bool(params.get("diff_context_include_source", settings.get("diff_context_include_source", True))),
    }


def _agent_out_dir(params: dict[str, Any], settings: dict[str, Any]) -> str:
    out_dir = params.get("out_dir", settings.get("out_dir"))
    if not isinstance(out_dir, str) or not out_dir.strip():
        raise ValueError("Method requires params.out_dir or config out_dir.")
    return out_dir


def _agent_pack(params: dict[str, Any], settings: dict[str, Any]) -> str:
    pack = params.get("pack")
    if isinstance(pack, str) and pack.strip():
        return pack
    timeline = read_snapshot_timeline(_agent_out_dir(params, settings), limit=1)
    latest = timeline.get("latest")
    if isinstance(latest, dict) and latest.get("pack_path"):
        return str(latest["pack_path"])
    raise ValueError("Method requires params.pack or a snapshot timeline with latest pack.")


def _agent_pack_pair(params: dict[str, Any], settings: dict[str, Any]) -> tuple[str, str]:
    base_pack = params.get("base_pack")
    target_pack = params.get("target_pack")
    if isinstance(base_pack, str) and base_pack.strip() and isinstance(target_pack, str) and target_pack.strip():
        return base_pack, target_pack
    out_dir = params.get("out_dir", settings.get("out_dir"))
    if not isinstance(out_dir, str) or not out_dir.strip():
        raise ValueError("diff_context.build requires params.base_pack and params.target_pack, or params.out_dir/config out_dir.")
    timeline = read_snapshot_timeline(out_dir, limit=2)
    snapshots = timeline.get("snapshots", [])
    if len(snapshots) < 2:
        raise ValueError("diff_context.build requires at least two snapshots in the timeline.")
    latest = snapshots[0]
    previous = snapshots[1]
    if not isinstance(latest, dict) or not isinstance(previous, dict):
        raise ValueError("diff_context.build could not resolve latest and previous snapshots.")
    latest_pack = latest.get("pack_path")
    previous_pack = previous.get("pack_path")
    if not latest_pack or not previous_pack:
        raise ValueError("diff_context.build snapshot entries must include pack_path.")
    return str(previous_pack), str(latest_pack)


def _agent_required_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Method requires params.{key}.")
    return value


def _agent_int(params: dict[str, Any], key: str, default: int) -> int:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"params.{key} must be an integer.")
    return value


def _agent_optional_int(params: dict[str, Any], key: str, default: int | None) -> int | None:
    if key not in params:
        return default
    value = params[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"params.{key} must be an integer or null.")
    return value


def _agent_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {
        "schema_version": "repomori.agent.response.v1",
        "jsonrpc": "2.0",
        "id": request_id,
        "ok": True,
        "result": result,
    }


def _agent_error_response(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": "repomori.agent.response.v1",
        "jsonrpc": "2.0",
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _mcp_initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    requested_version = params.get("protocolVersion")
    protocol_version = requested_version if isinstance(requested_version, str) and requested_version else MCP_PROTOCOL_VERSION
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {
            "name": "repomori",
            "title": "RepoMori",
            "version": MCP_SERVER_VERSION,
        },
        "instructions": (
            "RepoMori exposes local, dependency-free repository memory tools. "
            "It never calls an AI model or network service."
        ),
    }


def _mcp_tool_definitions() -> list[dict[str, Any]]:
    definitions = []
    for tool in MCP_TOOLS:
        item = {
            "name": tool["name"],
            "title": tool["title"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
            "annotations": tool["annotations"],
        }
        definitions.append(json.loads(json.dumps(item)))
    return definitions


def _mcp_call_tool(
    params: dict[str, Any],
    *,
    config_path: Path | str | None,
    profile: str | None,
    start_dir: Path | str | None,
) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("tools/call requires params.name.")
    agent_method = MCP_TOOL_METHODS.get(name)
    if agent_method is None:
        raise ValueError(f"Unknown MCP tool: {name}")
    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("tools/call params.arguments must be an object.")
    agent_response = handle_agent_request(
        {"id": name, "method": agent_method, "params": arguments},
        config_path=config_path,
        profile=profile,
        start_dir=start_dir,
    )
    if agent_response.get("ok"):
        structured = agent_response["result"]
        return {
            "content": [{"type": "text", "text": _mcp_tool_text(name, structured)}],
            "structuredContent": structured,
            "isError": False,
        }
    error = agent_response.get("error", {"code": "execution_error", "message": "RepoMori tool failed."})
    structured_error = {
        "schema_version": "repomori.mcp.tool_error.v1",
        "tool": name,
        "agent_method": agent_method,
        "error": error,
    }
    return {
        "content": [{"type": "text", "text": f"{name} failed: {error.get('message', 'unknown error')}"}],
        "structuredContent": structured_error,
        "isError": True,
    }


def _mcp_tool_text(name: str, payload: dict[str, Any]) -> str:
    schema = payload.get("schema_version", "unknown")
    lines = [f"{name} returned `{schema}`."]
    if name == "repomori_query_run":
        results = payload.get("results", [])
        lines.append(f"results: {len(results)}")
        for item in results[:5]:
            lines.append(f"- {item.get('path')} score={item.get('score')}")
    elif name == "repomori_context_build":
        selection = payload.get("selection", {})
        sources = payload.get("sources", [])
        lines.append(f"sources: {len(sources)}")
        lines.append(f"source_bytes: {selection.get('source_bytes')}")
        for item in sources[:5]:
            lines.append(f"- {item.get('path')} score={item.get('score')}")
    elif name == "repomori_diff_context_build":
        summary = payload.get("summary", {})
        sources = payload.get("sources", [])
        lines.append(f"added: {summary.get('added_count')}")
        lines.append(f"changed: {summary.get('changed_count')}")
        lines.append(f"removed: {summary.get('removed_count')}")
        lines.append(f"sources: {len(sources)}")
        for item in sources[:5]:
            lines.append(f"- {item.get('change_type')} {item.get('path')} score={item.get('score')}")
    elif name == "repomori_file_get":
        lines.append(f"path: {payload.get('path')}")
        lines.append(f"size: {payload.get('size')}")
        lines.append(f"sha256: {payload.get('sha256')}")
    elif name == "repomori_stats_read":
        summary = payload.get("summary", {})
        lines.append(f"snapshots: {payload.get('snapshot_count')}")
        lines.append(f"incremental_snapshots: {summary.get('incremental_snapshot_count')}")
        lines.append(f"reused_files: {summary.get('total_reused_files')}")
        lines.append(f"rebuilt_files: {summary.get('total_rebuilt_files')}")
        lines.append(f"reuse_percent: {summary.get('reuse_percent')}")
    elif name == "repomori_schema_list":
        lines.append(f"schemas: {payload.get('schema_count')}")
        lines.append(f"agent_methods: {len(payload.get('agent_methods', []))}")
        lines.append(f"mcp_tools: {len(payload.get('mcp_tools', []))}")
    elif "status" in payload:
        lines.append(f"status: {payload.get('status')}")
    return "\n".join(lines)


def _mcp_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _mcp_error_response(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _sum_snapshot_field(snapshots: list[dict[str, Any]], field: str) -> int:
    total = 0
    for item in snapshots:
        value = item.get(field)
        if isinstance(value, int):
            total += value
    return total


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _percent(part: int, whole: int) -> float:
    return round((part / whole) * 100, 2) if whole else 0.0


def _ratio(part: Any, whole: Any) -> float | None:
    try:
        numerator = float(part)
        denominator = float(whole)
    except (TypeError, ValueError):
        return None
    return round(numerator / denominator, 3) if denominator else None


def _add_verify_error(
    errors: list[dict[str, Any]],
    scope: str,
    path: str | None,
    message: str,
    *,
    expected: Any = None,
    actual: Any = None,
) -> None:
    error = {"scope": scope, "message": message}
    if path is not None:
        error["path"] = path
    if expected is not None:
        error["expected"] = expected
    if actual is not None:
        error["actual"] = actual
    errors.append(error)


def _init_db(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            compressor TEXT NOT NULL,
            raw_size INTEGER NOT NULL,
            compressed_size INTEGER NOT NULL,
            data BLOB NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            mode INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            language TEXT,
            is_text INTEGER NOT NULL,
            line_count INTEGER NOT NULL,
            token_count INTEGER NOT NULL,
            summary_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS file_chunks (
            path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_id TEXT NOT NULL,
            raw_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY (path, chunk_index),
            FOREIGN KEY (path) REFERENCES files(path),
            FOREIGN KEY (chunk_id) REFERENCES chunks(id)
        );

        CREATE TABLE IF NOT EXISTS symbols (
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            line INTEGER NOT NULL,
            signature TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS imports (
            path TEXT NOT NULL,
            target TEXT NOT NULL,
            line INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS search_index (
            path TEXT NOT NULL,
            field TEXT NOT NULL,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_search_path ON search_index(path);
        CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
        CREATE INDEX IF NOT EXISTS idx_imports_path ON imports(path);
        """
    )


def _put_metadata(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    for key, value in values.items():
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            (key, json.dumps(value, sort_keys=True, separators=(",", ":"))),
        )


def _metadata(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM metadata").fetchall()
    return {row["key"]: _safe_json(row["value"], row["value"]) for row in rows}


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _pack_file_index(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT
            path, size, mtime, mode, sha256, chunk_count, language, is_text,
            line_count, token_count, summary_json
        FROM files
        """
    ).fetchall()
    return {str(row["path"]): row for row in rows}


def _copy_file_from_base(
    conn: sqlite3.Connection,
    base_conn: sqlite3.Connection,
    base_row: sqlite3.Row,
    current_path: Path,
) -> dict[str, int]:
    rel = str(base_row["path"])
    chunk_rows = base_conn.execute(
        """
        SELECT
            fc.path,
            fc.chunk_index,
            fc.chunk_id,
            fc.raw_size AS file_raw_size,
            fc.sha256 AS file_sha256,
            c.id,
            c.compressor,
            c.raw_size,
            c.compressed_size,
            c.data
        FROM file_chunks fc
        LEFT JOIN chunks c ON c.id = fc.chunk_id
        WHERE fc.path=?
        ORDER BY fc.chunk_index
        """,
        (rel,),
    ).fetchall()
    if len(chunk_rows) != int(base_row["chunk_count"]) or any(row["id"] is None for row in chunk_rows):
        raise ValueError(f"Base pack has incomplete chunk links for {rel}")

    conn.executemany(
        """
        INSERT OR IGNORE INTO chunks(id, compressor, raw_size, compressed_size, data)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                row["id"],
                row["compressor"],
                int(row["raw_size"]),
                int(row["compressed_size"]),
                sqlite3.Binary(row["data"]),
            )
            for row in chunk_rows
        ],
    )

    st = current_path.stat()
    conn.execute(
        """
        INSERT INTO files(
            path, size, mtime, mode, sha256, chunk_count, language, is_text,
            line_count, token_count, summary_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rel,
            int(base_row["size"]),
            st.st_mtime,
            stat.S_IMODE(st.st_mode),
            base_row["sha256"],
            int(base_row["chunk_count"]),
            base_row["language"],
            int(base_row["is_text"]),
            int(base_row["line_count"]),
            int(base_row["token_count"]),
            base_row["summary_json"],
        ),
    )
    conn.executemany(
        """
        INSERT INTO file_chunks(path, chunk_index, chunk_id, raw_size, sha256)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                rel,
                int(row["chunk_index"]),
                row["chunk_id"],
                int(row["file_raw_size"]),
                row["file_sha256"],
            )
            for row in chunk_rows
        ],
    )
    _copy_index_rows(conn, base_conn, "symbols", rel, ("path", "kind", "name", "line", "signature"))
    _copy_index_rows(conn, base_conn, "imports", rel, ("path", "target", "line"))
    _copy_index_rows(conn, base_conn, "search_index", rel, ("path", "field", "value"))

    stats = _stats_from_file_row(base_row)
    stats["reused_chunk_count"] = len(chunk_rows)
    return stats


def _copy_index_rows(
    conn: sqlite3.Connection,
    base_conn: sqlite3.Connection,
    table: str,
    rel: str,
    columns: tuple[str, ...],
) -> None:
    column_sql = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = base_conn.execute(f"SELECT {column_sql} FROM {table} WHERE path=?", (rel,)).fetchall()
    conn.executemany(
        f"INSERT INTO {table}({column_sql}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def _stats_from_file_row(row: sqlite3.Row) -> dict[str, int]:
    summary = _safe_json(row["summary_json"], {})
    return {
        "file_count": 1,
        "text_file_count": 1 if int(row["is_text"]) else 0,
        "binary_file_count": 0 if int(row["is_text"]) else 1,
        "logical_bytes": int(row["size"]),
        "symbol_count": len(summary.get("symbols", [])) if isinstance(summary, dict) else 0,
        "import_count": len(summary.get("imports", [])) if isinstance(summary, dict) else 0,
    }


def _scan_relpath(repo_path: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(repo_path.resolve())
    except ValueError:
        return str(path)
    value = rel.as_posix()
    return value if value else "."


def _scan_add(
    findings: list[dict[str, Any]],
    severity: str,
    code: str,
    path: str,
    message: str,
    *,
    line: int | None = None,
    match: str | None = None,
    size: int | None = None,
) -> None:
    item: dict[str, Any] = {
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
    }
    if line is not None:
        item["line"] = line
    if match is not None:
        item["match"] = match
    if size is not None:
        item["size"] = size
    findings.append(item)


def _scan_read_prefix(path: Path, max_file_bytes: int) -> bytes | None:
    try:
        with path.open("rb") as handle:
            return handle.read(max_file_bytes)
    except OSError:
        return None


def _scan_text_for_secrets(rel: str, text: str, findings: list[dict[str, Any]]) -> None:
    for code, severity, pattern, message in SCAN_SECRET_PATTERNS:
        seen = 0
        for match in pattern.finditer(text):
            matched = match.group(2) if code == "generic_secret_assignment" and match.lastindex else match.group(0)
            if code == "generic_secret_assignment" and _scan_looks_like_placeholder(matched):
                continue
            _scan_add(
                findings,
                severity,
                code,
                rel,
                message,
                line=_scan_line_number(text, match.start()),
                match=_scan_redact(matched),
            )
            seen += 1
            if seen >= 5:
                break


def _scan_baseline_entries(baseline: Path | str | dict[str, Any] | None) -> tuple[list[dict[str, Any]], str | None]:
    if baseline is None:
        return [], None
    baseline_path = None
    if isinstance(baseline, (str, Path)):
        path = Path(baseline)
        baseline_path = str(path.resolve())
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid scan baseline JSON: {path}: {exc}") from exc
    elif isinstance(baseline, dict):
        payload = baseline
    else:
        raise ValueError("scan baseline must be a path, JSON object, or None")

    entries = payload.get("ignore", payload.get("ignored_findings", payload.get("findings", [])))
    if not isinstance(entries, list):
        raise ValueError("scan baseline must contain an `ignore`, `ignored_findings`, or `findings` list")
    normalized = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"scan baseline entry {index} must be an object")
        code = str(entry.get("code", "")).strip()
        path_value = str(entry.get("path", "")).strip()
        if not code or not path_value:
            raise ValueError(f"scan baseline entry {index} must include code and path")
        normalized_entry: dict[str, Any] = {"code": code, "path": path_value}
        for optional_key in ("line", "severity"):
            if optional_key in entry:
                normalized_entry[optional_key] = entry[optional_key]
        normalized.append(normalized_entry)
    return normalized, baseline_path


def _scan_filter_findings(
    findings: list[dict[str, Any]],
    *,
    ignore_codes: list[str],
    baseline_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active = []
    ignored = []
    ignore_code_set = set(ignore_codes)
    for finding in findings:
        reason = None
        if finding.get("code") in ignore_code_set:
            reason = "ignore_code"
        else:
            match = _scan_matching_baseline_entry(finding, baseline_entries)
            if match is not None:
                reason = "baseline"
        if reason:
            ignored_item = dict(finding)
            ignored_item["ignored_reason"] = reason
            ignored.append(ignored_item)
            continue
        active.append(finding)
    return active, ignored


def _scan_matching_baseline_entry(finding: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in entries:
        if entry.get("code") != finding.get("code"):
            continue
        if entry.get("path") != finding.get("path"):
            continue
        if "line" in entry and entry.get("line") != finding.get("line"):
            continue
        if "severity" in entry and entry.get("severity") != finding.get("severity"):
            continue
        return entry
    return None


def scan_baseline_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Create a compact scan baseline from a scan report."""

    entries = []
    for finding in report.get("findings", []):
        if not isinstance(finding, dict):
            continue
        entry = {
            "severity": finding.get("severity"),
            "code": finding.get("code"),
            "path": finding.get("path"),
            "message": finding.get("message"),
        }
        if finding.get("line") is not None:
            entry["line"] = finding.get("line")
        entries.append(entry)
    return {
        "schema_version": "repomori.scan.baseline.v1",
        "source_schema_version": report.get("schema_version"),
        "repo_path": report.get("repo_path"),
        "created_at": int(time.time()),
        "ignore": entries,
    }


def write_scan_baseline(report: dict[str, Any], path: Path | str) -> dict[str, Any]:
    """Write a scan baseline file from the active findings in a scan report."""

    output = Path(path)
    baseline = scan_baseline_from_report(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, baseline)
    return {
        "schema_version": "repomori.scan.baseline.write.v1",
        "path": str(output.resolve()),
        "ignored_count": len(baseline["ignore"]),
        "baseline": baseline,
    }


def _release_schema_check() -> dict[str, Any]:
    started = time.time()
    try:
        catalog = schema_catalog()
        schema_versions = [item["schema_version"] for item in catalog.get("schemas", [])]
        duplicate_versions = sorted(
            version
            for version, count in Counter(schema_versions).items()
            if count > 1
        )
        missing_required = [
            item.get("schema_version")
            for item in catalog.get("schemas", [])
            if not item.get("schema_version") or not item.get("required_fields")
        ]
        ok = not duplicate_versions and not missing_required
        return {
            "name": "schema",
            "ok": ok,
            "status": "pass" if ok else "fail",
            "schema_count": catalog.get("schema_count"),
            "duplicate_versions": duplicate_versions,
            "missing_required": missing_required,
            "elapsed_seconds": round(time.time() - started, 4),
        }
    except Exception as exc:
        return {
            "name": "schema",
            "ok": False,
            "status": "fail",
            "error": str(exc),
            "elapsed_seconds": round(time.time() - started, 4),
        }


def _release_tests_check(repo_path: Path, tests_dir: Path | str) -> dict[str, Any]:
    started = time.time()
    command = [sys.executable, "-m", "unittest", "discover", "-s", str(tests_dir)]
    completed = subprocess.run(
        command,
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    ok = completed.returncode == 0
    return {
        "name": "tests",
        "ok": ok,
        "status": "pass" if ok else "fail",
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": _release_tail(completed.stdout),
        "stderr_tail": _release_tail(completed.stderr),
        "elapsed_seconds": round(time.time() - started, 4),
    }


def _release_demo_check(
    repo_path: Path,
    *,
    demo_out: Path | str | None,
    keep_demo: bool,
) -> dict[str, Any]:
    started = time.time()
    out_path = Path(demo_out).resolve() if demo_out is not None else repo_path.parent / f".repomori-release-check-{int(time.time() * 1000)}"
    result: dict[str, Any]
    try:
        demo = run_demo(out_path, force=True)
        ok = demo.get("status") == "pass"
        result = {
            "name": "demo",
            "ok": ok,
            "status": "pass" if ok else "fail",
            "demo_status": demo.get("status"),
            "out_dir": str(out_path),
            "kept": keep_demo,
            "summary": demo.get("summary", {}),
            "elapsed_seconds": round(time.time() - started, 4),
        }
    except Exception as exc:
        result = {
            "name": "demo",
            "ok": False,
            "status": "fail",
            "out_dir": str(out_path),
            "kept": keep_demo,
            "error": str(exc),
            "elapsed_seconds": round(time.time() - started, 4),
        }
    finally:
        if not keep_demo and out_path.exists():
            try:
                if not _release_path_is_cleanup_safe(repo_path, out_path):
                    raise ValueError(f"Refusing to clean unexpected release-check path: {out_path}")
                shutil.rmtree(out_path)
            except Exception as exc:
                result["ok"] = False
                result["status"] = "fail"
                result["cleanup_error"] = str(exc)
                result["elapsed_seconds"] = round(time.time() - started, 4)
    return result


def _release_skipped_check(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": True,
        "status": "skipped",
        "skipped": True,
        "elapsed_seconds": 0.0,
    }


def _release_tail(value: str, *, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def _release_path_is_cleanup_safe(repo_path: Path, out_path: Path) -> bool:
    resolved = out_path.resolve()
    repo_resolved = repo_path.resolve()
    if resolved == repo_resolved:
        return False
    if resolved == repo_resolved.parent or resolved == repo_resolved.anchor:
        return False
    if resolved.name.startswith(".repomori-release-check-") and resolved.parent == repo_resolved.parent:
        return True
    try:
        relative = resolved.relative_to(repo_resolved)
    except ValueError:
        return False
    return relative.parts and relative.parts[0] in {".release-check-demo", ".repomori-release-check"}


def _scan_has_severity_threshold(report: dict[str, Any], threshold: str) -> bool:
    minimum = SCAN_SEVERITY_ORDER[threshold]
    return any(
        SCAN_SEVERITY_ORDER.get(str(finding.get("severity")), -1) >= minimum
        for finding in report.get("findings", [])
    )


def _scan_text_for_personal_paths(rel: str, text: str, findings: list[dict[str, Any]]) -> None:
    for code, severity, pattern, message in SCAN_PERSONAL_PATH_PATTERNS:
        seen = 0
        for match in pattern.finditer(text):
            _scan_add(
                findings,
                severity,
                code,
                rel,
                message,
                line=_scan_line_number(text, match.start()),
                match=_scan_redact(match.group(0)),
            )
            seen += 1
            if seen >= 3:
                break


def _scan_line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _scan_redact(value: str) -> str:
    compact = value.strip()
    if len(compact) <= 8:
        return "***"
    return f"{compact[:4]}...{compact[-4:]}"


def _scan_looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"changeme", "example", "placeholder", "password", "secret", "token"}:
        return True
    if "example" in lowered or "placeholder" in lowered:
        return True
    if lowered.startswith(("your-", "your_", "insert-", "insert_")):
        return True
    if set(lowered) <= {"x"}:
        return True
    return False


def _scan_license_posture(repo_path: Path, findings: list[dict[str, Any]], *, public_release: bool) -> None:
    try:
        root_files = {path.name.lower(): path for path in repo_path.iterdir() if path.is_file()}
    except OSError:
        root_files = {}
    has_license = any(name in SCAN_LICENSE_NAMES for name in root_files)
    if not has_license:
        _scan_add(findings, "medium", "missing_license", ".", "No root license file was found.")

    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        text = _decode_text(_scan_read_prefix(pyproject, SCAN_DEFAULT_MAX_FILE_BYTES) or b"")
        if text and re.search(r"(?im)^\s*license\s*=\s*(?:\"Private\"|\{[^}]*text\s*=\s*\"Private\")", text):
            _scan_add(
                findings,
                "medium",
                "private_license_metadata",
                "pyproject.toml",
                "Project metadata still describes the license as Private.",
            )

    license_texts = []
    for name in SCAN_LICENSE_NAMES:
        path = root_files.get(name)
        if not path:
            continue
        text = _decode_text(_scan_read_prefix(path, SCAN_DEFAULT_MAX_FILE_BYTES) or b"")
        if text:
            license_texts.append(text.lower())
    joined = "\n".join(license_texts)
    if "polyform noncommercial" in joined and any(marker in joined for marker in ("mit license", "apache license", "bsd license")):
        _scan_add(
            findings,
            "high" if public_release else "medium",
            "conflicting_license_notice",
            ".",
            "License text appears to mix noncommercial and permissive license notices.",
        )


def _scan_public_release_report(
    repo_path: Path,
    findings: list[dict[str, Any]],
    *,
    enabled: bool,
) -> dict[str, Any] | None:
    if not enabled:
        return None

    required_files = {name: (repo_path / name).exists() for name in SCAN_PUBLIC_REQUIRED_FILES}
    for name, exists in required_files.items():
        if not exists:
            _scan_add(
                findings,
                "medium" if name in {"LICENSE.md", "NOTICE.md"} else "low",
                "missing_public_release_file",
                name,
                "Expected public-release guardrail file is missing.",
            )

    checklist_path = repo_path / "PUBLIC_RELEASE_CHECKLIST.md"
    checklist = {
        "path": "PUBLIC_RELEASE_CHECKLIST.md",
        "found": checklist_path.exists(),
        "checked_items": 0,
        "unchecked_items": 0,
        "total_checkbox_items": 0,
    }
    if checklist_path.exists():
        text = _decode_text(_scan_read_prefix(checklist_path, SCAN_DEFAULT_MAX_FILE_BYTES) or b"") or ""
        checked = len(re.findall(r"(?im)^\s*-\s*\[[xX]\]\s+", text))
        unchecked = len(re.findall(r"(?im)^\s*-\s*\[\s\]\s+", text))
        checklist.update(
            {
                "checked_items": checked,
                "unchecked_items": unchecked,
                "total_checkbox_items": checked + unchecked,
            }
        )
        if unchecked:
            _scan_add(
                findings,
                "low",
                "unchecked_public_release_item",
                "PUBLIC_RELEASE_CHECKLIST.md",
                f"Public release checklist still has {unchecked} unchecked item(s).",
            )
    return {
        "enabled": True,
        "required_files": required_files,
        "checklist": checklist,
    }


def _scan_binary_heavy_dirs(
    binary_by_dir: Counter[str],
    total_by_dir: Counter[str],
    findings: list[dict[str, Any]],
) -> None:
    for rel in sorted(binary_by_dir):
        binary_count = binary_by_dir[rel]
        total = total_by_dir.get(rel, 0)
        if total >= 5 and binary_count / max(total, 1) >= 0.8:
            _scan_add(
                findings,
                "low",
                "binary_heavy_dir",
                rel,
                f"Directory is mostly binary files ({binary_count}/{total}).",
            )


def _scan_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(item.get("severity", "info")) for item in findings)
    max_severity = "none"
    if findings:
        max_severity = max(
            (str(item.get("severity", "info")) for item in findings),
            key=lambda value: SCAN_SEVERITY_ORDER.get(value, -1),
        )
    return {
        "findings": len(findings),
        "high": counts.get("high", 0),
        "medium": counts.get("medium", 0),
        "low": counts.get("low", 0),
        "info": counts.get("info", 0),
        "max_severity": max_severity,
    }


def _open_pack(pack: Path | str) -> sqlite3.Connection:
    path = Path(pack)
    if not path.exists():
        raise FileNotFoundError(f"Pack not found: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _iter_repo_files(
    repo_path: Path,
    output_path: Path,
    exclude_paths: Iterable[Path | str] = (),
) -> Iterable[Path]:
    output_resolved = output_path.resolve()
    excluded_roots = tuple(Path(path).resolve() for path in exclude_paths)
    for root, dirs, files in os.walk(repo_path):
        root_path = Path(root)
        kept_dirs = []
        for dirname in sorted(dirs):
            if dirname in EXCLUDED_DIRS:
                continue
            dir_path = (root_path / dirname).resolve()
            if any(dir_path == excluded or excluded in dir_path.parents for excluded in excluded_roots):
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for name in sorted(files):
            path = root_path / name
            if path.resolve() == output_resolved:
                continue
            if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
                continue
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            yield path


def _ingest_file(conn: sqlite3.Connection, repo_path: Path, path: Path, chunk_size: int) -> dict[str, int]:
    rel = _normalize_repo_path(path.relative_to(repo_path).as_posix())
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    text = _decode_text(data)
    is_text = text is not None
    language = _language_for_path(path)
    analysis = _analyze_text(rel, text, language) if is_text else _binary_summary(rel, data, language)
    chunk_ids = []
    for index, block in enumerate(_blocks(data, chunk_size)):
        chunk_id = hashlib.sha256(block).hexdigest()
        compressed = zlib.compress(block, 6)
        conn.execute(
            """
            INSERT OR IGNORE INTO chunks(id, compressor, raw_size, compressed_size, data)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chunk_id, "zlib", len(block), len(compressed), sqlite3.Binary(compressed)),
        )
        conn.execute(
            """
            INSERT INTO file_chunks(path, chunk_index, chunk_id, raw_size, sha256)
            VALUES (?, ?, ?, ?, ?)
            """,
            (rel, index, chunk_id, len(block), chunk_id),
        )
        chunk_ids.append(chunk_id)

    st = path.stat()
    mode = stat.S_IMODE(st.st_mode)
    conn.execute(
        """
        INSERT INTO files(
            path, size, mtime, mode, sha256, chunk_count, language, is_text,
            line_count, token_count, summary_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rel,
            len(data),
            st.st_mtime,
            mode,
            sha256,
            len(chunk_ids),
            language,
            1 if is_text else 0,
            int(analysis.get("line_count", 0)),
            int(analysis.get("token_count", 0)),
            json.dumps(analysis, sort_keys=True, separators=(",", ":")),
        ),
    )
    _insert_analysis_indexes(conn, rel, analysis, language)
    return {
        "file_count": 1,
        "text_file_count": 1 if is_text else 0,
        "binary_file_count": 0 if is_text else 1,
        "logical_bytes": len(data),
        "symbol_count": len(analysis.get("symbols", [])),
        "import_count": len(analysis.get("imports", [])),
    }


def _insert_analysis_indexes(
    conn: sqlite3.Connection,
    rel: str,
    analysis: dict[str, Any],
    language: str | None,
) -> None:
    values: list[tuple[str, str, str]] = [(rel, "path", rel)]
    if language:
        values.append((rel, "language", language))
    for term in analysis.get("top_terms", []):
        values.append((rel, "term", str(term)))
    for heading in analysis.get("headings", []):
        values.append((rel, "heading", str(heading.get("text", heading))))
    for symbol in analysis.get("symbols", []):
        name = str(symbol.get("name", ""))
        values.append((rel, "symbol", name))
        conn.execute(
            "INSERT INTO symbols(path, kind, name, line, signature) VALUES (?, ?, ?, ?, ?)",
            (
                rel,
                str(symbol.get("kind", "")),
                name,
                int(symbol.get("line", 0)),
                str(symbol.get("signature", "")),
            ),
        )
    for item in analysis.get("imports", []):
        target = str(item.get("target", ""))
        values.append((rel, "import", target))
        conn.execute(
            "INSERT INTO imports(path, target, line) VALUES (?, ?, ?)",
            (rel, target, int(item.get("line", 0))),
        )
    conn.executemany(
        "INSERT INTO search_index(path, field, value) VALUES (?, ?, ?)",
        [(path, field, value) for path, field, value in values if value],
    )


def _blocks(data: bytes, size: int) -> Iterable[bytes]:
    if not data:
        yield b""
        return
    for offset in range(0, len(data), size):
        yield data[offset : offset + size]


def _decode_text(data: bytes) -> str | None:
    if not data:
        return ""
    sample = data[:4096]
    if b"\x00" in sample:
        return None
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _language_for_path(path: Path) -> str | None:
    return LANG_BY_EXT.get(path.suffix.lower())


def _analyze_text(rel: str, text: str | None, language: str | None) -> dict[str, Any]:
    content = text or ""
    lines = content.splitlines()
    tokens = _tokens(content)
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    headings: list[dict[str, Any]] = []
    if language == "python":
        py_symbols, py_imports = _python_structure(content, lines)
        symbols.extend(py_symbols)
        imports.extend(py_imports)
    elif language in {"javascript", "typescript"}:
        js_symbols, js_imports = _js_ts_structure(content)
        symbols.extend(js_symbols)
        imports.extend(js_imports)
    if language == "markdown":
        headings.extend(_markdown_headings(lines))
    elif language == "json":
        headings.extend(_json_keys(content))
    return {
        "path": rel,
        "kind": "text",
        "language": language,
        "line_count": len(lines),
        "token_count": len(tokens),
        "top_terms": _top_terms(tokens),
        "symbols": symbols[:200],
        "imports": imports[:200],
        "headings": headings[:100],
    }


def _binary_summary(rel: str, data: bytes, language: str | None) -> dict[str, Any]:
    return {
        "path": rel,
        "kind": "binary",
        "language": language,
        "line_count": 0,
        "token_count": 0,
        "top_terms": [],
        "symbols": [],
        "imports": [],
        "headings": [],
        "byte_prefix_sha256": hashlib.sha256(data[:4096]).hexdigest(),
    }


def _python_structure(content: str, lines: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return symbols, imports
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            line = int(getattr(node, "lineno", 0))
            signature = lines[line - 1].strip() if 0 < line <= len(lines) else node.name
            symbols.append({"kind": kind, "name": node.name, "line": line, "signature": signature})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"target": alias.name, "line": int(getattr(node, "lineno", 0))})
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                target = f"{module}.{alias.name}" if module else alias.name
                imports.append({"target": target, "line": int(getattr(node, "lineno", 0))})
    return sorted(symbols, key=lambda item: (item["line"], item["name"])), sorted(
        imports, key=lambda item: (item["line"], item["target"])
    )


def _js_ts_structure(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    patterns = [
        ("class", re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(")),
        ("function", re.compile(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(")),
        ("function", re.compile(r"\bexport\s+function\s+([A-Za-z_$][\w$]*)\s*\(")),
    ]
    for kind, pattern in patterns:
        for match in pattern.finditer(content):
            line = content.count("\n", 0, match.start()) + 1
            symbols.append({"kind": kind, "name": match.group(1), "line": line, "signature": _line_at(content, line)})
    import_patterns = [
        re.compile(r"\bimport\b[^'\"]*['\"]([^'\"]+)['\"]"),
        re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
    ]
    for pattern in import_patterns:
        for match in pattern.finditer(content):
            imports.append({"target": match.group(1), "line": content.count("\n", 0, match.start()) + 1})
    return sorted(symbols, key=lambda item: (item["line"], item["name"])), sorted(
        imports, key=lambda item: (item["line"], item["target"])
    )


def _markdown_headings(lines: list[str]) -> list[dict[str, Any]]:
    headings = []
    for index, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append({"level": len(match.group(1)), "text": match.group(2).strip(), "line": index})
    return headings


def _json_keys(content: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return [{"level": 1, "text": key, "line": 0} for key in sorted(parsed)[:100]]
    return []


def _tokens(content: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_/-]{2,}", content.lower())
        if token not in STOPWORDS and len(token) <= 48
    ]


def _top_terms(tokens: list[str], limit: int = 32) -> list[str]:
    return [term for term, _count in Counter(tokens).most_common(limit)]


def _line_at(content: str, line_no: int) -> str:
    lines = content.splitlines()
    if 0 < line_no <= len(lines):
        return lines[line_no - 1].strip()
    return ""


def _query_tokens(query: str) -> list[str]:
    return _tokens(query) or [part.lower() for part in query.split() if part.strip()]


def _field_weight(field: str) -> float:
    return {
        "basename": 7.0,
        "symbol": 8.0,
        "heading": 6.0,
        "import": 5.0,
        "path": 4.0,
        "language": 2.0,
        "term": 1.0,
    }.get(field, 1.0)


def _query_phrases(query: str, tokens: list[str]) -> list[str]:
    raw = re.sub(r"\s+", " ", query.lower()).strip()
    token_phrase = " ".join(tokens).strip()
    phrases = []
    for phrase in (raw, token_phrase):
        if " " in phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def _score_query_value(
    path: str,
    field: str,
    value: str,
    tokens: list[str],
    phrases: list[str],
    weight: float,
    scores: dict[str, float],
    reasons: dict[str, set[str]],
    matched_tokens: dict[str, set[str]],
    breakdown: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    normalized = value.lower()
    if not normalized:
        return

    value_terms = set(_query_tokens(value))
    for phrase in phrases:
        if phrase in normalized:
            added = (weight * 2.0) + 4.0
            scores[path] += added
            reasons[path].add("phrase")
            reasons[path].add(field)
            if breakdown is not None:
                breakdown[path].append(
                    {
                        "field": field,
                        "kind": "phrase",
                        "phrase": phrase,
                        "value": _trace_value(value),
                        "weight": round(added, 2),
                    }
                )

    for token in tokens:
        if token not in normalized:
            continue
        scores[path] += weight
        reasons[path].add(field)
        matched_tokens[path].add(token)
        if breakdown is not None:
            breakdown[path].append(
                {
                    "field": field,
                    "kind": "token",
                    "token": token,
                    "value": _trace_value(value),
                    "weight": round(weight, 2),
                }
            )
        if token in value_terms:
            added = weight * 0.5
            scores[path] += added
            reasons[path].add(f"exact-{field}")
            if breakdown is not None:
                breakdown[path].append(
                    {
                        "field": field,
                        "kind": "exact",
                        "token": token,
                        "value": _trace_value(value),
                        "weight": round(added, 2),
                    }
                )


def _trace_value(value: str, limit: int = 160) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _markdown_fence_language(language: str | None) -> str:
    return {
        "batch": "bat",
        "csharp": "csharp",
        "javascript": "javascript",
        "json": "json",
        "markdown": "markdown",
        "powershell": "powershell",
        "python": "python",
        "shell": "bash",
        "typescript": "typescript",
        "yaml": "yaml",
    }.get(language or "", "")


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": summary.get("kind"),
        "line_count": summary.get("line_count", 0),
        "token_count": summary.get("token_count", 0),
        "top_terms": summary.get("top_terms", [])[:10],
        "symbols": summary.get("symbols", [])[:10],
        "imports": summary.get("imports", [])[:10],
        "headings": summary.get("headings", [])[:10],
    }


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _safe_json(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
