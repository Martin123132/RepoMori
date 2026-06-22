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
import tempfile
import time
import zipfile
import zlib
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
SNAPSHOT_CHAIN_VERSION = "repomori.snapshot_chain.v1"
SNAPSHOT_CHAIN_ALGORITHM = "sha256-canonical-json"
SNAPSHOT_CHAIN_FIELDS = {
    "chain_version",
    "chain_index",
    "previous_chain_hash",
    "entry_hash",
    "chain_hash",
}
ANCHOR_FRESHNESS_PROFILES = {
    "safe",
    "strict",
    "legacy",
}
HANDOFF_QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "safe": {
        "target_score": 70.0,
        "fail_below": None,
        "fail_on_score_status": {"fail"},
        "fail_on_high_priority": False,
    },
    "ci": {
        "target_score": 85.0,
        "fail_below": 85.0,
        "fail_on_score_status": {"fail"},
        "fail_on_high_priority": True,
    },
    "strict": {
        "target_score": 90.0,
        "fail_below": 90.0,
        "fail_on_score_status": {"fail", "warn"},
        "fail_on_high_priority": True,
    },
}

SCHEMA_DEFINITIONS = (
    {
        "schema_version": SCHEMA_VERSION,
        "kind": "pack",
        "title": "RepoMori pack metadata",
        "producer": "build_pack",
        "required_fields": ["schema_version", "repo_path", "pack_path", "chunk_size"],
    },
    {
        "schema_version": "repomori.inspect.v1",
        "kind": "report",
        "title": "RepoMori pack inspector report",
        "producer": "inspect_pack",
        "required_fields": ["schema_version", "status", "pack", "summary", "storage", "files", "vocabulary"],
    },
    {
        "schema_version": "repomori.compare.v1",
        "kind": "report",
        "title": "RepoMori pack comparison report",
        "producer": "compare_packs",
        "required_fields": ["schema_version", "base_pack", "target_pack", "summary", "language_delta", "files"],
    },
    {
        "schema_version": "repomori.inspect_diff.v1",
        "kind": "report",
        "title": "RepoMori pack inspector diff report",
        "producer": "inspect_pack_diff",
        "required_fields": ["schema_version", "status", "base_pack", "target_pack", "summary", "comparison", "storage_delta", "vocabulary_delta"],
    },
    {
        "schema_version": "repomori.verify.v1",
        "kind": "report",
        "title": "Pack integrity verification report",
        "producer": "verify_pack",
        "required_fields": ["schema_version", "pack_path", "pack_schema_version", "verified", "error_count", "checked_files", "checked_chunks", "errors"],
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
        "schema_version": "repomori.brief.v1",
        "kind": "report",
        "title": "Question-free repository orientation brief",
        "producer": "build_repo_brief",
        "required_fields": ["schema_version", "pack", "settings", "summary", "orientation", "vocabulary", "source_manifest"],
    },
    {
        "schema_version": "repomori.agent_brief.v1",
        "kind": "report",
        "title": "Snapshot directory agent start brief",
        "producer": "build_agent_brief",
        "required_fields": ["schema_version", "status", "out_dir", "summary", "latest_snapshot", "artifacts", "recommended_commands"],
    },
    {
        "schema_version": "repomori.capsule.v1",
        "kind": "report",
        "title": "Dense machine-readable pack capsule",
        "producer": "build_capsule",
        "required_fields": ["schema_version", "key", "pack", "selection", "files", "dictionary", "manifest"],
    },
    {
        "schema_version": "repomori.eval.v1",
        "kind": "report",
        "title": "Source-backed pack evaluation report",
        "producer": "evaluate_pack",
        "required_fields": ["schema_version", "pack", "settings", "summary", "coverage", "questions"],
    },
    {
        "schema_version": "repomori.context_eval.v1",
        "kind": "report",
        "title": "Fixture-backed context quality evaluation report",
        "producer": "evaluate_context_quality",
        "required_fields": ["schema_version", "status", "pack", "settings", "summary", "cases", "failures"],
    },
    {
        "schema_version": "repomori.handoff.v1",
        "kind": "manifest",
        "title": "Agent handoff package manifest",
        "producer": "build_handoff_package",
        "required_fields": ["schema_version", "status", "question", "out_dir", "artifacts", "verification"],
    },
    {
        "schema_version": "repomori.handoff_score.v1",
        "kind": "report",
        "title": "Agent handoff usefulness score",
        "producer": "score_handoff_package",
        "required_fields": ["schema_version", "status", "handoff_dir", "summary", "checks", "validation"],
    },
    {
        "schema_version": "repomori.handoff_triage.v1",
        "kind": "report",
        "title": "Agent handoff fix checklist",
        "producer": "triage_handoff_score",
        "required_fields": ["schema_version", "status", "source", "summary", "actions"],
    },
    {
        "schema_version": "repomori.handoff_quality.v1",
        "kind": "report",
        "title": "Agent handoff quality gate",
        "producer": "evaluate_handoff_quality",
        "required_fields": ["schema_version", "status", "profile", "summary", "warnings", "failures"],
    },
    {
        "schema_version": "repomori.handoff_improvement.v1",
        "kind": "report",
        "title": "Agent handoff improvement run",
        "producer": "improve_handoff_package",
        "required_fields": ["schema_version", "status", "pack", "question", "out_dir", "summary", "attempts", "artifacts"],
    },
    {
        "schema_version": "repomori.handoff_archive.v1",
        "kind": "report",
        "title": "Portable handoff archive report",
        "producer": "archive_handoff_package",
        "required_fields": ["schema_version", "status", "handoff_dir", "archive", "summary", "artifacts"],
    },
    {
        "schema_version": "repomori.handoff_health.v1",
        "kind": "report",
        "title": "Operational handoff health report",
        "producer": "build_handoff_health_report",
        "required_fields": ["schema_version", "status", "handoff_dir", "profile", "summary", "check", "score", "triage", "quality"],
    },
    {
        "schema_version": "repomori.handoff_health_record.v1",
        "kind": "record",
        "title": "Handoff health trend log row",
        "producer": "append_handoff_health_log",
        "required_fields": ["schema_version", "run_ts", "status", "handoff_dir", "score_percent", "quality_status", "triage_action_count"],
    },
    {
        "schema_version": "repomori.handoff_health_summary.v1",
        "kind": "report",
        "title": "Handoff health trend summary",
        "producer": "summarize_handoff_health_log",
        "required_fields": ["schema_version", "status", "log_path", "count", "pass_count", "warn_count", "fail_count", "trend"],
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
        "schema_version": "repomori.timeline_search.v1",
        "kind": "report",
        "title": "Snapshot timeline query report",
        "producer": "search_snapshot_timeline",
        "required_fields": ["schema_version", "status", "out_dir", "query", "summary", "matches", "file_history"],
    },
    {
        "schema_version": "repomori.stats.v1",
        "kind": "report",
        "title": "Snapshot incremental savings report",
        "producer": "read_snapshot_stats",
        "required_fields": ["schema_version", "out_dir", "snapshot_count", "returned_count", "summary", "latest", "snapshots", "top_reuse"],
    },
    {
        "schema_version": SNAPSHOT_CHAIN_VERSION,
        "kind": "report",
        "title": "Snapshot timeline chain verification report",
        "producer": "verify_snapshot_chain",
        "required_fields": ["schema_version", "status", "out_dir", "summary", "errors", "warnings"],
    },
    {
        "schema_version": "repomori.snapshot_anchor.v1",
        "kind": "report",
        "title": "Snapshot timeline anchor proof",
        "producer": "build_snapshot_anchor",
        "required_fields": ["schema_version", "status", "out_dir", "created_at", "chain", "latest_snapshot", "verification", "anchor_hash"],
    },
    {
        "schema_version": "repomori.snapshot_anchor.verify.v1",
        "kind": "report",
        "title": "Snapshot timeline anchor verification report",
        "producer": "verify_snapshot_anchor",
        "required_fields": ["schema_version", "status", "anchor_path", "out_dir", "summary", "errors", "warnings"],
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
        "schema_version": "repomori.release_candidate.v1",
        "kind": "ci-artifact",
        "title": "Release package workflow manifest",
        "producer": ".github/workflows/release-candidate.yml",
        "required_fields": ["schema_version", "status", "version", "commit", "artifacts"],
    },
    {
        "schema_version": "repomori.release_provenance.v1",
        "kind": "ci-artifact",
        "title": "Release package provenance",
        "producer": "write_release_package_artifacts",
        "required_fields": ["schema_version", "status", "version", "commit", "repository", "artifacts"],
    },
    {
        "schema_version": "repomori.release_verify.v1",
        "kind": "report",
        "title": "Release package integrity verification report",
        "producer": "verify_release_package",
        "required_fields": ["schema_version", "status", "root", "resolved_root", "summary", "checks", "artifacts"],
    },
    {
        "schema_version": "repomori.release_evidence.v1",
        "kind": "report",
        "title": "Release evidence bundle",
        "producer": "build_release_evidence",
        "required_fields": ["schema_version", "status", "package_dir", "summary", "checks", "release", "artifacts"],
    },
    {
        "schema_version": "repomori.health.v1",
        "kind": "report",
        "title": "Release health and trend bundle",
        "producer": "run_release_health",
        "required_fields": ["schema_version", "status", "repo_path", "settings", "summary", "checks"],
    },
    {
        "schema_version": "repomori.compat.v1",
        "kind": "report",
        "title": "RepoMori compatibility report",
        "producer": "check_compatibility",
        "required_fields": ["schema_version", "status", "summary", "checks", "schema_catalog"],
    },
    {
        "schema_version": "repomori.contract_check.v1",
        "kind": "report",
        "title": "RepoMori public contract fixture diff",
        "producer": "check_contract_fixture",
        "required_fields": ["schema_version", "status", "fixture_path", "summary", "diffs", "guidance"],
    },
    {
        "schema_version": "repomori.cli_commands.v1",
        "kind": "report",
        "title": "RepoMori CLI command inventory",
        "producer": "build_cli_command_inventory",
        "required_fields": ["schema_version", "status", "prog", "summary", "commands"],
    },
    {
        "schema_version": "repomori.baseline_drift_report.v1",
        "kind": "report",
        "title": "Baseline drift telemetry report",
        "producer": "build_baseline_drift_report",
        "required_fields": ["schema_version", "status", "strict_count", "semi_strict_count", "fallback_count", "ignored_total", "run_ts", "repo_path"],
    },
    {
        "schema_version": "repomori.baseline_drift_record.v1",
        "kind": "record",
        "title": "Baseline drift telemetry log row",
        "producer": "append_baseline_drift_log",
        "required_fields": ["schema_version", "run_ts", "repo_path", "status", "non_strict_count", "non_strict_ratio"],
    },
    {
        "schema_version": "repomori.baseline_drift_summary.v1",
        "kind": "report",
        "title": "Baseline drift log summary",
        "producer": "summarize_baseline_drift_log",
        "required_fields": ["schema_version", "status", "log_path", "count", "max_non_strict_ratio", "avg_non_strict_ratio"],
    },
    {
        "schema_version": "repomori.config.v1",
        "kind": "config",
        "title": "RepoMori TOML config",
        "producer": "init_config",
        "required_fields": ["schema_version", "default_profile", "profiles"],
    },
)

_RELEASE_CHECK_ARTIFACT_DIR_NAME = ".repomori-release-check"
_RELEASE_CHECK_ARTIFACT_MARKDOWN = "release-check.md"
_RELEASE_CHECK_ARTIFACT_REPORT = "release-check.json"
_RELEASE_CHECK_ARTIFACT_DRIFT_LOG = "baseline-drift.jsonl"

_RELEASE_EVIDENCE_ARTIFACT_MARKDOWN = "release-evidence.md"
_RELEASE_EVIDENCE_ARTIFACT_REPORT = "release-evidence.json"

_RELEASE_HEALTH_ARTIFACT_DIR_NAME = ".repomori-health"
_RELEASE_HEALTH_ARTIFACT_MARKDOWN = "release-health.md"
_RELEASE_HEALTH_ARTIFACT_REPORT = "release-health.json"
_RELEASE_HEALTH_COMPAT_ARTIFACT_MARKDOWN = "compat.md"
_RELEASE_HEALTH_COMPAT_ARTIFACT_REPORT = "compat.json"
_RELEASE_HEALTH_CONTRACT_ARTIFACT_MARKDOWN = "contract-check.md"
_RELEASE_HEALTH_CONTRACT_ARTIFACT_REPORT = "contract-check.json"

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
RELEASE_CHECK_WORKSPACE_GENERATED_DIRS = {
    "pack",
    "packs",
    "benchmark",
    "benchmarks",
    "handoff",
    "handoffs",
}
RELEASE_CHECK_WORKSPACE_ALLOWED_PREFIX = ".repomori-"
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

QUERY_TOKEN_ALIASES = {
    "command": ("cli", "argparse"),
    "commands": ("cli", "argparse"),
    "configuration": ("config", "settings"),
    "database": ("db", "sqlite", "postgres"),
    "databases": ("db", "sqlite", "postgres"),
    "connection": ("connect", "conn"),
    "connections": ("connect", "conn"),
    "persistent": ("persist", "store", "storage"),
    "persistence": ("persist", "store", "storage"),
    "storage": ("store", "persist", "persistence"),
    "stored": ("store", "persist"),
    "stores": ("store", "storage"),
    "testing": ("test", "tests", "unittest", "pytest"),
    "tests": ("test", "unittest", "pytest"),
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
            "baseline_match_counts": {
                "strict": sum(
                    1 for finding in ignored_findings if finding.get("baseline_match") == "strict"
                ),
                "semi_strict": sum(
                    1
                    for finding in ignored_findings
                    if finding.get("baseline_match") == "semi_strict"
                ),
                "fallback": sum(
                    1 for finding in ignored_findings if finding.get("baseline_match") == "fallback"
                ),
            },
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


def _release_check_artifacts_dir(repo_path: Path, artifacts_dir: Path | str | None) -> Path:
    if artifacts_dir is not None:
        return Path(artifacts_dir).resolve()
    return repo_path / _RELEASE_CHECK_ARTIFACT_DIR_NAME


def _release_check_workspace_health_check(
    repo_path: Path,
    *,
    allowed_paths: set[Path] | None = None,
) -> dict[str, Any]:
    """Check for top-level generated artifacts that should be isolated first."""

    allowed: set[Path] = set(allowed_paths or set())
    issues: list[dict[str, Any]] = []
    for entry in sorted(repo_path.iterdir()):
        resolved = entry.resolve()
        if resolved in allowed:
            continue
        rel = entry.name
        lower = rel.lower()
        if lower in {".git", ".github"}:
            continue
        if lower.startswith(RELEASE_CHECK_WORKSPACE_ALLOWED_PREFIX):
            continue
        if entry.is_dir() and lower in RELEASE_CHECK_WORKSPACE_GENERATED_DIRS:
            issues.append(
                {
                    "path": str(entry),
                    "scope": "directory",
                    "message": (
                        f"Top-level generated directory '{rel}' should be moved under a dedicated"
                        " workspace such as .repomori-packs or .repomori-release-check."
                    ),
                }
            )
            continue
        if (
            entry.is_file()
            and entry.suffix.lower() == ".repomori"
            and not rel.startswith(RELEASE_CHECK_WORKSPACE_ALLOWED_PREFIX)
        ):
            issues.append(
                {
                    "path": str(entry),
                    "scope": "file",
                    "message": (
                        f"Top-level '{rel}' pack artifact should be written outside the"
                        " repository root before release-check."
                    ),
                }
            )

    if issues:
        return {
            "name": "workspace",
            "ok": False,
            "status": "fail",
            "issues": issues,
            "count": len(issues),
        }
    return {"name": "workspace", "ok": True, "status": "pass", "issues": [], "count": 0}


def _release_check_scan_failure_reasons(scan_report: dict[str, Any]) -> list[str]:
    """Return concise release-check reasons for common scan-driven hard failures."""

    findings = scan_report.get("findings", [])
    generated_directories = [item for item in findings if item.get("code") == "generated_artifact_dir"]
    generated_pack_files = [item for item in findings if item.get("code") == "repomori_pack_artifact"]
    noise_directories = [item for item in findings if item.get("code") == "dependency_or_build_noise"]
    reasons: list[str] = []
    if generated_directories:
        paths = sorted({str(item.get("path", "")) for item in generated_directories})
        sample = ", ".join(paths[:3])
        if len(paths) > 3:
            sample = f"{sample}, +{len(paths)-3} more"
        reasons.append(
            f"Scan detected generated artifact directorie(s): {sample}"
            "; isolate RepoMori outputs from repo root."
        )
    if generated_pack_files:
        paths = sorted({str(item.get("path", "")) for item in generated_pack_files})
        sample = ", ".join(paths[:3])
        if len(paths) > 3:
            sample = f"{sample}, +{len(paths)-3} more"
        reasons.append(
            f"Scan detected .repomori pack artifact(s): {sample}; move generated pack"
            " outputs to a dedicated hidden path."
        )
    if noise_directories:
        paths = sorted({str(item.get("path", "")) for item in noise_directories})
        sample = ", ".join(paths[:3])
        if len(paths) > 3:
            sample = f"{sample}, +{len(paths)-3} more"
        reasons.append(
            "Scan detected build/dependency cache noise: "
            f"{sample}; avoid running release-check on a dirty working tree."
        )
    return reasons


def _release_health_artifacts_dir(repo_path: Path, artifacts_dir: Path | str | None) -> Path:
    if artifacts_dir is not None:
        return Path(artifacts_dir).resolve()
    return repo_path / _RELEASE_HEALTH_ARTIFACT_DIR_NAME


def _normalize_policy_threshold(raw: Any, *, metric: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    mapping: dict[str, Any] = {}
    for key, out_key in (
        ("warn-at", "warn_at"),
        ("warn_at", "warn_at"),
        ("warn", "warn_at"),
    ):
        if key in raw:
            mapping[out_key] = raw[key]
    for key, out_key in (
        ("fail-at", "fail_at"),
        ("fail_at", "fail_at"),
        ("fail", "fail_at"),
    ):
        if key in raw:
            mapping[out_key] = raw[key]
    if metric == "ratio":
        for key, out_key in (
            ("investigate-at", "investigate_at"),
            ("investigate_at", "investigate_at"),
            ("investigate", "investigate_at"),
        ):
            if key in raw:
                mapping[out_key] = raw[key]

    out: dict[str, Any] = {}
    for key in ("warn_at", "fail_at"):
        value = mapping.get(key)
        if value is None:
            continue
        if metric == "ratio":
            try:
                converted = float(value)
            except (TypeError, ValueError):
                continue
            if converted < 0:
                continue
            out[key] = converted
            continue
        try:
            converted = int(value)
        except (TypeError, ValueError):
            continue
        if converted < 0:
            continue
        out[key] = converted

    if metric == "ratio":
        value = mapping.get("investigate_at")
        if value is not None:
            try:
                converted = float(value)
            except (TypeError, ValueError):
                converted = None
            if converted is not None and converted >= 0:
                out["investigate_at"] = converted
    return out


def _read_drift_policy(policy_path: Path | str | None) -> dict[str, Any] | None:
    if policy_path is None:
        return None
    path = Path(policy_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Drift policy not found: {path}")
    raw_text = path.read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Drift policy must be valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Drift policy must be a JSON object: {path}")

    def _metric(name: str, metric: str) -> dict[str, Any]:
        value = raw.get(name, {})
        if not isinstance(value, dict):
            return {}
        return _normalize_policy_threshold(value, metric=metric)

    non_strict_ratio = _metric("non_strict_ratio", metric="ratio")
    semi_strict_delta = _metric("semi_strict_delta", metric="delta")
    fallback_delta = _metric("fallback_delta", metric="delta")

    global_ratio = _normalize_policy_threshold(raw, metric="ratio")
    global_delta = _normalize_policy_threshold(raw, metric="delta")
    if not non_strict_ratio:
        non_strict_ratio = dict(global_ratio)
    if not semi_strict_delta:
        semi_strict_delta = dict(global_delta)
    if not fallback_delta:
        fallback_delta = dict(global_delta)

    # If a user only provides top-level threshold keys and also metric-specific
    # entries, top-level values should still seed any missing metric fields.
    if "warn_at" in global_ratio and "warn_at" not in non_strict_ratio:
        non_strict_ratio["warn_at"] = global_ratio["warn_at"]
    if "fail_at" in global_ratio and "fail_at" not in non_strict_ratio:
        non_strict_ratio["fail_at"] = global_ratio["fail_at"]
    if "investigate_at" in global_ratio and "investigate_at" not in non_strict_ratio:
        non_strict_ratio["investigate_at"] = global_ratio["investigate_at"]

    if "warn_at" in global_delta and "warn_at" not in semi_strict_delta:
        semi_strict_delta["warn_at"] = global_delta["warn_at"]
    if "fail_at" in global_delta and "fail_at" not in semi_strict_delta:
        semi_strict_delta["fail_at"] = global_delta["fail_at"]
    if "warn_at" in global_delta and "warn_at" not in fallback_delta:
        fallback_delta["warn_at"] = global_delta["warn_at"]
    if "fail_at" in global_delta and "fail_at" not in fallback_delta:
        fallback_delta["fail_at"] = global_delta["fail_at"]

    # Keep only expected keys for deterministic reporting.
    def _trim_thresholds(values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: values[key]
            for key in ("warn_at", "investigate_at", "fail_at")
            if key in values
        }

    return {
        "path": str(path),
        "non_strict_ratio": _trim_thresholds(non_strict_ratio),
        "semi_strict_delta": _trim_thresholds(semi_strict_delta),
        "fallback_delta": _trim_thresholds(fallback_delta),
    }


def _evaluate_drift_policy(drift: dict[str, Any], policy: dict[str, Any] | None) -> dict[str, Any]:
    if policy is None:
        return {
            "status": "pass",
            "status_reason": "no_policy",
            "warn_count": 0,
            "fail_count": 0,
            "violations": [],
            "settings": None,
        }
    if not isinstance(policy, dict):
        return {
            "status": "warn",
            "status_reason": "invalid_policy",
            "warn_count": 1,
            "fail_count": 0,
            "violations": [
                {
                    "metric": "policy",
                    "level": "warn",
                    "message": "drift policy was not a valid object",
                    "path": str(policy) if not isinstance(policy, dict) else None,
                }
            ],
            "settings": None,
        }

    non_strict_ratio = policy.get("non_strict_ratio", {})
    semi_strict_delta = policy.get("semi_strict_delta", {})
    fallback_delta = policy.get("fallback_delta", {})
    violations: list[dict[str, Any]] = []
    warn_count = 0
    fail_count = 0

    def _check_ratio(
        value: Any,
        setting: dict[str, Any],
        *,
        metric_name: str,
    ) -> None:
        nonlocal warn_count, fail_count
        if not isinstance(value, (int, float)):
            return
        if "warn_at" in setting and value >= setting["warn_at"]:
            warn_count += 1
            violations.append(
                {
                    "metric": metric_name,
                    "level": "warn",
                    "value": value,
                    "threshold": setting["warn_at"],
                    "path": policy.get("path"),
                }
            )
        if "investigate_at" in setting and value >= setting["investigate_at"]:
            violations.append(
                {
                    "metric": metric_name,
                    "level": "investigate",
                    "value": value,
                    "threshold": setting["investigate_at"],
                    "path": policy.get("path"),
                }
            )
        if "fail_at" in setting and value > setting["fail_at"]:
            fail_count += 1
            violations.append(
                {
                    "metric": metric_name,
                    "level": "fail",
                    "value": value,
                    "threshold": setting["fail_at"],
                    "path": policy.get("path"),
                }
            )

    _check_ratio(
        drift.get("non_strict_ratio", 0.0),
        non_strict_ratio,
        metric_name="non_strict_ratio",
    )
    _check_ratio(
        drift.get("semi_strict_count", 0),
        semi_strict_delta,
        metric_name="semi_strict_delta",
    )
    _check_ratio(
        drift.get("fallback_count", 0),
        fallback_delta,
        metric_name="fallback_delta",
    )

    status = "fail" if fail_count else "warn" if warn_count else "pass"
    return {
        "status": status,
        "status_reason": None if status == "pass" else f"policy_{status}",
        "warn_count": warn_count,
        "fail_count": fail_count,
        "violations": violations,
        "settings": {
            "path": policy.get("path"),
            "non_strict_ratio": non_strict_ratio,
            "semi_strict_delta": semi_strict_delta,
            "fallback_delta": fallback_delta,
        },
    }


def _release_check_summary_status(
    checks: dict[str, dict[str, Any]],
    *,
    include_policy_warnings: bool = False,
    drift_policy_status: str | None = None,
) -> str:
    # keep --fail-on behavior stable in release-check; this helper is shared with
    # release-health where policy warnings can be folded into status.
    if include_policy_warnings:
        if drift_policy_status == "fail":
            return "fail"
        if drift_policy_status == "warn":
            return "warn"
    for check in checks.values():
        if check.get("status") == "fail":
            return "fail"
        if check.get("status") == "warn":
            return "warn"
    return "pass"


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
    drift_log: Path | str | None = None,
    drift_policy: Path | str | None = None,
    artifacts_dir: Path | str | None = None,
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
    artifacts_path = _release_check_artifacts_dir(repo_path, artifacts_dir=artifacts_dir)
    allowed_workspace_paths = {artifacts_path}
    workspace_check = _release_check_workspace_health_check(repo_path, allowed_paths=allowed_workspace_paths)

    schema_check = _release_schema_check()
    scan = scan_repository(
        repo_path,
        public_release=public_release,
        baseline=baseline_arg,
    )
    drift_meta = {
        "run_ts": int(started),
        "repo_path": str(repo_path),
        "baseline_path": str(baseline_arg) if baseline_arg else None,
    }
    drift = build_baseline_drift_report(
        scan,
        run_meta=drift_meta,
    )
    policy = _read_drift_policy(drift_policy) if drift_policy is not None else None
    drift_policy_report = _evaluate_drift_policy(drift, policy)

    drift_log_report = None
    drift_log_path: Path | None = None

    if drift_log is not None:
        drift_log_path = Path(drift_log).resolve()
    elif drift_log is None and artifacts_dir is not None:
        # ensure the report and drift telemetry are kept for CI uploads unless
        # this command is intentionally using a private temporary directory.
        drift_log_path = artifacts_path / _RELEASE_CHECK_ARTIFACT_DRIFT_LOG
    if drift_log_path is not None:
        drift_log_report = append_baseline_drift_log(drift, drift_log_path)
    scan_ok = not _scan_has_severity_threshold(scan, fail_on)
    tests_check = _release_tests_check(repo_path, tests_dir) if run_tests else _release_skipped_check("tests")
    demo_check = (
        _release_demo_check(repo_path, demo_out=demo_out, keep_demo=keep_demo)
        if run_demo_smoke
        else _release_skipped_check("demo")
    )

    checks = {
        "workspace": {
            "name": "workspace",
            "ok": workspace_check["ok"],
            "status": workspace_check["status"],
            "issues": workspace_check["issues"],
            "count": workspace_check["count"],
        },
        "schema": schema_check,
        "scan": {
            "name": "scan",
            "ok": scan_ok,
            "status": "pass" if scan_ok else "fail",
            "fail_on": fail_on,
            "baseline_path": str(baseline_arg) if baseline_arg else None,
            "summary": scan.get("summary", {}),
            "report": scan,
            "failure_reasons": _release_check_scan_failure_reasons(scan),
            "drift": drift,
            "drift_warnings": drift,
            "drift_log": drift_log_report,
            "drift_policy": drift_policy_report,
        },
        "tests": tests_check,
        "demo": demo_check,
    }
    failed = [name for name, check in checks.items() if not check.get("ok")]
    status = _release_check_summary_status(checks)
    elapsed = round(time.time() - started, 4)
    failure_reasons: list[str] = []
    for check_name, check in checks.items():
        if not isinstance(check, dict) or check.get("status") != "fail":
            continue
        if check_name == "workspace":
            for issue in check.get("issues", []):
                message = issue.get("message")
                if message:
                    failure_reasons.append(f"workspace: {message}")
        elif check_name == "scan":
            for reason in check.get("failure_reasons", []):
                if reason:
                    failure_reasons.append(f"scan: {reason}")
        else:
            failure_reason = check.get("status_reason")
            if isinstance(failure_reason, str) and failure_reason.strip():
                failure_reasons.append(f"{check_name}: {failure_reason}")

    report = {
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
            "drift_log": str(drift_log_path) if drift_log_path is not None else None,
            "drift_policy": str(Path(drift_policy).resolve()) if drift_policy is not None else None,
            "artifacts_dir": str(artifacts_path),
        },
        "summary": {
            "elapsed_seconds": elapsed,
            "failed_checks": failed,
            "scan_findings": scan.get("summary", {}).get("findings"),
            "scan_ignored_findings": scan.get("summary", {}).get("ignored_findings"),
            "tests_returncode": tests_check.get("returncode"),
            "demo_status": demo_check.get("demo_status"),
            "drift_policy_status": drift_policy_report.get("status"),
            "failure_reason_count": len(failure_reasons),
        },
        "failure_reasons": failure_reasons,
        "checks": checks,
        "artifacts": {
            "json": str(artifacts_path / _RELEASE_CHECK_ARTIFACT_REPORT),
            "markdown": str(artifacts_path / _RELEASE_CHECK_ARTIFACT_MARKDOWN),
            "drift_log": str(drift_log_path) if drift_log_path is not None else None,
        },
    }
    if artifacts_dir is not None:
        artifacts_path.mkdir(parents=True, exist_ok=True)
        report_path = artifacts_path / _RELEASE_CHECK_ARTIFACT_REPORT
        _write_json(report_path, report)
        markdown_path = artifacts_path / _RELEASE_CHECK_ARTIFACT_MARKDOWN
        markdown_path.write_text(format_release_check_markdown(report), encoding="utf-8")

    return report


def _build_missing_drift_summary(
    log_path: Path | str | None,
    *,
    status: str = "warn",
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "repomori.baseline_drift_summary.v1",
        "status": status,
        "log_path": str(log_path) if log_path is not None else "<missing>",
        "limit": 0,
        "count": 0,
        "warn_count": 0,
        "max_non_strict_ratio": 0.0,
        "avg_non_strict_ratio": 0.0,
        "trend": {
            "semi_strict_delta": 0,
            "fallback_delta": 0,
            "non_strict_delta": 0,
        },
        "rows": [],
        "ordered": True,
        "status_reason": reason,
    }


def check_compatibility(
    pack: Path | str | None = None,
    *,
    handoff: Path | str | None = None,
    snapshot_dir: Path | str | None = None,
    verify_pack_contents: bool = False,
    require_handoff: bool = True,
) -> dict[str, Any]:
    """Check whether local RepoMori pack, handoff, schema, agent, and MCP contracts line up."""

    started = time.time()
    checks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    resolved_pack = Path(pack).resolve() if pack is not None else _compat_latest_pack(snapshot_dir)
    handoff_path = Path(handoff).resolve() if handoff is not None else None

    pack_info = None
    pack_verification = None
    if resolved_pack is None:
        _compat_add_check(
            checks,
            "pack_input",
            "warn",
            "No pack was supplied and no latest snapshot pack could be resolved.",
            {"pack": None, "snapshot_dir": str(Path(snapshot_dir).resolve()) if snapshot_dir is not None else None},
        )
        warnings.append({"code": "pack_missing", "message": "No pack was available for compatibility checks."})
    elif not resolved_pack.is_file():
        _compat_add_check(
            checks,
            "pack_exists",
            "fail",
            "Pack path does not exist.",
            {"pack": str(resolved_pack)},
        )
        errors.append({"code": "pack_not_found", "path": str(resolved_pack), "message": "Pack path does not exist."})
    else:
        try:
            pack_info = info_pack(resolved_pack)
            pack_schema = pack_info.get("schema_version")
            _compat_add_check(
                checks,
                "pack_schema",
                "pass" if pack_schema == SCHEMA_VERSION else "fail",
                "Pack schema is compatible with this RepoMori build.",
                {"pack": str(resolved_pack), "expected": SCHEMA_VERSION, "actual": pack_schema},
            )
            if pack_schema != SCHEMA_VERSION:
                errors.append({"code": "pack_schema_mismatch", "expected": SCHEMA_VERSION, "actual": pack_schema})
        except Exception as exc:
            _compat_add_check(checks, "pack_schema", "fail", f"Could not read pack metadata: {exc}", {"pack": str(resolved_pack)})
            errors.append({"code": "pack_read_failed", "path": str(resolved_pack), "message": str(exc)})

        if verify_pack_contents and pack_info is not None:
            try:
                pack_verification = verify_pack(resolved_pack)
                verified = bool(pack_verification.get("verified"))
                _compat_add_check(
                    checks,
                    "pack_verification",
                    "pass" if verified else "fail",
                    "Pack content verification completed.",
                    {"verified": verified, "error_count": pack_verification.get("error_count")},
                )
                if not verified:
                    errors.append({"code": "pack_verification_failed", "message": "Pack verification failed."})
            except Exception as exc:
                _compat_add_check(checks, "pack_verification", "fail", f"Could not verify pack: {exc}", {"pack": str(resolved_pack)})
                errors.append({"code": "pack_verification_error", "message": str(exc)})

    handoff_check = None
    handoff_schemas: list[dict[str, Any]] = []
    if handoff_path is None:
        if require_handoff:
            _compat_add_check(checks, "handoff_input", "warn", "No handoff directory was supplied.", {"handoff": None})
            warnings.append({"code": "handoff_missing", "message": "No handoff directory was supplied."})
        else:
            _compat_add_check(
                checks,
                "handoff_input",
                "pass",
                "No handoff directory was supplied; handoff compatibility checks were skipped.",
                {"handoff": None, "required": False},
            )
    elif not handoff_path.is_dir():
        _compat_add_check(checks, "handoff_exists", "fail", "Handoff directory does not exist.", {"handoff": str(handoff_path)})
        errors.append({"code": "handoff_not_found", "path": str(handoff_path), "message": "Handoff directory does not exist."})
    else:
        handoff_check = check_handoff_package(handoff_path)
        handoff_valid = bool(handoff_check.get("valid"))
        _compat_add_check(
            checks,
            "handoff_integrity",
            "pass" if handoff_valid else "fail",
            "Handoff artifact hashes, sizes, JSON, and copied pack references are compatible.",
            {"valid": handoff_valid, "error_count": handoff_check.get("error_count")},
        )
        if not handoff_valid:
            errors.append({"code": "handoff_integrity_failed", "message": "Handoff validation failed."})
        handoff_schemas = _compat_handoff_schema_checks(handoff_path)
        bad_schemas = [item for item in handoff_schemas if item.get("status") == "fail"]
        warn_schemas = [item for item in handoff_schemas if item.get("status") == "warn"]
        _compat_add_check(
            checks,
            "handoff_schemas",
            "fail" if bad_schemas else "warn" if warn_schemas else "pass",
            "Handoff JSON artifacts use expected RepoMori schema versions.",
            {"checked": len(handoff_schemas), "failed": len(bad_schemas), "warned": len(warn_schemas), "artifacts": handoff_schemas},
        )
        for item in bad_schemas:
            errors.append({"code": "handoff_schema_mismatch", "path": item.get("path"), "message": item.get("message")})
        for item in warn_schemas:
            warnings.append({"code": "handoff_schema_missing", "path": item.get("path"), "message": item.get("message")})

    catalog = schema_catalog()
    schema_versions = {item["schema_version"] for item in catalog.get("schemas", [])}
    required_schemas = _compat_required_schemas()
    missing_schemas = sorted(required_schemas - schema_versions)
    _compat_add_check(
        checks,
        "schema_catalog",
        "pass" if not missing_schemas else "fail",
        "Schema catalog contains required agent-facing contracts.",
        {"required": sorted(required_schemas), "missing": missing_schemas},
    )
    if missing_schemas:
        errors.append({"code": "schema_catalog_missing", "missing": missing_schemas, "message": "Required schemas are missing."})

    agent_methods = set(catalog.get("agent_methods", []))
    required_methods = _compat_required_agent_methods()
    missing_methods = sorted(required_methods - agent_methods)
    _compat_add_check(
        checks,
        "agent_methods",
        "pass" if not missing_methods else "fail",
        "Agent bridge exposes required compatibility methods.",
        {"required": sorted(required_methods), "missing": missing_methods},
    )
    if missing_methods:
        errors.append({"code": "agent_methods_missing", "missing": missing_methods, "message": "Required agent methods are missing."})

    mcp_tools = set(catalog.get("mcp_tools", []))
    required_tools = _compat_required_mcp_tools()
    missing_tools = sorted(required_tools - mcp_tools)
    _compat_add_check(
        checks,
        "mcp_tools",
        "pass" if not missing_tools else "fail",
        "MCP bridge exposes required compatibility tools.",
        {"required": sorted(required_tools), "missing": missing_tools},
    )
    if missing_tools:
        errors.append({"code": "mcp_tools_missing", "missing": missing_tools, "message": "Required MCP tools are missing."})

    status = _worst_status(*(item.get("status") for item in checks))
    warning_count = sum(1 for item in checks if item.get("status") == "warn")
    error_count = sum(1 for item in checks if item.get("status") == "fail")
    return {
        "schema_version": "repomori.compat.v1",
        "status": status,
        "created_at": int(started),
        "settings": {
            "pack": str(Path(pack).resolve()) if pack is not None else None,
            "resolved_pack": str(resolved_pack) if resolved_pack is not None else None,
            "handoff": str(handoff_path) if handoff_path is not None else None,
            "snapshot_dir": str(Path(snapshot_dir).resolve()) if snapshot_dir is not None else None,
            "verify_pack_contents": verify_pack_contents,
            "require_handoff": require_handoff,
        },
        "summary": {
            "elapsed_seconds": round(time.time() - started, 4),
            "check_count": len(checks),
            "error_count": error_count,
            "warning_count": warning_count,
            "pack_schema": pack_info.get("schema_version") if isinstance(pack_info, dict) else None,
            "pack_path": str(resolved_pack) if resolved_pack is not None else None,
            "handoff_valid": handoff_check.get("valid") if isinstance(handoff_check, dict) else None,
            "schema_count": catalog.get("schema_count"),
            "agent_method_count": len(agent_methods),
            "mcp_tool_count": len(mcp_tools),
        },
        "checks": checks,
        "warnings": warnings,
        "errors": errors,
        "pack": pack_info,
        "pack_verification": pack_verification,
        "handoff": handoff_check,
        "handoff_schemas": handoff_schemas,
        "schema_catalog": {
            "schema_count": catalog.get("schema_count"),
            "required_schemas": sorted(required_schemas),
            "missing_schemas": missing_schemas,
            "required_agent_methods": sorted(required_methods),
            "missing_agent_methods": missing_methods,
            "required_mcp_tools": sorted(required_tools),
            "missing_mcp_tools": missing_tools,
        },
    }


def format_compat_markdown(report: dict[str, Any]) -> str:
    """Render a compact compatibility report as Markdown."""

    summary = report.get("summary", {})
    settings = report.get("settings", {})
    lines = [
        "# RepoMori Compatibility",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Pack: `{summary.get('pack_path')}`",
        f"- Handoff: `{settings.get('handoff')}`",
        f"- Pack schema: `{summary.get('pack_schema')}`",
        f"- Checks: `{summary.get('check_count')}`",
        f"- Warnings: `{summary.get('warning_count')}`",
        f"- Errors: `{summary.get('error_count')}`",
        "",
        "## Checks",
        "",
    ]
    for item in report.get("checks", []):
        lines.append(f"- `{item.get('id')}` status=`{item.get('status')}`")
        message = item.get("message")
        if message:
            lines.append(f"  - {message}")
    handoff_schemas = report.get("handoff_schemas", [])
    if handoff_schemas:
        lines.extend(["", "## Handoff Schemas", ""])
        for item in handoff_schemas:
            lines.append(
                f"- `{item.get('path')}` status=`{item.get('status')}` "
                f"expected=`{item.get('expected')}` actual=`{item.get('actual')}`"
            )
    errors = report.get("errors", [])
    if errors:
        lines.extend(["", "## Errors", ""])
        for item in errors:
            lines.append(f"- `{item.get('code')}` {item.get('message')}")
    warnings = report.get("warnings", [])
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for item in warnings:
            lines.append(f"- `{item.get('code')}` {item.get('message')}")
    return "\n".join(lines).rstrip() + "\n"


def check_contract_fixture(
    fixture: Path | str | None,
    *,
    required: bool = True,
) -> dict[str, Any]:
    """Compare the current public contracts with a checked-in contract fixture."""

    started = time.time()
    fixture_path = Path(fixture).resolve() if fixture is not None else None
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if fixture_path is None:
        message = "No contract fixture was supplied; contract check skipped."
        if required:
            errors.append({"code": "fixture_missing", "message": message})
        else:
            warnings.append({"code": "fixture_skipped", "message": message})
        return _contract_check_report(
            fixture_path,
            expected={},
            actual=_current_contract_snapshot(),
            diffs={},
            started=started,
            status="fail" if required else "pass",
            warnings=warnings,
            errors=errors,
            skipped=not required,
        )
    if not fixture_path.is_file():
        message = f"Contract fixture not found: {fixture_path}"
        errors.append({"code": "fixture_not_found", "path": str(fixture_path), "message": message})
        return _contract_check_report(
            fixture_path,
            expected={},
            actual=_current_contract_snapshot(),
            diffs={},
            started=started,
            status="fail",
            warnings=warnings,
            errors=errors,
            skipped=False,
        )
    try:
        expected = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append({"code": "fixture_read_failed", "path": str(fixture_path), "message": str(exc)})
        return _contract_check_report(
            fixture_path,
            expected={},
            actual=_current_contract_snapshot(),
            diffs={},
            started=started,
            status="fail",
            warnings=warnings,
            errors=errors,
            skipped=False,
        )
    if not isinstance(expected, dict):
        errors.append({"code": "fixture_invalid", "path": str(fixture_path), "message": "Contract fixture must be a JSON object."})
        expected = {}

    actual = _current_contract_snapshot()
    diffs: dict[str, Any] = {}
    for key in (
        "schema_versions",
        "agent_methods",
        "mcp_tools",
        "full_compat_check_ids",
        "release_health_compat_artifacts",
    ):
        diffs[key] = _contract_sequence_diff(expected.get(key), actual.get(key))
    change_count = sum(item["change_count"] for item in diffs.values())
    if change_count:
        errors.append(
            {
                "code": "contract_drift",
                "message": f"Public contract fixture differs from current RepoMori contracts ({change_count} changes).",
            }
        )
    return _contract_check_report(
        fixture_path,
        expected=expected,
        actual=actual,
        diffs=diffs,
        started=started,
        status="fail" if errors else "pass",
        warnings=warnings,
        errors=errors,
        skipped=False,
    )


def format_contract_check_markdown(report: dict[str, Any]) -> str:
    """Render a contract fixture diff as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Contract Check",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Fixture: `{report.get('fixture_path')}`",
        f"- Skipped: `{summary.get('skipped')}`",
        f"- Changes: `{summary.get('change_count', 0)}`",
        f"- Added: `{summary.get('added_count', 0)}`",
        f"- Removed: `{summary.get('removed_count', 0)}`",
        "",
        "## Diffs",
        "",
    ]
    for name, diff in report.get("diffs", {}).items():
        lines.append(
            f"- `{name}` status=`{diff.get('status')}` "
            f"expected=`{diff.get('expected_count')}` actual=`{diff.get('actual_count')}` "
            f"added=`{len(diff.get('added', []))}` removed=`{len(diff.get('removed', []))}`"
        )
        for value in diff.get("removed", []):
            lines.append(f"  - removed: `{value}`")
        for value in diff.get("added", []):
            lines.append(f"  - added: `{value}`")
    guidance = report.get("guidance", [])
    if guidance:
        lines.extend(["", "## Guidance", ""])
        for item in guidance:
            lines.append(f"- {item}")
    errors = report.get("errors", [])
    if errors:
        lines.extend(["", "## Errors", ""])
        for item in errors:
            lines.append(f"- `{item.get('code')}` {item.get('message')}")
    warnings = report.get("warnings", [])
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for item in warnings:
            lines.append(f"- `{item.get('code')}` {item.get('message')}")
    return "\n".join(lines).rstrip() + "\n"


def run_release_health(
    repo: Path | str,
    *,
    snapshot_dir: Path | str | None = None,
    baseline: Path | str | None = None,
    fail_on: str = "low",
    public_release: bool = True,
    run_tests: bool = True,
    run_demo_smoke: bool = True,
    demo_out: Path | str | None = None,
    keep_demo: bool = False,
    tests_dir: Path | str = "tests",
    drift_log: Path | str | None = None,
    drift_policy: Path | str | None = None,
    timeline_limit: int = 5,
    drift_summary_limit: int = 20,
    doctor_verify_packs: bool = False,
    compat_handoff: Path | str | None = None,
    compat_verify_pack: bool = False,
    contract_fixture: Path | str | None = None,
    artifacts_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run a snapshot + timeline release health bundle."""

    if timeline_limit <= 0:
        raise ValueError("timeline_limit must be greater than zero")
    if drift_summary_limit <= 0:
        raise ValueError("drift_summary_limit must be greater than zero")

    started = time.time()
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository folder not found: {repo_path}")
    if fail_on not in SCAN_SEVERITY_ORDER:
        raise ValueError("fail_on must be one of: info, low, medium, high")

    artifacts_path = _release_health_artifacts_dir(repo_path, artifacts_dir=artifacts_dir)
    release_check_artifacts = artifacts_path if artifacts_dir is not None else None
    if release_check_artifacts is not None:
        release_check_artifacts.mkdir(parents=True, exist_ok=True)

    snapshot_path = (
        Path(snapshot_dir).resolve()
        if snapshot_dir is not None
        else repo_path / ".repomori-packs"
    )
    if snapshot_dir is None:
        # keep behavior deterministic for legacy callers and docs.
        snapshot_path = Path(snapshot_path)
    # release-check defaults to repository-scoped drift telemetry defaults; pass that
    # path explicitly when this check is writing artifacts.
    release_check_log = (
        Path(drift_log).resolve()
        if drift_log is not None
        else None
    )
    if drift_log is None and release_check_artifacts is not None:
        release_check_log = release_check_artifacts / _RELEASE_CHECK_ARTIFACT_DRIFT_LOG

    release_check = run_release_check(
        repo_path,
        baseline=baseline,
        fail_on=fail_on,
        public_release=public_release,
        run_tests=run_tests,
        run_demo_smoke=run_demo_smoke,
        demo_out=demo_out,
        keep_demo=keep_demo,
        tests_dir=tests_dir,
        drift_log=release_check_log if release_check_log is not None else None,
        drift_policy=drift_policy,
        artifacts_dir=release_check_artifacts,
    )

    doctor = doctor_snapshot_dir(snapshot_path, verify_packs=doctor_verify_packs)
    chain = verify_snapshot_chain(snapshot_path)
    timeline = read_snapshot_timeline(snapshot_path, limit=timeline_limit)
    compat = check_compatibility(
        snapshot_dir=snapshot_path,
        handoff=compat_handoff,
        verify_pack_contents=compat_verify_pack,
        require_handoff=compat_handoff is not None,
    )
    contract_fixture_path = (
        Path(contract_fixture).resolve()
        if contract_fixture is not None
        else _default_contract_fixture(repo_path)
    )
    contract = check_contract_fixture(
        contract_fixture_path,
        required=contract_fixture is not None or contract_fixture_path is not None,
    )

    snapshot_path_check = ""
    if not snapshot_path.exists():
        snapshot_path_check = "Snapshot directory does not exist."
    elif not snapshot_path.is_dir():
        snapshot_path_check = "Snapshot path is not a directory."
    elif not (snapshot_path / "snapshots.json").exists():
        snapshot_path_check = "snapshots.json was not found."

    if snapshot_path_check:
        # A release-health run on a newly bootstrapped repo is expected to have no
        # snapshots yet. Treat missing timeline artifacts as warnings in this path so
        # the check remains visible-but-not-red until history exists.
        doctor_warnings = list(doctor.get("warnings", []))
        if not any(
            isinstance(item, dict) and item.get("path") == str(snapshot_path)
            and item.get("message") == snapshot_path_check
            for item in doctor_warnings
        ):
            doctor_warnings.append({"path": str(snapshot_path), "message": snapshot_path_check})
        doctor = {
            **doctor,
            "status": "warn",
            "errors": [],
            "warnings": doctor_warnings,
            "error_count": 0,
            "warning_count": len(doctor_warnings),
        }

        chain_warnings = list(chain.get("warnings", []))
        if not any(
            isinstance(item, dict) and item.get("path") == str(snapshot_path)
            and item.get("message") == snapshot_path_check
            for item in chain_warnings
        ):
            chain_warnings.append({"path": str(snapshot_path), "message": snapshot_path_check})
        chain = {
            **chain,
            "status": "warn",
            "errors": [],
            "warnings": chain_warnings,
        }

        timeline_summary = dict(timeline.get("summary", {}))
        timeline_summary["chain_status"] = "warn"
        timeline_summary["chain_head_hash"] = chain.get("summary", {}).get("head_chain_hash")
        timeline = {
            **timeline,
            "status": "warn",
            "summary": timeline_summary,
            "warnings": [
                {"path": str(snapshot_path), "message": snapshot_path_check},
            ],
        }

    timeline_status = (
        chain.get("status")
        or (timeline.get("summary", {}).get("chain_status"))
    )
    if timeline_status not in {"pass", "warn", "fail"}:
        timeline_status = "warn"
    timeline["status"] = timeline_status

    drift_summary_log = release_check.get("artifacts", {}).get("drift_log")
    if not drift_summary_log and release_check_log is not None:
        drift_summary_log = str(release_check_log)
    if drift_summary_log is None:
        drift_summary = _build_missing_drift_summary(
            release_check_log,
            status="warn",
            reason="No drift log path was supplied.",
        )
    else:
        try:
            drift_summary = summarize_baseline_drift_log(drift_summary_log, limit=drift_summary_limit)
        except FileNotFoundError:
            drift_summary = _build_missing_drift_summary(
                drift_summary_log,
                status="warn",
                reason="Drift summary log is not available yet.",
            )
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            drift_summary = _build_missing_drift_summary(
                drift_summary_log,
                status="warn",
                reason=f"Could not summarize drift log: {exc}",
            )

    checks = {
        "release_check": release_check,
        "doctor": doctor,
        "chain": chain,
        "timeline": timeline,
        "drift_summary": drift_summary,
        "compat": compat,
        "contract": contract,
    }
    status = _release_check_summary_status(
        checks,
        include_policy_warnings=True,
        drift_policy_status=release_check.get("summary", {}).get("drift_policy_status"),
    )
    summary = {
        "elapsed_seconds": round(time.time() - started, 4),
        "release_check_status": release_check.get("status"),
        "doctor_status": doctor.get("status"),
        "chain_status": chain.get("status"),
        "timeline_status": timeline_status,
        "drift_summary_status": drift_summary.get("status"),
        "compat_status": compat.get("status"),
        "contract_status": contract.get("status"),
        "handoff_score_pass_count": timeline.get("summary", {}).get("handoff_score_pass_count"),
        "handoff_score_warn_count": timeline.get("summary", {}).get("handoff_score_warn_count"),
        "handoff_score_fail_count": timeline.get("summary", {}).get("handoff_score_fail_count"),
        "handoff_triage_pass_count": timeline.get("summary", {}).get("handoff_triage_pass_count"),
        "handoff_triage_warn_count": timeline.get("summary", {}).get("handoff_triage_warn_count"),
        "handoff_triage_fail_count": timeline.get("summary", {}).get("handoff_triage_fail_count"),
        "failed_checks": [name for name, check in checks.items() if check.get("status") == "fail"],
    }
    report = {
        "schema_version": "repomori.health.v1",
        "status": status,
        "repo_path": str(repo_path),
        "snapshot_dir": str(snapshot_path),
        "settings": {
            "snapshot_dir": str(snapshot_path),
            "baseline": str(Path(baseline).resolve()) if baseline is not None else None,
            "fail_on": fail_on,
            "public_release": public_release,
            "run_tests": run_tests,
            "run_demo_smoke": run_demo_smoke,
            "demo_out": str(Path(demo_out).resolve()) if demo_out is not None else None,
            "keep_demo": keep_demo,
            "tests_dir": str(tests_dir),
            "drift_log": str(release_check_log) if release_check_log is not None else None,
            "drift_policy": str(Path(drift_policy).resolve()) if drift_policy is not None else None,
            "drift_summary_limit": drift_summary_limit,
            "timeline_limit": timeline_limit,
            "doctor_verify_packs": doctor_verify_packs,
            "compat_handoff": str(Path(compat_handoff).resolve()) if compat_handoff is not None else None,
            "compat_verify_pack": compat_verify_pack,
            "contract_fixture": str(contract_fixture_path) if contract_fixture_path is not None else None,
            "artifacts_dir": str(artifacts_path),
        },
        "summary": summary,
        "checks": checks,
        "artifacts": {
            "json": str(artifacts_path / _RELEASE_HEALTH_ARTIFACT_REPORT),
            "markdown": str(artifacts_path / _RELEASE_HEALTH_ARTIFACT_MARKDOWN),
            "compat_json": str(artifacts_path / _RELEASE_HEALTH_COMPAT_ARTIFACT_REPORT),
            "compat_markdown": str(artifacts_path / _RELEASE_HEALTH_COMPAT_ARTIFACT_MARKDOWN),
            "contract_json": str(artifacts_path / _RELEASE_HEALTH_CONTRACT_ARTIFACT_REPORT),
            "contract_markdown": str(artifacts_path / _RELEASE_HEALTH_CONTRACT_ARTIFACT_MARKDOWN),
        },
    }

    if artifacts_dir is not None:
        artifacts_path.mkdir(parents=True, exist_ok=True)
        health_report_path = artifacts_path / _RELEASE_HEALTH_ARTIFACT_REPORT
        _write_json(health_report_path, report)
        health_markdown = format_release_health_markdown(report)
        (artifacts_path / _RELEASE_HEALTH_ARTIFACT_MARKDOWN).write_text(
            health_markdown,
            encoding="utf-8",
        )
        _write_json(artifacts_path / _RELEASE_HEALTH_COMPAT_ARTIFACT_REPORT, compat)
        (artifacts_path / _RELEASE_HEALTH_COMPAT_ARTIFACT_MARKDOWN).write_text(
            format_compat_markdown(compat),
            encoding="utf-8",
        )
        _write_json(artifacts_path / _RELEASE_HEALTH_CONTRACT_ARTIFACT_REPORT, contract)
        (artifacts_path / _RELEASE_HEALTH_CONTRACT_ARTIFACT_MARKDOWN).write_text(
            format_contract_check_markdown(contract),
            encoding="utf-8",
        )

    return report


def format_release_check_markdown(report: dict[str, Any]) -> str:
    """Render a compact release-check report as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Release Check",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Repository: `{report.get('repo_path')}`",
        f"- Elapsed: `{summary.get('elapsed_seconds', 0)}s`",
        f"- Failed checks: `{', '.join(summary.get('failed_checks', []) or []) or 'none'}`",
    ]
    checks = report.get("checks", {})
    if checks:
        lines.append("")
        lines.append("## Checks")
        for name, check in checks.items():
            if not isinstance(check, dict):
                continue
            detail = check.get("status", "unknown")
            line = f"- {name}: `{detail}`"
            if name == "scan":
                scan_summary = check.get("summary", {})
                policy_status = None
                policy = check.get("drift_policy") or {}
                if isinstance(policy, dict):
                    policy_status = policy.get("status")
                detail_parts = [
                    f"findings={scan_summary.get('findings', 0)}",
                    f"ignored={scan_summary.get('ignored_findings', 0)}",
                    f"drift_policy={policy_status or 'na'}",
                ]
                line = line[:-1] + f" {' '.join(detail_parts)}`"
            elif name == "workspace":
                line = line[:-1] + f" issues={check.get('count', 0)}`"
                for issue in check.get("issues", []):
                    message = issue.get("message")
                    if message:
                        lines.append(f"  - {message}")
            lines.append(line)
        if report.get("failure_reasons"):
            lines.append("")
            lines.append("## Failure Reasons")
            for reason in report.get("failure_reasons", []):
                lines.append(f"- {reason}")
    return "\n".join(lines).rstrip() + "\n"


def format_release_health_markdown(report: dict[str, Any]) -> str:
    """Render a compact release-health report as Markdown."""

    summary = report.get("summary", {})
    checks = report.get("checks", {})
    lines = [
        "# RepoMori Release Health",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Repository: `{report.get('repo_path')}`",
        f"- Snapshot Directory: `{report.get('snapshot_dir')}`",
        f"- Elapsed: `{summary.get('elapsed_seconds', 0)}s`",
        f"- Handoff scores pass/warn/fail: `{summary.get('handoff_score_pass_count')}` / `{summary.get('handoff_score_warn_count')}` / `{summary.get('handoff_score_fail_count')}`",
        f"- Handoff triage pass/warn/fail: `{summary.get('handoff_triage_pass_count')}` / `{summary.get('handoff_triage_warn_count')}` / `{summary.get('handoff_triage_fail_count')}`",
    ]
    lines.append("")
    lines.append("## Checks")
    for name in ("release_check", "doctor", "chain", "timeline", "drift_summary", "compat", "contract"):
        check = checks.get(name, {})
        if not isinstance(check, dict):
            continue
        status = check.get("status", "unknown")
        detail = f"- {name}: `{status}`"
        if name == "release_check":
            drift_policy = check.get("summary", {}).get("drift_policy_status")
            if drift_policy:
                detail += f" drift_policy={drift_policy}"
        elif name == "drift_summary":
            detail += f" ratio={check.get('max_non_strict_ratio', 0.0):.2f}"
        elif name == "compat":
            compat_summary = check.get("summary", {})
            detail += (
                f" pack_schema={compat_summary.get('pack_schema') or 'none'}"
                f" handoff_valid={compat_summary.get('handoff_valid')}"
                f" warnings={compat_summary.get('warning_count', 0)}"
                f" errors={compat_summary.get('error_count', 0)}"
            )
        elif name == "contract":
            contract_summary = check.get("summary", {})
            detail += (
                f" changes={contract_summary.get('change_count', 0)}"
                f" skipped={contract_summary.get('skipped')}"
            )
        elif name == "timeline":
            chain_status = check.get("chain_status")
            if chain_status is not None:
                detail += f" chain={chain_status}"
            if check.get("chain", {}).get("status"):
                detail += f" chain_report={check['chain']['status']}"
        lines.append(detail)
    return "\n".join(lines).rstrip() + "\n"


def build_baseline_drift_report(
    scan_report: dict[str, Any],
    *,
    run_meta: dict[str, Any] | None = None,
    investigate_threshold: float = 0.20,
) -> dict[str, Any]:
    """Build non-blocking baseline drift telemetry for scan or release-check reports."""

    if not isinstance(scan_report, dict):
        raise TypeError("scan_report must be a JSON report object.")
    counts = dict(scan_report.get("summary", {}).get("baseline_match_counts", {}))
    strict_count = int(counts.get("strict", 0))
    semi_strict_count = int(counts.get("semi_strict", 0))
    fallback_count = int(counts.get("fallback", 0))

    ignored_total = strict_count + semi_strict_count + fallback_count
    non_strict_count = semi_strict_count + fallback_count
    non_strict_ratio = round(non_strict_count / ignored_total, 4) if ignored_total else 0.0

    downgraded_from_line_match = semi_strict_count > 0
    downgraded_from_message_match = fallback_count > 0
    warnings = []
    if downgraded_from_line_match:
        warnings.append("line-based strict baseline matches were downgraded to semi-strict by line drift")
    if downgraded_from_message_match:
        warnings.append("message-based fallback baseline matches were used due to safe uniqueness checks")
    status = "warn" if (downgraded_from_line_match or downgraded_from_message_match) else "pass"
    investigate = bool(non_strict_ratio >= investigate_threshold and non_strict_count)

    run_meta_dict = dict(run_meta or {})
    settings = scan_report.get("settings", {}) if isinstance(scan_report.get("settings"), dict) else {}
    return {
        "schema_version": "repomori.baseline_drift_report.v1",
        "status": status,
        "strict_count": strict_count,
        "semi_strict_count": semi_strict_count,
        "fallback_count": fallback_count,
        "ignored_total": ignored_total,
        "non_strict_count": non_strict_count,
        "non_strict_ratio": non_strict_ratio,
        "downgraded_from_line_match": downgraded_from_line_match,
        "downgraded_from_message_match": downgraded_from_message_match,
        "investigate": investigate,
        "warnings": warnings,
        "repo_path": run_meta_dict.get("repo_path") or scan_report.get("repo_path"),
        "baseline_path": run_meta_dict.get("baseline_path") or settings.get("baseline_path"),
        "run_ts": run_meta_dict.get("run_ts", int(time.time())),
        "run_id": run_meta_dict.get("run_id"),
    }


def append_baseline_drift_log(
    drift_report: dict[str, Any],
    log_path: Path | str,
) -> dict[str, Any]:
    """Append one timestamped baseline-drift record to a JSONL log and return metadata."""

    if not isinstance(drift_report, dict):
        raise TypeError("drift_report must be a JSON object.")

    row = dict(drift_report)
    if row.get("schema_version") != "repomori.baseline_drift_record.v1":
        row["schema_version"] = "repomori.baseline_drift_record.v1"
    if "run_ts" not in row:
        row["run_ts"] = int(time.time())

    log_file = Path(log_path).resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")

    return {
        "status": "appended",
        "log_path": str(log_file),
        "entry": row,
    }


def summarize_baseline_drift_log(
    log_path: Path | str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Summarize recent baseline drift rows from a JSONL telemetry log."""

    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    log_file = Path(log_path).resolve()
    if not log_file.exists():
        raise FileNotFoundError(f"Drift log not found: {log_file}")

    rows: list[dict[str, Any]] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("schema_version") != "repomori.baseline_drift_record.v1":
            continue
        rows.append(parsed)

    selected = rows[-limit:] if limit and rows else []
    count = len(selected)
    if count == 0:
        return {
            "schema_version": "repomori.baseline_drift_summary.v1",
            "status": "pass",
            "log_path": str(log_file),
            "limit": limit,
            "count": 0,
            "warn_count": 0,
            "max_non_strict_ratio": 0.0,
            "avg_non_strict_ratio": 0.0,
            "trend": {
                "semi_strict_delta": 0,
                "fallback_delta": 0,
                "non_strict_delta": 0,
            },
            "rows": [],
            "ordered": True,
        }

    ratios = [float(row.get("non_strict_ratio", 0.0)) for row in selected]
    max_ratio = round(max(ratios), 4) if ratios else 0.0
    avg_ratio = round(sum(ratios) / len(ratios), 4) if ratios else 0.0
    warn_count = sum(1 for row in selected if row.get("status") == "warn")
    first = selected[0]
    last = selected[-1]
    semi_strict_delta = int(last.get("semi_strict_count", 0)) - int(first.get("semi_strict_count", 0))
    fallback_delta = int(last.get("fallback_count", 0)) - int(first.get("fallback_count", 0))
    non_strict_delta = int(last.get("non_strict_count", 0)) - int(first.get("non_strict_count", 0))
    status = "warn" if warn_count > 0 else "pass"

    return {
        "schema_version": "repomori.baseline_drift_summary.v1",
        "status": status,
        "log_path": str(log_file),
        "limit": limit,
        "count": count,
        "warn_count": warn_count,
        "max_non_strict_ratio": max_ratio,
        "avg_non_strict_ratio": avg_ratio,
        "trend": {
            "semi_strict_delta": semi_strict_delta,
            "fallback_delta": fallback_delta,
            "non_strict_delta": non_strict_delta,
        },
        "rows": selected,
        "ordered": True,
    }


def _build_baseline_drift_warnings(
    counts: dict[str, Any] | None, *, investigate_threshold: float = 0.20
) -> dict[str, Any]:
    """Legacy helper used by tests and older callers."""

    counts_payload = {
        "summary": {
            "baseline_match_counts": counts or {},
        },
    }
    return build_baseline_drift_report(
        counts_payload,
        run_meta={},
        investigate_threshold=investigate_threshold,
    )


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


def inspect_pack(
    pack: Path | str,
    *,
    max_files: int = 20,
    top_terms: int = 30,
    top_symbols: int = 30,
    verify: bool = False,
) -> dict[str, Any]:
    """Build a structured inspection report for a RepoMori pack."""

    if max_files <= 0:
        raise ValueError("max_files must be greater than zero")
    if top_terms < 0:
        raise ValueError("top_terms must be zero or greater")
    if top_symbols < 0:
        raise ValueError("top_symbols must be zero or greater")

    started = time.time()
    pack_path = Path(pack)
    pack_info = info_pack(pack_path)
    pack_status = "pass" if pack_info.get("schema_version") == SCHEMA_VERSION else "warn"

    with closing(_open_pack(pack_path)) as conn:
        language_rows = conn.execute(
            """
            SELECT
                COALESCE(language, 'unknown') AS language,
                COUNT(*) AS file_count,
                COALESCE(SUM(size), 0) AS bytes,
                COALESCE(SUM(is_text), 0) AS text_files,
                COALESCE(SUM(1 - is_text), 0) AS binary_files,
                COALESCE(SUM(line_count), 0) AS lines,
                COALESCE(SUM(token_count), 0) AS tokens
            FROM files
            GROUP BY COALESCE(language, 'unknown')
            ORDER BY file_count DESC, bytes DESC, language
            """
        ).fetchall()
        compressor_rows = conn.execute(
            """
            SELECT
                compressor,
                COUNT(*) AS chunk_count,
                COALESCE(SUM(raw_size), 0) AS raw_bytes,
                COALESCE(SUM(compressed_size), 0) AS compressed_bytes
            FROM chunks
            GROUP BY compressor
            ORDER BY chunk_count DESC, compressor
            """
        ).fetchall()
        index_rows = conn.execute(
            """
            SELECT field, COUNT(*) AS row_count
            FROM search_index
            GROUP BY field
            ORDER BY row_count DESC, field
            """
        ).fetchall()
        file_rows = conn.execute(
            """
            SELECT path, language, size, sha256, is_text, line_count, token_count, chunk_count, summary_json
            FROM files
            ORDER BY path
            """
        ).fetchall()
        chunk_links = int(conn.execute("SELECT COUNT(*) FROM file_chunks").fetchone()[0])
        top_term_rows = conn.execute(
            """
            SELECT value, COUNT(*) AS count
            FROM search_index
            WHERE field='term'
            GROUP BY value
            ORDER BY count DESC, value
            LIMIT ?
            """,
            (top_terms,),
        ).fetchall()
        top_heading_rows = conn.execute(
            """
            SELECT value, COUNT(*) AS count
            FROM search_index
            WHERE field='heading'
            GROUP BY value
            ORDER BY count DESC, value
            LIMIT ?
            """,
            (top_terms,),
        ).fetchall()
        top_import_rows = conn.execute(
            """
            SELECT target, COUNT(*) AS count
            FROM imports
            GROUP BY target
            ORDER BY count DESC, target
            LIMIT ?
            """,
            (top_terms,),
        ).fetchall()
        top_symbol_rows = conn.execute(
            """
            SELECT kind, name, COUNT(*) AS count, MIN(line) AS first_line
            FROM symbols
            GROUP BY kind, name
            ORDER BY count DESC, name
            LIMIT ?
            """,
            (top_symbols,),
        ).fetchall()

    files = [_inspect_file_record(row) for row in file_rows]
    largest_files = sorted(files, key=lambda item: (-int(item.get("size") or 0), item["path"]))[:max_files]
    key_files = sorted(
        files,
        key=lambda item: (
            -_brief_file_score({**item, "_summary": item.get("_summary", {})}),
            -int(item.get("token_count") or 0),
            item["path"],
        ),
    )[:max_files]
    binary_files = sorted(
        [item for item in files if not item.get("is_text")],
        key=lambda item: (-int(item.get("size") or 0), item["path"]),
    )[:max_files]
    text_heavy_files = sorted(
        [item for item in files if item.get("is_text")],
        key=lambda item: (-int(item.get("token_count") or 0), item["path"]),
    )[:max_files]

    verification: dict[str, Any]
    if verify:
        verify_report = verify_pack(pack_path)
        verification = {
            "status": "pass" if verify_report.get("verified") else "fail",
            "verified": verify_report.get("verified"),
            "error_count": verify_report.get("error_count", 0),
            "checked_files": verify_report.get("checked_files", 0),
            "checked_chunks": verify_report.get("checked_chunks", 0),
            "elapsed_seconds": verify_report.get("elapsed_seconds"),
            "errors": verify_report.get("errors", []),
        }
        if not verify_report.get("verified"):
            pack_status = "fail"
    else:
        verification = {"status": "skipped", "verified": None, "error_count": None}

    chunk_raw = int(pack_info.get("unique_chunk_raw_bytes") or 0)
    chunk_compressed = int(pack_info.get("compressed_chunk_bytes") or 0)
    logical_bytes = int(pack_info.get("logical_bytes") or 0)
    pack_bytes = int(pack_info.get("pack_bytes") or 0)
    source_manifest = _inspect_source_manifest(largest_files + key_files, limit=max_files)

    return {
        "schema_version": "repomori.inspect.v1",
        "status": pack_status,
        "pack": {
            **_pack_identity(pack_info),
            "sha256": _path_sha256(pack_path),
            "text_files": pack_info.get("text_files"),
            "binary_files": pack_info.get("binary_files"),
            "logical_to_pack_ratio": pack_info.get("logical_to_pack_ratio"),
            "dedupe_ratio": pack_info.get("dedupe_ratio"),
        },
        "settings": {
            "max_files": max_files,
            "top_terms": top_terms,
            "top_symbols": top_symbols,
            "verify": verify,
        },
        "summary": {
            "file_count": pack_info.get("counts", {}).get("files", 0),
            "text_files": pack_info.get("text_files", 0),
            "binary_files": pack_info.get("binary_files", 0),
            "logical_bytes": logical_bytes,
            "pack_bytes": pack_bytes,
            "unique_chunk_raw_bytes": chunk_raw,
            "compressed_chunk_bytes": chunk_compressed,
            "pack_overhead_bytes": max(0, pack_bytes - chunk_compressed),
            "compression_savings_bytes": max(0, chunk_raw - chunk_compressed),
            "dedupe_savings_bytes": max(0, logical_bytes - chunk_raw),
            "logical_to_pack_ratio": pack_info.get("logical_to_pack_ratio"),
            "dedupe_ratio": pack_info.get("dedupe_ratio"),
            "top_language": language_rows[0]["language"] if language_rows else None,
            "elapsed_seconds": round(time.time() - started, 4),
        },
        "storage": {
            "chunks": {
                "count": pack_info.get("counts", {}).get("chunks", 0),
                "raw_bytes": chunk_raw,
                "compressed_bytes": chunk_compressed,
                "compression_ratio": _ratio(chunk_raw, chunk_compressed),
                "compressed_to_raw_ratio": _ratio(chunk_compressed, chunk_raw),
                "savings_bytes": max(0, chunk_raw - chunk_compressed),
                "compressors": [
                    {
                        "compressor": row["compressor"],
                        "chunk_count": row["chunk_count"],
                        "raw_bytes": row["raw_bytes"],
                        "compressed_bytes": row["compressed_bytes"],
                        "compressed_to_raw_ratio": _ratio(row["compressed_bytes"], row["raw_bytes"]),
                    }
                    for row in compressor_rows
                ],
            },
            "file_chunks": {
                "links": chunk_links,
                "unique_chunks": pack_info.get("counts", {}).get("chunks", 0),
                "duplicate_chunk_links": max(0, chunk_links - int(pack_info.get("counts", {}).get("chunks", 0) or 0)),
            },
            "index_rows": [
                {"field": row["field"], "row_count": row["row_count"]}
                for row in index_rows
            ],
        },
        "languages": [
            {
                "language": row["language"],
                "file_count": row["file_count"],
                "bytes": row["bytes"],
                "text_files": row["text_files"],
                "binary_files": row["binary_files"],
                "lines": row["lines"],
                "tokens": row["tokens"],
            }
            for row in language_rows
        ],
        "files": {
            "largest": [_visible_inspect_file(item) for item in largest_files],
            "key": [_visible_inspect_file(item) for item in key_files],
            "text_heavy": [_visible_inspect_file(item) for item in text_heavy_files],
            "binary": [_visible_inspect_file(item) for item in binary_files],
        },
        "vocabulary": {
            "top_terms": [[row["value"], row["count"]] for row in top_term_rows],
            "top_symbols": [
                {
                    "kind": row["kind"],
                    "name": row["name"],
                    "count": row["count"],
                    "first_line": row["first_line"],
                }
                for row in top_symbol_rows
            ],
            "top_imports": [[row["target"], row["count"]] for row in top_import_rows],
            "top_headings": [[row["value"], row["count"]] for row in top_heading_rows],
        },
        "verification": verification,
        "source_manifest": source_manifest,
    }


def format_pack_inspect_markdown(report: dict[str, Any]) -> str:
    """Render a pack inspection report as Markdown."""

    pack = report.get("pack", {})
    summary = report.get("summary", {})
    lines = [
        "# RepoMori Pack Inspector",
        "",
        f"Status: `{report.get('status')}`",
        f"Pack: `{pack.get('pack_path')}`",
        f"Repository: `{pack.get('repo_path')}`",
        f"Pack SHA-256: `{pack.get('sha256')}`",
        "",
        "## Summary",
        "",
        f"- Schema: `{pack.get('schema_version')}`",
        f"- Files: `{summary.get('file_count', 0)}`",
        f"- Text files: `{summary.get('text_files', 0)}`",
        f"- Binary files: `{summary.get('binary_files', 0)}`",
        f"- Logical bytes: `{summary.get('logical_bytes', 0)}`",
        f"- Pack bytes: `{summary.get('pack_bytes', 0)}`",
        f"- Unique chunk raw bytes: `{summary.get('unique_chunk_raw_bytes', 0)}`",
        f"- Compressed chunk bytes: `{summary.get('compressed_chunk_bytes', 0)}`",
        f"- Compression savings bytes: `{summary.get('compression_savings_bytes', 0)}`",
        f"- Dedupe savings bytes: `{summary.get('dedupe_savings_bytes', 0)}`",
        f"- Logical-to-pack ratio: `{summary.get('logical_to_pack_ratio')}`",
        f"- Top language: `{summary.get('top_language') or 'unknown'}`",
        "",
    ]

    verification = report.get("verification", {})
    lines.extend(
        [
            "## Verification",
            "",
            f"- Status: `{verification.get('status')}`",
            f"- Verified: `{verification.get('verified')}`",
            f"- Errors: `{verification.get('error_count')}`",
            "",
        ]
    )

    languages = report.get("languages", [])
    lines.extend(["## Languages", ""])
    if not languages:
        lines.extend(["No language data found.", ""])
    else:
        for item in languages:
            lines.append(
                f"- `{item.get('language')}` files=`{item.get('file_count')}` "
                f"bytes=`{item.get('bytes')}` tokens=`{item.get('tokens')}`"
            )
        lines.append("")

    storage = report.get("storage", {})
    chunks = storage.get("chunks", {})
    lines.extend(
        [
            "## Storage",
            "",
            f"- Chunks: `{chunks.get('count', 0)}`",
            f"- Compression ratio: `{chunks.get('compression_ratio')}`",
            f"- File chunk links: `{storage.get('file_chunks', {}).get('links', 0)}`",
            f"- Duplicate chunk links: `{storage.get('file_chunks', {}).get('duplicate_chunk_links', 0)}`",
            "",
        ]
    )

    _append_inspect_file_section(lines, "Key Files", report.get("files", {}).get("key", []))
    _append_inspect_file_section(lines, "Largest Files", report.get("files", {}).get("largest", []))

    vocabulary = report.get("vocabulary", {})
    lines.extend(["## Vocabulary", ""])
    terms = vocabulary.get("top_terms", [])
    symbols = vocabulary.get("top_symbols", [])
    imports = vocabulary.get("top_imports", [])
    if terms:
        lines.append("Top terms: " + ", ".join(f"`{term}`({count})" for term, count in terms[:20]))
    if symbols:
        lines.append(
            "Top symbols: "
            + ", ".join(
                f"`{item.get('kind')}:{item.get('name')}`({item.get('count')})"
                for item in symbols[:20]
            )
        )
    if imports:
        lines.append("Top imports: " + ", ".join(f"`{target}`({count})" for target, count in imports[:20]))
    if not terms and not symbols and not imports:
        lines.append("No vocabulary extracted.")
    lines.append("")

    manifest = report.get("source_manifest", [])
    lines.extend(["## Source Manifest", ""])
    if not manifest:
        lines.extend(["No manifest entries.", ""])
    else:
        for item in manifest:
            lines.append(f"- `{item.get('path')}` sha256=`{item.get('sha256')}` size=`{item.get('size')}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


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
                "match_reasons": result.get("match_reasons", result.get("why", [])),
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
            "expanded_terms": scored["expanded_terms"],
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
    expanded_terms = _expanded_query_terms(tokens)
    phrases = _query_phrases(query, tokens)
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, set[str]] = defaultdict(set)
    matched_tokens: dict[str, set[str]] = defaultdict(set)
    breakdown: dict[str, list[dict[str, Any]]] = defaultdict(list)

    if not tokens:
        return {
            "tokens": [],
            "expanded_terms": [],
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
                expanded_terms,
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
                expanded_terms,
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
                    expanded_terms,
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
                expanded_terms,
                phrases,
                _field_weight(field),
                scores,
                reasons,
                matched_tokens,
                breakdown,
            )

        for path, matches in matched_tokens.items():
            coverage = len(matches) / len(tokens)
            reason = "all-query-terms" if coverage == 1.0 else "partial-query-terms"
            missing_count = len(tokens) - len(matches)
            added = coverage * 3.0
            if reason == "all-query-terms":
                added += 12.0
            else:
                added = max(0.0, added - (missing_count * 2.0))
            scores[path] += added
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
        "expanded_terms": expanded_terms,
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
                "match_reasons": sorted(reasons[path]),
                "matched_terms": sorted(scored["matched_tokens"].get(path, set())),
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
            "match_reasons": result.get("match_reasons", result.get("why", [])),
            "matched_terms": result.get("matched_terms", []),
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
                f"- Match reasons: `{', '.join(source.get('match_reasons', source.get('why', []))) or 'none'}`",
                f"- Matched terms: `{', '.join(source.get('matched_terms', [])) or 'none'}`",
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


def evaluate_context_quality(
    pack: Path | str,
    cases: Iterable[dict[str, Any] | str],
    *,
    limit: int = 8,
    snippet_lines: int = 12,
    max_bytes: int | None = 4096,
    snippets_per_file: int = 2,
    include_source: bool = True,
) -> dict[str, Any]:
    """Run fixture-backed quality cases against context bundle output."""

    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if snippet_lines <= 0:
        raise ValueError("snippet_lines must be greater than zero")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be zero or greater")
    if snippets_per_file < 0:
        raise ValueError("snippets_per_file must be zero or greater")

    case_list = [_normalize_context_eval_case(case, index) for index, case in enumerate(cases, start=1)]
    if not case_list:
        raise ValueError("at least one context eval case is required")

    started = time.time()
    pack_info = info_pack(pack)
    evaluated_cases = []
    failures = []
    total_snippets = 0
    total_source_bytes = 0
    expected_path_ranks: list[int] = []

    for case in case_list:
        bundle = build_context_bundle(
            pack,
            case["question"],
            limit=limit,
            snippet_lines=snippet_lines,
            max_bytes=max_bytes,
            snippets_per_file=snippets_per_file,
            include_source=include_source,
        )
        sources = bundle.get("sources", [])
        result = _context_eval_result_summary(bundle)
        checks = _context_eval_checks(case, bundle)
        failed_checks = [check for check in checks if check["status"] == "fail"]
        status = "fail" if failed_checks else "pass"

        total_snippets += int(result["snippet_count"])
        total_source_bytes += int(result["source_bytes"])
        expected_path_ranks.extend(
            int(check["rank"])
            for check in checks
            if check["id"].startswith("expected_path:") and check.get("rank") is not None and check["status"] == "pass"
        )
        if failed_checks:
            failures.append(
                {
                    "case_id": case["id"],
                    "question": case["question"],
                    "failed_checks": failed_checks,
                }
            )

        evaluated_cases.append(
            {
                "id": case["id"],
                "question": case["question"],
                "status": status,
                "expectations": case["expectations"],
                "result": result,
                "checks": checks,
                "selected_sources": [_eval_source_summary(source) for source in sources],
            }
        )

    passed = sum(1 for case in evaluated_cases if case["status"] == "pass")
    failed = len(evaluated_cases) - passed
    elapsed = time.time() - started
    return {
        "schema_version": "repomori.context_eval.v1",
        "status": "fail" if failed else "pass",
        "pack": {
            "schema_version": pack_info.get("schema_version"),
            "repo_path": pack_info.get("repo_path"),
            "pack_path": pack_info.get("pack_path"),
            "logical_bytes": pack_info.get("logical_bytes"),
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
            "case_count": len(evaluated_cases),
            "passed_cases": passed,
            "failed_cases": failed,
            "total_snippets": total_snippets,
            "total_source_bytes": total_source_bytes,
            "average_expected_path_rank": round(sum(expected_path_ranks) / len(expected_path_ranks), 2)
            if expected_path_ranks
            else None,
            "elapsed_seconds": round(elapsed, 4),
        },
        "cases": evaluated_cases,
        "failures": failures,
    }


def format_context_eval_markdown(report: dict[str, Any]) -> str:
    """Render a context quality eval report as Markdown."""

    pack = report.get("pack", {})
    settings = report.get("settings", {})
    summary = report.get("summary", {})
    lines = [
        "# RepoMori Context Quality Eval",
        "",
        "## Pack",
        "",
        f"- Repository: `{pack.get('repo_path')}`",
        f"- Pack: `{pack.get('pack_path')}`",
        f"- Schema: `{pack.get('schema_version')}`",
        "",
        "## Settings",
        "",
        f"- Limit: `{settings.get('limit')}`",
        f"- Snippet lines: `{settings.get('snippet_lines')}`",
        f"- Max bytes per case: `{settings.get('max_bytes')}`",
        f"- Snippets per file: `{settings.get('snippets_per_file')}`",
        f"- Include source: `{settings.get('include_source')}`",
        "",
        "## Summary",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Cases: `{summary.get('case_count', 0)}`",
        f"- Passed: `{summary.get('passed_cases', 0)}`",
        f"- Failed: `{summary.get('failed_cases', 0)}`",
        f"- Total snippets: `{summary.get('total_snippets', 0)}`",
        f"- Total source bytes: `{summary.get('total_source_bytes', 0)}`",
        f"- Average expected path rank: `{summary.get('average_expected_path_rank')}`",
        "",
        "## Cases",
        "",
    ]
    for index, case in enumerate(report.get("cases", []), start=1):
        result = case.get("result", {})
        lines.extend(
            [
                f"### {index}. {case.get('id')} - {case.get('question')}",
                "",
                f"- Status: `{case.get('status')}`",
                f"- Top path: `{result.get('top_path')}`",
                f"- Top score: `{result.get('top_score')}`",
                f"- Selected paths: `{', '.join(result.get('selected_paths', [])) or 'none'}`",
                f"- Matched terms: `{', '.join(result.get('matched_terms', [])) or 'none'}`",
                f"- Snippets: `{result.get('snippet_count', 0)}`",
                "",
                "Checks:",
            ]
        )
        for check in case.get("checks", []):
            lines.append(
                f"- `{check.get('status')}` {check.get('id')}: {check.get('message')}"
            )
        lines.append("")
    failures = report.get("failures", [])
    lines.extend(["## Failures", ""])
    if failures:
        for failure in failures:
            lines.append(f"- `{failure.get('case_id')}` {failure.get('question')}")
    else:
        lines.append("No failing context quality cases.")
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


def inspect_pack_diff(
    base_pack: Path | str,
    target_pack: Path | str,
    *,
    max_files: int = 20,
    top_terms: int = 30,
    top_symbols: int = 30,
    verify: bool = False,
) -> dict[str, Any]:
    """Build a compact structural inspection diff between two RepoMori packs."""

    if max_files <= 0:
        raise ValueError("max_files must be greater than zero")
    if top_terms < 0:
        raise ValueError("top_terms must be zero or greater")
    if top_symbols < 0:
        raise ValueError("top_symbols must be zero or greater")

    started = time.time()
    base_inspect = inspect_pack(
        base_pack,
        max_files=max_files,
        top_terms=top_terms,
        top_symbols=top_symbols,
        verify=verify,
    )
    target_inspect = inspect_pack(
        target_pack,
        max_files=max_files,
        top_terms=top_terms,
        top_symbols=top_symbols,
        verify=verify,
    )
    comparison = compare_packs(base_pack, target_pack, limit=max_files)
    base_summary = base_inspect.get("summary", {})
    target_summary = target_inspect.get("summary", {})
    compare_summary = comparison.get("summary", {})
    storage_delta = _inspect_diff_storage_delta(base_inspect, target_inspect)
    vocabulary_delta = _inspect_diff_vocabulary_delta(base_inspect, target_inspect, limit=max(top_terms, top_symbols))
    status = _inspect_diff_status(base_inspect, target_inspect)

    return {
        "schema_version": "repomori.inspect_diff.v1",
        "status": status,
        "base_pack": base_inspect.get("pack", {}),
        "target_pack": target_inspect.get("pack", {}),
        "settings": {
            "max_files": max_files,
            "top_terms": top_terms,
            "top_symbols": top_symbols,
            "verify": verify,
        },
        "summary": {
            "added_count": compare_summary.get("added_count", 0),
            "removed_count": compare_summary.get("removed_count", 0),
            "changed_count": compare_summary.get("changed_count", 0),
            "unchanged_count": compare_summary.get("unchanged_count", 0),
            "file_count_delta": compare_summary.get("file_count_delta", 0),
            "text_files_delta": _inspect_delta_value(base_summary, target_summary, "text_files"),
            "binary_files_delta": _inspect_delta_value(base_summary, target_summary, "binary_files"),
            "byte_delta": compare_summary.get("byte_delta", 0),
            "logical_bytes_delta": compare_summary.get("logical_bytes_delta", 0),
            "pack_bytes_delta": _inspect_delta_value(base_summary, target_summary, "pack_bytes"),
            "unique_chunk_raw_bytes_delta": _inspect_delta_value(base_summary, target_summary, "unique_chunk_raw_bytes"),
            "compressed_chunk_bytes_delta": _inspect_delta_value(base_summary, target_summary, "compressed_chunk_bytes"),
            "compression_savings_bytes_delta": _inspect_delta_value(base_summary, target_summary, "compression_savings_bytes"),
            "dedupe_savings_bytes_delta": _inspect_delta_value(base_summary, target_summary, "dedupe_savings_bytes"),
            "base_top_language": base_summary.get("top_language"),
            "target_top_language": target_summary.get("top_language"),
            "elapsed_seconds": round(time.time() - started, 4),
        },
        "comparison": {
            "summary": compare_summary,
            "language_delta": comparison.get("language_delta", []),
            "truncated": comparison.get("truncated", {}),
        },
        "storage_delta": storage_delta,
        "language_delta": _inspect_diff_language_delta(base_inspect, target_inspect),
        "vocabulary_delta": vocabulary_delta,
        "files": comparison.get("files", {}),
        "verification": {
            "base": base_inspect.get("verification", {}),
            "target": target_inspect.get("verification", {}),
        },
        "source_manifest": _inspect_diff_source_manifest(comparison, limit=max_files),
    }


def format_pack_inspect_diff_markdown(report: dict[str, Any]) -> str:
    """Render a pack inspection diff report as Markdown."""

    summary = report.get("summary", {})
    base_pack = report.get("base_pack", {})
    target_pack = report.get("target_pack", {})
    lines = [
        "# RepoMori Pack Inspect Diff",
        "",
        f"Status: `{report.get('status')}`",
        f"Base: `{base_pack.get('pack_path')}`",
        f"Target: `{target_pack.get('pack_path')}`",
        "",
        "## Summary",
        "",
        f"- Added: `{summary.get('added_count', 0)}`",
        f"- Removed: `{summary.get('removed_count', 0)}`",
        f"- Changed: `{summary.get('changed_count', 0)}`",
        f"- Unchanged: `{summary.get('unchanged_count', 0)}`",
        f"- File count delta: `{summary.get('file_count_delta', 0)}`",
        f"- Text files delta: `{summary.get('text_files_delta', 0)}`",
        f"- Binary files delta: `{summary.get('binary_files_delta', 0)}`",
        f"- Logical bytes delta: `{summary.get('logical_bytes_delta', 0)}`",
        f"- Pack bytes delta: `{summary.get('pack_bytes_delta', 0)}`",
        f"- Base top language: `{summary.get('base_top_language') or 'unknown'}`",
        f"- Target top language: `{summary.get('target_top_language') or 'unknown'}`",
        "",
        "## Verification",
        "",
    ]

    verification = report.get("verification", {})
    for label in ("base", "target"):
        item = verification.get(label, {})
        lines.append(
            f"- {label}: status=`{item.get('status')}` verified=`{item.get('verified')}` "
            f"errors=`{item.get('error_count')}`"
        )
    lines.append("")

    storage = report.get("storage_delta", {})
    lines.extend(
        [
            "## Storage Delta",
            "",
            f"- Chunk count delta: `{storage.get('chunk_count_delta')}`",
            f"- Unique chunk raw bytes delta: `{storage.get('unique_chunk_raw_bytes_delta')}`",
            f"- Compressed chunk bytes delta: `{storage.get('compressed_chunk_bytes_delta')}`",
            f"- File chunk link delta: `{storage.get('file_chunk_link_delta')}`",
            f"- Duplicate chunk link delta: `{storage.get('duplicate_chunk_link_delta')}`",
            "",
        ]
    )

    language_delta = report.get("language_delta", [])
    lines.extend(["## Language Delta", ""])
    if not language_delta:
        lines.extend(["No language-level changes.", ""])
    else:
        for item in language_delta:
            lines.append(
                f"- `{item.get('language')}` files `{item.get('base_file_count')}` -> `{item.get('target_file_count')}` "
                f"delta=`{item.get('file_count_delta')}` bytes_delta=`{item.get('bytes_delta')}`"
            )
        lines.append("")

    vocabulary = report.get("vocabulary_delta", {})
    lines.extend(["## Vocabulary Delta", ""])
    for label, key in (
        ("Terms", "top_terms"),
        ("Symbols", "top_symbols"),
        ("Imports", "top_imports"),
        ("Headings", "top_headings"),
    ):
        item = vocabulary.get(key, {})
        added = item.get("added", [])
        removed = item.get("removed", [])
        changed = item.get("changed", [])
        lines.append(
            f"- {label}: added=`{len(added)}` removed=`{len(removed)}` count_changed=`{len(changed)}` shared=`{item.get('shared_count', 0)}`"
        )
        if added:
            lines.append("  - added: " + ", ".join(f"`{entry.get('value')}`" for entry in added[:8]))
        if removed:
            lines.append("  - removed: " + ", ".join(f"`{entry.get('value')}`" for entry in removed[:8]))
    lines.append("")

    files = report.get("files", {})
    _append_compare_file_section(lines, "Added Files", files.get("added", []))
    _append_compare_file_section(lines, "Removed Files", files.get("removed", []))
    lines.extend(["## Changed Files", ""])
    changed_files = files.get("changed", [])
    if not changed_files:
        lines.extend(["No changed files.", ""])
    else:
        for item in changed_files:
            before = item.get("before", {})
            after = item.get("after", {})
            reasons = ", ".join(item.get("change_reasons", []))
            lines.append(
                f"- `{item.get('path')}` reasons=`{reasons}` "
                f"bytes `{before.get('size')}` -> `{after.get('size')}` "
                f"sha `{before.get('sha256')}` -> `{after.get('sha256')}`"
            )
            detail = item.get("summary_delta", {})
            for key in ("added_symbols", "removed_symbols", "added_imports", "removed_imports", "added_terms", "removed_terms"):
                values = detail.get(key, [])
                if values:
                    lines.append(f"  - {key}: " + ", ".join(f"`{value}`" for value in values[:8]))
        lines.append("")

    manifest = report.get("source_manifest", [])
    lines.extend(["## Source Manifest", ""])
    if not manifest:
        lines.extend(["No changed source manifest entries.", ""])
    else:
        for item in manifest:
            lines.append(
                f"- `{item.get('path')}` change=`{item.get('change_type')}` "
                f"base_sha=`{item.get('base_sha256')}` target_sha=`{item.get('target_sha256')}`"
            )
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


def build_agent_brief(
    out_dir: Path | str,
    *,
    timeline_limit: int = 5,
    stats_limit: int = 10,
    verify_packs: bool = False,
    max_files: int = 8,
    top_terms: int = 40,
    top_symbols: int = 40,
) -> dict[str, Any]:
    """Build a one-file start brief from the latest snapshot memory state."""

    if timeline_limit <= 0:
        raise ValueError("timeline_limit must be greater than zero")
    if stats_limit <= 0:
        raise ValueError("stats_limit must be greater than zero")
    if max_files <= 0:
        raise ValueError("max_files must be greater than zero")
    if top_terms < 0:
        raise ValueError("top_terms must be zero or greater")
    if top_symbols < 0:
        raise ValueError("top_symbols must be zero or greater")

    started = time.time()
    out_path = Path(out_dir).resolve()
    timeline = read_snapshot_timeline(out_path, limit=timeline_limit)
    stats = read_snapshot_stats(out_path, limit=stats_limit)
    doctor = doctor_snapshot_dir(out_path, verify_packs=verify_packs)
    chain = verify_snapshot_chain(out_path)
    latest = timeline.get("latest") if isinstance(timeline.get("latest"), dict) else None
    latest_pack_path = _recorded_snapshot_path(out_path, latest.get("pack_path")) if latest else None
    repo_brief = None
    repo_brief_error = None
    if latest_pack_path is not None and latest_pack_path.exists() and latest_pack_path.is_file():
        repo_brief = build_repo_brief(
            latest_pack_path,
            max_files=max_files,
            top_terms=top_terms,
            top_symbols=top_symbols,
        )
    elif latest is not None:
        repo_brief_error = "latest snapshot pack is missing"

    latest_diff_context = _agent_brief_latest_diff_context(out_path, latest, max_files) if latest else None
    latest_inspect_diff = _agent_brief_latest_inspect_diff(out_path, latest, max_files) if latest else None
    artifacts = _agent_brief_artifacts(out_path, latest)
    latest_handoff = next((item for item in artifacts if item["kind"] == "handoff_dir"), None)
    stats_summary = stats.get("summary", {})
    timeline_summary = timeline.get("summary", {})
    chain_summary = chain.get("summary", {})
    inspect_diff_summary = latest_inspect_diff.get("summary", {}) if latest_inspect_diff else {}
    status = "pass"
    if (
        doctor.get("status") == "fail"
        or chain.get("status") == "fail"
        or latest is None
        or latest_pack_path is None
        or not latest_pack_path.exists()
    ):
        status = "fail"
    elif doctor.get("status") != "pass" or chain.get("status") != "pass" or repo_brief_error:
        status = "warn"

    summary = {
        "elapsed_seconds": round(time.time() - started, 4),
        "snapshot_count": timeline.get("snapshot_count"),
        "timeline_returned_count": timeline.get("returned_count"),
        "latest_pack_path": str(latest_pack_path) if latest_pack_path is not None else None,
        "latest_pack_sha256": latest.get("pack_sha256") if latest else None,
        "latest_created_at": latest.get("created_at") if latest else None,
        "doctor_status": doctor.get("status"),
        "doctor_errors": doctor.get("error_count"),
        "doctor_warnings": doctor.get("warning_count"),
        "chain_status": chain.get("status"),
        "chain_head_hash": chain_summary.get("head_chain_hash"),
        "chain_checked_count": chain_summary.get("checked_count"),
        "chain_anchored_to_pruned_history": chain_summary.get("anchored_to_pruned_history"),
        "total_added": timeline_summary.get("total_added"),
        "total_removed": timeline_summary.get("total_removed"),
        "total_changed": timeline_summary.get("total_changed"),
        "incremental_snapshot_count": stats_summary.get("incremental_snapshot_count"),
        "total_reused_files": stats_summary.get("total_reused_files"),
        "total_rebuilt_files": stats_summary.get("total_rebuilt_files"),
        "reuse_percent": stats_summary.get("reuse_percent"),
        "handoff_dir": latest_handoff.get("path") if latest_handoff else None,
        "handoff_exists": latest_handoff.get("exists") if latest_handoff else None,
        "handoff_score_status": latest.get("handoff_score_status") if latest else None,
        "handoff_score_percent": latest.get("handoff_score_percent") if latest else None,
        "handoff_score_failed_checks": latest.get("handoff_score_failed_checks") if latest else None,
        "handoff_score_warned_checks": latest.get("handoff_score_warned_checks") if latest else None,
        "handoff_triage_status": latest.get("handoff_triage_status") if latest else None,
        "handoff_triage_action_count": latest.get("handoff_triage_action_count") if latest else None,
        "handoff_triage_high_priority_count": latest.get("handoff_triage_high_priority_count") if latest else None,
        "inspect_diff_status": latest.get("inspect_diff_status") if latest else None,
        "inspect_diff_json": latest.get("inspect_diff_json") if latest else None,
        "inspect_diff_markdown": latest.get("inspect_diff_markdown") if latest else None,
        "inspect_diff_added_count": inspect_diff_summary.get("added_count"),
        "inspect_diff_changed_count": inspect_diff_summary.get("changed_count"),
        "inspect_diff_removed_count": inspect_diff_summary.get("removed_count"),
        "diff_context_status": latest.get("diff_context_status") if latest else None,
        "diff_context_selected_count": latest.get("diff_context_selected_count") if latest else None,
        "diff_context_added_count": latest.get("diff_context_added_count") if latest else None,
        "diff_context_changed_count": latest.get("diff_context_changed_count") if latest else None,
        "diff_context_removed_count": latest.get("diff_context_removed_count") if latest else None,
        "repo_brief_error": repo_brief_error,
    }

    return {
        "schema_version": "repomori.agent_brief.v1",
        "status": status,
        "out_dir": str(out_path),
        "created_at": int(time.time()),
        "settings": {
            "timeline_limit": timeline_limit,
            "stats_limit": stats_limit,
            "verify_packs": verify_packs,
            "max_files": max_files,
            "top_terms": top_terms,
            "top_symbols": top_symbols,
        },
        "summary": summary,
        "latest_snapshot": latest,
        "artifacts": artifacts,
        "latest_inspect_diff": latest_inspect_diff,
        "latest_diff_context": latest_diff_context,
        "repo_brief": repo_brief,
        "timeline": timeline,
        "stats": stats,
        "doctor": doctor,
        "chain": chain,
        "recommended_commands": _agent_brief_commands(out_path, latest_pack_path, latest),
    }


def format_agent_brief_markdown(brief: dict[str, Any]) -> str:
    """Render an agent start brief as Markdown."""

    summary = brief.get("summary", {})
    latest = brief.get("latest_snapshot") or {}
    lines = [
        "# RepoMori Agent Brief",
        "",
        f"- Status: `{brief.get('status')}`",
        f"- Snapshot directory: `{brief.get('out_dir')}`",
        f"- Latest pack: `{summary.get('latest_pack_path')}`",
        f"- Latest SHA-256: `{summary.get('latest_pack_sha256')}`",
        "",
        "## Health",
        "",
        f"- Doctor status: `{summary.get('doctor_status')}`",
        f"- Doctor errors: `{summary.get('doctor_errors')}`",
        f"- Doctor warnings: `{summary.get('doctor_warnings')}`",
        f"- Chain status: `{summary.get('chain_status')}`",
        f"- Chain head: `{summary.get('chain_head_hash')}`",
        f"- Chain checked: `{summary.get('chain_checked_count')}`",
        f"- Chain anchored to pruned history: `{summary.get('chain_anchored_to_pruned_history')}`",
        "",
        "## Timeline",
        "",
        f"- Snapshots: `{summary.get('snapshot_count')}`",
        f"- Returned: `{summary.get('timeline_returned_count')}`",
        f"- Added: `{summary.get('total_added')}`",
        f"- Changed: `{summary.get('total_changed')}`",
        f"- Removed: `{summary.get('total_removed')}`",
        "",
        "## Reuse",
        "",
        f"- Incremental snapshots: `{summary.get('incremental_snapshot_count')}`",
        f"- Reused files: `{summary.get('total_reused_files')}`",
        f"- Rebuilt files: `{summary.get('total_rebuilt_files')}`",
        f"- Reuse percent: `{summary.get('reuse_percent')}`",
        "",
        "## Latest Snapshot",
        "",
        f"- Pack name: `{latest.get('pack_name')}`",
        f"- Repo: `{latest.get('repo_path')}`",
        f"- Files: `{latest.get('file_count')}`",
        f"- Pack bytes: `{latest.get('pack_bytes')}`",
        f"- Verify passed: `{latest.get('verify_passed')}`",
        f"- Handoff: `{summary.get('handoff_dir')}` exists=`{summary.get('handoff_exists')}`",
        f"- Handoff score: `{summary.get('handoff_score_status')}` `{summary.get('handoff_score_percent')}`%",
        f"- Handoff triage: `{summary.get('handoff_triage_status')}` actions=`{summary.get('handoff_triage_action_count')}` high=`{summary.get('handoff_triage_high_priority_count')}`",
        "",
    ]

    inspect_diff = brief.get("latest_inspect_diff")
    lines.extend(["## Latest Inspect Diff", ""])
    if inspect_diff:
        inspect_summary = inspect_diff.get("summary", {})
        lines.extend(
            [
                f"- Status: `{inspect_diff.get('status')}`",
                f"- JSON: `{inspect_diff.get('json_path')}`",
                f"- Markdown: `{inspect_diff.get('markdown_path')}`",
                f"- Added: `{inspect_summary.get('added_count')}`",
                f"- Changed: `{inspect_summary.get('changed_count')}`",
                f"- Removed: `{inspect_summary.get('removed_count')}`",
                f"- Pack bytes delta: `{inspect_summary.get('pack_bytes_delta')}`",
                "",
            ]
        )
        for label, key in (("Added Files", "added"), ("Changed Files", "changed"), ("Removed Files", "removed")):
            entries = inspect_diff.get("files", {}).get(key, [])
            if entries:
                lines.extend([f"### {label}", ""])
                for item in entries:
                    reasons = ", ".join(str(reason) for reason in item.get("change_reasons", [])[:6])
                    suffix = f" reasons=`{reasons}`" if reasons else ""
                    lines.append(
                        f"- `{item.get('path')}` size=`{item.get('size')}` sha=`{item.get('sha256')}`{suffix}"
                    )
                lines.append("")
    else:
        lines.extend([f"Inspect diff status: `{summary.get('inspect_diff_status') or 'missing'}`", ""])

    diff_context = brief.get("latest_diff_context")
    lines.extend(["## Latest Diff Context", ""])
    if diff_context:
        diff_summary = diff_context.get("summary", {})
        lines.extend(
            [
                f"- Status: `{diff_context.get('status')}`",
                f"- JSON: `{diff_context.get('json_path')}`",
                f"- Markdown: `{diff_context.get('markdown_path')}`",
                f"- Added: `{diff_summary.get('added_count')}`",
                f"- Changed: `{diff_summary.get('changed_count')}`",
                f"- Removed: `{diff_summary.get('removed_count')}`",
                f"- Selected: `{diff_summary.get('selected_count')}`",
                "",
            ]
        )
        sources = diff_context.get("sources", [])
        if sources:
            lines.extend(["### Changed Files", ""])
            for item in sources:
                reasons = ", ".join(str(reason) for reason in item.get("match_reasons", [])[:6])
                lines.append(
                    f"- `{item.get('change_type')}` `{item.get('path')}` "
                    f"score=`{item.get('score')}` reasons=`{reasons}`"
                )
            lines.append("")
    else:
        lines.extend([f"Diff context status: `{summary.get('diff_context_status') or 'missing'}`", ""])

    repo_brief = brief.get("repo_brief") or {}
    orientation = repo_brief.get("orientation", {})
    if repo_brief:
        lines.extend(["## Repo Orientation", ""])
        _append_brief_file_section(lines, "Entrypoints", orientation.get("entrypoints", []))
        _append_brief_file_section(lines, "Key Files", orientation.get("key_files", []))
    elif summary.get("repo_brief_error"):
        lines.extend(["## Repo Orientation", "", f"Repo brief unavailable: `{summary.get('repo_brief_error')}`", ""])

    artifacts = brief.get("artifacts", [])
    if artifacts:
        lines.extend(["## Artifacts", ""])
        for item in artifacts:
            lines.append(
                f"- `{item.get('kind')}`: `{item.get('path')}` "
                f"exists=`{item.get('exists')}` size=`{item.get('size')}`"
            )
        lines.append("")

    commands = brief.get("recommended_commands", [])
    if commands:
        lines.extend(["## Recommended Commands", "", "```powershell"])
        for item in commands:
            lines.append(str(item.get("command", "")))
        lines.extend(["```", ""])

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

        inspect_diff = inspect_pack_diff(
            base_pack_path,
            pack_path,
            max_files=max_files,
            top_terms=top_terms,
            top_symbols=top_terms,
        )
        inspect_diff_json = out_path / "inspect-diff.json"
        inspect_diff_md = out_path / "inspect-diff.md"
        _write_json(inspect_diff_json, inspect_diff)
        inspect_diff_md.write_text(format_pack_inspect_diff_markdown(inspect_diff), encoding="utf-8")
        artifacts.append(_artifact_record(out_path, inspect_diff_json, "inspect_diff_json"))
        artifacts.append(_artifact_record(out_path, inspect_diff_md, "inspect_diff_markdown"))

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
        has_inspect_diff = (root / "inspect-diff.json").exists() or any(
            isinstance(artifact, dict) and artifact.get("path") == "inspect-diff.json"
            for artifact in artifacts
        )
        if has_inspect_diff:
            json_names.insert(-1, "inspect-diff.json")
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


def score_handoff_package(handoff_dir: Path | str) -> dict[str, Any]:
    """Score whether a handoff is valid, source-backed, and useful for another agent."""

    started = time.time()
    root = Path(handoff_dir).resolve()
    validation = check_handoff_package(root)
    checks: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    manifest = _handoff_score_read_json(root, "manifest.json")
    manifest_ok = isinstance(manifest, dict) and manifest.get("schema_version") == "repomori.handoff.v1"
    manifest_status = manifest.get("status") if isinstance(manifest, dict) else None
    manifest_points = 0.0
    if manifest_ok:
        manifest_points += 3
    if isinstance(manifest, dict) and manifest.get("question"):
        manifest_points += 2
    if isinstance(manifest, dict) and isinstance(manifest.get("pack"), dict):
        manifest_points += 2
    if manifest_status in {"complete", "complete_unverified"}:
        manifest_points += 3
    _handoff_score_add_check(
        checks,
        "manifest",
        manifest_points,
        10,
        "pass" if manifest_points == 10 else "fail",
        "Manifest has expected schema, question, pack identity, and completion status.",
        {"manifest_status": manifest_status, "schema_version": manifest.get("schema_version") if isinstance(manifest, dict) else None},
    )

    _handoff_score_add_check(
        checks,
        "integrity",
        25 if validation.get("valid") else 0,
        25,
        "pass" if validation.get("valid") else "fail",
        "check-handoff validates artifact hashes, sizes, JSON, and copied pack when present.",
        {
            "valid": validation.get("valid"),
            "error_count": validation.get("error_count"),
            "checked_artifacts": validation.get("checked_artifacts"),
            "checked_json": validation.get("checked_json"),
        },
    )
    if not validation.get("valid"):
        errors.append({"code": "handoff_validation_failed", "message": "Handoff failed artifact or JSON validation."})

    artifact_paths = set()
    artifact_kinds = set()
    if isinstance(manifest, dict):
        for artifact in manifest.get("artifacts", []):
            if isinstance(artifact, dict):
                artifact_paths.add(str(artifact.get("path", "")))
                artifact_kinds.add(str(artifact.get("kind", "")))

    required_paths = [
        "README.md",
        "brief.json",
        "brief.md",
        "context.json",
        "context.md",
        "capsule.json",
        "eval.json",
        "eval.md",
        "verify.json",
    ]
    present_required = [
        path
        for path in required_paths
        if path in artifact_paths and _handoff_score_file_exists(root, path)
    ]
    missing_required = [path for path in required_paths if path not in present_required]
    artifact_points = round(15 * (len(present_required) / len(required_paths)), 2)
    _handoff_score_add_check(
        checks,
        "artifact_coverage",
        artifact_points,
        15,
        "pass" if not missing_required else ("warn" if present_required else "fail"),
        "Core readable and machine-readable handoff artifacts are present and recorded in the manifest.",
        {"present": present_required, "missing": missing_required, "artifact_kinds": sorted(artifact_kinds)},
    )
    for missing in missing_required:
        warnings.append({"code": "missing_core_artifact", "path": missing, "message": "Expected handoff artifact is missing."})

    context = _handoff_score_read_json(root, "context.json")
    context_sources = context.get("sources", []) if isinstance(context, dict) else []
    context_manifest = context.get("source_manifest", []) if isinstance(context, dict) else []
    snippets = [
        snippet
        for source in context_sources
        if isinstance(source, dict)
        for snippet in source.get("snippets", [])
        if isinstance(snippet, dict)
    ]
    line_numbered = bool(snippets) and all(
        isinstance(snippet.get("start_line"), int)
        and isinstance(snippet.get("end_line"), int)
        and snippet["start_line"] <= snippet["end_line"]
        for snippet in snippets
    )
    context_points = 0.0
    if isinstance(context, dict) and context.get("schema_version") == "repomori.context.v1":
        context_points += 4
    if context_sources:
        context_points += 5
    if context_manifest:
        context_points += 4
    if snippets:
        context_points += 4
    if line_numbered:
        context_points += 3
    _handoff_score_add_check(
        checks,
        "source_context",
        context_points,
        20,
        "pass" if context_points == 20 else ("warn" if context_points >= 10 else "fail"),
        "Context bundle contains ranked sources, source manifest entries, and exact line-numbered snippets.",
        {
            "source_count": len(context_sources),
            "source_manifest_count": len(context_manifest),
            "snippet_count": len(snippets),
            "line_numbered_snippets": line_numbered,
        },
    )

    brief = _handoff_score_read_json(root, "brief.json")
    capsule = _handoff_score_read_json(root, "capsule.json")
    brief_orientation = brief.get("orientation", {}) if isinstance(brief, dict) else {}
    capsule_files = capsule.get("files", []) if isinstance(capsule, dict) else []
    capsule_manifest = capsule.get("manifest", []) if isinstance(capsule, dict) else []
    capsule_terms = capsule.get("dictionary", {}).get("terms", []) if isinstance(capsule, dict) else []
    machine_points = 0.0
    if isinstance(brief, dict) and brief.get("schema_version") == "repomori.brief.v1":
        machine_points += 3
    if brief_orientation.get("key_files") or brief_orientation.get("entrypoints"):
        machine_points += 4
    if isinstance(capsule, dict) and capsule.get("schema_version") == "repomori.capsule.v1":
        machine_points += 3
    if capsule_files and capsule_manifest:
        machine_points += 3
    if capsule_terms:
        machine_points += 2
    _handoff_score_add_check(
        checks,
        "machine_state",
        machine_points,
        15,
        "pass" if machine_points == 15 else ("warn" if machine_points >= 8 else "fail"),
        "Brief and capsule provide orientation, compact file records, source manifest, and vocabulary.",
        {
            "brief_key_files": len(brief_orientation.get("key_files", []) or []),
            "brief_entrypoints": len(brief_orientation.get("entrypoints", []) or []),
            "capsule_files": len(capsule_files),
            "capsule_manifest_count": len(capsule_manifest),
            "capsule_term_count": len(capsule_terms),
        },
    )

    eval_report = _handoff_score_read_json(root, "eval.json")
    eval_summary = eval_report.get("summary", {}) if isinstance(eval_report, dict) else {}
    question_count = int(eval_summary.get("question_count") or 0)
    passed_questions = int(eval_summary.get("passed_questions") or 0)
    weak_questions = int(eval_summary.get("weak_questions") or 0)
    eval_points = 0.0
    if isinstance(eval_report, dict) and eval_report.get("schema_version") == "repomori.eval.v1":
        eval_points += 2
    if question_count:
        eval_points += round(6 * (passed_questions / question_count), 2)
    if int(eval_summary.get("total_snippets") or 0) > 0:
        eval_points += 2
    _handoff_score_add_check(
        checks,
        "context_eval",
        eval_points,
        10,
        "pass" if eval_points == 10 else ("warn" if eval_points >= 5 else "fail"),
        "Eval report shows whether default and user questions receive enough ranked source context.",
        {
            "question_count": question_count,
            "passed_questions": passed_questions,
            "weak_questions": weak_questions,
            "total_snippets": eval_summary.get("total_snippets"),
        },
    )
    if weak_questions:
        warnings.append({"code": "weak_eval_questions", "message": "Some eval questions produced weak context.", "count": weak_questions})

    base_pack_present = isinstance(manifest, dict) and isinstance(manifest.get("base_pack"), dict)
    compare = _handoff_score_read_json(root, "compare.json") if _handoff_score_file_exists(root, "compare.json") else None
    inspect_diff = _handoff_score_read_json(root, "inspect-diff.json") if _handoff_score_file_exists(root, "inspect-diff.json") else None
    if base_pack_present:
        delta_points = 0.0
        if isinstance(compare, dict) and compare.get("schema_version") == "repomori.compare.v1":
            delta_points += 2.5
        if isinstance(inspect_diff, dict) and inspect_diff.get("schema_version") == "repomori.inspect_diff.v1":
            delta_points += 2.5
        delta_status = "pass" if delta_points == 5 else ("warn" if delta_points else "fail")
        delta_message = "Base-pack handoff includes both file-level compare and structural inspect-diff artifacts."
    else:
        delta_points = 5.0
        delta_status = "pass"
        delta_message = "No base pack was recorded, so delta artifacts are not required."
    _handoff_score_add_check(
        checks,
        "delta_context",
        delta_points,
        5,
        delta_status,
        delta_message,
        {
            "base_pack_present": base_pack_present,
            "compare_present": isinstance(compare, dict),
            "inspect_diff_present": isinstance(inspect_diff, dict),
        },
    )

    total_score = round(sum(float(item["points"]) for item in checks), 2)
    max_score = round(sum(float(item["max_points"]) for item in checks), 2)
    percent = round((total_score / max_score) * 100, 2) if max_score else 0.0
    failed_checks = [item["id"] for item in checks if item["status"] == "fail"]
    warned_checks = [item["id"] for item in checks if item["status"] == "warn"]
    critical_failed = any(check_id in {"manifest", "integrity"} for check_id in failed_checks)
    if not validation.get("valid") or critical_failed or percent < 60:
        status = "fail"
    elif failed_checks or percent < 85:
        status = "warn"
    else:
        status = "pass"

    return {
        "schema_version": "repomori.handoff_score.v1",
        "status": status,
        "handoff_dir": str(root),
        "created_at": int(time.time()),
        "summary": {
            "score": total_score,
            "max_score": max_score,
            "score_percent": percent,
            "valid": bool(validation.get("valid")),
            "failed_checks": failed_checks,
            "warned_checks": warned_checks,
            "artifact_count": len(artifact_paths),
            "context_source_count": len(context_sources),
            "context_snippet_count": len(snippets),
            "eval_question_count": question_count,
            "eval_weak_questions": weak_questions,
            "base_pack_present": base_pack_present,
            "compare_present": isinstance(compare, dict),
            "inspect_diff_present": isinstance(inspect_diff, dict),
            "elapsed_seconds": round(time.time() - started, 4),
        },
        "checks": checks,
        "validation": validation,
        "errors": errors,
        "warnings": warnings,
    }


def format_handoff_score_markdown(report: dict[str, Any]) -> str:
    """Render a handoff usefulness score report as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Handoff Score",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Handoff: `{report.get('handoff_dir')}`",
        f"- Score: `{summary.get('score')}` / `{summary.get('max_score')}` (`{summary.get('score_percent')}`%)",
        f"- Valid: `{summary.get('valid')}`",
        "",
        "## Checks",
        "",
    ]
    for item in report.get("checks", []):
        lines.append(
            f"- `{item.get('id')}`: status=`{item.get('status')}` "
            f"points=`{item.get('points')}/{item.get('max_points')}`"
        )
        message = item.get("message")
        if message:
            lines.append(f"  - {message}")
    lines.append("")

    warnings = report.get("warnings", [])
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            message = warning.get("message", "")
            code = warning.get("code", "warning")
            suffix = f" path=`{warning.get('path')}`" if warning.get("path") else ""
            lines.append(f"- `{code}` {message}{suffix}")
        lines.append("")

    errors = report.get("errors", [])
    if errors:
        lines.extend(["## Errors", ""])
        for error in errors:
            lines.append(f"- `{error.get('code')}` {error.get('message')}")
        lines.append("")

    validation = report.get("validation", {})
    lines.extend(
        [
            "## Validation",
            "",
            f"- Valid: `{validation.get('valid')}`",
            f"- Checked artifacts: `{validation.get('checked_artifacts')}`",
            f"- Checked JSON: `{validation.get('checked_json')}`",
            f"- Error count: `{validation.get('error_count')}`",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def triage_handoff_score(score_or_handoff: Path | str | dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
    """Turn a handoff score report into a short prioritized fix checklist."""

    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    score, source = _load_handoff_score_input(score_or_handoff)
    summary = score.get("summary", {})
    actions = _handoff_triage_actions(score)
    actions = sorted(actions, key=lambda item: (item["priority"], item["id"]))[:limit]
    high_count = sum(1 for item in actions if item["priority"] == 1)
    medium_count = sum(1 for item in actions if item["priority"] == 2)
    low_count = sum(1 for item in actions if item["priority"] >= 3)
    status = "fail" if high_count else "warn" if actions or score.get("status") != "pass" else "pass"

    return {
        "schema_version": "repomori.handoff_triage.v1",
        "status": status,
        "source": source,
        "created_at": int(time.time()),
        "settings": {"limit": limit},
        "summary": {
            "score_status": score.get("status"),
            "score_percent": summary.get("score_percent"),
            "score": summary.get("score"),
            "max_score": summary.get("max_score"),
            "handoff_dir": score.get("handoff_dir"),
            "action_count": len(actions),
            "high_priority_count": high_count,
            "medium_priority_count": medium_count,
            "low_priority_count": low_count,
        },
        "actions": actions,
        "score": score,
    }


def format_handoff_triage_markdown(report: dict[str, Any]) -> str:
    """Render a handoff triage report as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Handoff Triage",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Handoff: `{summary.get('handoff_dir')}`",
        f"- Score: `{summary.get('score_percent')}`% status=`{summary.get('score_status')}`",
        "",
        "## Checklist",
        "",
    ]
    actions = report.get("actions", [])
    if not actions:
        lines.extend(["No urgent handoff fixes. Keep the current handoff with its source pack.", ""])
    else:
        for index, action in enumerate(actions, start=1):
            lines.append(
                f"{index}. [P{action.get('priority')}] {action.get('title')} "
                f"(`{action.get('id')}`)"
            )
            lines.append(f"   - Why: {action.get('reason')}")
            fix = action.get("fix")
            if fix:
                lines.append(f"   - Fix: {fix}")
            command = action.get("command")
            if command:
                lines.append(f"   - Command: `{command}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_handoff_score_artifacts(handoff_path: Path) -> tuple[dict[str, Any], Path, Path]:
    score = score_handoff_package(handoff_path)
    score_json = handoff_path / "handoff-score.json"
    score_md = handoff_path / "handoff-score.md"
    _write_json(score_json, score)
    score_md.write_text(format_handoff_score_markdown(score), encoding="utf-8")
    return score, score_json, score_md


def _write_handoff_triage_artifacts(
    handoff_path: Path,
    score: dict[str, Any],
) -> tuple[dict[str, Any], Path | None, Path | None]:
    triage = triage_handoff_score(score)
    if triage.get("status") == "pass":
        return triage, None, None

    triage_json = handoff_path / "handoff-triage.json"
    triage_md = handoff_path / "handoff-triage.md"
    _write_json(triage_json, triage)
    triage_md.write_text(format_handoff_triage_markdown(triage), encoding="utf-8")
    return triage, triage_json, triage_md


def evaluate_handoff_quality(
    score_or_handoff: Path | str | dict[str, Any],
    *,
    profile: str = "safe",
    target_score: float | None = None,
) -> dict[str, Any]:
    """Apply an operational quality profile to a handoff score."""

    profile_name = _normalize_handoff_quality_profile(profile)
    profile_config = HANDOFF_QUALITY_PROFILES[profile_name]
    target = float(profile_config["target_score"] if target_score is None else target_score)
    if target < 0 or target > 100:
        raise ValueError("target_score must be between 0 and 100")

    score, source = _load_handoff_score_input(score_or_handoff)
    triage = triage_handoff_score(score)
    score_summary = score.get("summary", {})
    triage_summary = triage.get("summary", {})
    score_percent = float(score_summary.get("score_percent") or 0.0)
    high_priority_count = int(triage_summary.get("high_priority_count") or 0)
    action_count = int(triage_summary.get("action_count") or 0)
    score_status = str(score.get("status") or "unknown")
    triage_status = str(triage.get("status") or "unknown")
    fail_on_score_status = set(profile_config["fail_on_score_status"])
    fail_below = profile_config.get("fail_below")
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if score_status in fail_on_score_status:
        failures.append(
            {
                "code": "score_status",
                "message": f"Handoff score status is {score_status}.",
                "actual": score_status,
                "expected": sorted(fail_on_score_status),
            }
        )
    if fail_below is not None and score_percent < float(fail_below):
        failures.append(
            {
                "code": "score_below_threshold",
                "message": f"Handoff score {score_percent}% is below {fail_below}%.",
                "actual": score_percent,
                "expected": float(fail_below),
            }
        )
    if bool(profile_config.get("fail_on_high_priority")) and high_priority_count:
        failures.append(
            {
                "code": "high_priority_triage",
                "message": "Handoff triage contains priority-1 repair items.",
                "actual": high_priority_count,
                "expected": 0,
            }
        )

    if score_percent < target:
        warnings.append(
            {
                "code": "score_below_target",
                "message": f"Handoff score {score_percent}% is below target {target}%.",
                "actual": score_percent,
                "expected": target,
            }
        )
    if triage_status != "pass" or action_count:
        warnings.append(
            {
                "code": "triage_actions",
                "message": "Handoff triage produced repair actions.",
                "actual": action_count,
                "expected": 0,
            }
        )

    status = "fail" if failures else "warn" if warnings else "pass"
    return {
        "schema_version": "repomori.handoff_quality.v1",
        "status": status,
        "profile": profile_name,
        "created_at": int(time.time()),
        "source": source,
        "thresholds": {
            "target_score": target,
            "fail_below": fail_below,
            "fail_on_score_status": sorted(fail_on_score_status),
            "fail_on_high_priority": bool(profile_config.get("fail_on_high_priority")),
        },
        "summary": {
            "handoff_dir": score.get("handoff_dir"),
            "score_status": score_status,
            "score_percent": score_percent,
            "triage_status": triage_status,
            "triage_action_count": action_count,
            "triage_high_priority_count": high_priority_count,
            "target_met": score_percent >= target,
        },
        "warnings": warnings,
        "failures": failures,
        "score": score,
        "triage": triage,
    }


def format_handoff_quality_markdown(report: dict[str, Any]) -> str:
    """Render a handoff quality gate report as Markdown."""

    summary = report.get("summary", {})
    thresholds = report.get("thresholds", {})
    lines = [
        "# RepoMori Handoff Quality",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Profile: `{report.get('profile')}`",
        f"- Handoff: `{summary.get('handoff_dir')}`",
        f"- Score: `{summary.get('score_percent')}`% status=`{summary.get('score_status')}`",
        f"- Triage: `{summary.get('triage_status')}` actions=`{summary.get('triage_action_count')}` high=`{summary.get('triage_high_priority_count')}`",
        f"- Target score: `{thresholds.get('target_score')}`",
        "",
    ]
    failures = report.get("failures", [])
    warnings = report.get("warnings", [])
    if failures:
        lines.extend(["## Failures", ""])
        for item in failures:
            lines.append(f"- `{item.get('code')}` {item.get('message')}")
        lines.append("")
    if warnings:
        lines.extend(["## Warnings", ""])
        for item in warnings:
            lines.append(f"- `{item.get('code')}` {item.get('message')}")
        lines.append("")
    if not failures and not warnings:
        lines.extend(["No quality gate issues detected.", ""])
    return "\n".join(lines).rstrip() + "\n"


def improve_handoff_package(
    pack: Path | str,
    question: str,
    out_dir: Path | str,
    *,
    base_pack: Path | str | None = None,
    force: bool = False,
    copy_pack: bool = False,
    allow_unverified: bool = False,
    target_score: float = 90.0,
    quality_profile: str = "ci",
    max_attempts: int = 3,
    max_files: int = 8,
    max_bytes: int | None = 4096,
    snippet_lines: int = 12,
    snippets_per_file: int = 2,
    capsule_max_files: int | None = None,
    top_terms: int = 128,
    eval_questions: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build, score, triage, and locally retry a handoff with richer settings."""

    if not question.strip():
        raise ValueError("question must not be empty")
    if target_score < 0 or target_score > 100:
        raise ValueError("target_score must be between 0 and 100")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than zero")
    if max_files <= 0:
        raise ValueError("max_files must be greater than zero")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be zero or greater")
    if snippet_lines <= 0:
        raise ValueError("snippet_lines must be greater than zero")
    if snippets_per_file < 0:
        raise ValueError("snippets_per_file must be zero or greater")

    profile_name = _normalize_handoff_quality_profile(quality_profile)
    pack_path = Path(pack).resolve()
    base_pack_path = Path(base_pack).resolve() if base_pack is not None else None
    out_path = Path(out_dir).resolve()
    if out_path.exists() and not force:
        raise FileExistsError(f"Handoff output already exists: {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    eval_question_list = list(eval_questions or [])
    started = time.time()
    base_settings = {
        "max_files": max_files,
        "max_bytes": max_bytes,
        "snippet_lines": snippet_lines,
        "snippets_per_file": snippets_per_file,
        "capsule_max_files": capsule_max_files,
        "top_terms": top_terms,
    }
    planned_settings = _handoff_improvement_attempt_settings(base_settings, max_attempts)
    attempts: list[dict[str, Any]] = []
    best_attempt: dict[str, Any] | None = None
    work_root = Path(tempfile.mkdtemp(prefix=f".{out_path.name}-improve-", dir=str(out_path.parent)))
    try:
        for index, attempt_settings in enumerate(planned_settings, start=1):
            attempt_dir = work_root / f"attempt-{index}"
            manifest = build_handoff_package(
                pack_path,
                question,
                attempt_dir,
                base_pack=base_pack_path,
                force=True,
                copy_pack=copy_pack,
                allow_unverified=allow_unverified,
                eval_questions=eval_question_list,
                **attempt_settings,
            )
            score = score_handoff_package(attempt_dir)
            triage = triage_handoff_score(score)
            quality = evaluate_handoff_quality(score, profile=profile_name, target_score=target_score)
            score_summary = score.get("summary", {})
            triage_summary = triage.get("summary", {})
            record = {
                "attempt": index,
                "out_dir": str(attempt_dir),
                "settings": attempt_settings,
                "manifest_status": manifest.get("status"),
                "score_status": score.get("status"),
                "score_percent": score_summary.get("score_percent"),
                "triage_status": triage.get("status"),
                "triage_action_count": triage_summary.get("action_count"),
                "triage_high_priority_count": triage_summary.get("high_priority_count"),
                "quality_status": quality.get("status"),
                "target_met": bool(quality.get("summary", {}).get("target_met")),
                "score": score,
                "triage": triage,
                "quality": quality,
            }
            attempts.append(record)
            if best_attempt is None or _handoff_improvement_rank(record) > _handoff_improvement_rank(best_attempt):
                best_attempt = record
            if quality.get("status") == "pass":
                break

        if best_attempt is None:
            raise RuntimeError("No handoff improvement attempts were produced.")

        final_settings = dict(best_attempt["settings"])
        manifest = build_handoff_package(
            pack_path,
            question,
            out_path,
            base_pack=base_pack_path,
            force=True,
            copy_pack=copy_pack,
            allow_unverified=allow_unverified,
            eval_questions=eval_question_list,
            **final_settings,
        )
        final_score, score_json, score_md = _write_handoff_score_artifacts(out_path)
        final_triage, triage_json, triage_md = _write_handoff_triage_artifacts(out_path, final_score)
        final_quality = evaluate_handoff_quality(final_score, profile=profile_name, target_score=target_score)
        first_attempt = attempts[0]
        before_score_json = out_path / "handoff-score-before.json"
        before_triage_json = out_path / "handoff-triage-before.json"
        after_score_json = out_path / "handoff-score-after.json"
        after_triage_json = out_path / "handoff-triage-after.json"
        quality_json = out_path / "handoff-quality.json"
        quality_md = out_path / "handoff-quality.md"
        _write_json(before_score_json, first_attempt["score"])
        _write_json(before_triage_json, first_attempt["triage"])
        _write_json(after_score_json, final_score)
        _write_json(after_triage_json, final_triage)
        _write_json(quality_json, final_quality)
        quality_md.write_text(format_handoff_quality_markdown(final_quality), encoding="utf-8")
        initial_score = float(first_attempt.get("score_percent") or 0.0)
        final_score_percent = float(final_score.get("summary", {}).get("score_percent") or 0.0)
        artifacts = {
            "handoff": str(out_path),
            "manifest": str(out_path / "manifest.json"),
            "score_json": str(score_json),
            "score_markdown": str(score_md),
            "score_before_json": str(before_score_json),
            "score_after_json": str(after_score_json),
            "triage_before_json": str(before_triage_json),
            "triage_after_json": str(after_triage_json),
            "quality_json": str(quality_json),
            "quality_markdown": str(quality_md),
        }
        if triage_json is not None and triage_md is not None:
            artifacts["triage_json"] = str(triage_json)
            artifacts["triage_markdown"] = str(triage_md)
        report = {
            "schema_version": "repomori.handoff_improvement.v1",
            "status": final_quality.get("status"),
            "pack": str(pack_path),
            "base_pack": str(base_pack_path) if base_pack_path is not None else None,
            "question": question,
            "out_dir": str(out_path),
            "created_at": int(started),
            "settings": {
                "target_score": target_score,
                "quality_profile": profile_name,
                "max_attempts": max_attempts,
                "copy_pack": copy_pack,
                "allow_unverified": allow_unverified,
                "eval_questions": eval_question_list,
                "initial": base_settings,
                "selected": final_settings,
            },
            "summary": {
                "elapsed_seconds": round(time.time() - started, 4),
                "attempt_count": len(attempts),
                "selected_attempt": best_attempt.get("attempt"),
                "initial_score_percent": initial_score,
                "final_score_percent": final_score_percent,
                "score_delta": round(final_score_percent - initial_score, 2),
                "final_score_status": final_score.get("status"),
                "final_triage_status": final_triage.get("status"),
                "final_triage_action_count": final_triage.get("summary", {}).get("action_count"),
                "final_quality_status": final_quality.get("status"),
                "target_met": final_score_percent >= target_score,
            },
            "attempts": [
                {
                    key: value
                    for key, value in attempt.items()
                    if key not in {"score", "triage", "quality"}
                }
                for attempt in attempts
            ],
            "artifacts": artifacts,
            "handoff": manifest,
            "score_before": first_attempt["score"],
            "triage_before": first_attempt["triage"],
            "score_after": final_score,
            "triage_after": final_triage,
            "quality": final_quality,
        }
        improvement_json = out_path / "handoff-improvement.json"
        improvement_md = out_path / "handoff-improvement.md"
        report["artifacts"]["improvement_json"] = str(improvement_json)
        report["artifacts"]["improvement_markdown"] = str(improvement_md)
        _write_json(improvement_json, report)
        improvement_md.write_text(format_handoff_improvement_markdown(report), encoding="utf-8")
        return report
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def format_handoff_improvement_markdown(report: dict[str, Any]) -> str:
    """Render a handoff improvement run as Markdown."""

    summary = report.get("summary", {})
    settings = report.get("settings", {})
    lines = [
        "# RepoMori Handoff Improvement",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Handoff: `{report.get('out_dir')}`",
        f"- Question: {report.get('question')}",
        f"- Quality profile: `{settings.get('quality_profile')}`",
        f"- Target score: `{settings.get('target_score')}`",
        f"- Attempts: `{summary.get('attempt_count')}` selected=`{summary.get('selected_attempt')}`",
        f"- Initial score: `{summary.get('initial_score_percent')}`%",
        f"- Final score: `{summary.get('final_score_percent')}`% delta=`{summary.get('score_delta')}`",
        f"- Final triage: `{summary.get('final_triage_status')}` actions=`{summary.get('final_triage_action_count')}`",
        "",
        "## Attempts",
        "",
    ]
    for attempt in report.get("attempts", []):
        lines.append(
            f"- Attempt `{attempt.get('attempt')}` score=`{attempt.get('score_percent')}`% "
            f"quality=`{attempt.get('quality_status')}` triage=`{attempt.get('triage_status')}` "
            f"actions=`{attempt.get('triage_action_count')}`"
        )
    lines.extend(["", "## Artifacts", ""])
    for label, path in report.get("artifacts", {}).items():
        lines.append(f"- {label}: `{path}`")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def archive_handoff_package(
    handoff_dir: Path | str,
    out: Path | str | None = None,
    *,
    force: bool = False,
    quality_profile: str = "safe",
) -> dict[str, Any]:
    """Write a portable zip archive for a handoff directory."""

    root = Path(handoff_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Handoff directory not found: {root}")
    archive_path = Path(out).resolve() if out is not None else root.with_suffix(".zip")
    if archive_path.suffix.lower() != ".zip":
        archive_path = archive_path.with_suffix(".zip")
    if archive_path.exists() and not force:
        raise FileExistsError(f"Handoff archive already exists: {archive_path}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    check = check_handoff_package(root)
    score, score_json, score_md = _write_handoff_score_artifacts(root)
    triage, triage_json, triage_md = _write_handoff_triage_artifacts(root, score)
    quality = evaluate_handoff_quality(score, profile=quality_profile)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.resolve() == archive_path.resolve():
                continue
            archive.write(path, path.relative_to(root).as_posix())

    status = "fail" if not check.get("valid") or score.get("status") == "fail" else "warn" if quality.get("status") != "pass" else "pass"
    artifacts = {
        "archive": str(archive_path),
        "score_json": str(score_json),
        "score_markdown": str(score_md),
    }
    if triage_json is not None and triage_md is not None:
        artifacts["triage_json"] = str(triage_json)
        artifacts["triage_markdown"] = str(triage_md)
    return {
        "schema_version": "repomori.handoff_archive.v1",
        "status": status,
        "handoff_dir": str(root),
        "archive": {
            "path": str(archive_path),
            "size": archive_path.stat().st_size,
            "sha256": _path_sha256(archive_path),
        },
        "summary": {
            "valid": check.get("valid"),
            "score_status": score.get("status"),
            "score_percent": score.get("summary", {}).get("score_percent"),
            "triage_status": triage.get("status"),
            "triage_action_count": triage.get("summary", {}).get("action_count"),
            "quality_status": quality.get("status"),
            "file_count": len(files),
        },
        "artifacts": artifacts,
        "check": check,
        "score": score,
        "triage": triage,
        "quality": quality,
    }


def format_handoff_archive_markdown(report: dict[str, Any]) -> str:
    """Render a handoff archive report as Markdown."""

    archive = report.get("archive", {})
    summary = report.get("summary", {})
    lines = [
        "# RepoMori Handoff Archive",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Handoff: `{report.get('handoff_dir')}`",
        f"- Archive: `{archive.get('path')}`",
        f"- Archive bytes: `{archive.get('size')}`",
        f"- Archive SHA-256: `{archive.get('sha256')}`",
        f"- Score: `{summary.get('score_status')}` `{summary.get('score_percent')}`%",
        f"- Triage: `{summary.get('triage_status')}` actions=`{summary.get('triage_action_count')}`",
        f"- Quality: `{summary.get('quality_status')}`",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_handoff_health_report(
    handoff_dir: Path | str,
    *,
    profile: str = "safe",
    target_score: float | None = None,
    improve_pack: Path | str | None = None,
    question: str | None = None,
    improve_out: Path | str | None = None,
    base_pack: Path | str | None = None,
    force: bool = False,
    copy_pack: bool = False,
    allow_unverified: bool = False,
    archive: bool = False,
    archive_out: Path | str | None = None,
    max_attempts: int = 3,
    max_files: int = 8,
    max_bytes: int | None = 4096,
    snippet_lines: int = 12,
    snippets_per_file: int = 2,
    capsule_max_files: int | None = None,
    top_terms: int = 128,
    eval_questions: Iterable[str] | None = None,
    health_log: Path | str | None = None,
    run_meta: dict[str, Any] | None = None,
    artifacts_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run deterministic handoff health checks with optional local repair/archive."""

    started = time.time()
    root = Path(handoff_dir).resolve()
    profile_name = _normalize_handoff_quality_profile(profile)
    eval_question_list = list(eval_questions or [])
    check = check_handoff_package(root)
    score = score_handoff_package(root)
    triage = triage_handoff_score(score)
    quality = evaluate_handoff_quality(score, profile=profile_name, target_score=target_score)
    quality_summary = quality.get("summary", {})
    quality_thresholds = quality.get("thresholds", {})
    target = float(quality_thresholds.get("target_score") or 0.0)
    artifacts: dict[str, Any] = {}
    improvement = None
    archive_report = None
    improvement_skipped_reason = None
    active_handoff_dir = root
    active_quality = quality

    if improve_pack is not None:
        if quality.get("status") == "pass":
            improvement_skipped_reason = "quality_passed"
        else:
            improve_question = question or _handoff_health_manifest_question(root)
            if not improve_question:
                raise ValueError("question is required when improve_pack is supplied and the handoff needs improvement")
            improve_path = Path(improve_out).resolve() if improve_out is not None else root.with_name(f"{root.name}-improved")
            improvement = improve_handoff_package(
                improve_pack,
                improve_question,
                improve_path,
                base_pack=base_pack,
                force=force,
                copy_pack=copy_pack,
                allow_unverified=allow_unverified,
                target_score=target,
                quality_profile=profile_name,
                max_attempts=max_attempts,
                max_files=max_files,
                max_bytes=max_bytes,
                snippet_lines=snippet_lines,
                snippets_per_file=snippets_per_file,
                capsule_max_files=capsule_max_files,
                top_terms=top_terms,
                eval_questions=eval_question_list,
            )
            active_handoff_dir = Path(str(improvement.get("out_dir"))).resolve()
            active_quality = improvement.get("quality", quality)
            artifacts["improvement_dir"] = _directory_artifact_record(active_handoff_dir, "handoff_improvement_dir")
            for label, path in (improvement.get("artifacts") or {}).items():
                _add_handoff_health_artifact(artifacts, f"improvement_{label}", path)

    if archive:
        archive_report = archive_handoff_package(
            active_handoff_dir,
            archive_out,
            force=force,
            quality_profile=profile_name,
        )
        archive_info = archive_report.get("archive", {})
        if archive_info.get("path"):
            _add_handoff_health_artifact(artifacts, "archive", archive_info.get("path"))

    base_status = str(active_quality.get("status") or quality.get("status") or "fail")
    status = _worst_status(base_status, archive_report.get("status") if isinstance(archive_report, dict) else None)
    score_summary = score.get("summary", {})
    triage_summary = triage.get("summary", {})
    active_summary = active_quality.get("summary", {}) if isinstance(active_quality, dict) else {}
    archive_info = archive_report.get("archive", {}) if isinstance(archive_report, dict) else {}
    summary = {
        "valid": bool(check.get("valid")),
        "score_status": score.get("status"),
        "score_percent": score_summary.get("score_percent"),
        "triage_status": triage.get("status"),
        "triage_action_count": triage_summary.get("action_count"),
        "triage_high_priority_count": triage_summary.get("high_priority_count"),
        "quality_status": quality.get("status"),
        "quality_target_met": quality_summary.get("target_met"),
        "final_quality_status": active_quality.get("status") if isinstance(active_quality, dict) else None,
        "final_score_percent": active_summary.get("score_percent"),
        "final_target_met": active_summary.get("target_met"),
        "active_handoff_dir": str(active_handoff_dir),
        "improved": improvement is not None,
        "improvement_status": improvement.get("status") if isinstance(improvement, dict) else None,
        "improvement_path": improvement.get("out_dir") if isinstance(improvement, dict) else None,
        "improvement_skipped_reason": improvement_skipped_reason,
        "archived": archive_report is not None,
        "archive_path": archive_info.get("path"),
        "archive_sha256": archive_info.get("sha256"),
        "elapsed_seconds": round(time.time() - started, 4),
    }
    report = {
        "schema_version": "repomori.handoff_health.v1",
        "status": status,
        "handoff_dir": str(root),
        "active_handoff_dir": str(active_handoff_dir),
        "profile": profile_name,
        "target_score": target,
        "created_at": int(started),
        "settings": {
            "target_score": target,
            "improve_pack": str(Path(improve_pack).resolve()) if improve_pack is not None else None,
            "question": question,
            "improve_out": str(Path(improve_out).resolve()) if improve_out is not None else None,
            "base_pack": str(Path(base_pack).resolve()) if base_pack is not None else None,
            "force": force,
            "copy_pack": copy_pack,
            "allow_unverified": allow_unverified,
            "archive": archive,
            "archive_out": str(Path(archive_out).resolve()) if archive_out is not None else None,
            "max_attempts": max_attempts,
            "max_files": max_files,
            "max_bytes": max_bytes,
            "snippet_lines": snippet_lines,
            "snippets_per_file": snippets_per_file,
            "capsule_max_files": capsule_max_files,
            "top_terms": top_terms,
            "eval_questions": eval_question_list,
        },
        "summary": summary,
        "artifacts": artifacts,
        "check": check,
        "score": score,
        "triage": triage,
        "quality": quality,
        "improvement": improvement,
        "archive": archive_report,
    }
    if health_log is not None:
        report["health_log"] = append_handoff_health_log(report, health_log, run_meta=run_meta)
    if artifacts_dir is not None:
        _write_handoff_health_artifacts(report, Path(artifacts_dir).resolve())
    return report


def format_handoff_health_markdown(report: dict[str, Any]) -> str:
    """Render an operational handoff health report as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Handoff Health",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Handoff: `{report.get('handoff_dir')}`",
        f"- Active handoff: `{report.get('active_handoff_dir')}`",
        f"- Profile: `{report.get('profile')}`",
        f"- Target score: `{report.get('target_score')}`",
        f"- Valid: `{summary.get('valid')}`",
        f"- Score: `{summary.get('score_status')}` `{summary.get('score_percent')}`%",
        f"- Triage: `{summary.get('triage_status')}` actions=`{summary.get('triage_action_count')}` high=`{summary.get('triage_high_priority_count')}`",
        f"- Quality: `{summary.get('quality_status')}` target_met=`{summary.get('quality_target_met')}`",
        f"- Final quality: `{summary.get('final_quality_status')}` target_met=`{summary.get('final_target_met')}`",
        f"- Improved: `{summary.get('improved')}`",
        f"- Archived: `{summary.get('archived')}`",
        "",
    ]
    if summary.get("improvement_skipped_reason"):
        lines.extend(["## Improvement", "", f"- Skipped: `{summary.get('improvement_skipped_reason')}`", ""])
    elif summary.get("improved"):
        lines.extend(
            [
                "## Improvement",
                "",
                f"- Status: `{summary.get('improvement_status')}`",
                f"- Path: `{summary.get('improvement_path')}`",
                "",
            ]
        )
    if summary.get("archived"):
        lines.extend(
            [
                "## Archive",
                "",
                f"- Path: `{summary.get('archive_path')}`",
                f"- SHA-256: `{summary.get('archive_sha256')}`",
                "",
            ]
        )
    failures = report.get("quality", {}).get("failures", [])
    warnings = report.get("quality", {}).get("warnings", [])
    if failures:
        lines.extend(["## Failures", ""])
        for item in failures:
            lines.append(f"- `{item.get('code')}` {item.get('message')}")
        lines.append("")
    if warnings:
        lines.extend(["## Warnings", ""])
        for item in warnings:
            lines.append(f"- `{item.get('code')}` {item.get('message')}")
        lines.append("")
    actions = report.get("triage", {}).get("actions", [])
    if actions:
        lines.extend(["## Triage Actions", ""])
        for action in actions[:8]:
            lines.append(f"- [P{action.get('priority')}] `{action.get('id')}` {action.get('title')}")
        lines.append("")
    artifacts = report.get("artifacts", {})
    if artifacts:
        lines.extend(["## Artifacts", ""])
        for label, artifact in artifacts.items():
            if isinstance(artifact, dict):
                suffix = f" sha=`{artifact.get('sha256')}`" if artifact.get("sha256") else ""
                lines.append(f"- {label}: `{artifact.get('path')}` size=`{artifact.get('size')}`{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_handoff_health_record(
    health_report: dict[str, Any],
    *,
    run_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one compact JSONL-ready handoff health trend row."""

    if not isinstance(health_report, dict):
        raise TypeError("health_report must be a JSON report object.")
    if health_report.get("schema_version") != "repomori.handoff_health.v1":
        raise ValueError("health_report must use schema repomori.handoff_health.v1")

    summary = health_report.get("summary", {}) if isinstance(health_report.get("summary"), dict) else {}
    improvement = health_report.get("improvement") if isinstance(health_report.get("improvement"), dict) else None
    improvement_summary = improvement.get("summary", {}) if improvement else {}
    run_meta_dict = dict(run_meta or {})
    return {
        "schema_version": "repomori.handoff_health_record.v1",
        "run_ts": run_meta_dict.get("run_ts", int(time.time())),
        "run_id": run_meta_dict.get("run_id"),
        "status": health_report.get("status"),
        "handoff_dir": health_report.get("handoff_dir"),
        "active_handoff_dir": health_report.get("active_handoff_dir"),
        "profile": health_report.get("profile"),
        "target_score": health_report.get("target_score"),
        "valid": bool(summary.get("valid")),
        "score_status": summary.get("score_status"),
        "score_percent": _optional_float(summary.get("score_percent")),
        "triage_status": summary.get("triage_status"),
        "triage_action_count": _optional_int_value(summary.get("triage_action_count"), 0),
        "triage_high_priority_count": _optional_int_value(summary.get("triage_high_priority_count"), 0),
        "quality_status": summary.get("quality_status"),
        "quality_target_met": summary.get("quality_target_met"),
        "final_quality_status": summary.get("final_quality_status"),
        "final_score_percent": _optional_float(summary.get("final_score_percent")),
        "final_target_met": summary.get("final_target_met"),
        "improved": bool(summary.get("improved")),
        "improvement_status": summary.get("improvement_status"),
        "improvement_score_delta": _optional_float(improvement_summary.get("score_delta")),
        "archived": bool(summary.get("archived")),
        "archive_path": summary.get("archive_path"),
        "archive_sha256": summary.get("archive_sha256"),
    }


def append_handoff_health_log(
    health_report: dict[str, Any],
    log_path: Path | str,
    *,
    run_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one handoff-health trend row to a JSONL log and return metadata."""

    row = build_handoff_health_record(health_report, run_meta=run_meta)
    log_file = Path(log_path).resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "status": "appended",
        "log_path": str(log_file),
        "entry": row,
    }


def summarize_handoff_health_log(
    log_path: Path | str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Summarize recent handoff-health JSONL trend rows."""

    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    log_file = Path(log_path).resolve()
    if not log_file.exists():
        raise FileNotFoundError(f"Handoff health log not found: {log_file}")

    rows: list[dict[str, Any]] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("schema_version") != "repomori.handoff_health_record.v1":
            continue
        rows.append(parsed)

    selected = rows[-limit:] if rows else []
    count = len(selected)
    if not selected:
        return {
            "schema_version": "repomori.handoff_health_summary.v1",
            "status": "pass",
            "log_path": str(log_file),
            "limit": limit,
            "count": 0,
            "pass_count": 0,
            "warn_count": 0,
            "fail_count": 0,
            "max_score_percent": 0.0,
            "avg_score_percent": 0.0,
            "max_triage_action_count": 0,
            "improvement_count": 0,
            "archive_count": 0,
            "latest": None,
            "trend": {
                "score_percent_delta": 0.0,
                "triage_action_delta": 0,
                "triage_high_priority_delta": 0,
            },
            "rows": [],
        }

    pass_count = sum(1 for row in selected if row.get("status") == "pass")
    warn_count = sum(1 for row in selected if row.get("status") == "warn")
    fail_count = sum(1 for row in selected if row.get("status") == "fail")
    scores = [_optional_float(row.get("final_score_percent")) for row in selected]
    scores = [score for score in scores if score is not None]
    max_score = round(max(scores), 2) if scores else 0.0
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    first = selected[0]
    last = selected[-1]
    score_delta = round(
        (_optional_float(last.get("final_score_percent")) or 0.0)
        - (_optional_float(first.get("final_score_percent")) or 0.0),
        2,
    )
    triage_delta = _optional_int_value(last.get("triage_action_count"), 0) - _optional_int_value(first.get("triage_action_count"), 0)
    high_delta = _optional_int_value(last.get("triage_high_priority_count"), 0) - _optional_int_value(first.get("triage_high_priority_count"), 0)
    status = "fail" if fail_count else "warn" if warn_count else "pass"
    return {
        "schema_version": "repomori.handoff_health_summary.v1",
        "status": status,
        "log_path": str(log_file),
        "limit": limit,
        "count": count,
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "max_score_percent": max_score,
        "avg_score_percent": avg_score,
        "max_triage_action_count": max(_optional_int_value(row.get("triage_action_count"), 0) for row in selected),
        "improvement_count": sum(1 for row in selected if row.get("improved")),
        "archive_count": sum(1 for row in selected if row.get("archived")),
        "latest": {
            "run_ts": last.get("run_ts"),
            "status": last.get("status"),
            "handoff_dir": last.get("handoff_dir"),
            "active_handoff_dir": last.get("active_handoff_dir"),
            "final_score_percent": last.get("final_score_percent"),
            "final_quality_status": last.get("final_quality_status"),
            "archive_sha256": last.get("archive_sha256"),
        },
        "trend": {
            "score_percent_delta": score_delta,
            "triage_action_delta": triage_delta,
            "triage_high_priority_delta": high_delta,
        },
        "rows": selected,
    }


def format_handoff_health_summary_markdown(report: dict[str, Any]) -> str:
    """Render a handoff-health trend summary as Markdown."""

    latest = report.get("latest") or {}
    trend = report.get("trend", {})
    lines = [
        "# RepoMori Handoff Health Summary",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Log: `{report.get('log_path')}`",
        f"- Rows: `{report.get('count')}`",
        f"- Pass/warn/fail: `{report.get('pass_count')}` / `{report.get('warn_count')}` / `{report.get('fail_count')}`",
        f"- Max score: `{report.get('max_score_percent')}`%",
        f"- Average score: `{report.get('avg_score_percent')}`%",
        f"- Max triage actions: `{report.get('max_triage_action_count')}`",
        f"- Improvement runs: `{report.get('improvement_count')}`",
        f"- Archives: `{report.get('archive_count')}`",
        f"- Score delta: `{trend.get('score_percent_delta')}`",
        f"- Triage action delta: `{trend.get('triage_action_delta')}`",
        "",
    ]
    if latest:
        lines.extend(
            [
                "## Latest",
                "",
                f"- Status: `{latest.get('status')}`",
                f"- Active handoff: `{latest.get('active_handoff_dir')}`",
                f"- Final score: `{latest.get('final_score_percent')}`%",
                f"- Final quality: `{latest.get('final_quality_status')}`",
                f"- Archive SHA-256: `{latest.get('archive_sha256')}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


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
    handoff_score, handoff_score_json, handoff_score_md = _write_handoff_score_artifacts(handoff_path)
    handoff_triage, handoff_triage_json, handoff_triage_md = _write_handoff_triage_artifacts(
        handoff_path,
        handoff_score,
    )
    elapsed = time.time() - started
    status = "pass" if verify["verified"] and handoff_check["valid"] else "fail"
    handoff_score_summary = handoff_score.get("summary", {})
    handoff_triage_summary = handoff_triage.get("summary", {})

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
            "handoff_score_status": handoff_score.get("status"),
            "handoff_score_percent": handoff_score_summary.get("score_percent"),
            "handoff_score_json": str(handoff_score_json),
            "handoff_score_markdown": str(handoff_score_md),
            "handoff_triage_status": handoff_triage.get("status"),
            "handoff_triage_action_count": handoff_triage_summary.get("action_count"),
            "handoff_triage_high_priority_count": handoff_triage_summary.get("high_priority_count"),
            "handoff_triage_json": str(handoff_triage_json) if handoff_triage_json is not None else None,
            "handoff_triage_markdown": str(handoff_triage_md) if handoff_triage_md is not None else None,
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
            "handoff_score_json": str(handoff_score_json),
            "handoff_score_markdown": str(handoff_score_md),
            "bench_json": "bench.json",
            "bench_markdown": "bench.md",
        },
        "build": build,
        "verify": verify,
        "brief": brief,
        "eval": eval_report,
        "handoff": handoff,
        "handoff_check": handoff_check,
        "handoff_score": handoff_score,
        "handoff_triage": handoff_triage,
    }
    if handoff_triage_json is not None and handoff_triage_md is not None:
        report["artifacts"]["handoff_triage_json"] = str(handoff_triage_json)
        report["artifacts"]["handoff_triage_markdown"] = str(handoff_triage_md)
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
    inspect_json_path = out_path / "inspect.json"
    inspect_md_path = out_path / "inspect.md"
    context_path = out_path / "context.md"
    config_path = out_path / "repomori.toml"
    packs_path = out_path / "packs"
    readme_path = out_path / "README.md"
    demo_json_path = out_path / "demo.json"

    _write_demo_repo(repo_path)
    build = build_pack(repo_path, pack_path, BuildOptions(chunk_size=chunk_size, force=True))
    verify = verify_pack(pack_path)
    inspect = inspect_pack(pack_path, max_files=8, top_terms=20, top_symbols=20, verify=True)
    _write_json(inspect_json_path, inspect)
    inspect_md_path.write_text(format_pack_inspect_markdown(inspect), encoding="utf-8")
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
    status = (
        "pass"
        if verify.get("verified")
        and inspect.get("status") == "pass"
        and query
        and context.get("sources")
        and memory.get("status") != "fail"
        and mcp_ok
        else "fail"
    )
    elapsed = time.time() - started
    summary = {
        "elapsed_seconds": round(elapsed, 4),
        "pack_path": str(pack_path),
        "config_path": str(config_path),
        "memory_out_dir": str(packs_path),
        "pack_bytes": build.get("pack_bytes"),
        "logical_bytes": build.get("logical_bytes"),
        "file_count": build.get("file_count"),
        "inspect_status": inspect.get("status"),
        "inspect_schema": inspect.get("schema_version"),
        "query_top_path": query[0].get("path") if query else None,
        "context_source_count": len(context.get("sources", [])),
        "memory_status": memory.get("status"),
        "mcp_tool_count": len(mcp_tool_names),
        "mcp_context_schema": mcp_context_result.get("structuredContent", {}).get("schema_version") if isinstance(mcp_context_result, dict) else None,
    }
    artifacts = {
        "demo_repo": repo_path.name,
        "pack": pack_path.name,
        "inspect_json": inspect_json_path.name,
        "inspect_markdown": inspect_md_path.name,
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
        "inspect": inspect,
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
    inspect_diff = None
    inspect_diff_json = None
    inspect_diff_md = None
    if compare and previous_pack is not None:
        comparison = compare_packs(previous_pack, pack_path, limit=compare_limit)
        compare_json = out_path / f"{pack_path.stem}.compare.json"
        compare_md = out_path / f"{pack_path.stem}.compare.md"
        _write_json(compare_json, comparison)
        compare_md.write_text(format_compare_markdown(comparison), encoding="utf-8")
        inspect_diff = inspect_pack_diff(previous_pack, pack_path, max_files=compare_limit)
        inspect_diff_json = out_path / f"{pack_path.stem}.inspect-diff.json"
        inspect_diff_md = out_path / f"{pack_path.stem}.inspect-diff.md"
        _write_json(inspect_diff_json, inspect_diff)
        inspect_diff_md.write_text(format_pack_inspect_diff_markdown(inspect_diff), encoding="utf-8")

    handoff = None
    handoff_check = None
    handoff_score = None
    handoff_score_json = None
    handoff_score_md = None
    handoff_triage = None
    handoff_triage_json = None
    handoff_triage_md = None
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
        handoff_score, handoff_score_json, handoff_score_md = _write_handoff_score_artifacts(handoff_path)
        handoff_triage, handoff_triage_json, handoff_triage_md = _write_handoff_triage_artifacts(
            handoff_path,
            handoff_score,
        )

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
    handoff_score_summary = handoff_score.get("summary", {}) if handoff_score else {}
    handoff_triage_summary = handoff_triage.get("summary", {}) if handoff_triage else {}
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
            "inspect_diff_status": inspect_diff.get("status") if inspect_diff else None,
            "inspect_diff_json": str(inspect_diff_json) if inspect_diff_json is not None else None,
            "inspect_diff_markdown": str(inspect_diff_md) if inspect_diff_md is not None else None,
            "handoff_dir": str(handoff_path) if handoff_path is not None else None,
            "handoff_passed": handoff_check.get("valid") if handoff_check else None,
            "handoff_score_status": handoff_score.get("status") if handoff_score else None,
            "handoff_score_percent": handoff_score_summary.get("score_percent"),
            "handoff_score_json": str(handoff_score_json) if handoff_score_json is not None else None,
            "handoff_score_markdown": str(handoff_score_md) if handoff_score_md is not None else None,
            "handoff_score_failed_checks": handoff_score_summary.get("failed_checks"),
            "handoff_score_warned_checks": handoff_score_summary.get("warned_checks"),
            "handoff_triage_status": handoff_triage.get("status") if handoff_triage else None,
            "handoff_triage_action_count": handoff_triage_summary.get("action_count"),
            "handoff_triage_high_priority_count": handoff_triage_summary.get("high_priority_count"),
            "handoff_triage_json": str(handoff_triage_json) if handoff_triage_json is not None else None,
            "handoff_triage_markdown": str(handoff_triage_md) if handoff_triage_md is not None else None,
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
        "inspect_diff": inspect_diff,
        "handoff": handoff,
        "handoff_check": handoff_check,
        "handoff_score": handoff_score,
        "handoff_triage": handoff_triage,
        "diff_context": diff_context_bundle,
    }
    if compare_json is not None and compare_md is not None:
        report["artifacts"]["compare_json"] = compare_json.name
        report["artifacts"]["compare_markdown"] = compare_md.name
    if inspect_diff_json is not None and inspect_diff_md is not None:
        report["artifacts"]["inspect_diff_json"] = inspect_diff_json.name
        report["artifacts"]["inspect_diff_markdown"] = inspect_diff_md.name
    if handoff_path is not None:
        report["artifacts"]["handoff"] = handoff_path.name
    if handoff_score_json is not None and handoff_score_md is not None:
        report["artifacts"]["handoff_score_json"] = str(handoff_score_json)
        report["artifacts"]["handoff_score_markdown"] = str(handoff_score_md)
    if handoff_triage_json is not None and handoff_triage_md is not None:
        report["artifacts"]["handoff_triage_json"] = str(handoff_triage_json)
        report["artifacts"]["handoff_triage_markdown"] = str(handoff_triage_md)
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
        f"- Handoff score: `{summary.get('handoff_score_status')}` `{summary.get('handoff_score_percent')}`%",
        f"- Handoff triage: `{summary.get('handoff_triage_status')}` actions=`{summary.get('handoff_triage_action_count')}` high=`{summary.get('handoff_triage_high_priority_count')}`",
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

    lines.extend(["## Inspect Diff", ""])
    inspect_diff = report.get("inspect_diff")
    if inspect_diff:
        inspect_summary = inspect_diff.get("summary", {})
        lines.extend(
            [
                f"- Status: `{inspect_diff.get('status')}`",
                f"- Added: `{inspect_summary.get('added_count')}`",
                f"- Removed: `{inspect_summary.get('removed_count')}`",
                f"- Changed: `{inspect_summary.get('changed_count')}`",
                f"- JSON: `{summary.get('inspect_diff_json')}`",
                f"- Markdown: `{summary.get('inspect_diff_markdown')}`",
                "",
            ]
        )
    elif report.get("settings", {}).get("compare"):
        lines.extend(["No previous `latest.repomori` snapshot was available to inspect-diff.", ""])
    else:
        lines.extend(["Inspect diff disabled because comparison is disabled.", ""])

    handoff = report.get("handoff")
    lines.extend(["## Handoff", ""])
    if handoff:
        summary = report.get("summary", {})
        lines.extend(
            [
                f"- Directory: `{summary.get('handoff_dir')}`",
                f"- Check passed: `{summary.get('handoff_passed')}`",
                f"- Score: `{summary.get('handoff_score_status')}` `{summary.get('handoff_score_percent')}`%",
                f"- Score JSON: `{summary.get('handoff_score_json')}`",
                f"- Score Markdown: `{summary.get('handoff_score_markdown')}`",
                f"- Triage: `{summary.get('handoff_triage_status')}` actions=`{summary.get('handoff_triage_action_count')}` high=`{summary.get('handoff_triage_high_priority_count')}`",
                f"- Triage JSON: `{summary.get('handoff_triage_json')}`",
                f"- Triage Markdown: `{summary.get('handoff_triage_markdown')}`",
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
    chain = verify_snapshot_chain(out_path)
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
            "handoff_score_pass_count": sum(1 for item in snapshots if item.get("handoff_score_status") == "pass"),
            "handoff_score_warn_count": sum(1 for item in snapshots if item.get("handoff_score_status") == "warn"),
            "handoff_score_fail_count": sum(1 for item in snapshots if item.get("handoff_score_status") == "fail"),
            "handoff_triage_pass_count": sum(1 for item in snapshots if item.get("handoff_triage_status") == "pass"),
            "handoff_triage_warn_count": sum(1 for item in snapshots if item.get("handoff_triage_status") == "warn"),
            "handoff_triage_fail_count": sum(1 for item in snapshots if item.get("handoff_triage_status") == "fail"),
            "incremental_snapshot_count": sum(1 for item in snapshots if item.get("incremental")),
            "total_reused_files": _sum_snapshot_field(snapshots, "reused_file_count"),
            "total_rebuilt_files": _sum_snapshot_field(snapshots, "rebuilt_file_count"),
            "total_reused_chunks": _sum_snapshot_field(snapshots, "reused_chunk_count"),
            "chain_status": chain.get("status"),
            "chain_head_hash": chain.get("summary", {}).get("head_chain_hash"),
            "chain_checked_count": chain.get("summary", {}).get("checked_count"),
            "chain_anchored_to_pruned_history": chain.get("summary", {}).get("anchored_to_pruned_history"),
        },
        "chain": chain,
        "snapshots": recent,
    }


def search_snapshot_timeline(
    out_dir: Path | str,
    text: str,
    *,
    limit: int = 10,
    per_snapshot_limit: int = 3,
) -> dict[str, Any]:
    """Query every indexed snapshot pack and summarize first/last appearances."""

    if not text.strip():
        raise ValueError("text must not be empty")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if per_snapshot_limit <= 0:
        raise ValueError("per_snapshot_limit must be greater than zero")

    out_path = Path(out_dir).resolve()
    index = _read_snapshot_index(out_path / "snapshots.json", out_path)
    chain = verify_snapshot_chain(out_path)
    snapshots = sorted(
        list(index.get("snapshots", [])),
        key=lambda item: _snapshot_entry_sort_key(out_path, item),
    )
    matches: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    file_history: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        pack_path = _recorded_snapshot_path(out_path, snapshot.get("pack_path"))
        if pack_path is None or not pack_path.exists() or not pack_path.is_file():
            warnings.append(
                {
                    "scope": "pack",
                    "pack_path": str(pack_path) if pack_path is not None else str(snapshot.get("pack_path")),
                    "message": "Snapshot pack is missing; search skipped this entry.",
                }
            )
            continue
        try:
            results = query_pack(pack_path, text, limit=per_snapshot_limit)
        except (sqlite3.DatabaseError, ValueError, zlib.error) as exc:
            warnings.append(
                {
                    "scope": "pack",
                    "pack_path": str(pack_path),
                    "message": f"Snapshot pack could not be queried: {exc}",
                }
            )
            continue
        if not results:
            continue
        record = {
            "created_at": snapshot.get("created_at"),
            "pack_name": snapshot.get("pack_name"),
            "pack_path": str(pack_path),
            "pack_sha256": snapshot.get("pack_sha256"),
            "chain_index": snapshot.get("chain_index"),
            "chain_hash": snapshot.get("chain_hash"),
            "result_count": len(results),
            "top_score": results[0].get("score"),
            "top_path": results[0].get("path"),
            "results": results,
        }
        matches.append(record)
        for result in results:
            path = str(result.get("path"))
            entry = file_history.setdefault(
                path,
                {
                    "path": path,
                    "first_seen_at": snapshot.get("created_at"),
                    "first_pack": str(pack_path),
                    "last_seen_at": snapshot.get("created_at"),
                    "last_pack": str(pack_path),
                    "seen_count": 0,
                    "best_score": 0.0,
                    "latest_score": result.get("score"),
                    "latest_why": result.get("why", []),
                },
            )
            entry["last_seen_at"] = snapshot.get("created_at")
            entry["last_pack"] = str(pack_path)
            entry["seen_count"] = int(entry.get("seen_count") or 0) + 1
            entry["latest_score"] = result.get("score")
            entry["latest_why"] = result.get("why", [])
            entry["best_score"] = max(float(entry.get("best_score") or 0.0), float(result.get("score") or 0.0))

    returned = list(reversed(matches))[:limit]
    status = "warn" if warnings or chain.get("status") == "warn" else "pass"
    if chain.get("status") == "fail":
        status = "fail"
    history = sorted(
        file_history.values(),
        key=lambda item: (-float(item.get("best_score") or 0.0), str(item.get("path"))),
    )
    return {
        "schema_version": "repomori.timeline_search.v1",
        "status": status,
        "out_dir": str(out_path),
        "query": text,
        "settings": {"limit": limit, "per_snapshot_limit": per_snapshot_limit},
        "summary": {
            "snapshot_count": len(snapshots),
            "matched_snapshot_count": len(matches),
            "returned_count": len(returned),
            "matched_file_count": len(history),
            "first_match_at": matches[0].get("created_at") if matches else None,
            "first_match_pack": matches[0].get("pack_path") if matches else None,
            "latest_match_at": matches[-1].get("created_at") if matches else None,
            "latest_match_pack": matches[-1].get("pack_path") if matches else None,
            "chain_status": chain.get("status"),
            "warning_count": len(warnings),
        },
        "matches": returned,
        "file_history": history,
        "warnings": warnings,
        "chain": chain,
    }


def format_timeline_search_markdown(report: dict[str, Any]) -> str:
    """Render a snapshot timeline search report as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Timeline Search",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Output: `{report.get('out_dir')}`",
        f"- Query: {report.get('query')}",
        f"- Snapshots: `{summary.get('snapshot_count')}`",
        f"- Matched snapshots: `{summary.get('matched_snapshot_count')}`",
        f"- Matched files: `{summary.get('matched_file_count')}`",
        f"- First match: `{summary.get('first_match_at')}` `{summary.get('first_match_pack')}`",
        f"- Latest match: `{summary.get('latest_match_at')}` `{summary.get('latest_match_pack')}`",
        "",
        "## File History",
        "",
    ]
    history = report.get("file_history", [])
    if not history:
        lines.extend(["No matching files were found.", ""])
    else:
        for item in history[:20]:
            lines.append(
                f"- `{item.get('path')}` first=`{item.get('first_seen_at')}` "
                f"last=`{item.get('last_seen_at')}` seen=`{item.get('seen_count')}` "
                f"best=`{item.get('best_score')}`"
            )
        lines.append("")
    lines.extend(["## Recent Matching Snapshots", ""])
    matches = report.get("matches", [])
    if not matches:
        lines.extend(["No matching snapshots returned.", ""])
    else:
        for item in matches:
            lines.append(
                f"- `{item.get('pack_name')}` at=`{item.get('created_at')}` "
                f"top=`{item.get('top_path')}` score=`{item.get('top_score')}`"
            )
        lines.append("")
    warnings = report.get("warnings", [])
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- `{warning.get('pack_path')}` {warning.get('message')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def verify_snapshot_chain(out_dir: Path | str) -> dict[str, Any]:
    """Verify the tamper-evident hash chain recorded in snapshots.json."""

    out_path = Path(out_dir).resolve()
    index_path = out_path / "snapshots.json"
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "chain_version": SNAPSHOT_CHAIN_VERSION,
        "algorithm": SNAPSHOT_CHAIN_ALGORITHM,
        "snapshot_count": 0,
        "checked_count": 0,
        "head_chain_hash": None,
        "anchored_to_pruned_history": False,
        "legacy_unchained": False,
    }

    index: dict[str, Any] | None = None
    snapshots: list[dict[str, Any]] = []
    if not out_path.exists():
        _add_chain_issue(errors, str(out_path), "Snapshot directory does not exist.")
    elif not out_path.is_dir():
        _add_chain_issue(errors, str(out_path), "Snapshot path is not a directory.")
    elif not index_path.exists():
        _add_chain_issue(errors, str(index_path), "snapshots.json was not found.")
    else:
        try:
            index = _read_snapshot_index(index_path, out_path)
        except json.JSONDecodeError as exc:
            _add_chain_issue(errors, str(index_path), f"snapshots.json is invalid JSON: {exc}")
        except ValueError as exc:
            _add_chain_issue(errors, str(index_path), str(exc))

    if index is not None:
        raw_snapshots = index.get("snapshots", [])
        if isinstance(raw_snapshots, list):
            for offset, item in enumerate(raw_snapshots):
                if isinstance(item, dict):
                    snapshots.append(item)
                else:
                    _add_chain_issue(errors, str(index_path), "Snapshot index entry is not an object.", index=offset)
        else:
            _add_chain_issue(errors, str(index_path), "Snapshot index snapshots must be a list.")

        chain_meta = index.get("chain")
        summary["snapshot_count"] = len(snapshots)
        has_entry_chain = any(any(field in snapshot for field in SNAPSHOT_CHAIN_FIELDS) for snapshot in snapshots)
        if not snapshots and chain_meta is None:
            pass
        elif not has_entry_chain and chain_meta is None:
            summary["legacy_unchained"] = bool(snapshots)
            if snapshots:
                _add_chain_issue(
                    warnings,
                    str(index_path),
                    "Snapshot timeline has no hash chain; run a new snapshot or memory cycle to add one.",
                )
        else:
            _verify_snapshot_chain_entries(out_path, snapshots, errors, warnings, summary)
            _verify_snapshot_chain_meta(index_path, chain_meta, snapshots, errors, warnings, summary)

    status = "fail" if errors else "warn" if warnings else "pass"
    return {
        "schema_version": SNAPSHOT_CHAIN_VERSION,
        "status": status,
        "out_dir": str(out_path),
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }


def format_snapshot_chain_markdown(report: dict[str, Any]) -> str:
    """Render snapshot chain verification as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Snapshot Chain",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Output: `{report.get('out_dir')}`",
        f"- Chain version: `{summary.get('chain_version')}`",
        f"- Algorithm: `{summary.get('algorithm')}`",
        f"- Snapshots: `{summary.get('snapshot_count')}`",
        f"- Checked: `{summary.get('checked_count')}`",
        f"- Head hash: `{summary.get('head_chain_hash')}`",
        f"- Anchored to pruned history: `{summary.get('anchored_to_pruned_history')}`",
        f"- Legacy unchained: `{summary.get('legacy_unchained')}`",
        "",
    ]
    errors = report.get("errors", [])
    warnings = report.get("warnings", [])
    if errors:
        lines.extend(["## Errors", ""])
        for error in errors:
            lines.append(f"- `{error.get('path')}` {error.get('message')}")
        lines.append("")
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- `{warning.get('path')}` {warning.get('message')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_snapshot_anchor(out_dir: Path | str) -> dict[str, Any]:
    """Build a small external proof record for the current snapshot chain head."""

    out_path = Path(out_dir).resolve()
    chain = verify_snapshot_chain(out_path)
    latest = None
    if (out_path / "snapshots.json").exists():
        try:
            index = _read_snapshot_index(out_path / "snapshots.json", out_path)
        except (json.JSONDecodeError, ValueError):
            index = {}
        latest = index.get("latest") if isinstance(index.get("latest"), dict) else None
    chain_summary = chain.get("summary", {})
    latest_summary = _snapshot_anchor_latest(out_path, latest)
    anchor = {
        "schema_version": "repomori.snapshot_anchor.v1",
        "status": chain.get("status"),
        "out_dir": str(out_path),
        "created_at": int(time.time()),
        "producer": {
            "name": "RepoMori",
            "pack_schema_version": SCHEMA_VERSION,
            "anchor_schema_version": "repomori.snapshot_anchor.v1",
        },
        "chain": {
            "chain_version": chain_summary.get("chain_version"),
            "algorithm": chain_summary.get("algorithm"),
            "head_chain_hash": chain_summary.get("head_chain_hash"),
            "snapshot_count": chain_summary.get("snapshot_count"),
            "checked_count": chain_summary.get("checked_count"),
            "anchored_to_pruned_history": chain_summary.get("anchored_to_pruned_history"),
            "legacy_unchained": chain_summary.get("legacy_unchained"),
        },
        "latest_snapshot": latest_summary,
        "verification": {
            "schema_version": chain.get("schema_version"),
            "status": chain.get("status"),
            "error_count": len(chain.get("errors", [])),
            "warning_count": len(chain.get("warnings", [])),
            "errors": chain.get("errors", []),
            "warnings": chain.get("warnings", []),
        },
    }
    anchor["anchor_hash"] = _canonical_json_hash(anchor)
    return anchor


def format_snapshot_anchor_markdown(anchor: dict[str, Any]) -> str:
    """Render a snapshot anchor proof as Markdown."""

    chain = anchor.get("chain", {})
    latest = anchor.get("latest_snapshot") or {}
    verification = anchor.get("verification", {})
    lines = [
        "# RepoMori Snapshot Anchor",
        "",
        f"- Status: `{anchor.get('status')}`",
        f"- Output: `{anchor.get('out_dir')}`",
        f"- Created at: `{anchor.get('created_at')}`",
        f"- Anchor hash: `{anchor.get('anchor_hash')}`",
        "",
        "## Chain",
        "",
        f"- Version: `{chain.get('chain_version')}`",
        f"- Algorithm: `{chain.get('algorithm')}`",
        f"- Head hash: `{chain.get('head_chain_hash')}`",
        f"- Snapshot count: `{chain.get('snapshot_count')}`",
        f"- Checked count: `{chain.get('checked_count')}`",
        f"- Anchored to pruned history: `{chain.get('anchored_to_pruned_history')}`",
        f"- Legacy unchained: `{chain.get('legacy_unchained')}`",
        "",
        "## Latest Snapshot",
        "",
    ]
    if latest:
        lines.extend(
            [
                f"- Pack name: `{latest.get('pack_name')}`",
                f"- Pack path: `{latest.get('pack_path')}`",
                f"- Pack SHA-256: `{latest.get('pack_sha256')}`",
                f"- Created at: `{latest.get('created_at')}`",
                f"- Chain index: `{latest.get('chain_index')}`",
                f"- Chain hash: `{latest.get('chain_hash')}`",
                "",
            ]
        )
    else:
        lines.extend(["No latest snapshot is recorded.", ""])
    lines.extend(
        [
            "## Verification",
            "",
            f"- Status: `{verification.get('status')}`",
            f"- Errors: `{verification.get('error_count')}`",
            f"- Warnings: `{verification.get('warning_count')}`",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def verify_snapshot_anchor(
    anchor: Path | str | dict[str, Any],
    out_dir: Path | str | None = None,
    *,
    check_current: bool = True,
) -> dict[str, Any]:
    """Verify an exported snapshot anchor proof and optionally the current chain."""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    anchor_path: Path | None = None
    anchor_payload: dict[str, Any] | None = None
    source = "<memory>"

    if isinstance(anchor, dict):
        anchor_payload = anchor
    else:
        anchor_path = Path(anchor).resolve()
        source = str(anchor_path)
        try:
            loaded = json.loads(anchor_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                anchor_payload = loaded
            else:
                _add_anchor_issue(errors, source, "Anchor file must contain a JSON object.")
        except FileNotFoundError:
            _add_anchor_issue(errors, source, "Anchor file was not found.")
        except json.JSONDecodeError as exc:
            _add_anchor_issue(errors, source, f"Anchor file is invalid JSON: {exc}")

    actual_anchor_hash = None
    expected_anchor_hash = None
    anchor_hash_valid: bool | None = None
    anchor_status = None
    anchor_head_hash = None
    anchor_latest: dict[str, Any] | None = None
    current_chain: dict[str, Any] | None = None
    current_latest: dict[str, Any] | None = None
    current_head_hash = None
    chain_head_matches: bool | None = None
    latest_snapshot_matches: bool | None = None
    latest_pack_hash_matches: bool | None = None
    current_pack_hash_matches: bool | None = None

    if anchor_payload is not None:
        if anchor_payload.get("schema_version") != "repomori.snapshot_anchor.v1":
            _add_anchor_issue(
                errors,
                source,
                "Unexpected anchor schema version.",
                expected="repomori.snapshot_anchor.v1",
                actual=anchor_payload.get("schema_version"),
            )
        actual_anchor_hash = anchor_payload.get("anchor_hash")
        if isinstance(actual_anchor_hash, str) and actual_anchor_hash:
            expected_anchor_hash = _snapshot_anchor_expected_hash(anchor_payload)
            anchor_hash_valid = actual_anchor_hash == expected_anchor_hash
            if not anchor_hash_valid:
                _add_anchor_issue(
                    errors,
                    source,
                    "Anchor hash does not match the proof payload.",
                    expected=expected_anchor_hash,
                    actual=actual_anchor_hash,
                )
        else:
            anchor_hash_valid = False
            _add_anchor_issue(errors, source, "Anchor proof is missing anchor_hash.")

        anchor_status = anchor_payload.get("status")
        if anchor_status == "fail":
            _add_anchor_issue(warnings, source, "Anchor proof records a failing chain status.")
        elif anchor_status == "warn":
            _add_anchor_issue(warnings, source, "Anchor proof records a warning chain status.")

        chain = anchor_payload.get("chain") if isinstance(anchor_payload.get("chain"), dict) else {}
        anchor_head_hash = chain.get("head_chain_hash")
        anchor_latest = anchor_payload.get("latest_snapshot") if isinstance(anchor_payload.get("latest_snapshot"), dict) else None
        verification = anchor_payload.get("verification") if isinstance(anchor_payload.get("verification"), dict) else {}
        if verification and verification.get("status") != anchor_status:
            _add_anchor_issue(
                errors,
                source,
                "Anchor status does not match embedded verification status.",
                expected=anchor_status,
                actual=verification.get("status"),
            )
        if anchor_latest and anchor_latest.get("chain_hash") and anchor_head_hash and anchor_latest.get("chain_hash") != anchor_head_hash:
            _add_anchor_issue(
                errors,
                source,
                "Anchor latest snapshot chain hash does not match anchor head hash.",
                expected=anchor_head_hash,
                actual=anchor_latest.get("chain_hash"),
            )

    resolved_out_dir = Path(out_dir).resolve() if out_dir is not None else None
    if resolved_out_dir is None and anchor_payload is not None and anchor_payload.get("out_dir"):
        resolved_out_dir = Path(str(anchor_payload.get("out_dir"))).resolve()

    if check_current:
        if resolved_out_dir is None:
            _add_anchor_issue(errors, source, "No snapshot directory was supplied or recorded in the anchor.")
        else:
            current_chain = verify_snapshot_chain(resolved_out_dir)
            current_head_hash = current_chain.get("summary", {}).get("head_chain_hash")
            if current_chain.get("status") == "fail":
                _add_anchor_issue(errors, str(resolved_out_dir), "Current snapshot chain verification failed.")
            elif current_chain.get("status") == "warn":
                _add_anchor_issue(warnings, str(resolved_out_dir), "Current snapshot chain verification has warnings.")

            if anchor_payload is not None:
                chain_head_matches = anchor_head_hash == current_head_hash
                if not chain_head_matches:
                    _add_anchor_issue(
                        errors,
                        str(resolved_out_dir),
                        "Anchor chain head does not match current snapshot timeline head.",
                        expected=anchor_head_hash,
                        actual=current_head_hash,
                    )

            try:
                index = _read_snapshot_index(resolved_out_dir / "snapshots.json", resolved_out_dir)
                current_latest = _snapshot_anchor_latest(
                    resolved_out_dir,
                    index.get("latest") if isinstance(index.get("latest"), dict) else None,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                _add_anchor_issue(errors, str(resolved_out_dir / "snapshots.json"), str(exc))

            if anchor_payload is not None and anchor_latest is not None and current_latest is not None:
                latest_snapshot_matches = (
                    anchor_latest.get("chain_hash") == current_latest.get("chain_hash")
                    and anchor_latest.get("pack_sha256") == current_latest.get("pack_sha256")
                )
                if not latest_snapshot_matches:
                    _add_anchor_issue(
                        errors,
                        str(resolved_out_dir),
                        "Anchor latest snapshot does not match current latest snapshot.",
                        expected={
                            "chain_hash": anchor_latest.get("chain_hash"),
                            "pack_sha256": anchor_latest.get("pack_sha256"),
                        },
                        actual={
                            "chain_hash": current_latest.get("chain_hash"),
                            "pack_sha256": current_latest.get("pack_sha256"),
                        },
                    )
                if anchor_latest.get("pack_sha256") and current_latest.get("pack_sha256"):
                    latest_pack_hash_matches = anchor_latest.get("pack_sha256") == current_latest.get("pack_sha256")
                pack_path = _recorded_snapshot_path(resolved_out_dir, current_latest.get("pack_path"))
                if pack_path is not None and pack_path.exists():
                    actual_pack_hash = _path_sha256(pack_path)
                    current_pack_hash_matches = actual_pack_hash == current_latest.get("pack_sha256")
                    if not current_pack_hash_matches:
                        _add_anchor_issue(
                            errors,
                            str(pack_path),
                            "Current latest pack SHA-256 does not match snapshots.json.",
                            expected=current_latest.get("pack_sha256"),
                            actual=actual_pack_hash,
                        )
                elif pack_path is not None:
                    current_pack_hash_matches = False
                    _add_anchor_issue(errors, str(pack_path), "Current latest pack file was not found.")
            elif anchor_payload is not None and (anchor_latest is not None or current_latest is not None):
                latest_snapshot_matches = False
                _add_anchor_issue(errors, str(resolved_out_dir), "Anchor/latest snapshot comparison could not be completed.")

    status = "fail" if errors else "warn" if warnings else "pass"
    return {
        "schema_version": "repomori.snapshot_anchor.verify.v1",
        "status": status,
        "anchor_path": str(anchor_path) if anchor_path is not None else None,
        "out_dir": str(resolved_out_dir) if resolved_out_dir is not None else None,
        "checked_at": int(time.time()),
        "settings": {"check_current": check_current},
        "summary": {
            "anchor_schema_version": anchor_payload.get("schema_version") if anchor_payload else None,
            "anchor_status": anchor_status,
            "anchor_hash": actual_anchor_hash,
            "expected_anchor_hash": expected_anchor_hash,
            "anchor_hash_valid": anchor_hash_valid,
            "anchor_head_hash": anchor_head_hash,
            "current_head_hash": current_head_hash,
            "chain_head_matches": chain_head_matches,
            "latest_snapshot_matches": latest_snapshot_matches,
            "latest_pack_hash_matches": latest_pack_hash_matches,
            "current_pack_hash_matches": current_pack_hash_matches,
            "current_chain_status": current_chain.get("status") if current_chain else None,
        },
        "current_chain": current_chain,
        "current_latest_snapshot": current_latest,
        "errors": errors,
        "warnings": warnings,
    }


def format_snapshot_anchor_verification_markdown(report: dict[str, Any]) -> str:
    """Render snapshot anchor verification as Markdown."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Snapshot Anchor Verification",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Anchor: `{report.get('anchor_path')}`",
        f"- Output: `{report.get('out_dir')}`",
        f"- Anchor hash valid: `{summary.get('anchor_hash_valid')}`",
        f"- Anchor hash: `{summary.get('anchor_hash')}`",
        f"- Anchor head: `{summary.get('anchor_head_hash')}`",
        f"- Current head: `{summary.get('current_head_hash')}`",
        f"- Chain head matches: `{summary.get('chain_head_matches')}`",
        f"- Latest snapshot matches: `{summary.get('latest_snapshot_matches')}`",
        f"- Current pack hash matches: `{summary.get('current_pack_hash_matches')}`",
        "",
    ]
    errors = report.get("errors", [])
    warnings = report.get("warnings", [])
    if errors:
        lines.extend(["## Errors", ""])
        for error in errors:
            lines.append(f"- `{error.get('path')}` {error.get('message')}")
        lines.append("")
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- `{warning.get('path')}` {warning.get('message')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
            "handoff_score_pass_count": sum(1 for item in snapshots if item.get("handoff_score_status") == "pass"),
            "handoff_score_warn_count": sum(1 for item in snapshots if item.get("handoff_score_status") == "warn"),
            "handoff_score_fail_count": sum(1 for item in snapshots if item.get("handoff_score_status") == "fail"),
            "handoff_triage_pass_count": sum(1 for item in snapshots if item.get("handoff_triage_status") == "pass"),
            "handoff_triage_warn_count": sum(1 for item in snapshots if item.get("handoff_triage_status") == "warn"),
            "handoff_triage_fail_count": sum(1 for item in snapshots if item.get("handoff_triage_status") == "fail"),
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

    chain_report = verify_snapshot_chain(out_path)
    chain_summary = chain_report.get("summary", {})
    summary["chain_status"] = chain_report.get("status")
    summary["chain_head_hash"] = chain_summary.get("head_chain_hash")
    summary["chain_checked_count"] = chain_summary.get("checked_count")
    summary["chain_anchored_to_pruned_history"] = chain_summary.get("anchored_to_pruned_history")
    for issue in chain_report.get("errors", []):
        _add_doctor_issue(
            errors,
            "chain",
            str(issue.get("path", "")),
            str(issue.get("message", "Snapshot chain verification failed.")),
            index=issue.get("index"),
            expected=issue.get("expected"),
            actual=issue.get("actual"),
        )
    for issue in chain_report.get("warnings", []):
        _add_doctor_issue(
            warnings,
            "chain",
            str(issue.get("path", "")),
            str(issue.get("message", "Snapshot chain verification warning.")),
            index=issue.get("index"),
            expected=issue.get("expected"),
            actual=issue.get("actual"),
        )

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
        updated = _chain_snapshot_index(
            out_path,
            {
                "schema_version": "repomori.snapshots.v1",
                "out_dir": str(out_path),
                "updated_at": int(time.time()),
                "snapshot_count": len(retained_snapshots),
                "latest": index.get("latest"),
                "snapshots": retained_snapshots,
            },
        )
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
    handoff_quality_profile: str | None = None,
    handoff_quality_target: float | None = None,
    anchor_out: str | None = None,
    anchor_verify: bool = False,
    allow_unverified_anchor: bool = False,
    anchor_log: str | None = None,
    anchor_freshness: str | None = None,
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
    if handoff_quality_profile is not None:
        handoff_quality_profile = _normalize_handoff_quality_profile(handoff_quality_profile)
    if handoff_quality_target is not None and (handoff_quality_target < 0 or handoff_quality_target > 100):
        raise ValueError("handoff_quality_target must be between 0 and 100")

    started = time.time()
    repo_path = Path(repo).resolve()
    out_path = Path(out_dir).resolve()
    anchor_report = None
    anchor_verification = None
    anchor_log_report = None
    failure_reasons: list[str] = []

    if anchor_freshness is not None:
        anchor_freshness = _normalize_anchor_freshness(anchor_freshness)
    anchor_check_current = True
    if anchor_out is not None and anchor_freshness is not None:
        if anchor_freshness == "strict":
            anchor_verify = True
            allow_unverified_anchor = False
            anchor_check_current = True
        elif anchor_freshness == "safe":
            anchor_verify = True
            allow_unverified_anchor = True
            anchor_check_current = True
        elif anchor_freshness == "legacy":
            anchor_verify = True
            allow_unverified_anchor = True
            anchor_check_current = False

    if anchor_verify and anchor_out is None:
        raise ValueError("anchor_verify requires anchor_out.")
    if anchor_log is not None and anchor_out is None:
        raise ValueError("anchor_log requires anchor_out.")

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
    handoff_quality = None
    if handoff_quality_profile is not None and snapshot.get("handoff_score") is not None:
        handoff_quality = evaluate_handoff_quality(
            snapshot["handoff_score"],
            profile=handoff_quality_profile,
            target_score=handoff_quality_target,
        )

    if anchor_out is not None:
        if not anchor_out.strip():
            raise ValueError("anchor_out must not be empty.")
        anchor_report = build_snapshot_anchor(out_path)
        anchor_out_path = Path(anchor_out).resolve()
        anchor_out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(anchor_out_path, anchor_report)
        if anchor_verify:
            anchor_verification = verify_snapshot_anchor(
                anchor_out_path,
                out_path,
                check_current=anchor_check_current,
            )
        if anchor_log is not None:
            anchor_payload_for_log = (
                anchor_verification if anchor_verification is not None else anchor_report
            )
            anchor_log_report = append_anchor_log(anchor_payload_for_log, anchor_log, out_dir=out_path)
            anchor_log_report["anchor_path"] = str(anchor_out_path)

    doctor = doctor_snapshot_dir(out_path, verify_packs=verify_packs)
    prune = prune_snapshots(out_path, keep=keep, apply=prune_apply)
    timeline = read_snapshot_timeline(out_path, limit=timeline_limit)

    anchor_verification_status = anchor_verification["status"] if anchor_verification is not None else None
    anchor_failed = anchor_verification_status == "fail"
    anchor_failed_allowed = anchor_failed and allow_unverified_anchor
    if (
        snapshot.get("status") != "pass"
        or doctor.get("status") == "fail"
        or prune.get("errors")
        or (handoff_quality is not None and handoff_quality.get("status") == "fail")
        or (anchor_failed and not allow_unverified_anchor)
    ):
        status = "fail"
    elif (
        doctor.get("status") == "warn"
        or (handoff_quality is not None and handoff_quality.get("status") == "warn")
        or anchor_verification_status == "warn"
        or anchor_failed_allowed
    ):
        status = "warn"
    else:
        status = "pass"

    if anchor_report is None and anchor_verify:
        status = "fail"

    if snapshot.get("status") != "pass":
        verify_report = snapshot.get("verify", {})
        verify_errors = verify_report.get("errors")
        if isinstance(verify_errors, list) and verify_errors:
            first = verify_errors[0]
            if isinstance(first, dict):
                message = str(first.get("message", "Snapshot verification failed.")).strip()
            else:
                message = "Snapshot verification failed."
            failure_reasons.append(f"snapshot: {message}")
        else:
            failure_reasons.append("snapshot: snapshot verification failed")
    if doctor.get("status") == "fail":
        for error in doctor.get("errors", []):
            message = str(error.get("message", "")).strip()
            if message:
                failure_reasons.append(f"doctor: {message}")
        if not doctor.get("errors"):
            failure_reasons.append("doctor: snapshot doctor reported fail status")
    elif doctor.get("status") == "warn":
        for warning in doctor.get("warnings", []):
            message = str(warning.get("message", "")).strip()
            if message:
                failure_reasons.append(f"doctor: {message}")
    if prune.get("errors"):
        for error in prune.get("errors", []):
            message = str(error.get("message", "")).strip()
            if message:
                failure_reasons.append(f"prune: {message}")
        if not prune.get("errors"):
            failure_reasons.append("prune: prune reported error status")
    if handoff_quality is not None:
        for failure in handoff_quality.get("failures", []):
            message = str(failure.get("message", "")).strip()
            if message:
                failure_reasons.append(f"handoff-quality: {message}")
        if handoff_quality.get("status") == "warn":
            for warning in handoff_quality.get("warnings", [])[:3]:
                message = str(warning.get("message", "")).strip()
                if message:
                    failure_reasons.append(f"handoff-quality: {message}")
    if anchor_verification is not None:
        for error in anchor_verification.get("errors", []):
            message = str(error.get("message", "")).strip()
            if message:
                failure_reasons.append(f"anchor: {message}")
        if anchor_failed and not allow_unverified_anchor and not anchor_verification.get("errors"):
            failure_reasons.append(
                "anchor: anchor verification failed and no verification override was enabled"
            )
        if anchor_failed and allow_unverified_anchor:
            failure_reasons.append(
                "anchor: anchor verification failed, but continue requested by allow_unverified_anchor"
            )
    elif anchor_verify:
        failure_reasons.append("anchor: anchor verification requested but no anchor verification report was produced")
    if status == "fail" and not failure_reasons:
        if snapshot.get("status") != "pass":
            failure_reasons.append("snapshot: snapshot step failed")
        elif doctor.get("status") == "fail":
            failure_reasons.append("doctor: snapshot doctor failed")
        elif prune.get("errors"):
            failure_reasons.append("prune: prune reported errors")
        elif anchor_failed and not allow_unverified_anchor:
            failure_reasons.append("anchor: anchor verification failed")

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
    if snapshot.get("artifacts", {}).get("handoff_score_json"):
        artifacts["handoff_score_json"] = snapshot["artifacts"]["handoff_score_json"]
    if snapshot.get("artifacts", {}).get("handoff_score_markdown"):
        artifacts["handoff_score_markdown"] = snapshot["artifacts"]["handoff_score_markdown"]
    if snapshot.get("artifacts", {}).get("handoff_triage_json"):
        artifacts["handoff_triage_json"] = snapshot["artifacts"]["handoff_triage_json"]
    if snapshot.get("artifacts", {}).get("handoff_triage_markdown"):
        artifacts["handoff_triage_markdown"] = snapshot["artifacts"]["handoff_triage_markdown"]
    if snapshot.get("artifacts", {}).get("compare_json"):
        artifacts["compare_json"] = snapshot["artifacts"]["compare_json"]
    if snapshot.get("artifacts", {}).get("compare_markdown"):
        artifacts["compare_markdown"] = snapshot["artifacts"]["compare_markdown"]
    if snapshot.get("artifacts", {}).get("inspect_diff_json"):
        artifacts["inspect_diff_json"] = snapshot["artifacts"]["inspect_diff_json"]
    if snapshot.get("artifacts", {}).get("inspect_diff_markdown"):
        artifacts["inspect_diff_markdown"] = snapshot["artifacts"]["inspect_diff_markdown"]
    if snapshot.get("artifacts", {}).get("diff_context_json"):
        artifacts["diff_context_json"] = snapshot["artifacts"]["diff_context_json"]
    if snapshot.get("artifacts", {}).get("diff_context_markdown"):
        artifacts["diff_context_markdown"] = snapshot["artifacts"]["diff_context_markdown"]
    if anchor_report is not None:
        artifacts["anchor"] = _artifact_record_any_path(out_path, Path(anchor_out).resolve(), "anchor_json")

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
            "handoff_quality_profile": handoff_quality_profile,
            "handoff_quality_target": handoff_quality_target,
            "anchor_out": anchor_out,
            "anchor_verify": anchor_verify,
            "anchor_freshness": anchor_freshness,
            "allow_unverified_anchor": allow_unverified_anchor,
            "anchor_log": anchor_log,
        },
        "summary": {
            "elapsed_seconds": round(time.time() - started, 4),
            "snapshot_status": snapshot.get("status"),
            "doctor_status": doctor.get("status"),
            "failure_reason_count": len(failure_reasons),
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
            "handoff_score_status": snapshot_summary.get("handoff_score_status"),
            "handoff_score_percent": snapshot_summary.get("handoff_score_percent"),
            "handoff_score_json": snapshot_summary.get("handoff_score_json"),
            "handoff_score_markdown": snapshot_summary.get("handoff_score_markdown"),
            "handoff_score_failed_checks": snapshot_summary.get("handoff_score_failed_checks"),
            "handoff_score_warned_checks": snapshot_summary.get("handoff_score_warned_checks"),
            "handoff_triage_status": snapshot_summary.get("handoff_triage_status"),
            "handoff_triage_action_count": snapshot_summary.get("handoff_triage_action_count"),
            "handoff_triage_high_priority_count": snapshot_summary.get("handoff_triage_high_priority_count"),
            "handoff_triage_json": snapshot_summary.get("handoff_triage_json"),
            "handoff_triage_markdown": snapshot_summary.get("handoff_triage_markdown"),
            "handoff_quality_status": handoff_quality.get("status") if handoff_quality is not None else None,
            "handoff_quality_profile": handoff_quality.get("profile") if handoff_quality is not None else None,
            "handoff_quality_target_met": handoff_quality.get("summary", {}).get("target_met") if handoff_quality is not None else None,
            "inspect_diff_status": snapshot_summary.get("inspect_diff_status"),
            "inspect_diff_json": snapshot_summary.get("inspect_diff_json"),
            "inspect_diff_markdown": snapshot_summary.get("inspect_diff_markdown"),
            "diff_context_status": snapshot_summary.get("diff_context_status"),
            "diff_context_json": snapshot_summary.get("diff_context_json"),
            "diff_context_markdown": snapshot_summary.get("diff_context_markdown"),
            "diff_context_selected_count": snapshot_summary.get("diff_context_selected_count"),
            "diff_context_added_count": snapshot_summary.get("diff_context_added_count"),
            "diff_context_changed_count": snapshot_summary.get("diff_context_changed_count"),
            "diff_context_removed_count": snapshot_summary.get("diff_context_removed_count"),
            "anchor_status": anchor_report.get("status") if anchor_report is not None else None,
            "anchor_path": str(Path(anchor_out).resolve()) if anchor_out is not None else None,
            "anchor_freshness": anchor_freshness,
            "anchor_verification_status": anchor_verification_status,
            "anchor_hash": anchor_report.get("anchor_hash") if anchor_report is not None else None,
        },
        "failure_reasons": failure_reasons,
        "artifacts": artifacts,
        "snapshot": snapshot,
        "doctor": doctor,
        "prune": prune,
        "timeline": timeline,
        "inspect_diff": snapshot.get("inspect_diff"),
        "handoff_triage": snapshot.get("handoff_triage"),
        "handoff_quality": handoff_quality,
        "diff_context": snapshot.get("diff_context"),
        "anchor": anchor_report,
        "anchor_verification": anchor_verification,
        "anchor_log": anchor_log_report,
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
    anchor_freshness: str | None = None,
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
    if anchor_freshness is not None:
        anchor_freshness = _normalize_anchor_freshness(anchor_freshness)

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
        "handoff_quality_profile": None,
        "handoff_quality_target": None,
        "anchor_out": None,
        "anchor_verify": False,
        "allow_unverified_anchor": False,
        "anchor_freshness": anchor_freshness,
        "anchor_log": None,
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


def write_release_package_artifacts(
    root: Path | str,
    *,
    version: str,
    commit: str | None = None,
    ref: str | None = None,
    run_id: str | None = None,
    repository: str | None = None,
    workflow: str = "release-candidate.yml",
    generated_at: int | None = None,
) -> dict[str, Any]:
    """Write release manifest, provenance, SBOM, and checksum artifacts."""

    root_path = Path(root)
    dist_path = root_path / "dist"
    if not dist_path.exists():
        raise FileNotFoundError(f"Release dist directory not found: {dist_path}")

    generated_at_value = int(time.time()) if generated_at is None else int(generated_at)
    generated_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(generated_at_value))
    dist_artifacts = _release_artifact_records(root_path, dist_path)

    sbom = _release_sbom_document(
        version=version,
        commit=commit,
        repository=repository,
        generated_at_utc=generated_at_utc,
        artifacts=dist_artifacts,
    )
    sbom_path = root_path / "sbom.spdx.json"
    root_path.mkdir(parents=True, exist_ok=True)
    sbom_path.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    sbom_record = _release_artifact_record(root_path, sbom_path, kind="sbom")

    provenance_artifacts = [*dist_artifacts, sbom_record]
    provenance = {
        "schema_version": "repomori.release_provenance.v1",
        "status": "pass",
        "version": version,
        "commit": commit,
        "ref": ref,
        "repository": repository,
        "workflow": workflow,
        "run_id": run_id,
        "generated_at": generated_at_value,
        "generated_at_utc": generated_at_utc,
        "artifacts": provenance_artifacts,
        "license": "LicenseRef-PolyForm-Noncommercial-1.0.0",
        "notes": [
            "Checksums and provenance are tamper-evident aids, not cryptographic signatures.",
            "No network, model provider, or signing service is required to generate these artifacts.",
        ],
    }
    provenance_path = root_path / "release-provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    provenance_record = _release_artifact_record(root_path, provenance_path, kind="provenance")

    checksum_records = [*dist_artifacts, sbom_record, provenance_record]
    checksums_path = root_path / "checksums.txt"
    checksums_path.write_text(
        "".join(f"{item['sha256']}  {item['path']}\n" for item in checksum_records),
        encoding="utf-8",
    )
    checksums_record = _release_artifact_record(root_path, checksums_path, kind="checksums")

    integrity = {
        "checksums": checksums_record,
        "provenance": provenance_record,
        "sbom": sbom_record,
    }
    manifest = {
        "schema_version": "repomori.release_candidate.v1",
        "status": "pass",
        "version": version,
        "commit": commit,
        "ref": ref,
        "run_id": run_id,
        "generated_at": generated_at_value,
        "artifacts": dist_artifacts,
        "integrity": integrity,
        "release_check": {
            "json": ".repomori-release-check/release-check.json",
            "markdown": ".repomori-release-check/release-check.md",
            "drift_log": ".repomori-release-check/baseline-drift.jsonl",
        },
    }
    manifest_path = root_path / "release-candidate.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    markdown_path = root_path / "release-candidate.md"
    markdown_path.write_text(_format_release_package_manifest_markdown(manifest), encoding="utf-8")
    return manifest


def _release_artifact_records(root: Path, directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir()):
        if path.is_file():
            records.append(_release_artifact_record(root, path, kind=_release_artifact_kind(path)))
    return records


def _release_artifact_record(root: Path, path: Path, *, kind: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _release_artifact_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".whl"):
        return "wheel"
    if name.endswith(".zip"):
        return "source_archive"
    return "release_artifact"


def _release_spdx_id(prefix: str, value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9.-]+", "-", value).strip("-")
    if not safe:
        safe = "artifact"
    return f"SPDXRef-{prefix}-{safe}"


def _release_sbom_document(
    *,
    version: str,
    commit: str | None,
    repository: str | None,
    generated_at_utc: str,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    namespace_commit = commit or "local"
    repository_part = repository or "local"
    packages: list[dict[str, Any]] = [
        {
            "name": "repomori",
            "SPDXID": "SPDXRef-Package-RepoMori",
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "LicenseRef-PolyForm-Noncommercial-1.0.0",
            "licenseDeclared": "LicenseRef-PolyForm-Noncommercial-1.0.0",
            "copyrightText": "Copyright (c) 2026 TWO HANDS NETWORK LTD",
            "supplier": "Organization: TWO HANDS NETWORK LTD",
            "primaryPackagePurpose": "APPLICATION",
        }
    ]
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-Package-RepoMori",
        }
    ]
    for artifact in artifacts:
        artifact_id = _release_spdx_id("Artifact", str(artifact.get("path") or "artifact"))
        packages.append(
            {
                "name": artifact["path"],
                "SPDXID": artifact_id,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "LicenseRef-PolyForm-Noncommercial-1.0.0",
                "licenseDeclared": "LicenseRef-PolyForm-Noncommercial-1.0.0",
                "copyrightText": "Copyright (c) 2026 TWO HANDS NETWORK LTD",
                "checksums": [{"algorithm": "SHA256", "checksumValue": artifact["sha256"]}],
                "primaryPackagePurpose": "FILE",
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-RepoMori",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": artifact_id,
            }
        )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"RepoMori {version} release artifacts",
        "documentNamespace": f"https://github.com/{repository_part}/spdx/repomori-{version}-{namespace_commit}",
        "creationInfo": {
            "created": generated_at_utc,
            "creators": [
                "Tool: RepoMori release package workflow",
                "Organization: TWO HANDS NETWORK LTD",
            ],
        },
        "packages": packages,
        "relationships": relationships,
        "hasExtractedLicensingInfos": [
            {
                "licenseId": "LicenseRef-PolyForm-Noncommercial-1.0.0",
                "extractedText": "RepoMori is source-available for personal and non-commercial use. Commercial use requires a separate written license from TWO HANDS NETWORK LTD.",
                "name": "PolyForm Noncommercial License 1.0.0 with RepoMori required notice",
            }
        ],
    }


def _format_release_package_manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        f"# RepoMori {manifest.get('version')} Release Package",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Commit: `{manifest.get('commit')}`",
        f"- Ref: `{manifest.get('ref')}`",
        f"- Run ID: `{manifest.get('run_id')}`",
        "",
        "## Artifacts",
        "",
    ]
    for artifact in manifest.get("artifacts", []):
        lines.append(f"- `{artifact['path']}` `{artifact['bytes']}` bytes `{artifact['sha256']}`")
    lines.extend(["", "## Integrity", ""])
    integrity = manifest.get("integrity", {})
    for key in ("checksums", "provenance", "sbom"):
        artifact = integrity.get(key)
        if isinstance(artifact, dict):
            lines.append(f"- `{artifact['path']}` `{artifact['bytes']}` bytes `{artifact['sha256']}`")
    return "\n".join(lines).rstrip() + "\n"


def verify_release_package(root: Path | str) -> dict[str, Any]:
    """Verify a release package manifest, checksums, provenance, and SBOM."""

    started = time.time()
    requested_root = Path(root).resolve()
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    artifacts: dict[str, dict[str, Any]] = {}

    resolved_root = _resolve_release_package_root(requested_root, warnings, errors)
    required_files = (
        "release-candidate.json",
        "checksums.txt",
        "release-provenance.json",
        "sbom.spdx.json",
    )
    if resolved_root is None:
        _release_verify_add_check(
            checks,
            "package_root",
            "fail",
            "Release package root could not be resolved.",
            {"requested_root": str(requested_root)},
        )
        return _release_verify_report(
            requested_root,
            None,
            started=started,
            checks=checks,
            artifacts=artifacts,
            warnings=warnings,
            errors=errors,
        )

    _release_verify_add_check(
        checks,
        "package_root",
        "pass" if resolved_root == requested_root else "warn",
        "Release package root resolved.",
        {"requested_root": str(requested_root), "resolved_root": str(resolved_root)},
    )

    missing_required = [name for name in required_files if not (resolved_root / name).is_file()]
    for name in missing_required:
        _release_verify_error(
            errors,
            "required_file_missing",
            f"Required release package file is missing: {name}",
            path=name,
        )
    _release_verify_add_check(
        checks,
        "required_files",
        "pass" if not missing_required else "fail",
        "Required release integrity files are present.",
        {"required": list(required_files), "missing": missing_required},
    )

    manifest = _release_verify_load_json(resolved_root / "release-candidate.json", errors, "manifest")
    provenance = _release_verify_load_json(resolved_root / "release-provenance.json", errors, "provenance")
    sbom = _release_verify_load_json(resolved_root / "sbom.spdx.json", errors, "sbom")

    _release_verify_add_check(
        checks,
        "manifest_schema",
        "pass" if isinstance(manifest, dict) and manifest.get("schema_version") == "repomori.release_candidate.v1" else "fail",
        "Release manifest uses the expected schema.",
        {"expected": "repomori.release_candidate.v1", "actual": manifest.get("schema_version") if isinstance(manifest, dict) else None},
    )
    if isinstance(manifest, dict) and manifest.get("schema_version") != "repomori.release_candidate.v1":
        _release_verify_error(
            errors,
            "manifest_schema_mismatch",
            "release-candidate.json has an unexpected schema_version.",
            path="release-candidate.json",
            expected="repomori.release_candidate.v1",
            actual=manifest.get("schema_version"),
        )

    _release_verify_add_check(
        checks,
        "provenance_schema",
        "pass" if isinstance(provenance, dict) and provenance.get("schema_version") == "repomori.release_provenance.v1" else "fail",
        "Release provenance uses the expected schema.",
        {"expected": "repomori.release_provenance.v1", "actual": provenance.get("schema_version") if isinstance(provenance, dict) else None},
    )
    if isinstance(provenance, dict) and provenance.get("schema_version") != "repomori.release_provenance.v1":
        _release_verify_error(
            errors,
            "provenance_schema_mismatch",
            "release-provenance.json has an unexpected schema_version.",
            path="release-provenance.json",
            expected="repomori.release_provenance.v1",
            actual=provenance.get("schema_version"),
        )

    _release_verify_add_check(
        checks,
        "sbom_schema",
        "pass" if isinstance(sbom, dict) and sbom.get("spdxVersion") == "SPDX-2.3" else "fail",
        "SBOM uses SPDX 2.3.",
        {"expected": "SPDX-2.3", "actual": sbom.get("spdxVersion") if isinstance(sbom, dict) else None},
    )
    if isinstance(sbom, dict) and sbom.get("spdxVersion") != "SPDX-2.3":
        _release_verify_error(
            errors,
            "sbom_schema_mismatch",
            "sbom.spdx.json has an unexpected spdxVersion.",
            path="sbom.spdx.json",
            expected="SPDX-2.3",
            actual=sbom.get("spdxVersion"),
        )

    checksum_entries = _release_verify_parse_checksums(resolved_root, errors)
    _release_verify_add_check(
        checks,
        "checksums_parse",
        "pass" if checksum_entries is not None else "fail",
        "checksums.txt contains parseable SHA-256 records.",
        {"checksum_count": len(checksum_entries or {})},
    )
    if checksum_entries:
        for relative, digest in checksum_entries.items():
            _release_verify_record_file(
                resolved_root,
                {"path": relative, "sha256": digest},
                source="checksums.txt",
                artifacts=artifacts,
                errors=errors,
            )
    checksum_file_failures = [
        item
        for item in artifacts.values()
        if "checksums.txt" in item.get("sources", []) and item.get("status") == "fail"
    ]
    _release_verify_add_check(
        checks,
        "checksum_files",
        "pass" if checksum_entries is not None and not checksum_file_failures else "fail",
        "Files listed in checksums.txt match their SHA-256 values.",
        {"failed": [item.get("path") for item in checksum_file_failures]},
    )

    manifest_artifacts = _release_verify_records(manifest.get("artifacts") if isinstance(manifest, dict) else None)
    integrity = manifest.get("integrity") if isinstance(manifest, dict) else None
    integrity = integrity if isinstance(integrity, dict) else {}
    manifest_integrity_ok = isinstance(manifest, dict) and isinstance(manifest.get("integrity"), dict)
    expected_integrity_paths = {
        "checksums": "checksums.txt",
        "provenance": "release-provenance.json",
        "sbom": "sbom.spdx.json",
    }
    manifest_integrity_error_start = len(errors)
    for key, expected_path in expected_integrity_paths.items():
        record = integrity.get(key)
        if not isinstance(record, dict):
            manifest_integrity_ok = False
            _release_verify_error(errors, "manifest_integrity_missing", f"Manifest integrity record is missing: {key}", path=expected_path)
            continue
        if record.get("path") != expected_path:
            manifest_integrity_ok = False
            _release_verify_error(
                errors,
                "manifest_integrity_path_mismatch",
                f"Manifest integrity record for {key} points at the wrong path.",
                path=str(record.get("path")),
                expected=expected_path,
                actual=record.get("path"),
            )
        _release_verify_record_file(
            resolved_root,
            record,
            source=f"manifest.integrity.{key}",
            artifacts=artifacts,
                errors=errors,
        )
    manifest_integrity_ok = manifest_integrity_ok and len(errors) == manifest_integrity_error_start
    _release_verify_add_check(
        checks,
        "manifest_integrity",
        "pass" if manifest_integrity_ok else "fail",
        "Manifest integrity block points at checksum, provenance, and SBOM files.",
        {"expected": expected_integrity_paths},
    )

    manifest_artifacts_ok = bool(manifest_artifacts)
    manifest_artifacts_error_start = len(errors)
    for record in manifest_artifacts:
        _release_verify_record_file(
            resolved_root,
            record,
            source="manifest.artifacts",
            artifacts=artifacts,
            errors=errors,
        )
    wheel_present = any(
        str(record.get("kind")) == "wheel" or str(record.get("path", "")).endswith(".whl")
        for record in manifest_artifacts
    )
    source_present = any(
        str(record.get("kind")) == "source_archive" or str(record.get("path", "")).endswith(".zip")
        for record in manifest_artifacts
    )
    if not wheel_present:
        manifest_artifacts_ok = False
        _release_verify_error(errors, "wheel_missing", "Release manifest does not list a wheel artifact.")
    if not source_present:
        manifest_artifacts_ok = False
        _release_verify_error(errors, "source_archive_missing", "Release manifest does not list a source archive artifact.")
    manifest_artifacts_ok = manifest_artifacts_ok and len(errors) == manifest_artifacts_error_start
    _release_verify_add_check(
        checks,
        "manifest_artifacts",
        "pass" if manifest_artifacts_ok else "fail",
        "Release manifest lists wheel and source artifacts with valid hashes.",
        {"artifact_count": len(manifest_artifacts), "wheel_present": wheel_present, "source_archive_present": source_present},
    )

    manifest_artifact_paths = {str(record.get("path")) for record in manifest_artifacts if isinstance(record.get("path"), str)}
    required_checksum_paths = set(manifest_artifact_paths) | {"release-provenance.json", "sbom.spdx.json"}
    checksum_coverage_ok = bool(checksum_entries)
    checksum_paths = set(checksum_entries or {})
    missing_checksums = sorted(required_checksum_paths - checksum_paths)
    if missing_checksums:
        checksum_coverage_ok = False
        _release_verify_error(
            errors,
            "checksum_coverage_missing",
            "checksums.txt does not cover every required release artifact.",
            expected=sorted(required_checksum_paths),
            actual=sorted(checksum_paths),
        )
    _release_verify_add_check(
        checks,
        "checksum_coverage",
        "pass" if checksum_coverage_ok else "fail",
        "Checksums cover dist artifacts, provenance, and SBOM.",
        {"required": sorted(required_checksum_paths), "missing": missing_checksums},
    )

    provenance_artifacts = _release_verify_records(provenance.get("artifacts") if isinstance(provenance, dict) else None)
    provenance_ok = isinstance(provenance, dict) and bool(provenance_artifacts)
    if isinstance(manifest, dict) and isinstance(provenance, dict) and manifest.get("version") != provenance.get("version"):
        provenance_ok = False
        _release_verify_error(
            errors,
            "provenance_version_mismatch",
            "Release provenance version does not match manifest version.",
            expected=manifest.get("version"),
            actual=provenance.get("version"),
        )
    provenance_paths = {str(record.get("path")) for record in provenance_artifacts if isinstance(record.get("path"), str)}
    required_provenance_paths = set(manifest_artifact_paths) | {"sbom.spdx.json"}
    missing_provenance_paths = sorted(required_provenance_paths - provenance_paths)
    if missing_provenance_paths:
        provenance_ok = False
        _release_verify_error(
            errors,
            "provenance_coverage_missing",
            "release-provenance.json does not cover every required release artifact.",
            expected=sorted(required_provenance_paths),
            actual=sorted(provenance_paths),
        )
    provenance_artifacts_error_start = len(errors)
    for record in provenance_artifacts:
        _release_verify_record_file(
            resolved_root,
            record,
            source="release-provenance.json",
            artifacts=artifacts,
            errors=errors,
        )
    provenance_ok = provenance_ok and len(errors) == provenance_artifacts_error_start
    _release_verify_add_check(
        checks,
        "provenance_artifacts",
        "pass" if provenance_ok else "fail",
        "Provenance artifact records match current files.",
        {"required": sorted(required_provenance_paths), "missing": missing_provenance_paths},
    )

    sbom_ok = isinstance(sbom, dict)
    sbom_packages = sbom.get("packages") if isinstance(sbom, dict) else None
    sbom_package_names = {
        package.get("name")
        for package in sbom_packages
        if isinstance(package, dict) and isinstance(package.get("name"), str)
    } if isinstance(sbom_packages, list) else set()
    missing_sbom_packages = sorted(manifest_artifact_paths - sbom_package_names)
    if "repomori" not in sbom_package_names:
        sbom_ok = False
        _release_verify_error(errors, "sbom_package_missing", "SBOM does not include the main repomori package.", path="sbom.spdx.json")
    if missing_sbom_packages:
        sbom_ok = False
        _release_verify_error(
            errors,
            "sbom_artifact_coverage_missing",
            "SBOM does not list every release artifact from the manifest.",
            path="sbom.spdx.json",
            expected=sorted(manifest_artifact_paths),
            actual=sorted(sbom_package_names),
        )
    _release_verify_add_check(
        checks,
        "sbom_artifacts",
        "pass" if sbom_ok else "fail",
        "SBOM describes the package and release artifacts.",
        {"missing": missing_sbom_packages},
    )

    return _release_verify_report(
        requested_root,
        resolved_root,
        started=started,
        checks=checks,
        artifacts=artifacts,
        warnings=warnings,
        errors=errors,
        manifest=manifest if isinstance(manifest, dict) else None,
        provenance=provenance if isinstance(provenance, dict) else None,
        sbom=sbom if isinstance(sbom, dict) else None,
        checksum_count=len(checksum_entries or {}),
        manifest_artifact_count=len(manifest_artifacts),
        wheel_present=wheel_present,
        source_archive_present=source_present,
    )


def format_release_verify_markdown(report: dict[str, Any]) -> str:
    """Render a compact release package verification report."""

    summary = report.get("summary", {})
    lines = [
        "# RepoMori Release Verification",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Root: `{report.get('resolved_root') or report.get('root')}`",
        f"- Version: `{summary.get('manifest_version')}`",
        f"- Commit: `{summary.get('commit')}`",
        f"- Ref: `{summary.get('ref')}`",
        f"- Run ID: `{summary.get('run_id')}`",
        f"- Checked files: `{summary.get('checked_files', 0)}`",
        f"- Errors: `{summary.get('error_count', 0)}`",
        f"- Warnings: `{summary.get('warning_count', 0)}`",
        "",
        "## Checks",
        "",
    ]
    for check in report.get("checks", []):
        lines.append(f"- `{check.get('id')}` status=`{check.get('status')}`")
        message = check.get("message")
        if message:
            lines.append(f"  - {message}")

    artifacts = report.get("artifacts", [])
    if artifacts:
        lines.extend(["", "## Artifacts", ""])
        lines.append("| Path | Status | Bytes | SHA-256 |")
        lines.append("| --- | --- | ---: | --- |")
        for artifact in artifacts:
            sha = artifact.get("actual_sha256") or artifact.get("expected_sha256") or ""
            lines.append(
                f"| `{artifact.get('path')}` | `{artifact.get('status')}` | "
                f"{artifact.get('actual_bytes', artifact.get('expected_bytes', ''))} | `{sha}` |"
            )

    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        for error in report["errors"]:
            path = f" `{error.get('path')}`" if error.get("path") else ""
            lines.append(f"- `{error.get('code')}`{path} {error.get('message')}")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in report["warnings"]:
            path = f" `{warning.get('path')}`" if warning.get("path") else ""
            lines.append(f"- `{warning.get('code')}`{path} {warning.get('message')}")
    return "\n".join(lines).rstrip() + "\n"


def build_release_evidence(
    package_dir: Path | str,
    *,
    repo: Path | str | None = None,
    release_check: Path | str | None = None,
    release_health: Path | str | None = None,
    out_dir: Path | str | None = None,
    run_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a procurement-friendly release evidence bundle from local artifacts."""

    started = time.time()
    requested_package = Path(package_dir).resolve()
    verify_report = verify_release_package(requested_package)
    resolved_package = Path(verify_report["resolved_root"]).resolve() if verify_report.get("resolved_root") else requested_package
    repo_path = Path(repo).resolve() if repo is not None else None
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    release_check_path = Path(release_check).resolve() if release_check is not None else _release_evidence_default_release_check(resolved_package)
    release_health_path = Path(release_health).resolve() if release_health is not None else _release_evidence_default_release_health(resolved_package)
    release_check_report = _release_evidence_load_json_report(release_check_path, "repomori.release_check.v1", warnings, errors, required=True)
    release_health_report = _release_evidence_load_json_report(release_health_path, "repomori.health.v1", warnings, errors, required=False)

    signature_report = _release_evidence_signature_report(resolved_package)
    artifacts = _release_evidence_artifacts(resolved_package)
    release_verify_artifact = _release_evidence_file_record(resolved_package, resolved_package / "release-verify.json", role="release_verify_report")
    release_verify_markdown = _release_evidence_file_record(resolved_package, resolved_package / "release-verify.md", role="release_verify_markdown")
    if release_verify_artifact is not None and all(item["path"] != release_verify_artifact["path"] for item in artifacts):
        artifacts.append(release_verify_artifact)
    if release_verify_markdown is not None and all(item["path"] != release_verify_markdown["path"] for item in artifacts):
        artifacts.append(release_verify_markdown)
    artifacts.sort(key=lambda item: item["path"])

    manifest = verify_report.get("manifest") if isinstance(verify_report.get("manifest"), dict) else {}
    provenance = verify_report.get("provenance") if isinstance(verify_report.get("provenance"), dict) else {}
    sbom = verify_report.get("sbom") if isinstance(verify_report.get("sbom"), dict) else {}
    release = {
        "version": manifest.get("version") or verify_report.get("summary", {}).get("manifest_version"),
        "commit": manifest.get("commit") or verify_report.get("summary", {}).get("commit"),
        "ref": manifest.get("ref") or verify_report.get("summary", {}).get("ref"),
        "run_id": manifest.get("run_id") or verify_report.get("summary", {}).get("run_id"),
        "repository": provenance.get("repository"),
        "workflow": provenance.get("workflow"),
        "run_url": _release_evidence_run_url(provenance.get("repository"), manifest.get("run_id") or verify_report.get("summary", {}).get("run_id")),
        "license": "LicenseRef-PolyForm-Noncommercial-1.0.0",
    }

    checks = {
        "release_verify": {
            "status": verify_report.get("status"),
            "schema_version": verify_report.get("schema_version"),
            "summary": verify_report.get("summary", {}),
        },
        "release_check": {
            "status": release_check_report.get("status") if isinstance(release_check_report, dict) else "missing",
            "path": str(release_check_path) if release_check_path is not None else None,
            "schema_version": release_check_report.get("schema_version") if isinstance(release_check_report, dict) else None,
            "summary": release_check_report.get("summary", {}) if isinstance(release_check_report, dict) else {},
        },
        "release_health": {
            "status": release_health_report.get("status") if isinstance(release_health_report, dict) else "not_provided",
            "path": str(release_health_path) if release_health_path is not None else None,
            "schema_version": release_health_report.get("schema_version") if isinstance(release_health_report, dict) else None,
            "summary": release_health_report.get("summary", {}) if isinstance(release_health_report, dict) else {},
        },
        "signatures": signature_report,
    }
    status = _release_evidence_status(checks, errors, warnings)
    output_dir_path = Path(out_dir).resolve() if out_dir is not None else None
    report = {
        "schema_version": "repomori.release_evidence.v1",
        "status": status,
        "created_at": int(started),
        "package_dir": str(requested_package),
        "resolved_package_dir": str(resolved_package) if resolved_package.exists() else None,
        "repo_path": str(repo_path) if repo_path is not None else None,
        "settings": {
            "release_check": str(release_check_path) if release_check_path is not None else None,
            "release_health": str(release_health_path) if release_health_path is not None else None,
            "out_dir": str(output_dir_path) if output_dir_path is not None else None,
        },
        "summary": {
            "elapsed_seconds": round(time.time() - started, 4),
            "version": release.get("version"),
            "commit": release.get("commit"),
            "ref": release.get("ref"),
            "run_id": release.get("run_id"),
            "release_verify_status": checks["release_verify"]["status"],
            "release_check_status": checks["release_check"]["status"],
            "release_health_status": checks["release_health"]["status"],
            "signature_status": signature_report["status"],
            "artifact_count": len(artifacts),
            "warning_count": len(warnings) + len(signature_report.get("warnings", [])),
            "error_count": len(errors),
        },
        "release": release,
        "checks": checks,
        "artifacts": artifacts,
        "reports": {
            "release_verify": verify_report,
            "release_check": release_check_report,
            "release_health": release_health_report,
        },
        "run_meta": run_meta or {},
        "outputs": {
            "json": str(output_dir_path / _RELEASE_EVIDENCE_ARTIFACT_REPORT) if output_dir_path is not None else None,
            "markdown": str(output_dir_path / _RELEASE_EVIDENCE_ARTIFACT_MARKDOWN) if output_dir_path is not None else None,
        },
        "warnings": [*warnings, *signature_report.get("warnings", [])],
        "errors": errors,
    }
    if output_dir_path is not None:
        output_dir_path.mkdir(parents=True, exist_ok=True)
        _write_json(output_dir_path / _RELEASE_EVIDENCE_ARTIFACT_REPORT, report)
        (output_dir_path / _RELEASE_EVIDENCE_ARTIFACT_MARKDOWN).write_text(
            format_release_evidence_markdown(report),
            encoding="utf-8",
        )
    return report


def format_release_evidence_markdown(report: dict[str, Any]) -> str:
    """Render a compact release evidence report."""

    summary = report.get("summary", {})
    release = report.get("release", {})
    checks = report.get("checks", {})
    lines = [
        "# RepoMori Release Evidence",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Version: `{summary.get('version')}`",
        f"- Commit: `{summary.get('commit')}`",
        f"- Ref: `{summary.get('ref')}`",
        f"- Run ID: `{summary.get('run_id')}`",
        f"- Workflow: `{release.get('workflow')}`",
        f"- Repository: `{release.get('repository')}`",
        f"- Run URL: `{release.get('run_url')}`",
        f"- Release verification: `{summary.get('release_verify_status')}`",
        f"- Release check: `{summary.get('release_check_status')}`",
        f"- Release health: `{summary.get('release_health_status')}`",
        f"- Signatures: `{summary.get('signature_status')}`",
        "",
        "## Checks",
        "",
    ]
    for name in ("release_verify", "release_check", "release_health"):
        check = checks.get(name, {})
        lines.append(f"- `{name}` status=`{check.get('status')}` schema=`{check.get('schema_version')}`")
    signatures = checks.get("signatures", {})
    lines.append(
        f"- `signatures` status=`{signatures.get('status')}` "
        f"signed=`{signatures.get('signed_count')}` expected=`{signatures.get('expected_count')}` "
        f"public_key=`{signatures.get('public_key_status')}`"
    )

    artifacts = report.get("artifacts", [])
    if artifacts:
        lines.extend(["", "## Artifacts", ""])
        lines.append("| Path | Role | Bytes | SHA-256 |")
        lines.append("| --- | --- | ---: | --- |")
        for artifact in artifacts:
            lines.append(
                f"| `{artifact.get('path')}` | `{artifact.get('role')}` | "
                f"{artifact.get('bytes')} | `{artifact.get('sha256')}` |"
            )
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for item in report.get("warnings", []):
            path = f" `{item.get('path')}`" if item.get("path") else ""
            lines.append(f"- `{item.get('code')}`{path} {item.get('message')}")
    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        for item in report.get("errors", []):
            path = f" `{item.get('path')}`" if item.get("path") else ""
            lines.append(f"- `{item.get('code')}`{path} {item.get('message')}")
    return "\n".join(lines).rstrip() + "\n"


def _release_evidence_default_release_check(package_root: Path) -> Path | None:
    candidates = (
        package_root.parent / ".repomori-release-check" / _RELEASE_CHECK_ARTIFACT_REPORT,
        package_root / _RELEASE_CHECK_ARTIFACT_REPORT,
        package_root / "release-check" / _RELEASE_CHECK_ARTIFACT_REPORT,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _release_evidence_default_release_health(package_root: Path) -> Path | None:
    candidates = (
        package_root.parent / ".repomori-health" / _RELEASE_HEALTH_ARTIFACT_REPORT,
        package_root.parent / ".repomori-release-health" / _RELEASE_HEALTH_ARTIFACT_REPORT,
        package_root / _RELEASE_HEALTH_ARTIFACT_REPORT,
        package_root / "release-health" / _RELEASE_HEALTH_ARTIFACT_REPORT,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _release_evidence_load_json_report(
    path: Path | None,
    expected_schema: str,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    *,
    required: bool,
) -> dict[str, Any] | None:
    if path is None:
        if not required:
            return None
        target = errors if required else warnings
        target.append(
            {
                "code": "report_missing",
                "schema_version": expected_schema,
                "message": f"{expected_schema} report was not supplied or discoverable.",
            }
        )
        return None
    if not path.is_file():
        target = errors if required else warnings
        target.append(
            {
                "code": "report_not_found",
                "schema_version": expected_schema,
                "path": str(path),
                "message": f"{expected_schema} report file was not found.",
            }
        )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(
            {
                "code": "report_json_invalid",
                "schema_version": expected_schema,
                "path": str(path),
                "message": f"Could not parse report JSON: {exc}",
            }
        )
        return None
    if not isinstance(payload, dict):
        errors.append(
            {
                "code": "report_json_not_object",
                "schema_version": expected_schema,
                "path": str(path),
                "message": "Report JSON must contain an object.",
            }
        )
        return None
    if payload.get("schema_version") != expected_schema:
        errors.append(
            {
                "code": "report_schema_mismatch",
                "path": str(path),
                "message": "Report schema_version did not match expected value.",
                "expected": expected_schema,
                "actual": payload.get("schema_version"),
            }
        )
    return payload


def _release_evidence_signature_report(package_root: Path) -> dict[str, Any]:
    expected_targets = (
        "checksums.txt",
        "release-provenance.json",
        "sbom.spdx.json",
        "release-verify.json",
    )
    signatures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for target in expected_targets:
        signature_path = package_root / f"{target}.asc"
        record = {
            "target": target,
            "signature": f"{target}.asc",
            "present": signature_path.is_file(),
            "bytes": signature_path.stat().st_size if signature_path.is_file() else 0,
            "sha256": _path_sha256(signature_path) if signature_path.is_file() else None,
        }
        signatures.append(record)
    signed_count = sum(1 for item in signatures if item["present"])
    expected_count = len(expected_targets)
    if signed_count == 0:
        status = "unsigned"
    elif signed_count == expected_count:
        status = "signed"
    else:
        status = "partial"
        warnings.append(
            {
                "code": "signature_set_partial",
                "message": "Some release signature files are present, but the expected set is incomplete.",
            }
        )
    public_key = package_root / "repomori-release-public-key.asc"
    public_key_record = _release_evidence_file_record(package_root, public_key, role="public_key")
    if status == "signed" and public_key_record is None:
        warnings.append(
            {
                "code": "public_key_missing",
                "message": "Release is signed, but repomori-release-public-key.asc is not present in the package.",
            }
        )
    return {
        "status": status,
        "expected_count": expected_count,
        "signed_count": signed_count,
        "public_key_status": "present" if public_key_record is not None else "missing",
        "public_key": public_key_record,
        "signatures": signatures,
        "warnings": warnings,
        "note": "Signature presence is checked locally; trust still depends on verifying the public key fingerprint independently.",
    }


def _release_evidence_artifacts(package_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {_RELEASE_EVIDENCE_ARTIFACT_REPORT, _RELEASE_EVIDENCE_ARTIFACT_MARKDOWN}:
            continue
        record = _release_evidence_file_record(package_root, path, role=_release_evidence_artifact_role(package_root, path))
        if record is not None:
            records.append(record)
    return records


def _release_evidence_file_record(root: Path, path: Path, *, role: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = str(path.resolve())
    return {
        "path": relative,
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": _path_sha256(path),
    }


def _release_evidence_artifact_role(root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix()
    name = path.name
    if relative.startswith("dist/") and name.endswith(".whl"):
        return "wheel"
    if relative.startswith("dist/") and name.endswith(".zip"):
        return "source_archive"
    if name == "checksums.txt":
        return "checksums"
    if name == "release-provenance.json":
        return "provenance"
    if name == "sbom.spdx.json":
        return "sbom"
    if name == "release-candidate.json":
        return "manifest"
    if name == "release-candidate.md":
        return "manifest_markdown"
    if name == "release-verify.json":
        return "release_verify_report"
    if name == "release-verify.md":
        return "release_verify_markdown"
    if name == "repomori-release-public-key.asc":
        return "public_key"
    if name.endswith(".asc"):
        return "signature"
    return "release_artifact"


def _release_evidence_run_url(repository: Any, run_id: Any) -> str | None:
    if not repository or not run_id:
        return None
    return f"https://github.com/{repository}/actions/runs/{run_id}"


def _release_evidence_status(
    checks: dict[str, Any],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> str:
    if errors:
        return "fail"
    release_verify_status = checks.get("release_verify", {}).get("status")
    release_check_status = checks.get("release_check", {}).get("status")
    release_health_status = checks.get("release_health", {}).get("status")
    signature_status = checks.get("signatures", {}).get("status")
    signature_warnings = checks.get("signatures", {}).get("warnings", [])
    if release_verify_status == "fail" or release_check_status == "fail" or release_health_status == "fail":
        return "fail"
    if release_verify_status != "pass" or release_check_status != "pass":
        return "warn"
    if release_health_status == "warn" or signature_status == "partial" or warnings or signature_warnings:
        return "warn"
    return "pass"


def _resolve_release_package_root(
    requested_root: Path,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> Path | None:
    if requested_root.is_file() and requested_root.name == "release-candidate.json":
        return requested_root.parent.resolve()
    if requested_root.is_dir() and (requested_root / "release-candidate.json").is_file():
        return requested_root.resolve()
    if not requested_root.exists():
        _release_verify_error(
            errors,
            "package_root_missing",
            f"Release package path does not exist: {requested_root}",
            path=str(requested_root),
        )
        return None
    if not requested_root.is_dir():
        _release_verify_error(
            errors,
            "package_root_not_directory",
            f"Release package path is not a directory: {requested_root}",
            path=str(requested_root),
        )
        return None

    candidates = sorted(path.parent.resolve() for path in requested_root.rglob("release-candidate.json") if path.is_file())
    if len(candidates) == 1:
        warnings.append(
            {
                "code": "package_root_resolved_nested",
                "path": str(candidates[0]),
                "message": "Resolved a nested release package root from the supplied parent directory.",
            }
        )
        return candidates[0]
    if not candidates:
        _release_verify_error(
            errors,
            "manifest_not_found",
            "No release-candidate.json was found in the supplied package path.",
            path=str(requested_root),
        )
        return None
    _release_verify_error(
        errors,
        "package_root_ambiguous",
        "Multiple release-candidate.json files were found; point verify-release at one package root.",
        path=str(requested_root),
        actual=[str(path) for path in candidates],
    )
    return None


def _release_verify_load_json(path: Path, errors: list[dict[str, Any]], scope: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _release_verify_error(
            errors,
            f"{scope}_json_invalid",
            f"{path.name} could not be parsed as JSON: {exc}",
            path=path.name,
        )
        return None
    if not isinstance(payload, dict):
        _release_verify_error(
            errors,
            f"{scope}_json_not_object",
            f"{path.name} must contain a JSON object.",
            path=path.name,
        )
        return None
    return payload


def _release_verify_parse_checksums(root: Path, errors: list[dict[str, Any]]) -> dict[str, str] | None:
    checksums_path = root / "checksums.txt"
    if not checksums_path.is_file():
        return None
    entries: dict[str, str] = {}
    try:
        lines = checksums_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        _release_verify_error(errors, "checksums_read_failed", f"Could not read checksums.txt: {exc}", path="checksums.txt")
        return None

    ok = True
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if "  " not in line:
            ok = False
            _release_verify_error(
                errors,
                "checksum_line_invalid",
                f"checksums.txt line {lineno} is not '<sha256>  <path>'.",
                path="checksums.txt",
                actual=line,
            )
            continue
        digest, relative = line.split("  ", 1)
        digest = digest.strip().lower()
        relative = relative.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            ok = False
            _release_verify_error(
                errors,
                "checksum_digest_invalid",
                f"checksums.txt line {lineno} does not contain a valid SHA-256 digest.",
                path=relative or "checksums.txt",
                actual=digest,
            )
            continue
        safe_relative = _release_verify_normalize_relative(relative, errors, "checksums.txt")
        if safe_relative is None:
            ok = False
            continue
        if safe_relative in entries:
            ok = False
            _release_verify_error(
                errors,
                "checksum_path_duplicate",
                f"checksums.txt lists a path more than once: {safe_relative}",
                path=safe_relative,
            )
            continue
        entries[safe_relative] = digest
    return entries if ok else None


def _release_verify_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _release_verify_record_file(
    root: Path,
    record: dict[str, Any],
    *,
    source: str,
    artifacts: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    relative = _release_verify_normalize_relative(record.get("path"), errors, source)
    if relative is None:
        return
    artifact = artifacts.setdefault(
        relative,
        {
            "path": relative,
            "status": "pass",
            "sources": [],
            "expected_sha256": None,
            "actual_sha256": None,
            "expected_bytes": None,
            "actual_bytes": None,
        },
    )
    if source not in artifact["sources"]:
        artifact["sources"].append(source)

    expected_sha = record.get("sha256")
    if expected_sha is not None:
        expected_sha = str(expected_sha).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            artifact["status"] = "fail"
            _release_verify_error(
                errors,
                "artifact_hash_invalid",
                f"{source} records an invalid SHA-256 digest for {relative}.",
                path=relative,
                actual=record.get("sha256"),
            )
        elif artifact["expected_sha256"] is None:
            artifact["expected_sha256"] = expected_sha
        elif artifact["expected_sha256"] != expected_sha:
            artifact["status"] = "fail"
            _release_verify_error(
                errors,
                "artifact_expected_hash_conflict",
                f"Release records disagree about the expected SHA-256 for {relative}.",
                path=relative,
                expected=artifact["expected_sha256"],
                actual=expected_sha,
            )

    expected_bytes = record.get("bytes")
    if isinstance(expected_bytes, int) and expected_bytes >= 0:
        if artifact["expected_bytes"] is None:
            artifact["expected_bytes"] = expected_bytes
        elif artifact["expected_bytes"] != expected_bytes:
            artifact["status"] = "fail"
            _release_verify_error(
                errors,
                "artifact_expected_size_conflict",
                f"Release records disagree about the expected byte size for {relative}.",
                path=relative,
                expected=artifact["expected_bytes"],
                actual=expected_bytes,
            )

    path = _release_verify_abs_path(root, relative)
    if not path.is_file():
        artifact["status"] = "fail"
        _release_verify_error(errors, "artifact_file_missing", f"Release artifact is missing: {relative}", path=relative)
        return

    actual_bytes = path.stat().st_size
    actual_sha = _path_sha256(path)
    artifact["actual_bytes"] = actual_bytes
    artifact["actual_sha256"] = actual_sha
    if artifact["expected_bytes"] is not None and artifact["expected_bytes"] != actual_bytes:
        artifact["status"] = "fail"
        _release_verify_error(
            errors,
            "artifact_size_mismatch",
            f"Release artifact byte size does not match: {relative}",
            path=relative,
            expected=artifact["expected_bytes"],
            actual=actual_bytes,
        )
    if artifact["expected_sha256"] is not None and artifact["expected_sha256"] != actual_sha:
        artifact["status"] = "fail"
        _release_verify_error(
            errors,
            "artifact_hash_mismatch",
            f"Release artifact SHA-256 does not match: {relative}",
            path=relative,
            expected=artifact["expected_sha256"],
            actual=actual_sha,
        )


def _release_verify_normalize_relative(
    value: Any,
    errors: list[dict[str, Any]],
    source: str,
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        _release_verify_error(errors, "artifact_path_invalid", f"{source} contains an empty artifact path.", actual=value)
        return None
    relative = value.strip()
    if "\\" in relative or "\x00" in relative or re.match(r"^[A-Za-z]:", relative) or relative.startswith("/"):
        _release_verify_error(
            errors,
            "artifact_path_unsafe",
            f"{source} contains an unsafe artifact path.",
            path=relative,
        )
        return None
    posix = PurePosixPath(relative)
    if any(part in {"", ".", ".."} for part in posix.parts):
        _release_verify_error(
            errors,
            "artifact_path_unsafe",
            f"{source} contains an unsafe artifact path.",
            path=relative,
        )
        return None
    return posix.as_posix()


def _release_verify_abs_path(root: Path, relative: str) -> Path:
    return root.joinpath(*PurePosixPath(relative).parts)


def _release_verify_add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": status,
            "message": message,
            "details": details or {},
        }
    )


def _release_verify_error(
    errors: list[dict[str, Any]],
    code: str,
    message: str,
    *,
    path: str | None = None,
    expected: Any = None,
    actual: Any = None,
) -> None:
    error: dict[str, Any] = {"code": code, "message": message}
    if path is not None:
        error["path"] = path
    if expected is not None:
        error["expected"] = expected
    if actual is not None:
        error["actual"] = actual
    errors.append(error)


def _release_verify_report(
    requested_root: Path,
    resolved_root: Path | None,
    *,
    started: float,
    checks: list[dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    manifest: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    sbom: dict[str, Any] | None = None,
    checksum_count: int = 0,
    manifest_artifact_count: int = 0,
    wheel_present: bool = False,
    source_archive_present: bool = False,
) -> dict[str, Any]:
    artifact_list = sorted(artifacts.values(), key=lambda item: item["path"])
    failed_artifacts = [item for item in artifact_list if item.get("status") == "fail"]
    missing_files = sum(1 for item in artifact_list if item.get("actual_sha256") is None)
    mismatched_files = sum(
        1
        for item in artifact_list
        if item.get("actual_sha256") is not None
        and item.get("expected_sha256") is not None
        and item.get("actual_sha256") != item.get("expected_sha256")
    )
    status = "fail" if errors or any(check.get("status") == "fail" for check in checks) else "pass"
    return {
        "schema_version": "repomori.release_verify.v1",
        "status": status,
        "root": str(requested_root),
        "resolved_root": str(resolved_root) if resolved_root is not None else None,
        "summary": {
            "elapsed_seconds": round(time.time() - started, 4),
            "checked_files": len(artifact_list),
            "failed_files": len(failed_artifacts),
            "missing_files": missing_files,
            "mismatched_files": mismatched_files,
            "checksum_count": checksum_count,
            "artifact_count": manifest_artifact_count,
            "manifest_version": manifest.get("version") if isinstance(manifest, dict) else None,
            "provenance_version": provenance.get("version") if isinstance(provenance, dict) else None,
            "sbom_version": sbom.get("spdxVersion") if isinstance(sbom, dict) else None,
            "commit": manifest.get("commit") if isinstance(manifest, dict) else None,
            "ref": manifest.get("ref") if isinstance(manifest, dict) else None,
            "run_id": manifest.get("run_id") if isinstance(manifest, dict) else None,
            "wheel_present": wheel_present,
            "source_archive_present": source_archive_present,
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "checks": checks,
        "artifacts": artifact_list,
        "manifest": _release_verify_manifest_summary(manifest),
        "provenance": _release_verify_provenance_summary(provenance),
        "sbom": _release_verify_sbom_summary(sbom),
        "errors": errors,
        "warnings": warnings,
    }


def _release_verify_manifest_summary(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        return None
    return {
        "schema_version": manifest.get("schema_version"),
        "status": manifest.get("status"),
        "version": manifest.get("version"),
        "commit": manifest.get("commit"),
        "ref": manifest.get("ref"),
        "run_id": manifest.get("run_id"),
        "artifact_count": len(_release_verify_records(manifest.get("artifacts"))),
        "integrity_paths": {
            key: value.get("path")
            for key, value in (manifest.get("integrity") or {}).items()
            if isinstance(value, dict)
        } if isinstance(manifest.get("integrity"), dict) else {},
    }


def _release_verify_provenance_summary(provenance: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(provenance, dict):
        return None
    return {
        "schema_version": provenance.get("schema_version"),
        "status": provenance.get("status"),
        "version": provenance.get("version"),
        "repository": provenance.get("repository"),
        "workflow": provenance.get("workflow"),
        "run_id": provenance.get("run_id"),
        "artifact_count": len(_release_verify_records(provenance.get("artifacts"))),
    }


def _release_verify_sbom_summary(sbom: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(sbom, dict):
        return None
    packages = sbom.get("packages")
    relationships = sbom.get("relationships")
    return {
        "spdxVersion": sbom.get("spdxVersion"),
        "name": sbom.get("name"),
        "package_count": len(packages) if isinstance(packages, list) else 0,
        "relationship_count": len(relationships) if isinstance(relationships, list) else 0,
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
        f"- Handoff scores pass/warn/fail: `{summary.get('handoff_score_pass_count')}` / `{summary.get('handoff_score_warn_count')}` / `{summary.get('handoff_score_fail_count')}`",
        f"- Handoff triage pass/warn/fail: `{summary.get('handoff_triage_pass_count')}` / `{summary.get('handoff_triage_warn_count')}` / `{summary.get('handoff_triage_fail_count')}`",
        f"- Total added: `{summary.get('total_added')}`",
        f"- Total removed: `{summary.get('total_removed')}`",
        f"- Total changed: `{summary.get('total_changed')}`",
        f"- Incremental snapshots: `{summary.get('incremental_snapshot_count')}`",
        f"- Reused files: `{summary.get('total_reused_files')}`",
        f"- Rebuilt files: `{summary.get('total_rebuilt_files')}`",
        f"- Reused chunks: `{summary.get('total_reused_chunks')}`",
        f"- Chain status: `{summary.get('chain_status')}`",
        f"- Chain head: `{summary.get('chain_head_hash')}`",
        f"- Chain checked: `{summary.get('chain_checked_count')}`",
        f"- Chain anchored to pruned history: `{summary.get('chain_anchored_to_pruned_history')}`",
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
    tokens = _query_tokens(question)
    terms = _expanded_query_terms(tokens)
    candidates: list[tuple[int, int, str, int]] = []
    order = 0

    def add(priority: int, line: int, matched: str) -> None:
        nonlocal order
        order += 1
        candidates.append((priority, line, matched, order))

    summary = result.get("summary", {})
    field_priorities = {"symbols": 0, "headings": 1, "imports": 2}
    for field in ("symbols", "headings", "imports"):
        for item in summary.get(field, []):
            line = int(item.get("line", 0) or 0)
            if line <= 0:
                continue
            label = str(item.get("name") or item.get("text") or item.get("target") or field)
            haystack = " ".join(
                str(item.get(key, ""))
                for key in ("kind", "name", "text", "target", "signature")
                if item.get(key)
            )
            matches = _matching_query_terms(haystack or label, terms)
            if matches:
                add(field_priorities[field], line, f"{field}:{label} query:{_anchor_term_label(matches[0])}")
            else:
                add(6 + field_priorities[field], line, f"{field}:{label}")

    for index, line in enumerate(lines, start=1):
        matches = _matching_query_terms(line, terms)
        if not matches:
            continue
        priority = 3
        stripped = line.lstrip()
        if stripped.startswith(("#", "import ", "from ", "class ", "def ")):
            priority = 2
        add(priority, index, f"query:{_anchor_term_label(matches[0])}")

    if not candidates:
        for index, line in enumerate(lines, start=1):
            if line.strip():
                add(9, index, "fallback:first-useful-line")
                break
    anchors = [(line, matched) for priority, line, matched, order in sorted(candidates)]
    return _dedupe_anchors(anchors, len(lines))


def _matching_query_terms(value: str, terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lowered = value.lower()
    value_terms = set(_query_tokens(value))
    matches = []
    for term in terms:
        token = str(term.get("token", ""))
        if token and (token in lowered or token in value_terms):
            matches.append(term)
    return matches


def _anchor_term_label(term: dict[str, Any]) -> str:
    token = str(term.get("query_token") or term.get("token") or "")
    matched = str(term.get("token") or "")
    kind = str(term.get("kind") or "query")
    if kind == "query" or matched == token:
        return token
    return f"{token}->{matched}"


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
        "match_reasons": source.get("match_reasons", source.get("why", [])),
        "matched_terms": source.get("matched_terms", []),
        "snippet_status": source.get("snippet_status"),
        "snippet_count": len(source.get("snippets", [])),
        "source_bytes": source.get("source_bytes", 0),
    }


def _normalize_context_eval_case(raw: dict[str, Any] | str, index: int) -> dict[str, Any]:
    if isinstance(raw, str):
        item: dict[str, Any] = {"question": raw}
    elif isinstance(raw, dict):
        item = dict(raw)
    else:
        raise TypeError("context eval cases must be objects or strings")

    question = str(item.get("question") or item.get("q") or "").strip()
    if not question:
        raise ValueError(f"context eval case {index} is missing a question")

    expected_paths = _context_eval_list(item.get("expected_paths", item.get("expected_path")))
    required_snippets = _context_eval_list(item.get("required_snippets", item.get("expected_snippets")))
    required_terms = _context_eval_list(item.get("required_terms", item.get("expected_terms")))
    max_rank = _optional_positive_int(item.get("max_rank"))
    min_top_score = _optional_float(item.get("min_top_score"))
    min_snippets = _optional_positive_int(item.get("min_snippets"))

    return {
        "id": str(item.get("id") or f"case-{index}"),
        "question": question,
        "expectations": {
            "expected_paths": [_normalize_repo_path(path) for path in expected_paths],
            "required_snippets": required_snippets,
            "required_terms": [term.lower() for term in required_terms],
            "max_rank": max_rank,
            "min_top_score": min_top_score,
            "min_snippets": min_snippets,
        },
    }


def _context_eval_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _optional_positive_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("context eval integer thresholds must be greater than zero")
    return parsed


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _context_eval_result_summary(bundle: dict[str, Any]) -> dict[str, Any]:
    sources = bundle.get("sources", [])
    selected_paths = [str(source.get("path")) for source in sources]
    matched_terms = sorted(
        {
            str(term).lower()
            for source in sources
            for term in source.get("matched_terms", [])
            if str(term).strip()
        }
    )
    snippet_count = sum(len(source.get("snippets", [])) for source in sources)
    source_bytes = int(bundle.get("selection", {}).get("source_bytes") or 0)
    top = sources[0] if sources else {}
    return {
        "selected_count": len(sources),
        "selected_paths": selected_paths,
        "top_path": top.get("path"),
        "top_score": top.get("score"),
        "matched_terms": matched_terms,
        "snippet_count": snippet_count,
        "source_bytes": source_bytes,
    }


def _context_eval_checks(case: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    expectations = case["expectations"]
    result = _context_eval_result_summary(bundle)
    sources = bundle.get("sources", [])
    selected_paths = result["selected_paths"]
    path_ranks = {path: index for index, path in enumerate(selected_paths, start=1)}
    snippet_text = "\n".join(
        str(snippet.get("text", ""))
        for source in sources
        for snippet in source.get("snippets", [])
    )
    snippet_text_lower = snippet_text.lower()
    matched_terms = set(result["matched_terms"])
    checks = [
        _context_eval_check(
            "sources_selected",
            bool(sources),
            "At least one source was selected.",
            actual=result["selected_count"],
        )
    ]

    max_rank = expectations.get("max_rank")
    for path in expectations.get("expected_paths", []):
        rank = path_ranks.get(path)
        ok = rank is not None and (max_rank is None or rank <= max_rank)
        checks.append(
            _context_eval_check(
                f"expected_path:{path}",
                ok,
                f"Expected `{path}` to be selected" + (f" by rank {max_rank}." if max_rank else "."),
                expected=path,
                actual=selected_paths,
                rank=rank,
            )
        )

    for text in expectations.get("required_snippets", []):
        ok = text.lower() in snippet_text_lower
        checks.append(
            _context_eval_check(
                f"required_snippet:{_trace_value(text, 48)}",
                ok,
                f"Expected snippet text containing `{_trace_value(text, 80)}`.",
                expected=text,
                actual="present" if ok else "missing",
            )
        )

    for term in expectations.get("required_terms", []):
        ok = term in matched_terms
        checks.append(
            _context_eval_check(
                f"required_term:{term}",
                ok,
                f"Expected matched term `{term}`.",
                expected=term,
                actual=sorted(matched_terms),
            )
        )

    min_top_score = expectations.get("min_top_score")
    if min_top_score is not None:
        top_score = float(result.get("top_score") or 0.0)
        checks.append(
            _context_eval_check(
                "min_top_score",
                top_score >= min_top_score,
                f"Expected top score >= {min_top_score}.",
                expected=min_top_score,
                actual=top_score,
            )
        )

    min_snippets = expectations.get("min_snippets")
    if min_snippets is not None:
        snippet_count = int(result.get("snippet_count") or 0)
        checks.append(
            _context_eval_check(
                "min_snippets",
                snippet_count >= min_snippets,
                f"Expected at least {min_snippets} snippets.",
                expected=min_snippets,
                actual=snippet_count,
            )
        )
    return checks


def _context_eval_check(
    check_id: str,
    ok: bool,
    message: str,
    *,
    expected: Any = None,
    actual: Any = None,
    rank: int | None = None,
) -> dict[str, Any]:
    row = {
        "id": check_id,
        "status": "pass" if ok else "fail",
        "message": message,
        "expected": expected,
        "actual": actual,
    }
    if rank is not None:
        row["rank"] = rank
    return row


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
        "Artifacts to inspect:\n\n"
        "- `inspect.md` / `inspect.json`: pack contents, storage, vocabulary, and verification.\n"
        "- `context.md`: source-backed context for the demo question.\n"
        "- `demo.json`: complete machine-readable demo report.\n\n"
        "Try these next:\n\n"
        "```powershell\n"
        f"python -m repomori query {pack} \"{question}\" --json\n"
        f"python -m repomori inspect {pack} --verify --out {out_dir}\\inspect.md\n"
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


def _compat_add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": status,
            "message": message,
            "details": details or {},
        }
    )


def _compat_latest_pack(snapshot_dir: Path | str | None) -> Path | None:
    if snapshot_dir is None:
        return None
    try:
        timeline = read_snapshot_timeline(snapshot_dir, limit=1)
    except Exception:
        return None
    latest = timeline.get("latest")
    if not isinstance(latest, dict):
        return None
    pack_path = latest.get("pack_path")
    if not isinstance(pack_path, str) or not pack_path:
        return None
    return Path(pack_path).resolve()


def _compat_required_schemas() -> set[str]:
    return {
        SCHEMA_VERSION,
        "repomori.verify.v1",
        "repomori.context.v1",
        "repomori.diff_context.v1",
        "repomori.brief.v1",
        "repomori.agent_brief.v1",
        "repomori.capsule.v1",
        "repomori.eval.v1",
        "repomori.context_eval.v1",
        "repomori.handoff.v1",
        "repomori.handoff_score.v1",
        "repomori.handoff_triage.v1",
        "repomori.handoff_quality.v1",
        "repomori.handoff_health.v1",
        "repomori.handoff_health_record.v1",
        "repomori.handoff_health_summary.v1",
        "repomori.compat.v1",
        "repomori.contract_check.v1",
        "repomori.cli_commands.v1",
        "repomori.memory.v1",
        "repomori.health.v1",
        "repomori.schema.catalog.v1",
    }


def _compat_required_agent_methods() -> set[str]:
    return {
        "schema.list",
        "compat.check",
        "memory.run",
        "timeline.read",
        "query.run",
        "context.build",
        "handoff.build",
        "handoff.health",
        "capsule.build",
        "file.get",
    }


def _compat_required_mcp_tools() -> set[str]:
    return {
        "repomori_schema_list",
        "repomori_compat_check",
        "repomori_memory_run",
        "repomori_timeline_read",
        "repomori_query_run",
        "repomori_context_build",
        "repomori_handoff_build",
        "repomori_handoff_health",
        "repomori_capsule_build",
        "repomori_file_get",
    }


def _compat_full_check_ids() -> list[str]:
    return [
        "pack_schema",
        "pack_verification",
        "handoff_integrity",
        "handoff_schemas",
        "schema_catalog",
        "agent_methods",
        "mcp_tools",
    ]


def _release_health_contract_artifacts() -> list[str]:
    return [
        _RELEASE_HEALTH_COMPAT_ARTIFACT_REPORT,
        _RELEASE_HEALTH_COMPAT_ARTIFACT_MARKDOWN,
        _RELEASE_HEALTH_CONTRACT_ARTIFACT_REPORT,
        _RELEASE_HEALTH_CONTRACT_ARTIFACT_MARKDOWN,
    ]


def _current_contract_snapshot() -> dict[str, Any]:
    catalog = schema_catalog()
    return {
        "schema_versions": sorted(item["schema_version"] for item in catalog.get("schemas", [])),
        "agent_methods": list(catalog.get("agent_methods", [])),
        "mcp_tools": list(catalog.get("mcp_tools", [])),
        "full_compat_check_ids": _compat_full_check_ids(),
        "release_health_compat_artifacts": _release_health_contract_artifacts(),
    }


def _contract_sequence_diff(expected_value: Any, actual_value: Any) -> dict[str, Any]:
    expected = [str(item) for item in expected_value] if isinstance(expected_value, list) else []
    actual = [str(item) for item in actual_value] if isinstance(actual_value, list) else []
    expected_set = set(expected)
    actual_set = set(actual)
    added = [item for item in actual if item not in expected_set]
    removed = [item for item in expected if item not in actual_set]
    order_changed = not added and not removed and expected != actual
    return {
        "status": "pass" if not added and not removed and not order_changed else "fail",
        "expected_count": len(expected),
        "actual_count": len(actual),
        "added": added,
        "removed": removed,
        "order_changed": order_changed,
        "expected": expected,
        "actual": actual,
        "change_count": len(added) + len(removed) + (1 if order_changed else 0),
    }


def _contract_check_report(
    fixture_path: Path | None,
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
    diffs: dict[str, Any],
    started: float,
    status: str,
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    skipped: bool,
) -> dict[str, Any]:
    added_count = sum(len(diff.get("added", [])) for diff in diffs.values())
    removed_count = sum(len(diff.get("removed", [])) for diff in diffs.values())
    order_change_count = sum(1 for diff in diffs.values() if diff.get("order_changed"))
    change_count = added_count + removed_count + order_change_count
    guidance = []
    if skipped:
        guidance.append("No fixture was supplied; provide --fixture to compare public contracts.")
    elif change_count:
        guidance.append("Review added/removed contract names and update code, docs, tests, and fixture together when intentional.")
        guidance.append("Restore removed names or add aliases when compatibility should be preserved.")
    else:
        guidance.append("Contract fixture matches current schema, agent, MCP, and artifact contracts.")
    return {
        "schema_version": "repomori.contract_check.v1",
        "status": status,
        "created_at": int(started),
        "fixture_path": str(fixture_path) if fixture_path is not None else None,
        "summary": {
            "elapsed_seconds": round(time.time() - started, 4),
            "skipped": skipped,
            "change_count": change_count,
            "added_count": added_count,
            "removed_count": removed_count,
            "order_change_count": order_change_count,
            "warning_count": len(warnings),
            "error_count": len(errors),
        },
        "expected": {
            key: expected.get(key)
            for key in (
                "schema_versions",
                "agent_methods",
                "mcp_tools",
                "full_compat_check_ids",
                "release_health_compat_artifacts",
            )
        },
        "actual": actual,
        "diffs": diffs,
        "guidance": guidance,
        "warnings": warnings,
        "errors": errors,
    }


def _default_contract_fixture(repo_path: Path) -> Path | None:
    fixture = repo_path / "tests" / "fixtures" / "compat-contracts.json"
    return fixture if fixture.is_file() else None


def _compat_handoff_schema_checks(root: Path) -> list[dict[str, Any]]:
    core_expected = {
        "manifest.json": "repomori.handoff.v1",
        "brief.json": "repomori.brief.v1",
        "context.json": "repomori.context.v1",
        "capsule.json": "repomori.capsule.v1",
        "eval.json": "repomori.eval.v1",
        "verify.json": "repomori.verify.v1",
    }
    optional_expected = {
        "compare.json": "repomori.compare.v1",
        "inspect-diff.json": "repomori.inspect_diff.v1",
        "handoff-score.json": "repomori.handoff_score.v1",
        "handoff-triage.json": "repomori.handoff_triage.v1",
        "handoff-quality.json": "repomori.handoff_quality.v1",
        "handoff-health.json": "repomori.handoff_health.v1",
        "handoff-improvement.json": "repomori.handoff_improvement.v1",
    }
    results = []
    for relative, expected in core_expected.items():
        results.append(_compat_json_schema_check(root, relative, expected, required=True))
    for relative, expected in optional_expected.items():
        path = root / relative
        if path.exists():
            results.append(_compat_json_schema_check(root, relative, expected, required=False))
    return results


def _compat_json_schema_check(root: Path, relative: str, expected: str, *, required: bool) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        return {
            "path": relative,
            "status": "fail" if required else "warn",
            "expected": expected,
            "actual": None,
            "message": "Required JSON artifact is missing." if required else "Optional JSON artifact is missing.",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": relative,
            "status": "fail",
            "expected": expected,
            "actual": None,
            "message": f"Could not parse JSON artifact: {exc}",
        }
    actual = payload.get("schema_version") if isinstance(payload, dict) else None
    return {
        "path": relative,
        "status": "pass" if actual == expected else "fail",
        "expected": expected,
        "actual": actual,
        "message": "Schema version matches." if actual == expected else "Schema version mismatch.",
    }


def _artifact_record(root: Path, path: Path, kind: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "kind": kind,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _artifact_record_any_path(root: Path, path: Path, kind: str) -> dict[str, Any]:
    data = path.read_bytes()
    path_value = path.resolve()
    try:
        path_value = path_value.relative_to(root.resolve())
    except ValueError:
        pass
    if isinstance(path_value, Path):
        path_value = path_value.as_posix()
    return {
        "path": str(path_value),
        "kind": kind,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _directory_artifact_record(path: Path, kind: str) -> dict[str, Any]:
    files = sorted(item for item in path.rglob("*") if item.is_file()) if path.exists() else []
    return {
        "path": str(path),
        "kind": kind,
        "exists": path.is_dir(),
        "file_count": len(files),
        "size": sum(item.stat().st_size for item in files),
    }


def _add_handoff_health_artifact(artifacts: dict[str, Any], label: str, path_value: Any) -> None:
    if not isinstance(path_value, str) or not path_value:
        return
    path = Path(path_value).resolve()
    if path.is_file():
        artifacts[label] = {
            "path": str(path),
            "kind": label,
            "size": path.stat().st_size,
            "sha256": _path_sha256(path),
        }
    elif path.is_dir():
        artifacts[label] = _directory_artifact_record(path, label)


def _write_handoff_health_artifacts(report: dict[str, Any], artifacts_dir: Path) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    health_json = artifacts_dir / "handoff-health.json"
    health_md = artifacts_dir / "handoff-health.md"
    report.setdefault("artifacts", {})["health_json"] = {
        "path": str(health_json),
        "kind": "handoff_health_json",
        "note": "self hash omitted because this file contains the artifact list",
    }
    report["artifacts"]["health_markdown"] = {
        "path": str(health_md),
        "kind": "handoff_health_markdown",
        "note": "hash written after markdown render",
    }
    health_md.write_text(format_handoff_health_markdown(report), encoding="utf-8")
    report["artifacts"]["health_markdown"] = {
        "path": str(health_md),
        "kind": "handoff_health_markdown",
        "size": health_md.stat().st_size,
        "sha256": _path_sha256(health_md),
    }
    _write_json(health_json, report)


def _handoff_health_manifest_question(root: Path) -> str | None:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    question = payload.get("question") if isinstance(payload, dict) else None
    return question if isinstance(question, str) and question.strip() else None


def _worst_status(*statuses: Any) -> str:
    order = {"pass": 0, "warn": 1, "fail": 2}
    worst = "pass"
    for status in statuses:
        value = str(status or "pass")
        if order.get(value, 2) > order[worst]:
            worst = value if value in order else "fail"
    return worst


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _optional_int_value(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def append_anchor_log(
    anchor_json_path_or_dict: Path | str | dict[str, Any],
    log_path: Path | str,
    *,
    out_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Append one timestamped anchor audit row to a JSONL log and return metadata."""

    if isinstance(anchor_json_path_or_dict, (str, Path)):
        anchor_payload_path = Path(anchor_json_path_or_dict).resolve()
        anchor_payload = json.loads(anchor_payload_path.read_text(encoding="utf-8"))
        if not isinstance(anchor_payload, dict):
            raise ValueError("Anchor payload must be a JSON object.")
        anchor_path = str(anchor_payload_path)
    elif isinstance(anchor_json_path_or_dict, dict):
        anchor_payload = anchor_json_path_or_dict
        anchor_path = None
    else:
        raise TypeError("anchor_json_path_or_dict must be a path or object payload.")

    if not isinstance(anchor_payload, dict):
        raise ValueError("Anchor payload must be a JSON object.")

    payload_chain = (
        anchor_payload.get("chain") if isinstance(anchor_payload.get("chain"), dict) else {}
    )
    payload_summary = (
        anchor_payload.get("summary") if isinstance(anchor_payload.get("summary"), dict) else {}
    )
    payload_verification = (
        anchor_payload.get("verification")
        if isinstance(anchor_payload.get("verification"), dict)
        else {}
    )
    payload_current_chain = (
        anchor_payload.get("current_chain")
        if isinstance(anchor_payload.get("current_chain"), dict)
        else {}
    )
    payload_current_chain_summary = (
        payload_current_chain.get("summary")
        if isinstance(payload_current_chain.get("summary"), dict)
        else {}
    )

    errors = anchor_payload.get("errors")
    if not isinstance(errors, list):
        errors = (
            payload_verification.get("errors", [])
            if isinstance(payload_verification.get("errors"), list)
            else []
        )
    warnings = anchor_payload.get("warnings")
    if not isinstance(warnings, list):
        warnings = (
            payload_verification.get("warnings", [])
            if isinstance(payload_verification.get("warnings"), list)
            else []
        )
    if not errors and isinstance(payload_summary.get("error_count"), int):
        errors = ["count:" + str(payload_summary.get("error_count"))]
    if not warnings and isinstance(payload_summary.get("warning_count"), int):
        warnings = ["count:" + str(payload_summary.get("warning_count"))]

    resolved_out_dir = str(out_dir) if out_dir is not None else str(anchor_payload.get("out_dir") or "")

    row = {
        "timestamp": int(time.time()),
        "out_dir": resolved_out_dir,
        "anchor_path": anchor_path or str(anchor_payload.get("anchor_path") or ""),
        "anchor_schema_version": anchor_payload.get("schema_version"),
        "anchor_status": anchor_payload.get("status"),
        "chain_head_hash": (
            payload_chain.get("head_chain_hash")
            or payload_chain.get("chain_head_hash")
            or payload_summary.get("head_chain_hash")
            or payload_summary.get("current_head_hash")
            or payload_summary.get("anchor_head_hash")
            or payload_current_chain_summary.get("head_chain_hash")
        ),
        "snapshot_count": (
            payload_chain.get("snapshot_count")
            or payload_summary.get("snapshot_count")
            or payload_summary.get("checked_count")
            or payload_current_chain_summary.get("snapshot_count")
            or payload_current_chain_summary.get("checked_count")
        ),
        "errors": errors,
        "warnings": warnings,
    }

    log_file = Path(log_path).resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")

    return {
        "status": "appended",
        "log_path": str(log_file),
        "entry": row,
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
        "8. `compare.md` / `compare.json` - file-level delta from the base pack.\n"
        "9. `inspect-diff.md` / `inspect-diff.json` - structural state delta from the base pack.\n"
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


def _handoff_score_add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    points: float,
    max_points: float,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": status,
            "points": round(points, 2),
            "max_points": round(max_points, 2),
            "message": message,
            "details": details or {},
        }
    )


def _handoff_score_read_json(root: Path, relative: str) -> dict[str, Any] | None:
    try:
        path = _safe_child_path(root, relative)
    except ValueError:
        return None
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _handoff_score_file_exists(root: Path, relative: str) -> bool:
    try:
        path = _safe_child_path(root, relative)
    except ValueError:
        return False
    return path.exists() and path.is_file()


def _load_handoff_score_input(score_or_handoff: Path | str | dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(score_or_handoff, dict):
        if score_or_handoff.get("schema_version") != "repomori.handoff_score.v1":
            raise ValueError("handoff score input must use schema repomori.handoff_score.v1")
        return score_or_handoff, {"type": "object", "path": None}

    path = Path(score_or_handoff).resolve()
    if path.is_dir():
        score_path = path / "handoff-score.json"
        if score_path.exists() and score_path.is_file():
            score = _read_handoff_score_file(score_path)
            return score, {"type": "handoff_dir_score", "path": str(path), "score_path": str(score_path)}
        score = score_handoff_package(path)
        return score, {"type": "handoff_dir", "path": str(path), "score_path": None}

    score = _read_handoff_score_file(path)
    return score, {"type": "score_file", "path": str(path), "score_path": str(path)}


def _read_handoff_score_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Handoff score file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "repomori.handoff_score.v1":
        raise ValueError(f"Unexpected handoff score schema: {path}")
    return payload


def _handoff_triage_actions(score: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    checks = {
        str(item.get("id")): item
        for item in score.get("checks", [])
        if isinstance(item, dict) and item.get("id")
    }
    warnings = [item for item in score.get("warnings", []) if isinstance(item, dict)]
    errors = [item for item in score.get("errors", []) if isinstance(item, dict)]
    handoff_dir = score.get("handoff_dir") or "<handoff-dir>"

    if score.get("status") == "fail" or checks.get("integrity", {}).get("status") == "fail":
        actions.append(
            _handoff_triage_action(
                "fix-integrity",
                1,
                "Fix failed handoff validation",
                "The score report says artifact hashes, JSON files, or copied pack verification failed.",
                "Run check-handoff, restore or regenerate the broken artifact, then rescore the handoff.",
                f"python -m repomori check-handoff {handoff_dir} --json",
                "integrity",
            )
        )
    if checks.get("manifest", {}).get("status") == "fail":
        actions.append(
            _handoff_triage_action(
                "rebuild-manifest",
                1,
                "Rebuild the handoff manifest",
                "The handoff manifest is missing required identity, question, pack, or completion fields.",
                "Regenerate the handoff from the source pack so manifest.json and artifact hashes agree.",
                None,
                "manifest",
            )
        )

    missing = [item for item in warnings if item.get("code") == "missing_core_artifact"]
    if missing or checks.get("artifact_coverage", {}).get("status") in {"fail", "warn"}:
        paths = ", ".join(str(item.get("path")) for item in missing[:5] if item.get("path"))
        reason = "Core handoff artifacts are missing from disk or the manifest."
        if paths:
            reason += f" Missing: {paths}."
        actions.append(
            _handoff_triage_action(
                "restore-core-artifacts",
                1 if checks.get("artifact_coverage", {}).get("status") == "fail" else 2,
                "Restore missing handoff artifacts",
                reason,
                "Rebuild the handoff with --force, or restore the missing files from the last good package.",
                None,
                "artifact_coverage",
            )
        )

    source_context = checks.get("source_context", {})
    if source_context.get("status") in {"fail", "warn"}:
        details = source_context.get("details", {})
        actions.append(
            _handoff_triage_action(
                "improve-source-context",
                1 if source_context.get("status") == "fail" else 2,
                "Regenerate richer source context",
                f"Context has sources={details.get('source_count')} snippets={details.get('snippet_count')} line_numbered={details.get('line_numbered_snippets')}.",
                "Use a more specific handoff question, raise --max-files, or raise --snippets-per-file.",
                None,
                "source_context",
            )
        )

    machine_state = checks.get("machine_state", {})
    if machine_state.get("status") in {"fail", "warn"}:
        actions.append(
            _handoff_triage_action(
                "refresh-machine-state",
                2,
                "Refresh brief and capsule machine state",
                "The handoff has weak orientation, capsule files, source manifest, or vocabulary coverage.",
                "Rebuild the handoff from a freshly verified pack and include enough files for the capsule.",
                None,
                "machine_state",
            )
        )

    context_eval = checks.get("context_eval", {})
    weak_eval = next((item for item in warnings if item.get("code") == "weak_eval_questions"), None)
    if context_eval.get("status") in {"fail", "warn"} or weak_eval:
        details = context_eval.get("details", {})
        actions.append(
            _handoff_triage_action(
                "tighten-eval-questions",
                2 if context_eval.get("status") != "fail" else 1,
                "Tighten the handoff question and eval prompts",
                f"Eval has passed={details.get('passed_questions')} weak={details.get('weak_questions')} total={details.get('question_count')}.",
                "Add targeted --eval-question values and prefer a task-shaped handoff question over a broad continuation prompt.",
                None,
                "context_eval",
            )
        )

    delta_context = checks.get("delta_context", {})
    if delta_context.get("status") in {"fail", "warn"}:
        actions.append(
            _handoff_triage_action(
                "restore-delta-context",
                2 if delta_context.get("status") == "warn" else 1,
                "Restore compare and inspect-diff artifacts",
                "The handoff records a base pack but is missing compare.json or inspect-diff.json.",
                "Rebuild the handoff with --base-pack pointing at the previous pack.",
                None,
                "delta_context",
            )
        )

    if score.get("status") == "pass" and not actions:
        return []
    if not errors and not any(action["id"] == "rescore-after-fixes" for action in actions):
        actions.append(
            _handoff_triage_action(
                "rescore-after-fixes",
                3,
                "Rescore after applying fixes",
                "The checklist should end with a fresh deterministic score report.",
                "Run score-handoff again and keep handoff-score.json with the package.",
                f"python -m repomori score-handoff {handoff_dir} --json",
                "score",
            )
        )
    return actions


def _handoff_triage_action(
    action_id: str,
    priority: int,
    title: str,
    reason: str,
    fix: str,
    command: str | None,
    related_check: str,
) -> dict[str, Any]:
    data = {
        "id": action_id,
        "priority": priority,
        "title": title,
        "reason": reason,
        "fix": fix,
        "related_check": related_check,
    }
    if command:
        data["command"] = command
    return data


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


def _inspect_file_record(row: sqlite3.Row) -> dict[str, Any]:
    summary = _safe_json(row["summary_json"], {})
    return {
        "path": row["path"],
        "language": row["language"],
        "size": row["size"],
        "sha256": row["sha256"],
        "is_text": bool(row["is_text"]),
        "line_count": row["line_count"],
        "token_count": row["token_count"],
        "chunk_count": row["chunk_count"],
        "summary": _compact_summary(summary),
        "_summary": summary,
    }


def _visible_inspect_file(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _inspect_source_manifest(records: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    seen = set()
    manifest = []
    for record in records:
        path = record.get("path")
        if not path or path in seen:
            continue
        seen.add(path)
        manifest.append(
            {
                "path": path,
                "sha256": record.get("sha256"),
                "size": record.get("size"),
                "is_text": record.get("is_text"),
                "language": record.get("language"),
            }
        )
        if len(manifest) >= limit:
            break
    return manifest


def _append_inspect_file_section(lines: list[str], title: str, files: list[dict[str, Any]]) -> None:
    lines.extend([f"## {title}", ""])
    if not files:
        lines.extend([f"No {title.lower()}.", ""])
        return
    for item in files:
        lines.append(
            f"- `{item.get('path')}` [{item.get('language') or 'unknown'}] "
            f"size=`{item.get('size')}` sha256=`{item.get('sha256')}`"
        )
        summary = item.get("summary") or {}
        terms = summary.get("top_terms") or []
        if terms:
            lines.append("  - terms: " + ", ".join(f"`{term}`" for term in terms[:8]))
    lines.append("")


def _inspect_diff_status(base_inspect: dict[str, Any], target_inspect: dict[str, Any]) -> str:
    statuses = {base_inspect.get("status"), target_inspect.get("status")}
    base_verify = base_inspect.get("verification", {})
    target_verify = target_inspect.get("verification", {})
    statuses.update({base_verify.get("status"), target_verify.get("status")})
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _inspect_delta_value(before: dict[str, Any], after: dict[str, Any], key: str) -> int | None:
    before_value = before.get(key)
    after_value = after.get(key)
    if isinstance(before_value, int) and isinstance(after_value, int):
        return after_value - before_value
    return None


def _inspect_diff_storage_delta(base_inspect: dict[str, Any], target_inspect: dict[str, Any]) -> dict[str, Any]:
    base_summary = base_inspect.get("summary", {})
    target_summary = target_inspect.get("summary", {})
    base_storage = base_inspect.get("storage", {})
    target_storage = target_inspect.get("storage", {})
    base_chunks = base_storage.get("chunks", {})
    target_chunks = target_storage.get("chunks", {})
    base_file_chunks = base_storage.get("file_chunks", {})
    target_file_chunks = target_storage.get("file_chunks", {})
    return {
        "chunk_count_delta": _inspect_delta_value(base_chunks, target_chunks, "count"),
        "unique_chunk_raw_bytes_delta": _inspect_delta_value(base_summary, target_summary, "unique_chunk_raw_bytes"),
        "compressed_chunk_bytes_delta": _inspect_delta_value(base_summary, target_summary, "compressed_chunk_bytes"),
        "compression_savings_bytes_delta": _inspect_delta_value(base_summary, target_summary, "compression_savings_bytes"),
        "dedupe_savings_bytes_delta": _inspect_delta_value(base_summary, target_summary, "dedupe_savings_bytes"),
        "file_chunk_link_delta": _inspect_delta_value(base_file_chunks, target_file_chunks, "links"),
        "duplicate_chunk_link_delta": _inspect_delta_value(base_file_chunks, target_file_chunks, "duplicate_chunk_links"),
        "base_compression_ratio": base_chunks.get("compression_ratio"),
        "target_compression_ratio": target_chunks.get("compression_ratio"),
        "base_logical_to_pack_ratio": base_summary.get("logical_to_pack_ratio"),
        "target_logical_to_pack_ratio": target_summary.get("logical_to_pack_ratio"),
    }


def _inspect_diff_language_delta(base_inspect: dict[str, Any], target_inspect: dict[str, Any]) -> list[dict[str, Any]]:
    base_languages = {
        str(item.get("language") or "unknown"): item
        for item in base_inspect.get("languages", [])
        if isinstance(item, dict)
    }
    target_languages = {
        str(item.get("language") or "unknown"): item
        for item in target_inspect.get("languages", [])
        if isinstance(item, dict)
    }
    rows = []
    for language in sorted(set(base_languages) | set(target_languages)):
        before = base_languages.get(language, {})
        after = target_languages.get(language, {})
        row = {
            "language": language,
            "base_file_count": int(before.get("file_count") or 0),
            "target_file_count": int(after.get("file_count") or 0),
            "base_bytes": int(before.get("bytes") or 0),
            "target_bytes": int(after.get("bytes") or 0),
            "base_tokens": int(before.get("tokens") or 0),
            "target_tokens": int(after.get("tokens") or 0),
            "base_lines": int(before.get("lines") or 0),
            "target_lines": int(after.get("lines") or 0),
        }
        row["file_count_delta"] = row["target_file_count"] - row["base_file_count"]
        row["bytes_delta"] = row["target_bytes"] - row["base_bytes"]
        row["tokens_delta"] = row["target_tokens"] - row["base_tokens"]
        row["lines_delta"] = row["target_lines"] - row["base_lines"]
        if any(row[key] for key in ("file_count_delta", "bytes_delta", "tokens_delta", "lines_delta")):
            rows.append(row)
    return rows


def _inspect_diff_vocabulary_delta(
    base_inspect: dict[str, Any],
    target_inspect: dict[str, Any],
    *,
    limit: int,
) -> dict[str, Any]:
    base_vocab = base_inspect.get("vocabulary", {})
    target_vocab = target_inspect.get("vocabulary", {})
    result = {}
    for key in ("top_terms", "top_symbols", "top_imports", "top_headings"):
        result[key] = _inspect_count_delta(
            _inspect_vocabulary_counts(base_vocab.get(key, []), key),
            _inspect_vocabulary_counts(target_vocab.get(key, []), key),
            limit=limit,
        )
    return result


def _inspect_vocabulary_counts(items: Any, key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(items, list):
        return counts
    for item in items:
        value = None
        count = 1
        if key == "top_symbols" and isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            kind = str(item.get("kind", "symbol")).strip() or "symbol"
            if name:
                value = f"{kind}:{name}"
            raw_count = item.get("count")
            if isinstance(raw_count, int):
                count = raw_count
        elif isinstance(item, (list, tuple)) and item:
            value = str(item[0]).strip()
            if len(item) > 1 and isinstance(item[1], int):
                count = item[1]
        if value:
            counts[value] = count
    return counts


def _inspect_count_delta(base: dict[str, int], target: dict[str, int], *, limit: int) -> dict[str, Any]:
    added = sorted(set(target) - set(base))
    removed = sorted(set(base) - set(target))
    changed = sorted(value for value in set(base) & set(target) if base[value] != target[value])
    return {
        "added": [{"value": value, "target_count": target[value]} for value in added[:limit]],
        "removed": [{"value": value, "base_count": base[value]} for value in removed[:limit]],
        "changed": [
            {
                "value": value,
                "base_count": base[value],
                "target_count": target[value],
                "delta": target[value] - base[value],
            }
            for value in changed[:limit]
        ],
        "shared_count": len(set(base) & set(target)),
        "truncated": {
            "added": len(added) > limit,
            "removed": len(removed) > limit,
            "changed": len(changed) > limit,
        },
    }


def _inspect_diff_source_manifest(comparison: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    manifest = []
    files = comparison.get("files", {})
    for item in files.get("added", []):
        manifest.append(
            {
                "path": item.get("path"),
                "change_type": "added",
                "base_sha256": None,
                "target_sha256": item.get("sha256"),
                "base_size": None,
                "target_size": item.get("size"),
                "language": item.get("language"),
            }
        )
    for item in files.get("removed", []):
        manifest.append(
            {
                "path": item.get("path"),
                "change_type": "removed",
                "base_sha256": item.get("sha256"),
                "target_sha256": None,
                "base_size": item.get("size"),
                "target_size": None,
                "language": item.get("language"),
            }
        )
    for item in files.get("changed", []):
        before = item.get("before", {})
        after = item.get("after", {})
        manifest.append(
            {
                "path": item.get("path"),
                "change_type": "changed",
                "base_sha256": before.get("sha256"),
                "target_sha256": after.get("sha256"),
                "base_size": before.get("size"),
                "target_size": after.get("size"),
                "language": after.get("language") or before.get("language"),
                "change_reasons": item.get("change_reasons", []),
            }
        )
    return manifest[:limit]


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


def _agent_brief_artifacts(out_path: Path, latest: dict[str, Any] | None) -> list[dict[str, Any]]:
    artifacts = [_agent_brief_artifact("snapshot_index", out_path / "snapshots.json")]
    artifacts.append(_agent_brief_artifact("latest_alias", out_path / "latest.repomori"))
    if latest is None:
        return artifacts
    for kind, field in (
        ("latest_pack", "pack_path"),
        ("snapshot_json", "snapshot_json"),
        ("snapshot_markdown", "snapshot_markdown"),
        ("compare_json", "compare_json"),
        ("compare_markdown", "compare_markdown"),
        ("inspect_diff_json", "inspect_diff_json"),
        ("inspect_diff_markdown", "inspect_diff_markdown"),
        ("handoff_score_json", "handoff_score_json"),
        ("handoff_score_markdown", "handoff_score_markdown"),
        ("handoff_triage_json", "handoff_triage_json"),
        ("handoff_triage_markdown", "handoff_triage_markdown"),
        ("diff_context_json", "diff_context_json"),
        ("diff_context_markdown", "diff_context_markdown"),
        ("handoff_dir", "handoff_dir"),
    ):
        path = _recorded_snapshot_path(out_path, latest.get(field))
        if path is not None:
            artifacts.append(_agent_brief_artifact(kind, path))
    return artifacts


def _agent_brief_artifact(kind: str, path: Path) -> dict[str, Any]:
    exists = path.exists()
    data = {
        "kind": kind,
        "path": str(path),
        "exists": exists,
        "size": None,
        "sha256": None,
    }
    if exists and path.is_file():
        data["size"] = path.stat().st_size
        data["sha256"] = _path_sha256(path)
    return data


def _agent_brief_latest_inspect_diff(
    out_path: Path,
    latest: dict[str, Any],
    max_files: int,
) -> dict[str, Any] | None:
    json_path = _recorded_snapshot_path(out_path, latest.get("inspect_diff_json"))
    markdown_path = _recorded_snapshot_path(out_path, latest.get("inspect_diff_markdown"))
    if json_path is None or not json_path.exists() or not json_path.is_file():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "status": "invalid_json",
            "json_path": str(json_path),
            "markdown_path": str(markdown_path) if markdown_path is not None else None,
            "summary": {},
            "files": {"added": [], "changed": [], "removed": []},
            "source_manifest": [],
        }

    files = payload.get("files", {})
    return {
        "status": payload.get("status") or "available",
        "json_path": str(json_path),
        "markdown_path": str(markdown_path) if markdown_path is not None else None,
        "summary": payload.get("summary", {}),
        "comparison_summary": payload.get("comparison", {}).get("summary", {}),
        "storage_delta": payload.get("storage_delta", {}),
        "language_delta": payload.get("language_delta", [])[:max_files],
        "files": {
            "added": _agent_brief_inspect_files(files.get("added", []), "added", max_files),
            "changed": _agent_brief_inspect_files(files.get("changed", []), "changed", max_files),
            "removed": _agent_brief_inspect_files(files.get("removed", []), "removed", max_files),
        },
        "source_manifest": payload.get("source_manifest", [])[:max_files],
    }


def _agent_brief_inspect_files(items: list[dict[str, Any]], change_type: str, limit: int) -> list[dict[str, Any]]:
    records = []
    for item in items[:limit]:
        if change_type == "changed":
            after = item.get("after", {})
            before = item.get("before", {})
            records.append(
                {
                    "path": item.get("path"),
                    "change_type": change_type,
                    "size": after.get("size"),
                    "sha256": after.get("sha256"),
                    "previous_size": before.get("size"),
                    "previous_sha256": before.get("sha256"),
                    "change_reasons": item.get("change_reasons", []),
                }
            )
        else:
            records.append(
                {
                    "path": item.get("path"),
                    "change_type": change_type,
                    "size": item.get("size"),
                    "sha256": item.get("sha256"),
                    "language": item.get("language"),
                }
            )
    return records


def _agent_brief_latest_diff_context(
    out_path: Path,
    latest: dict[str, Any],
    max_files: int,
) -> dict[str, Any] | None:
    json_path = _recorded_snapshot_path(out_path, latest.get("diff_context_json"))
    markdown_path = _recorded_snapshot_path(out_path, latest.get("diff_context_markdown"))
    if json_path is None or not json_path.exists() or not json_path.is_file():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "status": "invalid_json",
            "json_path": str(json_path),
            "markdown_path": str(markdown_path) if markdown_path is not None else None,
            "summary": {},
            "sources": [],
        }
    sources = []
    for source in payload.get("sources", [])[:max_files]:
        sources.append(
            {
                "path": source.get("path"),
                "change_type": source.get("change_type"),
                "score": source.get("score"),
                "match_reasons": source.get("match_reasons", []),
                "source_pack": source.get("source_pack"),
                "sha256": source.get("sha256"),
                "size": source.get("size"),
                "snippet_count": len(source.get("snippets", [])),
                "snippet_status": source.get("snippet_status"),
            }
        )
    return {
        "status": "available",
        "json_path": str(json_path),
        "markdown_path": str(markdown_path) if markdown_path is not None else None,
        "question": payload.get("question"),
        "summary": payload.get("summary", {}),
        "sources": sources,
        "source_manifest": payload.get("source_manifest", [])[:max_files],
    }


def _agent_brief_commands(
    out_path: Path,
    latest_pack_path: Path | None,
    latest: dict[str, Any] | None,
) -> list[dict[str, str]]:
    commands = [
        {
            "purpose": "Check snapshot health",
            "command": f"python -m repomori doctor {out_path} --json",
        },
        {
            "purpose": "Read recent timeline",
            "command": f"python -m repomori timeline {out_path} --format json",
        },
        {
            "purpose": "Read reuse stats",
            "command": f"python -m repomori stats {out_path} --format json",
        },
    ]
    repo_path = latest.get("repo_path") if isinstance(latest, dict) else None
    if repo_path:
        commands.insert(
            0,
            {
                "purpose": "Run the next memory cycle",
                "command": f"python -m repomori memory {repo_path} --out-dir {out_path} --diff-context --json",
            },
        )
    if latest_pack_path is not None:
        commands.extend(
            [
                {
                    "purpose": "Search the latest pack",
                    "command": f"python -m repomori query {latest_pack_path} \"<question>\" --json",
                },
                {
                    "purpose": "Build source-backed context",
                    "command": f"python -m repomori context {latest_pack_path} \"<question>\" --format markdown --out {out_path}\\context.md",
                },
            ]
        )
    return commands


def _snapshot_anchor_latest(out_path: Path, latest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(latest, dict):
        return None
    pack_path = _recorded_snapshot_path(out_path, latest.get("pack_path"))
    return {
        "created_at": latest.get("created_at"),
        "status": latest.get("status"),
        "repo_path": latest.get("repo_path"),
        "pack_name": latest.get("pack_name"),
        "pack_path": str(pack_path) if pack_path is not None else latest.get("pack_path"),
        "pack_sha256": latest.get("pack_sha256"),
        "file_count": latest.get("file_count"),
        "logical_bytes": latest.get("logical_bytes"),
        "pack_bytes": latest.get("pack_bytes"),
        "chain_index": latest.get("chain_index"),
        "entry_hash": latest.get("entry_hash"),
        "chain_hash": latest.get("chain_hash"),
    }


def _snapshot_anchor_expected_hash(anchor: dict[str, Any]) -> str:
    payload = dict(anchor)
    payload.pop("anchor_hash", None)
    return _canonical_json_hash(payload)


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
    updated = _chain_snapshot_index(
        out_path,
        {
            "schema_version": "repomori.snapshots.v1",
            "out_dir": str(out_path),
            "updated_at": int(time.time()),
            "snapshot_count": len(snapshots),
            "latest": entry,
            "snapshots": snapshots,
        },
    )
    _write_json(index_path, updated)
    return updated


def _chain_snapshot_index(out_path: Path, index: dict[str, Any]) -> dict[str, Any]:
    snapshots = [dict(snapshot) for snapshot in index.get("snapshots", []) if isinstance(snapshot, dict)]
    chained_snapshots = _chain_snapshot_entries(snapshots)
    latest = index.get("latest")
    latest_chained = latest
    if isinstance(latest, dict):
        latest_key = _snapshot_pack_key(out_path, latest)
        latest_chained = next(
            (
                snapshot
                for snapshot in chained_snapshots
                if _snapshot_pack_key(out_path, snapshot) == latest_key
            ),
            dict(latest),
        )
        if latest_chained is not latest and isinstance(latest_chained, dict):
            latest_chained = dict(latest_chained)
    head_hash = chained_snapshots[-1].get("chain_hash") if chained_snapshots else None
    chained = {
        "schema_version": "repomori.snapshots.v1",
        "out_dir": index.get("out_dir", str(out_path)),
        "updated_at": index.get("updated_at", int(time.time())),
        "snapshot_count": len(chained_snapshots),
        "latest": latest_chained,
        "snapshots": chained_snapshots,
        "chain": {
            "chain_version": SNAPSHOT_CHAIN_VERSION,
            "algorithm": SNAPSHOT_CHAIN_ALGORITHM,
            "head_chain_hash": head_hash,
            "snapshot_count": len(chained_snapshots),
            "anchored_to_pruned_history": bool(
                chained_snapshots and chained_snapshots[0].get("previous_chain_hash")
            ),
        },
    }
    return chained


def _chain_snapshot_entries(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_hash = None
    if snapshots:
        first_previous = snapshots[0].get("previous_chain_hash")
        if isinstance(first_previous, str) and first_previous:
            previous_hash = first_previous
    chained = []
    for index, snapshot in enumerate(snapshots):
        entry = _snapshot_entry_without_chain(snapshot)
        entry_hash = _snapshot_entry_hash(entry)
        chain_hash = _snapshot_chain_hash(previous_hash, entry_hash)
        entry.update(
            {
                "chain_version": SNAPSHOT_CHAIN_VERSION,
                "chain_index": index,
                "previous_chain_hash": previous_hash,
                "entry_hash": entry_hash,
                "chain_hash": chain_hash,
            }
        )
        chained.append(entry)
        previous_hash = chain_hash
    return chained


def _snapshot_entry_without_chain(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key not in SNAPSHOT_CHAIN_FIELDS}


def _snapshot_entry_hash(snapshot: dict[str, Any]) -> str:
    return _canonical_json_hash(_snapshot_entry_without_chain(snapshot))


def _snapshot_chain_hash(previous_chain_hash: str | None, entry_hash: str) -> str:
    return _canonical_json_hash(
        {
            "chain_version": SNAPSHOT_CHAIN_VERSION,
            "previous_chain_hash": previous_chain_hash,
            "entry_hash": entry_hash,
        }
    )


def _canonical_json_hash(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _verify_snapshot_chain_entries(
    out_path: Path,
    snapshots: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    previous_hash = None
    for index, snapshot in enumerate(snapshots):
        missing = [field for field in SNAPSHOT_CHAIN_FIELDS if field not in snapshot]
        if missing:
            _add_chain_issue(
                errors,
                str(_recorded_snapshot_path(out_path, snapshot.get("pack_path")) or snapshot.get("pack_path") or ""),
                "Snapshot chain fields are missing.",
                index=index,
                expected=sorted(missing),
            )
            continue
        if snapshot.get("chain_version") != SNAPSHOT_CHAIN_VERSION:
            _add_chain_issue(
                errors,
                str(_recorded_snapshot_path(out_path, snapshot.get("pack_path")) or snapshot.get("pack_path") or ""),
                "Snapshot chain version does not match.",
                index=index,
                expected=SNAPSHOT_CHAIN_VERSION,
                actual=snapshot.get("chain_version"),
            )
        if snapshot.get("chain_index") != index:
            _add_chain_issue(
                errors,
                str(_recorded_snapshot_path(out_path, snapshot.get("pack_path")) or snapshot.get("pack_path") or ""),
                "Snapshot chain index does not match its timeline position.",
                index=index,
                expected=index,
                actual=snapshot.get("chain_index"),
            )
        if index == 0:
            previous_hash = snapshot.get("previous_chain_hash")
            summary["anchored_to_pruned_history"] = bool(previous_hash)
        elif snapshot.get("previous_chain_hash") != previous_hash:
            _add_chain_issue(
                errors,
                str(_recorded_snapshot_path(out_path, snapshot.get("pack_path")) or snapshot.get("pack_path") or ""),
                "Snapshot previous chain hash does not match the prior snapshot.",
                index=index,
                expected=previous_hash,
                actual=snapshot.get("previous_chain_hash"),
            )
        expected_entry_hash = _snapshot_entry_hash(snapshot)
        if snapshot.get("entry_hash") != expected_entry_hash:
            _add_chain_issue(
                errors,
                str(_recorded_snapshot_path(out_path, snapshot.get("pack_path")) or snapshot.get("pack_path") or ""),
                "Snapshot entry hash does not match canonical snapshot metadata.",
                index=index,
                expected=expected_entry_hash,
                actual=snapshot.get("entry_hash"),
            )
        expected_chain_hash = _snapshot_chain_hash(snapshot.get("previous_chain_hash"), expected_entry_hash)
        if snapshot.get("chain_hash") != expected_chain_hash:
            _add_chain_issue(
                errors,
                str(_recorded_snapshot_path(out_path, snapshot.get("pack_path")) or snapshot.get("pack_path") or ""),
                "Snapshot chain hash does not match previous hash and entry hash.",
                index=index,
                expected=expected_chain_hash,
                actual=snapshot.get("chain_hash"),
            )
        previous_hash = expected_chain_hash
        summary["checked_count"] += 1
        summary["head_chain_hash"] = expected_chain_hash


def _verify_snapshot_chain_meta(
    index_path: Path,
    chain_meta: Any,
    snapshots: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    if not isinstance(chain_meta, dict):
        _add_chain_issue(errors, str(index_path), "Snapshot index chain metadata is missing or invalid.")
        return
    expected_head = summary.get("head_chain_hash")
    expected_anchored = bool(snapshots and snapshots[0].get("previous_chain_hash"))
    checks = (
        ("chain_version", SNAPSHOT_CHAIN_VERSION),
        ("algorithm", SNAPSHOT_CHAIN_ALGORITHM),
        ("head_chain_hash", expected_head),
        ("snapshot_count", len(snapshots)),
        ("anchored_to_pruned_history", expected_anchored),
    )
    for field, expected in checks:
        if chain_meta.get(field) != expected:
            _add_chain_issue(
                errors,
                str(index_path),
                f"Snapshot index chain `{field}` does not match the indexed snapshots.",
                expected=expected,
                actual=chain_meta.get(field),
            )


def _add_chain_issue(
    issues: list[dict[str, Any]],
    path: str,
    message: str,
    *,
    index: int | None = None,
    expected: Any = None,
    actual: Any = None,
) -> None:
    issue = {"path": path, "message": message}
    if index is not None:
        issue["index"] = index
    if expected is not None:
        issue["expected"] = expected
    if actual is not None:
        issue["actual"] = actual
    issues.append(issue)


def _add_anchor_issue(
    issues: list[dict[str, Any]],
    path: str,
    message: str,
    *,
    expected: Any = None,
    actual: Any = None,
) -> None:
    issue = {"path": path, "message": message}
    if expected is not None:
        issue["expected"] = expected
    if actual is not None:
        issue["actual"] = actual
    issues.append(issue)


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
        "inspect_diff_json": artifacts.get("inspect_diff_json"),
        "inspect_diff_markdown": artifacts.get("inspect_diff_markdown"),
        "handoff_score_json": artifacts.get("handoff_score_json"),
        "handoff_score_markdown": artifacts.get("handoff_score_markdown"),
        "handoff_triage_json": artifacts.get("handoff_triage_json"),
        "handoff_triage_markdown": artifacts.get("handoff_triage_markdown"),
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
        "handoff_score_status": summary.get("handoff_score_status"),
        "handoff_score_percent": summary.get("handoff_score_percent"),
        "handoff_score_failed_checks": summary.get("handoff_score_failed_checks"),
        "handoff_score_warned_checks": summary.get("handoff_score_warned_checks"),
        "handoff_triage_status": summary.get("handoff_triage_status"),
        "handoff_triage_action_count": summary.get("handoff_triage_action_count"),
        "handoff_triage_high_priority_count": summary.get("handoff_triage_high_priority_count"),
        "inspect_diff_status": summary.get("inspect_diff_status"),
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
        "handoff_score_status": item.get("handoff_score_status"),
        "handoff_score_percent": item.get("handoff_score_percent"),
        "handoff_triage_status": item.get("handoff_triage_status"),
        "handoff_triage_action_count": _snapshot_int(item, "handoff_triage_action_count"),
        "handoff_triage_high_priority_count": _snapshot_int(item, "handoff_triage_high_priority_count"),
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
    for field in ("inspect_diff_json", "inspect_diff_markdown"):
        if snapshot.get(field):
            _doctor_check_snapshot_artifact(out_path, snapshot, index, field, errors, summary)
    for field in ("handoff_score_json", "handoff_score_markdown"):
        if snapshot.get(field):
            _doctor_check_snapshot_artifact(out_path, snapshot, index, field, errors, summary)
    for field in ("handoff_triage_json", "handoff_triage_markdown"):
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
        "inspect_diff_json": snapshot.get("inspect_diff_json"),
        "inspect_diff_markdown": snapshot.get("inspect_diff_markdown"),
        "handoff_score_json": snapshot.get("handoff_score_json"),
        "handoff_score_markdown": snapshot.get("handoff_score_markdown"),
        "handoff_triage_json": snapshot.get("handoff_triage_json"),
        "handoff_triage_markdown": snapshot.get("handoff_triage_markdown"),
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
        ("inspect_diff_json", "inspect_diff_json"),
        ("inspect_diff_markdown", "inspect_diff_markdown"),
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
        "handoff_quality_profile",
        "handoff_quality_target",
        "anchor_out",
        "anchor_verify",
        "allow_unverified_anchor",
        "anchor_freshness",
        "anchor_log",
    ):
        lines.append(f"{key} = {_toml_value(settings[key])}")
    return "\n".join(lines).rstrip() + "\n"


def _normalize_anchor_freshness(value: Any) -> str | None:
    if value is None:
        return None  # type: ignore[return-value]
    if not isinstance(value, str):
        raise ValueError("anchor_freshness must be one of: safe, strict, legacy")
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"safe", "strict", "legacy"}:
        return normalized
    raise ValueError(f"Invalid anchor_freshness '{value}'. Expected safe, strict, or legacy.")


def _normalize_handoff_quality_profile(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("handoff quality profile must be one of: safe, ci, strict")
    normalized = value.strip().lower()
    if normalized in HANDOFF_QUALITY_PROFILES:
        return normalized
    raise ValueError(f"Invalid handoff quality profile '{value}'. Expected safe, ci, or strict.")


def _handoff_improvement_attempt_settings(
    base_settings: dict[str, Any],
    max_attempts: int,
) -> list[dict[str, Any]]:
    base_max_files = int(base_settings["max_files"])
    base_snippet_lines = int(base_settings["snippet_lines"])
    base_snippets_per_file = int(base_settings["snippets_per_file"])
    base_top_terms = int(base_settings["top_terms"])
    base_max_bytes = base_settings.get("max_bytes")
    bytes_floor = int(base_max_bytes or 0)
    attempts = [
        {
            **base_settings,
            "max_files": base_max_files,
            "max_bytes": base_max_bytes,
            "snippet_lines": base_snippet_lines,
            "snippets_per_file": base_snippets_per_file,
            "top_terms": base_top_terms,
        },
        {
            **base_settings,
            "max_files": max(base_max_files * 2, 12),
            "max_bytes": max(bytes_floor * 2, 8192),
            "snippet_lines": max(base_snippet_lines, 14),
            "snippets_per_file": max(base_snippets_per_file + 1, 3),
            "top_terms": max(base_top_terms, 192),
        },
        {
            **base_settings,
            "max_files": max(base_max_files * 3, 24),
            "max_bytes": max(bytes_floor * 4, 16384),
            "snippet_lines": max(base_snippet_lines, 16),
            "snippets_per_file": max(base_snippets_per_file + 2, 4),
            "top_terms": max(base_top_terms, 256),
        },
        {
            **base_settings,
            "max_files": max(base_max_files * 4, 32),
            "max_bytes": max(bytes_floor * 8, 32768),
            "snippet_lines": max(base_snippet_lines, 20),
            "snippets_per_file": max(base_snippets_per_file + 3, 5),
            "top_terms": max(base_top_terms, 320),
        },
    ]
    return attempts[:max_attempts]


def _handoff_improvement_rank(attempt: dict[str, Any]) -> tuple[int, float, int, int]:
    quality_rank = {"fail": 0, "warn": 1, "pass": 2}.get(str(attempt.get("quality_status")), 0)
    score = float(attempt.get("score_percent") or 0.0)
    high_priority = int(attempt.get("triage_high_priority_count") or 0)
    actions = int(attempt.get("triage_action_count") or 0)
    return quality_rank, score, -high_priority, -actions


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
        "handoff_quality_profile": None,
        "handoff_quality_target": None,
        "anchor_out": None,
        "anchor_verify": False,
        "allow_unverified_anchor": False,
        "anchor_freshness": None,
        "anchor_log": None,
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
    for key in (
        "no_handoff",
        "prune_apply",
        "verify_packs",
        "incremental",
        "compare",
        "diff_context",
        "diff_context_include_source",
        "anchor_verify",
        "allow_unverified_anchor",
    ):
        normalized[key] = _coerce_config_bool(path, key, normalized[key])
    normalized["anchor_freshness"] = _normalize_anchor_freshness(normalized.get("anchor_freshness"))
    for key in ("keep", "timeline_limit", "chunk_size", "compare_limit", "diff_context_limit", "diff_context_snippet_lines", "diff_context_snippets_per_file", "diff_context_max_bytes"):
        normalized[key] = _coerce_config_int(path, key, normalized[key])
    quality_profile = normalized.get("handoff_quality_profile")
    normalized["handoff_quality_profile"] = (
        _normalize_handoff_quality_profile(quality_profile)
        if isinstance(quality_profile, str) and quality_profile.strip()
        else None
    )
    quality_target = normalized.get("handoff_quality_target")
    if quality_target in {None, ""}:
        normalized["handoff_quality_target"] = None
    elif isinstance(quality_target, bool) or not isinstance(quality_target, (int, float)):
        raise ValueError(f"RepoMori config key `handoff_quality_target` must be a number or omitted: {path}")
    else:
        normalized["handoff_quality_target"] = float(quality_target)
    normalized["handoff_question"] = str(normalized.get("handoff_question") or "")
    normalized["diff_context_question"] = str(normalized.get("diff_context_question") or "")
    normalized["anchor_out"] = _coerce_optional_config_path(path, "anchor_out", normalized.get("anchor_out"))
    normalized["anchor_log"] = _coerce_optional_config_path(path, "anchor_log", normalized.get("anchor_log"))
    return normalized


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "\"\""
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


def _coerce_optional_config_path(path: Path, key: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"RepoMori config key `{key}` must be a path string or omitted: {path}")
    text = value.strip()
    if not text:
        return None
    file_path = Path(text)
    if not file_path.is_absolute():
        file_path = path.parent / file_path
    return str(file_path.resolve())


def _coerce_config_int(path: Path, key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"RepoMori config key `{key}` must be an integer: {path}")
    return value


AGENT_METHODS = (
    "agent.help",
    "ping",
    "memory.run",
    "brief.build",
    "anchor.build",
    "anchor.verify",
    "chain.verify",
    "timeline.read",
    "timeline.search",
    "stats.read",
    "doctor.run",
    "inspect.build",
    "inspect_diff.build",
    "query.run",
    "context.build",
    "diff_context.build",
    "handoff.build",
    "handoff.score",
    "handoff.triage",
    "handoff.quality",
    "handoff.improve",
    "handoff.archive",
    "handoff.health",
    "capsule.build",
    "file.get",
    "compat.check",
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
                "anchor_out": {"type": "string"},
                "anchor_verify": {"type": "boolean"},
                "allow_unverified_anchor": {"type": "boolean"},
                "anchor_freshness": {"type": "string", "enum": ["safe", "strict", "legacy"]},
                "anchor_log": {"type": "string"},
                "handoff_question": {"type": "string"},
                "no_handoff": {"type": "boolean"},
                "handoff_quality_profile": {"type": "string", "enum": ["safe", "ci", "strict"]},
                "handoff_quality_target": {"type": ["number", "null"]},
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
        "name": "repomori_brief_build",
        "title": "RepoMori Brief Build",
        "description": "Build a concise agent start brief from the configured snapshot timeline.",
        "agent_method": "brief.build",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out_dir": {"type": "string"},
                "timeline_limit": {"type": "integer"},
                "stats_limit": {"type": "integer"},
                "verify_packs": {"type": "boolean"},
                "max_files": {"type": "integer"},
                "top_terms": {"type": "integer"},
                "top_symbols": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_chain_verify",
        "title": "RepoMori Chain Verify",
        "description": "Verify the configured snapshot timeline hash chain.",
        "agent_method": "chain.verify",
        "inputSchema": {
            "type": "object",
            "properties": {"out_dir": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_anchor_build",
        "title": "RepoMori Anchor Build",
        "description": "Build a small external proof record for the current snapshot timeline head.",
        "agent_method": "anchor.build",
        "inputSchema": {
            "type": "object",
            "properties": {"out_dir": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_anchor_verify",
        "title": "RepoMori Anchor Verify",
        "description": "Verify an exported snapshot timeline anchor proof.",
        "agent_method": "anchor.verify",
        "inputSchema": {
            "type": "object",
            "properties": {
                "anchor": {"type": "string"},
                "path": {"type": "string"},
                "out_dir": {"type": "string"},
                "check_current": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
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
        "name": "repomori_timeline_search",
        "title": "RepoMori Timeline Search",
        "description": "Query all indexed snapshot packs for a path, symbol, or concept.",
        "agent_method": "timeline.search",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out_dir": {"type": "string"},
                "text": {"type": "string"},
                "limit": {"type": "integer"},
                "per_snapshot_limit": {"type": "integer"},
            },
            "required": ["text"],
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
        "name": "repomori_pack_inspect",
        "title": "RepoMori Pack Inspect",
        "description": "Inspect a pack's contents, storage, indexes, vocabulary, and optional verification status.",
        "agent_method": "inspect.build",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack": {"type": "string"},
                "out_dir": {"type": "string"},
                "max_files": {"type": "integer"},
                "top_terms": {"type": "integer"},
                "top_symbols": {"type": "integer"},
                "verify": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_pack_inspect_diff",
        "title": "RepoMori Pack Inspect Diff",
        "description": "Inspect structural storage, language, vocabulary, and file changes between two packs.",
        "agent_method": "inspect_diff.build",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_pack": {"type": "string"},
                "target_pack": {"type": "string"},
                "out_dir": {"type": "string"},
                "max_files": {"type": "integer"},
                "top_terms": {"type": "integer"},
                "top_symbols": {"type": "integer"},
                "verify": {"type": "boolean"},
            },
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
        "name": "repomori_handoff_score",
        "title": "RepoMori Handoff Score",
        "description": "Score a handoff package for source-backed agent usefulness.",
        "agent_method": "handoff.score",
        "inputSchema": {
            "type": "object",
            "properties": {"handoff_dir": {"type": "string"}},
            "required": ["handoff_dir"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_handoff_triage",
        "title": "RepoMori Handoff Triage",
        "description": "Turn a handoff score into a prioritized repair checklist.",
        "agent_method": "handoff.triage",
        "inputSchema": {
            "type": "object",
            "properties": {"score_or_handoff": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["score_or_handoff"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_handoff_quality",
        "title": "RepoMori Handoff Quality",
        "description": "Apply a safe, ci, or strict handoff quality gate.",
        "agent_method": "handoff.quality",
        "inputSchema": {
            "type": "object",
            "properties": {
                "score_or_handoff": {"type": "string"},
                "profile": {"type": "string", "enum": ["safe", "ci", "strict"]},
                "target_score": {"type": ["number", "null"]},
            },
            "required": ["score_or_handoff"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "repomori_handoff_improve",
        "title": "RepoMori Handoff Improve",
        "description": "Build, score, triage, and retry a handoff with richer local settings.",
        "agent_method": "handoff.improve",
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
                "target_score": {"type": "number"},
                "quality_profile": {"type": "string", "enum": ["safe", "ci", "strict"]},
                "max_attempts": {"type": "integer"},
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
        "name": "repomori_handoff_archive",
        "title": "RepoMori Handoff Archive",
        "description": "Write a portable zip archive for a handoff directory.",
        "agent_method": "handoff.archive",
        "inputSchema": {
            "type": "object",
            "properties": {
                "handoff_dir": {"type": "string"},
                "out": {"type": "string"},
                "force": {"type": "boolean"},
                "quality_profile": {"type": "string", "enum": ["safe", "ci", "strict"]},
            },
            "required": ["handoff_dir"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "repomori_handoff_health",
        "title": "RepoMori Handoff Health",
        "description": "Run handoff check, score, triage, quality, and optional repair/archive.",
        "agent_method": "handoff.health",
        "inputSchema": {
            "type": "object",
            "properties": {
                "handoff_dir": {"type": "string"},
                "profile": {"type": "string", "enum": ["safe", "ci", "strict"]},
                "target_score": {"type": ["number", "null"]},
                "improve_pack": {"type": "string"},
                "question": {"type": "string"},
                "improve_out": {"type": "string"},
                "base_pack": {"type": "string"},
                "force": {"type": "boolean"},
                "copy_pack": {"type": "boolean"},
                "allow_unverified": {"type": "boolean"},
                "archive": {"type": "boolean"},
                "archive_out": {"type": "string"},
                "max_attempts": {"type": "integer"},
                "max_files": {"type": "integer"},
                "max_bytes": {"type": ["integer", "null"]},
                "snippet_lines": {"type": "integer"},
                "snippets_per_file": {"type": "integer"},
                "capsule_max_files": {"type": ["integer", "null"]},
                "top_terms": {"type": "integer"},
                "eval_questions": {"type": "array", "items": {"type": "string"}},
                "health_log": {"type": "string"},
                "artifacts_dir": {"type": "string"},
            },
            "required": ["handoff_dir"],
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
        "name": "repomori_compat_check",
        "title": "RepoMori Compat Check",
        "description": "Check local pack, handoff, schema, agent, and MCP compatibility.",
        "agent_method": "compat.check",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pack": {"type": "string"},
                "handoff": {"type": "string"},
                "snapshot_dir": {"type": "string"},
                "out_dir": {"type": "string"},
                "verify_pack_contents": {"type": "boolean"},
                "require_handoff": {"type": "boolean"},
            },
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
    if method == "compat.check":
        pack = params.get("pack")
        if pack is not None and not isinstance(pack, str):
            raise ValueError("params.pack must be a string when supplied.")
        handoff = params.get("handoff")
        if handoff is not None and not isinstance(handoff, str):
            raise ValueError("params.handoff must be a string when supplied.")
        snapshot_dir = params.get("snapshot_dir") or params.get("out_dir") or settings.get("out_dir")
        if snapshot_dir is not None and not isinstance(snapshot_dir, str):
            raise ValueError("params.snapshot_dir/out_dir must be a string when supplied.")
        return check_compatibility(
            pack if isinstance(pack, str) and pack.strip() else None,
            handoff=handoff if isinstance(handoff, str) and handoff.strip() else None,
            snapshot_dir=snapshot_dir,
            verify_pack_contents=bool(params.get("verify_pack_contents", False)),
            require_handoff=bool(params.get("require_handoff", True)),
        )
    if method == "memory.run":
        return run_memory_cycle(**_agent_memory_kwargs(params, settings))
    if method == "brief.build":
        return build_agent_brief(
            _agent_out_dir(params, settings),
            timeline_limit=_agent_int(params, "timeline_limit", 5),
            stats_limit=_agent_int(params, "stats_limit", 10),
            verify_packs=bool(params.get("verify_packs", settings.get("verify_packs", False))),
            max_files=_agent_int(params, "max_files", 8),
            top_terms=_agent_int(params, "top_terms", 40),
            top_symbols=_agent_int(params, "top_symbols", 40),
        )
    if method == "anchor.build":
        return build_snapshot_anchor(_agent_out_dir(params, settings))
    if method == "anchor.verify":
        anchor_path = params.get("anchor") or params.get("path")
        if not isinstance(anchor_path, str) or not anchor_path.strip():
            raise ValueError("anchor.verify requires params.anchor or params.path.")
        out_value = params.get("out_dir") or settings.get("out_dir")
        if out_value is not None and not isinstance(out_value, str):
            raise ValueError("params.out_dir must be a string when supplied.")
        return verify_snapshot_anchor(
            anchor_path,
            out_value,
            check_current=bool(params.get("check_current", True)),
        )
    if method == "chain.verify":
        return verify_snapshot_chain(_agent_out_dir(params, settings))
    if method == "timeline.read":
        return read_snapshot_timeline(
            _agent_out_dir(params, settings),
            limit=_agent_optional_int(params, "limit", None),
        )
    if method == "timeline.search":
        return search_snapshot_timeline(
            _agent_out_dir(params, settings),
            _agent_required_str(params, "text"),
            limit=_agent_int(params, "limit", 10),
            per_snapshot_limit=_agent_int(params, "per_snapshot_limit", 3),
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
    if method == "inspect.build":
        return inspect_pack(
            _agent_pack(params, settings),
            max_files=_agent_int(params, "max_files", 20),
            top_terms=_agent_int(params, "top_terms", 30),
            top_symbols=_agent_int(params, "top_symbols", 30),
            verify=bool(params.get("verify", False)),
        )
    if method == "inspect_diff.build":
        base_pack, target_pack = _agent_pack_pair(params, settings, method_name="inspect_diff.build")
        return inspect_pack_diff(
            base_pack,
            target_pack,
            max_files=_agent_int(params, "max_files", 20),
            top_terms=_agent_int(params, "top_terms", 30),
            top_symbols=_agent_int(params, "top_symbols", 30),
            verify=bool(params.get("verify", False)),
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
        base_pack, target_pack = _agent_pack_pair(params, settings, method_name="diff_context.build")
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
    if method == "handoff.score":
        handoff_dir = _agent_required_str(params, "handoff_dir")
        return score_handoff_package(handoff_dir)
    if method == "handoff.triage":
        score_or_handoff = _agent_required_str(params, "score_or_handoff")
        return triage_handoff_score(score_or_handoff, limit=_agent_int(params, "limit", 8))
    if method == "handoff.quality":
        score_or_handoff = _agent_required_str(params, "score_or_handoff")
        return evaluate_handoff_quality(
            score_or_handoff,
            profile=str(params.get("profile", "safe")),
            target_score=_agent_optional_number(params, "target_score", None),
        )
    if method == "handoff.improve":
        out = params.get("out") or params.get("out_dir")
        if not isinstance(out, str) or not out.strip():
            raise ValueError("handoff.improve requires params.out or params.out_dir.")
        target_score = _agent_optional_number(params, "target_score", 90.0)
        return improve_handoff_package(
            _agent_pack(params, settings),
            _agent_required_str(params, "question"),
            out,
            base_pack=params.get("base_pack"),
            force=bool(params.get("force", False)),
            copy_pack=bool(params.get("copy_pack", False)),
            allow_unverified=bool(params.get("allow_unverified", False)),
            target_score=90.0 if target_score is None else target_score,
            quality_profile=str(params.get("quality_profile", "ci")),
            max_attempts=_agent_int(params, "max_attempts", 3),
            max_files=_agent_int(params, "max_files", 8),
            max_bytes=_agent_optional_int(params, "max_bytes", 4096),
            snippet_lines=_agent_int(params, "snippet_lines", 12),
            snippets_per_file=_agent_int(params, "snippets_per_file", 2),
            capsule_max_files=_agent_optional_int(params, "capsule_max_files", None),
            top_terms=_agent_int(params, "top_terms", 128),
            eval_questions=params.get("eval_questions"),
        )
    if method == "handoff.archive":
        return archive_handoff_package(
            _agent_required_str(params, "handoff_dir"),
            params.get("out"),
            force=bool(params.get("force", False)),
            quality_profile=str(params.get("quality_profile", "safe")),
        )
    if method == "handoff.health":
        return build_handoff_health_report(
            _agent_required_str(params, "handoff_dir"),
            profile=str(params.get("profile", "safe")),
            target_score=_agent_optional_number(params, "target_score", None),
            improve_pack=params.get("improve_pack"),
            question=params.get("question"),
            improve_out=params.get("improve_out") or params.get("out"),
            base_pack=params.get("base_pack"),
            force=bool(params.get("force", False)),
            copy_pack=bool(params.get("copy_pack", False)),
            allow_unverified=bool(params.get("allow_unverified", False)),
            archive=bool(params.get("archive", False)),
            archive_out=params.get("archive_out"),
            max_attempts=_agent_int(params, "max_attempts", 3),
            max_files=_agent_int(params, "max_files", 8),
            max_bytes=_agent_optional_int(params, "max_bytes", 4096),
            snippet_lines=_agent_int(params, "snippet_lines", 12),
            snippets_per_file=_agent_int(params, "snippets_per_file", 2),
            capsule_max_files=_agent_optional_int(params, "capsule_max_files", None),
            top_terms=_agent_int(params, "top_terms", 128),
            eval_questions=params.get("eval_questions"),
            health_log=params.get("health_log"),
            artifacts_dir=params.get("artifacts_dir"),
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
        "anchor_out": params.get("anchor_out") if params.get("anchor_out") is not None else settings.get("anchor_out"),
        "anchor_verify": bool(params.get("anchor_verify", settings.get("anchor_verify", False))),
        "allow_unverified_anchor": bool(params.get("allow_unverified_anchor", settings.get("allow_unverified_anchor", False))),
        "anchor_freshness": params.get("anchor_freshness", settings.get("anchor_freshness")),
        "anchor_log": params.get("anchor_log") if params.get("anchor_log") is not None else settings.get("anchor_log"),
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
        "handoff_quality_profile": params.get("handoff_quality_profile", settings.get("handoff_quality_profile")),
        "handoff_quality_target": _agent_optional_number(params, "handoff_quality_target", settings.get("handoff_quality_target")),
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


def _agent_pack_pair(params: dict[str, Any], settings: dict[str, Any], *, method_name: str = "diff_context.build") -> tuple[str, str]:
    base_pack = params.get("base_pack")
    target_pack = params.get("target_pack")
    if isinstance(base_pack, str) and base_pack.strip() and isinstance(target_pack, str) and target_pack.strip():
        return base_pack, target_pack
    out_dir = params.get("out_dir", settings.get("out_dir"))
    if not isinstance(out_dir, str) or not out_dir.strip():
        raise ValueError(f"{method_name} requires params.base_pack and params.target_pack, or params.out_dir/config out_dir.")
    timeline = read_snapshot_timeline(out_dir, limit=2)
    snapshots = timeline.get("snapshots", [])
    if len(snapshots) < 2:
        raise ValueError(f"{method_name} requires at least two snapshots in the timeline.")
    latest = snapshots[0]
    previous = snapshots[1]
    if not isinstance(latest, dict) or not isinstance(previous, dict):
        raise ValueError(f"{method_name} could not resolve latest and previous snapshots.")
    latest_pack = latest.get("pack_path")
    previous_pack = previous.get("pack_path")
    if not latest_pack or not previous_pack:
        raise ValueError(f"{method_name} snapshot entries must include pack_path.")
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


def _agent_optional_number(params: dict[str, Any], key: str, default: Any) -> float | None:
    if key not in params:
        value = default
    else:
        value = params[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"params.{key} must be a number or null.")
    return float(value)


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
    elif name == "repomori_brief_build":
        summary = payload.get("summary", {})
        lines.append(f"status: {payload.get('status')}")
        lines.append(f"snapshots: {summary.get('snapshot_count')}")
        lines.append(f"doctor: {summary.get('doctor_status')}")
        lines.append(f"latest_pack: {summary.get('latest_pack_path')}")
        lines.append(f"diff_context: {summary.get('diff_context_status')}")
    elif name == "repomori_chain_verify":
        summary = payload.get("summary", {})
        lines.append(f"status: {payload.get('status')}")
        lines.append(f"checked: {summary.get('checked_count')}")
        lines.append(f"head: {summary.get('head_chain_hash')}")
        lines.append(f"anchored: {summary.get('anchored_to_pruned_history')}")
    elif name == "repomori_anchor_build":
        chain = payload.get("chain", {})
        latest = payload.get("latest_snapshot") or {}
        lines.append(f"status: {payload.get('status')}")
        lines.append(f"head: {chain.get('head_chain_hash')}")
        lines.append(f"anchor_hash: {payload.get('anchor_hash')}")
        lines.append(f"latest_pack: {latest.get('pack_path')}")
    elif name == "repomori_anchor_verify":
        summary = payload.get("summary", {})
        lines.append(f"status: {payload.get('status')}")
        lines.append(f"anchor_hash_valid: {summary.get('anchor_hash_valid')}")
        lines.append(f"head_matches: {summary.get('chain_head_matches')}")
        lines.append(f"latest_matches: {summary.get('latest_snapshot_matches')}")
    elif name == "repomori_file_get":
        lines.append(f"path: {payload.get('path')}")
        lines.append(f"size: {payload.get('size')}")
        lines.append(f"sha256: {payload.get('sha256')}")
    elif name == "repomori_pack_inspect":
        summary = payload.get("summary", {})
        lines.append(f"status: {payload.get('status')}")
        lines.append(f"files: {summary.get('file_count')}")
        lines.append(f"text_files: {summary.get('text_files')}")
        lines.append(f"binary_files: {summary.get('binary_files')}")
        lines.append(f"logical_bytes: {summary.get('logical_bytes')}")
        lines.append(f"pack_bytes: {summary.get('pack_bytes')}")
        lines.append(f"verification: {payload.get('verification', {}).get('status')}")
    elif name == "repomori_pack_inspect_diff":
        summary = payload.get("summary", {})
        lines.append(f"status: {payload.get('status')}")
        lines.append(f"added: {summary.get('added_count')}")
        lines.append(f"removed: {summary.get('removed_count')}")
        lines.append(f"changed: {summary.get('changed_count')}")
        lines.append(f"file_count_delta: {summary.get('file_count_delta')}")
        lines.append(f"logical_bytes_delta: {summary.get('logical_bytes_delta')}")
        lines.append(f"pack_bytes_delta: {summary.get('pack_bytes_delta')}")
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
    elif name == "repomori_compat_check":
        summary = payload.get("summary", {})
        lines.append(f"status: {payload.get('status')}")
        lines.append(f"checks: {summary.get('check_count')}")
        lines.append(f"warnings: {summary.get('warning_count')}")
        lines.append(f"errors: {summary.get('error_count')}")
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
        for optional_key in ("line", "severity", "match", "message"):
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
    fallback_signature_counts = _scan_fallback_finding_signature_counts(findings)
    baseline_signature_counts = _scan_fallback_baseline_signature_counts(baseline_entries)
    ignore_code_set = set(ignore_codes)
    for finding in findings:
        reason = None
        if finding.get("code") in ignore_code_set:
            reason = "ignore_code"
        else:
            match, match_type = _scan_matching_baseline_entry(
                finding,
                baseline_entries,
                fallback_finding_signature_counts=fallback_signature_counts,
                baseline_signature_counts=baseline_signature_counts,
            )
            if match is not None:
                reason = "baseline"
        if reason:
            ignored_item = dict(finding)
            ignored_item["ignored_reason"] = reason
            if reason == "baseline" and match_type is not None:
                ignored_item["baseline_match"] = match_type
            ignored.append(ignored_item)
            continue
        active.append(finding)
    return active, ignored


def _scan_fallback_signature(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("code", "")),
        str(item.get("path", "")),
        str(item.get("severity", "")),
        str(item.get("message", "")).strip(),
    )


def _scan_fallback_finding_signature_counts(findings: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], int]:
    counts: dict[tuple[str, str, str, str], int] = {}
    for finding in findings:
        key = _scan_fallback_signature(finding)
        if not key[2] or not key[3]:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _scan_fallback_baseline_signature_counts(entries: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], int]:
    counts: dict[tuple[str, str, str, str], int] = {}
    for entry in entries:
        if "message" not in entry or "severity" not in entry:
            continue
        key = _scan_fallback_signature(entry)
        if not key[2] or not key[3]:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _scan_matching_baseline_entry(
    finding: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    fallback_finding_signature_counts: dict[tuple[str, str, str, str], int],
    baseline_signature_counts: dict[tuple[str, str, str, str], int],
) -> tuple[dict[str, Any] | None, str | None]:
    finding_code = finding.get("code")
    finding_path = finding.get("path")
    finding_severity = str(finding.get("severity", ""))
    finding_line = finding.get("line")
    finding_match = finding.get("match")
    finding_message = str(finding.get("message", "")).strip()

    for entry in entries:
        if entry.get("code") != finding_code:
            continue
        if entry.get("path") != finding_path:
            continue
        if entry.get("severity") is not None and str(entry.get("severity")) != finding_severity:
            continue
        if "line" not in entry or entry.get("line") is None:
            continue
        if entry.get("line") != finding_line:
            continue
        if entry.get("match") is not None and entry.get("match") != finding_match:
            continue
        return entry, "strict"

    for entry in entries:
        if entry.get("code") != finding_code:
            continue
        if entry.get("path") != finding_path:
            continue
        if entry.get("severity") is not None and str(entry.get("severity")) != finding_severity:
            continue
        if entry.get("match") is None:
            continue
        if entry.get("match") != finding_match:
            continue
        if entry.get("line") == finding_line:
            continue
        return entry, "semi_strict"

    if not finding_message:
        return None, None
    signature = _scan_fallback_signature(
        {
            "code": finding_code,
            "path": finding_path,
            "severity": finding_severity,
            "message": finding_message,
        }
    )
    if (
        baseline_signature_counts.get(signature, 0) != 1
        or fallback_finding_signature_counts.get(signature, 0) != 1
    ):
        return None, None

    for entry in entries:
        if entry.get("code") != finding_code:
            continue
        if entry.get("path") != finding_path:
            continue
        if entry.get("severity") is not None and str(entry.get("severity")) != finding_severity:
            continue
        if entry.get("message") is None:
            continue
        if str(entry.get("message")).strip() != finding_message:
            continue
        return entry, "fallback"

    return None, None


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
        if finding.get("match") is not None:
            entry["match"] = finding.get("match")
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
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_/-]{2,}", query) or [
        part for part in query.split() if part.strip()
    ]
    tokens: list[str] = []
    for token in raw_tokens:
        tokens.extend(_identifier_terms(token, include_original=True))
    return _unique_items(
        token
        for token in tokens
        if len(token) >= 3 and len(token) <= 48 and token not in STOPWORDS
    )


def _identifier_terms(value: str, *, include_original: bool = False) -> list[str]:
    original = value.strip().lower()
    split_value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    split_value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", split_value)
    parts = [part.lower() for part in re.split(r"[^A-Za-z0-9]+", split_value) if part]
    terms = []
    if include_original and original and original not in parts and len(parts) <= 1:
        terms.append(original)
    terms.extend(parts)
    return _unique_items(terms)


def _token_variants(token: str) -> list[str]:
    variants = []
    if len(token) > 5 and token.endswith("ies"):
        variants.append(token[:-3] + "y")
    if len(token) > 5 and token.endswith("ing"):
        stem = token[:-3]
        if len(stem) > 3 and stem[-1] == stem[-2]:
            stem = stem[:-1]
        variants.append(stem)
    if len(token) > 4 and token.endswith("ed"):
        variants.append(token[:-2])
    if len(token) > 4 and token.endswith("es"):
        variants.append(token[:-2])
    if len(token) > 3 and token.endswith("s"):
        variants.append(token[:-1])
    return _unique_items(
        variant
        for variant in variants
        if len(variant) >= 3 and variant != token and variant not in STOPWORDS
    )


def _expanded_query_terms(tokens: list[str]) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(token: str, query_token: str, kind: str, factor: float) -> None:
        if not token or len(token) < 3 or token in STOPWORDS:
            return
        key = (token, query_token, kind)
        if key in seen:
            return
        seen.add(key)
        terms.append(
            {
                "token": token,
                "query_token": query_token,
                "kind": kind,
                "factor": factor,
            }
        )

    for query_token in tokens:
        add(query_token, query_token, "query", 1.0)
        roots = [query_token, *_token_variants(query_token)]
        for variant in roots[1:]:
            add(variant, query_token, "variant", 0.7)
        for root in roots:
            for alias in QUERY_TOKEN_ALIASES.get(root, ()):
                alias = alias.lower()
                add(alias, query_token, "alias", 0.45)
                for variant in _token_variants(alias):
                    add(variant, query_token, "alias", 0.35)
    return terms


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
    compact_phrase = re.sub(r"[^a-z0-9]+", "", token_phrase)
    phrases = []
    for phrase in (raw, token_phrase):
        if " " in phrase and phrase not in phrases:
            phrases.append(phrase)
    if len(compact_phrase) >= 6 and compact_phrase not in tokens and compact_phrase not in phrases:
        phrases.append(compact_phrase)
    return phrases


def _score_query_value(
    path: str,
    field: str,
    value: str,
    terms: list[dict[str, Any]],
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

    for term in terms:
        term_text = str(term.get("token", ""))
        if not term_text:
            continue
        if term_text not in normalized:
            continue
        query_token = str(term.get("query_token") or term_text)
        term_kind = str(term.get("kind") or "query")
        factor = float(term.get("factor") or 1.0)
        added = weight * factor
        scores[path] += added
        reasons[path].add(field)
        if term_kind != "query":
            reasons[path].add(f"{term_kind}-{field}")
        matched_tokens[path].add(query_token)
        if breakdown is not None:
            breakdown[path].append(
                {
                    "field": field,
                    "kind": "token" if term_kind == "query" else term_kind,
                    "token": query_token,
                    "matched_token": term_text,
                    "value": _trace_value(value),
                    "weight": round(added, 2),
                }
            )
        if term_text in value_terms:
            exact_added = weight * 0.5 * factor
            scores[path] += exact_added
            exact_reason = f"exact-{field}" if term_kind == "query" else f"exact-{term_kind}-{field}"
            reasons[path].add(exact_reason)
            if breakdown is not None:
                breakdown[path].append(
                    {
                        "field": field,
                        "kind": "exact" if term_kind == "query" else f"exact-{term_kind}",
                        "token": query_token,
                        "matched_token": term_text,
                        "value": _trace_value(value),
                        "weight": round(exact_added, 2),
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
