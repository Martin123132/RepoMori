"""Core RepoMori pack format.

The pack is a SQLite database with compressed chunks and small, queryable
machine summaries. Exact source is still recoverable through the chunk map.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
import zlib
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "repomori.pack.v1"
DEFAULT_CHUNK_SIZE = 256 * 1024

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


def build_pack(repo: Path | str, output: Path | str, options: BuildOptions | None = None) -> dict[str, Any]:
    """Build a `.repomori` pack from a repository folder."""

    opts = options or BuildOptions()
    repo_path = Path(repo).resolve()
    output_path = Path(output).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository folder not found: {repo_path}")
    if opts.chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
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
            },
        )
        for path in _iter_repo_files(repo_path, output_path):
            file_stats = _ingest_file(conn, repo_path, path, opts.chunk_size)
            for key, value in file_stats.items():
                stats[key] += value
        chunk_row = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(raw_size),0) AS raw, COALESCE(SUM(compressed_size),0) AS compressed FROM chunks"
        ).fetchone()
        stats["unique_chunks"] = int(chunk_row[0])
        stats["unique_chunk_raw_bytes"] = int(chunk_row[1])
        stats["compressed_chunk_bytes"] = int(chunk_row[2])
        elapsed = time.time() - started
        stats["elapsed_seconds"] = round(elapsed, 4)
        _put_metadata(conn, {"build_summary": stats})
        conn.commit()
    stats["pack_bytes"] = output_path.stat().st_size
    return stats


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

    tokens = _query_tokens(query)
    if not tokens:
        return []
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, set[str]] = defaultdict(set)

    with closing(_open_pack(pack)) as conn:
        files = conn.execute(
            "SELECT path, language, size, sha256, summary_json FROM files"
        ).fetchall()
        for row in files:
            path = row["path"]
            haystack = f"{path} {row['language'] or ''}".lower()
            for token in tokens:
                if token in haystack:
                    scores[path] += 4.0
                    reasons[path].add("path/language")

        index_rows = conn.execute("SELECT path, field, value FROM search_index").fetchall()
        for row in index_rows:
            value = str(row["value"] or "").lower()
            for token in tokens:
                if token in value:
                    weight = _field_weight(str(row["field"]))
                    scores[row["path"]] += weight
                    reasons[row["path"]].add(str(row["field"]))

        if not scores:
            return []
        placeholders = ",".join("?" for _ in scores)
        file_rows = conn.execute(
            f"SELECT path, language, size, sha256, summary_json FROM files WHERE path IN ({placeholders})",
            tuple(scores),
        ).fetchall()

    results = []
    for row in file_rows:
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


def _open_pack(pack: Path | str) -> sqlite3.Connection:
    path = Path(pack)
    if not path.exists():
        raise FileNotFoundError(f"Pack not found: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _iter_repo_files(repo_path: Path, output_path: Path) -> Iterable[Path]:
    output_resolved = output_path.resolve()
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        root_path = Path(root)
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
        "symbol": 8.0,
        "heading": 6.0,
        "import": 5.0,
        "path": 4.0,
        "language": 2.0,
        "term": 1.0,
    }.get(field, 1.0)


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
