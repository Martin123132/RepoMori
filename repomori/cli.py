"""RepoMori command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .codec import (
    BuildOptions,
    build_context_bundle,
    build_pack,
    format_context_markdown,
    get_file_bytes,
    info_pack,
    query_pack,
    tree_pack,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repomori",
        description="Build and query machine-readable repository packs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a .repomori pack from a repository.")
    build.add_argument("repo", type=Path)
    build.add_argument("pack", type=Path)
    build.add_argument("--chunk-size", type=int, default=256 * 1024)
    build.add_argument("--force", action="store_true", help="Overwrite an existing pack.")
    build.add_argument("--json", action="store_true", help="Print JSON output.")

    info = sub.add_parser("info", help="Show pack metadata.")
    info.add_argument("pack", type=Path)
    info.add_argument("--json", action="store_true")

    tree = sub.add_parser("tree", help="List files stored in a pack.")
    tree.add_argument("pack", type=Path)
    tree.add_argument("--limit", type=int, default=200)
    tree.add_argument("--json", action="store_true")

    query = sub.add_parser("query", help="Search the machine-readable pack index.")
    query.add_argument("pack", type=Path)
    query.add_argument("text")
    query.add_argument("--limit", type=int, default=10)
    query.add_argument("--json", action="store_true")

    context = sub.add_parser("context", help="Build source-backed agent context.")
    context.add_argument("pack", type=Path)
    context.add_argument("question")
    context.add_argument("--limit", type=int, default=8)
    context.add_argument("--snippet-lines", type=int, default=12)
    context.add_argument("--format", choices=("markdown", "json"), default="markdown")
    context.add_argument("--out", type=Path, help="Write the context bundle to this file.")

    get = sub.add_parser("get", help="Restore one exact file from the pack.")
    get.add_argument("pack", type=Path)
    get.add_argument("path")
    get.add_argument("--out", type=Path, help="Write restored bytes to this file.")

    args = parser.parse_args(argv)
    if args.command == "build":
        result = build_pack(
            args.repo,
            args.pack,
            BuildOptions(chunk_size=args.chunk_size, force=args.force),
        )
        _print(result, args.json)
        return 0
    if args.command == "info":
        _print(info_pack(args.pack), args.json)
        return 0
    if args.command == "tree":
        _print(tree_pack(args.pack, limit=args.limit), args.json)
        return 0
    if args.command == "query":
        _print(query_pack(args.pack, args.text, limit=args.limit), args.json)
        return 0
    if args.command == "context":
        bundle = build_context_bundle(
            args.pack,
            args.question,
            limit=args.limit,
            snippet_lines=args.snippet_lines,
        )
        output = (
            json.dumps(bundle, indent=2)
            if args.format == "json"
            else format_context_markdown(bundle)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0
    if args.command == "get":
        data = get_file_bytes(args.pack, args.path)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(data)
        else:
            sys.stdout.buffer.write(data)
        return 0
    return 2


def _print(value, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
        return
    if isinstance(value, list):
        for row in value:
            print(_format_row(row))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
        return
    print(value)


def _format_row(row: dict) -> str:
    path = row.get("path", "")
    score = row.get("score")
    language = row.get("language")
    if score is not None:
        why = ",".join(row.get("why", []))
        return f"{score:>6}  {path}  [{language or 'unknown'}]  {why}"
    return f"{path}  [{language or 'unknown'}]  {row.get('size', 0)} bytes"


if __name__ == "__main__":
    raise SystemExit(main())
