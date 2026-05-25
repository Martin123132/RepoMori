"""RepoMori command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .codec import (
    BuildOptions,
    benchmark_repo,
    build_repo_brief,
    build_capsule,
    build_context_bundle,
    build_handoff_package,
    build_pack,
    check_handoff_package,
    compare_packs,
    diagnose_query,
    doctor_snapshot_dir,
    evaluate_pack,
    format_brief_markdown,
    format_compare_markdown,
    format_context_markdown,
    format_eval_markdown,
    format_snapshot_markdown,
    format_timeline_markdown,
    get_file_bytes,
    init_config,
    info_pack,
    load_memory_config,
    query_pack,
    read_snapshot_timeline,
    prune_snapshots,
    run_agent_bridge,
    run_memory_cycle,
    schema_catalog,
    snapshot_repo,
    tree_pack,
    verify_pack,
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

    init = sub.add_parser("init", help="Write a RepoMori config file.")
    init.add_argument("repo", type=Path, help="Repository folder to remember.")
    init.add_argument("--out-dir", type=Path, required=True, help="Directory for snapshot packs and reports.")
    init.add_argument("--config", type=Path, help="Config file path; defaults to <repo>/repomori.toml.")
    init.add_argument("--profile", default="default", help="Profile name to write.")
    init.add_argument("--force", action="store_true", help="Overwrite an existing config file.")
    init.add_argument("--handoff-question", default="continue this repo")
    init.add_argument("--no-handoff", action="store_true", help="Skip default handoffs in this profile.")
    init.add_argument("--keep", type=int, default=20, help="Newest snapshots to keep in addition to latest.")
    init.add_argument("--prune-apply", action="store_true", help="Apply safe prune in this profile.")
    init.add_argument("--verify-packs", action="store_true", help="Run full pack verification during doctor.")
    init.add_argument("--timeline-limit", type=int, default=5, help="Recent snapshots to return.")
    init.add_argument("--chunk-size", type=int, default=256 * 1024)
    init.add_argument("--no-compare", action="store_true", help="Do not compare against latest.repomori.")
    init.add_argument("--compare-limit", type=int, default=50)
    init.add_argument("--json", action="store_true", help="Print config init JSON.")

    snapshot = sub.add_parser("snapshot", help="Build a timestamped pack snapshot.")
    snapshot.add_argument("repo", type=Path)
    snapshot.add_argument("--out-dir", type=Path, required=True, help="Directory for snapshot packs and reports.")
    snapshot.add_argument("--chunk-size", type=int, default=256 * 1024)
    snapshot.add_argument("--no-compare", action="store_true", help="Do not compare against latest.repomori.")
    snapshot.add_argument("--compare-limit", type=int, default=50)
    snapshot.add_argument("--handoff", help="Build a handoff package for this snapshot using this question.")
    snapshot.add_argument("--handoff-out", type=Path, help="Directory for the snapshot handoff package.")
    snapshot.add_argument("--handoff-force", action="store_true", help="Overwrite an existing snapshot handoff.")
    snapshot.add_argument("--json", action="store_true", help="Print snapshot JSON.")

    timeline = sub.add_parser("timeline", help="Read a snapshot index timeline.")
    timeline.add_argument("out_dir", type=Path)
    timeline.add_argument("--limit", type=int, help="Maximum recent snapshots to return.")
    timeline.add_argument("--format", choices=("markdown", "json"), default="markdown")
    timeline.add_argument("--out", type=Path, help="Write the timeline report to this file.")

    doctor = sub.add_parser("doctor", help="Check snapshot directory health.")
    doctor.add_argument("out_dir", type=Path)
    doctor.add_argument("--verify-packs", action="store_true", help="Run full pack verification for indexed packs.")
    doctor.add_argument("--json", action="store_true", help="Print doctor JSON.")

    prune = sub.add_parser("prune", help="Plan or apply safe snapshot cleanup.")
    prune.add_argument("out_dir", type=Path)
    prune.add_argument("--keep", type=int, default=20, help="Newest snapshots to keep in addition to latest.")
    prune.add_argument("--apply", action="store_true", help="Delete planned in-dir artifacts and update snapshots.json.")
    prune.add_argument("--json", action="store_true", help="Print prune JSON.")

    memory = sub.add_parser("memory", help="Run snapshot, handoff, doctor, prune, and timeline.")
    memory.add_argument("repo", type=Path, nargs="?", help="Repository folder; falls back to repomori.toml.")
    memory.add_argument("--out-dir", type=Path, help="Directory for snapshot packs and reports.")
    memory.add_argument("--config", type=Path, help="Config file path; defaults to nearest repomori.toml.")
    memory.add_argument("--profile", help="Config profile to use.")
    memory.add_argument("--handoff-question")
    handoff_group = memory.add_mutually_exclusive_group()
    handoff_group.add_argument("--no-handoff", dest="no_handoff", action="store_true", default=None, help="Skip the default snapshot handoff package.")
    handoff_group.add_argument("--with-handoff", dest="no_handoff", action="store_false", help="Force handoff even if config disables it.")
    memory.add_argument("--keep", type=int, help="Newest snapshots to keep in addition to latest.")
    prune_group = memory.add_mutually_exclusive_group()
    prune_group.add_argument("--prune-apply", dest="prune_apply", action="store_true", default=None, help="Apply safe prune after the snapshot.")
    prune_group.add_argument("--prune-dry-run", dest="prune_apply", action="store_false", help="Force prune dry-run even if config applies it.")
    verify_group = memory.add_mutually_exclusive_group()
    verify_group.add_argument("--verify-packs", dest="verify_packs", action="store_true", default=None, help="Run full pack verification during doctor.")
    verify_group.add_argument("--no-verify-packs", dest="verify_packs", action="store_false", help="Skip full pack verification during doctor.")
    memory.add_argument("--timeline-limit", type=int, help="Recent snapshots to return.")
    memory.add_argument("--chunk-size", type=int)
    compare_group = memory.add_mutually_exclusive_group()
    compare_group.add_argument("--no-compare", dest="compare", action="store_false", default=None, help="Do not compare against latest.repomori.")
    compare_group.add_argument("--compare", dest="compare", action="store_true", help="Compare against latest.repomori.")
    memory.add_argument("--compare-limit", type=int)
    memory.add_argument("--json", action="store_true", help="Print memory JSON.")

    agent = sub.add_parser("agent", help="Run the JSON-lines agent bridge on stdio.")
    agent.add_argument("--config", type=Path, help="Config file path; defaults to nearest repomori.toml.")
    agent.add_argument("--profile", help="Config profile to use.")

    schema = sub.add_parser("schema", help="Show supported RepoMori schemas and agent methods.")
    schema.add_argument("schema_version", nargs="?", help="Specific schema version to show.")
    schema.add_argument("--json", action="store_true", help="Print schema JSON.")

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

    diagnose = sub.add_parser("diagnose", help="Explain query ranking and snippet selection.")
    diagnose.add_argument("pack", type=Path)
    diagnose.add_argument("question")
    diagnose.add_argument("--limit", type=int, default=8)
    diagnose.add_argument("--max-files", type=int, help="Alias for --limit.")
    diagnose.add_argument("--snippet-lines", type=int, default=12)
    diagnose.add_argument("--snippets-per-file", type=int, default=2)
    diagnose.add_argument("--max-bytes", type=int, help="Maximum total snippet text bytes.")
    diagnose.add_argument("--json", action="store_true")

    compare = sub.add_parser("compare", help="Compare two .repomori packs.")
    compare.add_argument("base_pack", type=Path)
    compare.add_argument("target_pack", type=Path)
    compare.add_argument("--limit", type=int, default=50)
    compare.add_argument("--include-unchanged", action="store_true")
    compare.add_argument("--format", choices=("markdown", "json"), default="markdown")
    compare.add_argument("--out", type=Path, help="Write the comparison report to this file.")

    brief = sub.add_parser("brief", help="Build a question-free repository orientation brief.")
    brief.add_argument("pack", type=Path)
    brief.add_argument("--max-files", type=int, default=12)
    brief.add_argument("--top-terms", type=int, default=40)
    brief.add_argument("--top-symbols", type=int, default=40)
    brief.add_argument("--format", choices=("markdown", "json"), default="markdown")
    brief.add_argument("--out", type=Path, help="Write the brief to this file.")

    context = sub.add_parser("context", help="Build source-backed agent context.")
    context.add_argument("pack", type=Path)
    context.add_argument("question")
    context.add_argument("--limit", type=int, default=8)
    context.add_argument("--max-files", type=int, help="Alias for --limit.")
    context.add_argument("--snippet-lines", type=int, default=12)
    context.add_argument("--snippets-per-file", type=int, default=2)
    context.add_argument("--max-bytes", type=int, help="Maximum total snippet text bytes.")
    context.add_argument("--no-source", action="store_true", help="Return rankings and metadata without snippets.")
    context.add_argument("--format", choices=("markdown", "json"), default="markdown")
    context.add_argument("--out", type=Path, help="Write the context bundle to this file.")

    verify = sub.add_parser("verify", help="Verify pack chunks, hashes, and source recovery.")
    verify.add_argument("pack", type=Path)
    verify.add_argument("--json", action="store_true")

    eval_cmd = sub.add_parser("eval", help="Evaluate context usefulness for a pack.")
    eval_cmd.add_argument("pack", type=Path)
    eval_cmd.add_argument("--question", action="append", help="Question to evaluate; repeat for more.")
    eval_cmd.add_argument("--questions-file", type=Path, help="Read one eval question per line.")
    eval_cmd.add_argument("--limit", type=int, default=5)
    eval_cmd.add_argument("--max-files", type=int, help="Alias for --limit.")
    eval_cmd.add_argument("--snippet-lines", type=int, default=10)
    eval_cmd.add_argument("--snippets-per-file", type=int, default=2)
    eval_cmd.add_argument("--max-bytes", type=int, default=4096, help="Maximum snippet text bytes per question.")
    eval_cmd.add_argument("--no-source", action="store_true", help="Evaluate rankings and metadata without snippets.")
    eval_cmd.add_argument("--format", choices=("markdown", "json"), default="markdown")
    eval_cmd.add_argument("--out", type=Path, help="Write the eval report to this file.")

    capsule = sub.add_parser("capsule", help="Export a dense machine-readable capsule.")
    capsule.add_argument("pack", type=Path)
    capsule.add_argument("--max-files", type=int, help="Maximum files to include.")
    capsule.add_argument("--top-terms", type=int, default=128, help="Vocabulary terms to include.")
    capsule.add_argument("--out", type=Path, help="Write capsule JSON to this file.")

    handoff = sub.add_parser("handoff", help="Build an agent handoff package directory.")
    handoff.add_argument("pack", type=Path)
    handoff.add_argument("question")
    handoff.add_argument("--out", type=Path, required=True, help="Directory to write handoff artifacts.")
    handoff.add_argument("--base-pack", type=Path, help="Previous pack to compare against.")
    handoff.add_argument("--force", action="store_true", help="Overwrite an existing handoff directory.")
    handoff.add_argument("--copy-pack", action="store_true", help="Copy the .repomori pack into the handoff.")
    handoff.add_argument("--allow-unverified", action="store_true", help="Continue when pack verification fails.")
    handoff.add_argument("--max-files", type=int, default=8)
    handoff.add_argument("--max-bytes", type=int, help="Maximum total snippet text bytes.")
    handoff.add_argument("--snippet-lines", type=int, default=12)
    handoff.add_argument("--snippets-per-file", type=int, default=2)
    handoff.add_argument("--capsule-max-files", type=int)
    handoff.add_argument("--top-terms", type=int, default=128)
    handoff.add_argument("--eval-question", action="append", help="Extra eval question; repeat for more.")
    handoff.add_argument("--questions-file", type=Path, help="Read extra eval questions, one per line.")
    handoff.add_argument("--json", action="store_true", help="Print manifest JSON.")

    check_handoff = sub.add_parser("check-handoff", help="Validate a handoff package directory.")
    check_handoff.add_argument("handoff_dir", type=Path)
    check_handoff.add_argument("--json", action="store_true")

    bench = sub.add_parser("bench", help="Run an end-to-end repository benchmark.")
    bench.add_argument("repo", type=Path)
    bench.add_argument("--out", type=Path, required=True, help="Directory to write benchmark artifacts.")
    bench.add_argument("--question", default="How should an agent understand and continue this repository?")
    bench.add_argument("--force", action="store_true", help="Overwrite an existing benchmark directory.")
    bench.add_argument("--chunk-size", type=int, default=256 * 1024)
    bench.add_argument("--max-files", type=int, default=8)
    bench.add_argument("--max-bytes", type=int, default=4096)
    bench.add_argument("--snippet-lines", type=int, default=12)
    bench.add_argument("--snippets-per-file", type=int, default=2)
    bench.add_argument("--capsule-max-files", type=int)
    bench.add_argument("--top-terms", type=int, default=128)
    bench.add_argument("--eval-question", action="append", help="Extra eval question; repeat for more.")
    bench.add_argument("--questions-file", type=Path, help="Read extra eval questions, one per line.")
    bench.add_argument("--copy-pack", action="store_true", help="Copy the pack into the handoff.")
    bench.add_argument("--json", action="store_true", help="Print benchmark JSON.")

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
    if args.command == "init":
        result = init_config(
            args.repo,
            args.out_dir,
            config_path=args.config,
            profile=args.profile,
            force=args.force,
            handoff_question=args.handoff_question,
            no_handoff=args.no_handoff,
            keep=args.keep,
            prune_apply=args.prune_apply,
            verify_packs=args.verify_packs,
            timeline_limit=args.timeline_limit,
            chunk_size=args.chunk_size,
            compare=not args.no_compare,
            compare_limit=args.compare_limit,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"config: {result['config_path']}")
            print(f"profile: {result['profile']}")
            print(f"repo: {result['settings']['repo']}")
            print(f"out_dir: {result['settings']['out_dir']}")
        return 0
    if args.command == "snapshot":
        report = snapshot_repo(
            args.repo,
            args.out_dir,
            chunk_size=args.chunk_size,
            compare=not args.no_compare,
            compare_limit=args.compare_limit,
            handoff_question=args.handoff,
            handoff_out_dir=args.handoff_out,
            handoff_force=args.handoff_force,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(format_snapshot_markdown(report), end="")
        return 0 if report["status"] == "pass" else 1
    if args.command == "timeline":
        report = read_snapshot_timeline(args.out_dir, limit=args.limit)
        output = (
            json.dumps(report, indent=2)
            if args.format == "json"
            else format_timeline_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0
    if args.command == "doctor":
        result = doctor_snapshot_dir(args.out_dir, verify_packs=args.verify_packs)
        _print(result, args.json)
        return 0 if result["status"] != "fail" else 1
    if args.command == "prune":
        result = prune_snapshots(args.out_dir, keep=args.keep, apply=args.apply)
        _print(result, args.json)
        return 0 if not result["errors"] else 1
    if args.command == "memory":
        settings = _memory_settings(args, parser)
        report = run_memory_cycle(
            settings["repo"],
            settings["out_dir"],
            handoff_question=settings["handoff_question"],
            no_handoff=settings["no_handoff"],
            keep=settings["keep"],
            prune_apply=settings["prune_apply"],
            verify_packs=settings["verify_packs"],
            timeline_limit=settings["timeline_limit"],
            chunk_size=settings["chunk_size"],
            compare=settings["compare"],
            compare_limit=settings["compare_limit"],
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"memory: {report['out_dir']}")
            print(f"status: {report['status']}")
            print(f"pack: {report['summary']['pack_path']}")
            print(f"handoff: {report['summary']['handoff_dir']}")
            print(f"prune applied: {report['summary']['prune_applied']}")
            print(f"timeline snapshots: {report['summary']['timeline_snapshot_count']}")
        return 0 if report["status"] != "fail" else 1
    if args.command == "agent":
        return run_agent_bridge(
            sys.stdin,
            sys.stdout,
            config_path=args.config,
            profile=args.profile,
            start_dir=Path.cwd(),
        )
    if args.command == "schema":
        try:
            report = schema_catalog(args.schema_version)
        except ValueError as exc:
            parser.error(str(exc))
        if args.json:
            print(json.dumps(report, indent=2))
        elif args.schema_version:
            schema = report["schema"]
            print(f"{schema['schema_version']}: {schema['title']}")
            print(f"kind: {schema['kind']}")
            print(f"producer: {schema['producer']}")
            print("required fields: " + ", ".join(schema["required_fields"]))
        else:
            print("schemas:")
            for schema in report["schemas"]:
                print(f"- {schema['schema_version']} [{schema['kind']}] {schema['title']}")
            print("agent methods: " + ", ".join(report["agent_methods"]))
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
    if args.command == "diagnose":
        limit = args.max_files if args.max_files is not None else args.limit
        report = diagnose_query(
            args.pack,
            args.question,
            limit=limit,
            snippet_lines=args.snippet_lines,
            max_bytes=args.max_bytes,
            snippets_per_file=args.snippets_per_file,
        )
        _print(report, args.json)
        return 0
    if args.command == "compare":
        report = compare_packs(
            args.base_pack,
            args.target_pack,
            limit=args.limit,
            include_unchanged=args.include_unchanged,
        )
        output = (
            json.dumps(report, indent=2)
            if args.format == "json"
            else format_compare_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0
    if args.command == "brief":
        brief_report = build_repo_brief(
            args.pack,
            max_files=args.max_files,
            top_terms=args.top_terms,
            top_symbols=args.top_symbols,
        )
        output = (
            json.dumps(brief_report, indent=2)
            if args.format == "json"
            else format_brief_markdown(brief_report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0
    if args.command == "context":
        limit = args.max_files if args.max_files is not None else args.limit
        bundle = build_context_bundle(
            args.pack,
            args.question,
            limit=limit,
            snippet_lines=args.snippet_lines,
            max_bytes=args.max_bytes,
            snippets_per_file=args.snippets_per_file,
            include_source=not args.no_source,
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
    if args.command == "verify":
        result = verify_pack(args.pack)
        _print(result, args.json)
        return 0 if result["verified"] else 1
    if args.command == "eval":
        questions = _eval_questions(args.question, args.questions_file)
        limit = args.max_files if args.max_files is not None else args.limit
        report = evaluate_pack(
            args.pack,
            questions=questions,
            limit=limit,
            snippet_lines=args.snippet_lines,
            max_bytes=args.max_bytes,
            snippets_per_file=args.snippets_per_file,
            include_source=not args.no_source,
        )
        output = (
            json.dumps(report, indent=2)
            if args.format == "json"
            else format_eval_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0
    if args.command == "capsule":
        capsule = build_capsule(args.pack, max_files=args.max_files, top_terms=args.top_terms)
        output = json.dumps(capsule, separators=(",", ":"))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
        return 0
    if args.command == "handoff":
        extra_questions = _eval_questions(args.eval_question, args.questions_file)
        manifest = build_handoff_package(
            args.pack,
            args.question,
            args.out,
            base_pack=args.base_pack,
            force=args.force,
            copy_pack=args.copy_pack,
            allow_unverified=args.allow_unverified,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
            snippet_lines=args.snippet_lines,
            snippets_per_file=args.snippets_per_file,
            capsule_max_files=args.capsule_max_files,
            top_terms=args.top_terms,
            eval_questions=extra_questions,
        )
        if args.json:
            print(json.dumps(manifest, indent=2))
        else:
            print(f"handoff: {manifest['out_dir']}")
            print(f"status: {manifest['status']}")
            print(f"artifacts: {len(manifest['artifacts'])}")
        return 1 if manifest["status"] == "verification_failed" else 0
    if args.command == "check-handoff":
        result = check_handoff_package(args.handoff_dir)
        _print(result, args.json)
        return 0 if result["valid"] else 1
    if args.command == "bench":
        extra_questions = _eval_questions(args.eval_question, args.questions_file)
        report = benchmark_repo(
            args.repo,
            args.out,
            question=args.question,
            force=args.force,
            chunk_size=args.chunk_size,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
            snippet_lines=args.snippet_lines,
            snippets_per_file=args.snippets_per_file,
            capsule_max_files=args.capsule_max_files,
            top_terms=args.top_terms,
            eval_questions=extra_questions,
            copy_pack=args.copy_pack,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"bench: {report['out_dir']}")
            print(f"status: {report['status']}")
            print(f"pack: {report['summary']['pack_path']}")
            print(f"handoff: {report['summary']['handoff_dir']}")
        return 0 if report["status"] == "pass" else 1
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


def _eval_questions(questions: list[str] | None, questions_file: Path | None) -> list[str] | None:
    values = list(questions or [])
    if questions_file:
        values.extend(
            line.strip()
            for line in questions_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return values or None


def _memory_settings(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    config = None
    if args.config is not None or args.profile is not None or args.repo is None or args.out_dir is None:
        try:
            config = load_memory_config(args.config, start_dir=Path.cwd(), profile=args.profile)
        except (FileNotFoundError, ValueError) as exc:
            parser.error(str(exc))
    settings = dict(config.get("settings", {}) if config else {})
    repo = str(args.repo.resolve()) if args.repo is not None else settings.get("repo")
    out_dir = str(args.out_dir.resolve()) if args.out_dir is not None else settings.get("out_dir")
    if not repo:
        parser.error("memory requires a repo argument or a config profile with `repo`.")
    if not out_dir:
        parser.error("memory requires --out-dir or a config profile with `out_dir`.")
    return {
        "repo": repo,
        "out_dir": out_dir,
        "handoff_question": _setting(args.handoff_question, settings, "handoff_question", "continue this repo"),
        "no_handoff": _setting(args.no_handoff, settings, "no_handoff", False),
        "keep": _setting(args.keep, settings, "keep", 20),
        "prune_apply": _setting(args.prune_apply, settings, "prune_apply", False),
        "verify_packs": _setting(args.verify_packs, settings, "verify_packs", False),
        "timeline_limit": _setting(args.timeline_limit, settings, "timeline_limit", 5),
        "chunk_size": _setting(args.chunk_size, settings, "chunk_size", 256 * 1024),
        "compare": _setting(args.compare, settings, "compare", True),
        "compare_limit": _setting(args.compare_limit, settings, "compare_limit", 50),
    }


def _setting(value, settings: dict, key: str, default):
    if value is not None:
        return value
    return settings.get(key, default)


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
