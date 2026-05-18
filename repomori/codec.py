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
import shutil
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
DEFAULT_EVAL_QUESTIONS = (
    "Where is the command-line interface defined?",
    "How are files stored, compressed, or restored?",
    "What tests cover the project behavior?",
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
    readme_path.write_text(_handoff_readme(question, copy_pack), encoding="utf-8")
    artifacts.append(_artifact_record(out_path, readme_path, "handoff_readme"))

    manifest = _handoff_manifest(
        question,
        out_path,
        pack_info,
        verify_report,
        artifacts,
        status,
        {
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

        for name in ("context.json", "capsule.json", "eval.json", "verify.json"):
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
        },
        "artifacts": {
            "pack": pack_path.name,
            "handoff": handoff_path.name,
            "bench_json": "bench.json",
            "bench_markdown": "bench.md",
        },
        "build": build,
        "verify": verify,
        "eval": eval_report,
        "handoff": handoff,
        "handoff_check": handoff_check,
    }
    _write_json(out_path / "bench.json", report)
    (out_path / "bench.md").write_text(format_benchmark_markdown(report), encoding="utf-8")
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
) -> dict[str, Any]:
    return {
        "schema_version": "repomori.handoff.v1",
        "status": status,
        "created_at": int(time.time()),
        "question": question,
        "out_dir": str(out_path),
        "pack": {
            "schema_version": pack_info.get("schema_version"),
            "repo_path": pack_info.get("repo_path"),
            "pack_path": pack_info.get("pack_path"),
            "created_at": pack_info.get("created_at"),
            "logical_bytes": pack_info.get("logical_bytes"),
            "pack_bytes": pack_info.get("pack_bytes"),
            "counts": pack_info.get("counts", {}),
        },
        "verification": {
            "verified": verify_report.get("verified"),
            "error_count": verify_report.get("error_count"),
            "artifact": "verify.json",
        },
        "settings": settings,
        "artifacts": artifacts,
    }


def _handoff_readme(question: str, copied_pack: bool) -> str:
    pack_note = (
        "The `.repomori` pack is included in this directory.\n"
        if copied_pack
        else "The original `.repomori` pack is referenced in `manifest.json` but not copied here.\n"
    )
    return (
        "# RepoMori Agent Handoff\n\n"
        f"Question: {question}\n\n"
        "Use these files in order:\n\n"
        "1. `manifest.json` - artifact list, hashes, settings, and verification status.\n"
        "2. `context.md` - compact source-backed context for quick reading.\n"
        "3. `context.json` - raw context bundle for tools.\n"
        "4. `capsule.json` - dense machine-readable repository state.\n"
        "5. `eval.md` / `eval.json` - context quality report.\n"
        "6. `verify.json` - pack integrity report.\n\n"
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
