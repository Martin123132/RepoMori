"""RepoMori command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .codec import (
    BuildOptions,
    benchmark_repo,
    build_agent_brief,
    build_repo_brief,
    build_snapshot_anchor,
    build_capsule,
    build_context_bundle,
    build_diff_context_bundle,
    build_handoff_package,
    build_pack,
    check_handoff_package,
    compare_packs,
    diagnose_query,
    doctor_snapshot_dir,
    evaluate_pack,
    format_agent_brief_markdown,
    format_brief_markdown,
    format_compare_markdown,
    format_context_markdown,
    format_diff_context_markdown,
    format_eval_markdown,
    format_pack_inspect_markdown,
    format_snapshot_chain_markdown,
    format_snapshot_anchor_markdown,
    format_snapshot_anchor_verification_markdown,
    format_stats_markdown,
    format_snapshot_markdown,
    format_timeline_markdown,
    get_file_bytes,
    init_config,
    info_pack,
    inspect_pack,
    load_memory_config,
    query_pack,
    read_snapshot_stats,
    read_snapshot_timeline,
    prune_snapshots,
    run_agent_bridge,
    run_demo,
    run_release_check,
    run_release_health,
    summarize_baseline_drift_log,
    run_mcp_bridge,
    run_memory_cycle,
    schema_catalog,
    scan_repository,
    snapshot_repo,
    tree_pack,
    verify_snapshot_chain,
    verify_snapshot_anchor,
    verify_pack,
    write_scan_baseline,
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
    build.add_argument("--base", type=Path, help="Reuse unchanged file records and chunks from an existing pack.")
    build.add_argument("--force", action="store_true", help="Overwrite an existing pack.")
    build.add_argument("--json", action="store_true", help="Print JSON output.")

    demo = sub.add_parser("demo", help="Create and run a complete local quickstart demo.")
    demo.add_argument("--out", type=Path, required=True, help="Directory to write the demo repo and artifacts.")
    demo.add_argument("--force", action="store_true", help="Overwrite an existing demo output directory.")
    demo.add_argument("--question", default="sqlite connect Store", help="Question used for query, context, and MCP checks.")
    demo.add_argument("--chunk-size", type=int, default=256 * 1024)
    demo.add_argument("--json", action="store_true", help="Print demo JSON.")

    scan = sub.add_parser("scan", help="Scan a repository for public-release and packing risks.")
    scan.add_argument("repo", type=Path)
    scan.add_argument("--max-file-bytes", type=int, default=1024 * 1024)
    scan.add_argument("--include-hidden", action="store_true", help="Scan hidden dotfiles and dot-directories.")
    scan.add_argument("--public-release", action="store_true", help="Check source-available public-release guardrails.")
    scan.add_argument("--ignore-code", action="append", help="Ignore all findings with this code; repeat for more.")
    scan.add_argument("--baseline", type=Path, help="Ignore findings listed in a scan baseline JSON file.")
    scan.add_argument("--write-baseline", type=Path, help="Write current active findings to a baseline JSON file.")
    scan.add_argument("--fail-on", choices=("info", "low", "medium", "high"), default="high")
    scan.add_argument("--json", action="store_true", help="Print scan JSON.")

    release_check = sub.add_parser("release-check", help="Run local release readiness checks.")
    release_check.add_argument("repo", type=Path, nargs="?", default=Path.cwd(), help="Repository folder to check.")
    release_check.add_argument("--baseline", type=Path, help="Scan baseline; defaults to <repo>/.repomori-scan-baseline.json when present.")
    release_check.add_argument(
        "--fail-on",
        choices=("info", "low", "medium", "high"),
        default="low",
        help=(
            "Exit nonzero if scan findings reach this severity or worse."
            " Baseline drift telemetry is non-blocking by default."
        ),
    )
    release_check.add_argument("--no-public-release", action="store_true", help="Skip public-release guardrail checks in scan.")
    release_check.add_argument("--skip-tests", action="store_true", help="Skip unittest discovery.")
    release_check.add_argument("--skip-demo", action="store_true", help="Skip quickstart demo smoke.")
    release_check.add_argument("--demo-out", type=Path, help="Demo smoke output directory.")
    release_check.add_argument("--keep-demo", action="store_true", help="Keep demo smoke output directory.")
    release_check.add_argument("--tests-dir", default="tests", help="Directory passed to unittest discover.")
    release_check.add_argument("--drift-log", type=Path, help="Append baseline-drift telemetry as JSONL row.")
    release_check.add_argument(
        "--drift-policy",
        type=Path,
        help="JSON drift policy file for non-blocking policy checks.",
    )
    release_check.add_argument(
        "--artifacts-dir",
        type=Path,
        help="Write release-check artifacts to this directory.",
    )
    release_check.add_argument("--json", action="store_true", help="Print release-check JSON.")

    release_health = sub.add_parser("release-health", help="Run release-check, doctor, chain, timeline, and drift-summary.")
    release_health.add_argument("repo", type=Path, nargs="?", default=Path.cwd(), help="Repository folder to check.")
    release_health.add_argument("--snapshot-dir", type=Path, help="Snapshot directory for doctor, chain, and timeline.")
    release_health.add_argument("--baseline", type=Path, help="Scan baseline; defaults to <repo>/.repomori-scan-baseline.json when present.")
    release_health.add_argument(
        "--fail-on",
        choices=("info", "low", "medium", "high"),
        default="low",
        help=(
            "Exit nonzero if scan findings reach this severity or worse. "
            "Baseline drift telemetry remains non-blocking."
        ),
    )
    release_health.add_argument("--no-public-release", action="store_true", help="Skip public-release guardrail checks in scan.")
    release_health.add_argument("--skip-tests", action="store_true", help="Skip unittest discovery.")
    release_health.add_argument("--skip-demo", action="store_true", help="Skip quickstart demo smoke.")
    release_health.add_argument("--demo-out", type=Path, help="Demo smoke output directory.")
    release_health.add_argument("--keep-demo", action="store_true", help="Keep demo smoke output directory.")
    release_health.add_argument("--tests-dir", default="tests", help="Directory passed to unittest discover.")
    release_health.add_argument("--drift-log", type=Path, help="Append baseline-drift telemetry as JSONL row.")
    release_health.add_argument(
        "--drift-policy",
        type=Path,
        help="JSON drift policy file for non-blocking policy checks.",
    )
    release_health.add_argument(
        "--artifacts-dir",
        type=Path,
        help="Write release-health artifacts to this directory.",
    )
    release_health.add_argument("--timeline-limit", type=int, default=5, help="Recent snapshots to include.")
    release_health.add_argument("--drift-summary-limit", type=int, default=20, help="Rows to include in drift-summary.")
    release_health.add_argument(
        "--doctor-verify-packs",
        action="store_true",
        help="Run full pack verification during doctor.",
    )
    release_health.add_argument("--json", action="store_true", help="Print release-health JSON.")

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
    init_incremental_group = init.add_mutually_exclusive_group()
    init_incremental_group.add_argument("--incremental", dest="incremental", action="store_true", default=True, help="Reuse the latest pack as a memory base when available.")
    init_incremental_group.add_argument("--no-incremental", dest="incremental", action="store_false", help="Rebuild snapshot packs without reusing latest pack state.")
    init.add_argument("--no-compare", action="store_true", help="Do not compare against latest.repomori.")
    init.add_argument("--compare-limit", type=int, default=50)
    init.add_argument(
        "--anchor-freshness",
        choices=("safe", "strict", "legacy"),
        help="Anchor freshness mode for memory anchor verification.",
    )
    init.add_argument("--diff-context", action="store_true", help="Write changed-files context during memory runs.")
    init.add_argument("--diff-context-question", default="what changed?")
    init.add_argument("--diff-context-max-files", type=int, default=8)
    init.add_argument("--diff-context-snippet-lines", type=int, default=12)
    init.add_argument("--diff-context-snippets-per-file", type=int, default=2)
    init.add_argument("--diff-context-max-bytes", type=int, default=8192)
    init.add_argument("--diff-context-no-source", action="store_true", help="Configure diff context without exact snippets.")
    init.add_argument("--json", action="store_true", help="Print config init JSON.")

    snapshot = sub.add_parser("snapshot", help="Build a timestamped pack snapshot.")
    snapshot.add_argument("repo", type=Path)
    snapshot.add_argument("--out-dir", type=Path, required=True, help="Directory for snapshot packs and reports.")
    snapshot.add_argument("--chunk-size", type=int, default=256 * 1024)
    snapshot_incremental_group = snapshot.add_mutually_exclusive_group()
    snapshot_incremental_group.add_argument("--incremental", dest="incremental", action="store_true", default=True, help="Reuse the latest pack as a base when available.")
    snapshot_incremental_group.add_argument("--no-incremental", dest="incremental", action="store_false", help="Rebuild every file instead of reusing previous pack state.")
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

    drift_summary = sub.add_parser("drift-summary", help="Summarize baseline drift telemetry from a JSONL log.")
    drift_summary.add_argument("log", type=Path, help="Path to a baseline-drift JSONL log.")
    drift_summary.add_argument("--limit", type=int, default=20, help="Only analyze the newest N rows.")
    drift_summary.add_argument("--json", action="store_true", help="Print JSON output.")

    stats = sub.add_parser("stats", help="Read snapshot reuse and storage statistics.")
    stats.add_argument("out_dir", type=Path)
    stats.add_argument("--limit", type=int, default=10, help="Maximum recent and top-reuse snapshots to return.")
    stats.add_argument("--format", choices=("markdown", "json"), default="markdown")
    stats.add_argument("--out", type=Path, help="Write the stats report to this file.")

    chain = sub.add_parser("chain", help="Verify snapshot timeline hash chain.")
    chain.add_argument("out_dir", type=Path)
    chain.add_argument("--format", choices=("markdown", "json"), default="markdown")
    chain.add_argument("--out", type=Path, help="Write the chain report to this file.")
    chain.add_argument("--json", action="store_true", help="Print chain JSON.")

    anchor = sub.add_parser("anchor", help="Export a snapshot timeline anchor proof.")
    anchor.add_argument("out_dir", type=Path)
    anchor.add_argument("--format", choices=("json", "markdown"), default="json")
    anchor.add_argument("--out", type=Path, help="Write the anchor proof to this file.")
    anchor.add_argument("--json", action="store_true", help="Print anchor JSON.")

    verify_anchor = sub.add_parser("verify-anchor", help="Verify a snapshot timeline anchor proof.")
    verify_anchor.add_argument("anchor", type=Path, help="Anchor JSON file to verify.")
    verify_anchor.add_argument("out_dir", type=Path, nargs="?", help="Snapshot directory to compare against; defaults to anchor out_dir.")
    verify_anchor.add_argument("--no-current", action="store_true", help="Only verify the anchor proof hash, not the current snapshot timeline.")
    verify_anchor.add_argument("--format", choices=("markdown", "json"), default="markdown")
    verify_anchor.add_argument("--out", type=Path, help="Write the verification report to this file.")
    verify_anchor.add_argument("--json", action="store_true", help="Print verification JSON.")

    doctor = sub.add_parser("doctor", help="Check snapshot directory health.")
    doctor.add_argument("out_dir", type=Path)
    doctor.add_argument("--verify-packs", action="store_true", help="Run full pack verification for indexed packs.")
    doctor.add_argument("--json", action="store_true", help="Print doctor JSON.")
    doctor.add_argument("--out", type=Path, help="Write the doctor report to this file.")

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
    memory.add_argument("--anchor-out", type=Path, help="Write a timeline anchor to this file.")
    memory.add_argument(
        "--anchor-verify",
        action="store_true",
        default=None,
        help="Verify the exported anchor against current timeline.",
    )
    memory.add_argument(
        "--anchor-freshness",
        choices=("safe", "strict", "legacy"),
        help="Anchor freshness profile: strict = fail on mismatch, safe = allow mismatch, legacy = proof-only validation.",
    )
    memory.add_argument(
        "--allow-unverified-anchor",
        action="store_true",
        default=None,
        help="Allow memory runs to continue when anchor verification fails.",
    )
    memory.add_argument("--anchor-log", type=Path, help="Append one anchor audit row per memory run.")
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
    memory_incremental_group = memory.add_mutually_exclusive_group()
    memory_incremental_group.add_argument("--incremental", dest="incremental", action="store_true", default=None, help="Reuse the latest pack as a memory base when available.")
    memory_incremental_group.add_argument("--no-incremental", dest="incremental", action="store_false", help="Rebuild snapshot packs without reusing latest pack state.")
    compare_group = memory.add_mutually_exclusive_group()
    compare_group.add_argument("--no-compare", dest="compare", action="store_false", default=None, help="Do not compare against latest.repomori.")
    compare_group.add_argument("--compare", dest="compare", action="store_true", help="Compare against latest.repomori.")
    memory.add_argument("--compare-limit", type=int)
    diff_context_group = memory.add_mutually_exclusive_group()
    diff_context_group.add_argument("--diff-context", dest="diff_context", action="store_true", default=None, help="Write changed-files context beside snapshot reports.")
    diff_context_group.add_argument("--no-diff-context", dest="diff_context", action="store_false", help="Skip diff-context even if config enables it.")
    memory.add_argument("--diff-context-question")
    memory.add_argument("--diff-context-max-files", type=int)
    memory.add_argument("--diff-context-snippet-lines", type=int)
    memory.add_argument("--diff-context-snippets-per-file", type=int)
    memory.add_argument("--diff-context-max-bytes", type=int)
    diff_context_source_group = memory.add_mutually_exclusive_group()
    diff_context_source_group.add_argument("--diff-context-source", dest="diff_context_include_source", action="store_true", default=None, help="Include exact diff-context snippets.")
    diff_context_source_group.add_argument("--diff-context-no-source", dest="diff_context_include_source", action="store_false", help="Write diff-context metadata without snippets.")
    memory.add_argument("--json", action="store_true", help="Print memory JSON.")

    agent = sub.add_parser("agent", help="Run the JSON-lines agent bridge on stdio.")
    agent.add_argument("--config", type=Path, help="Config file path; defaults to nearest repomori.toml.")
    agent.add_argument("--profile", help="Config profile to use.")

    mcp = sub.add_parser("mcp", help="Run the dependency-free MCP stdio bridge.")
    mcp.add_argument("--config", type=Path, help="Config file path; defaults to nearest repomori.toml.")
    mcp.add_argument("--profile", help="Config profile to use.")

    schema = sub.add_parser("schema", help="Show supported RepoMori schemas and agent methods.")
    schema.add_argument("schema_version", nargs="?", help="Specific schema version to show.")
    schema.add_argument("--json", action="store_true", help="Print schema JSON.")

    info = sub.add_parser("info", help="Show pack metadata.")
    info.add_argument("pack", type=Path)
    info.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect", help="Inspect pack contents, storage, indexes, and vocabulary.")
    inspect.add_argument("pack", type=Path)
    inspect.add_argument("--max-files", type=int, default=20)
    inspect.add_argument("--top-terms", type=int, default=30)
    inspect.add_argument("--top-symbols", type=int, default=30)
    inspect.add_argument("--verify", action="store_true", help="Run full pack verification during inspection.")
    inspect.add_argument("--format", choices=("markdown", "json"), default="markdown")
    inspect.add_argument("--out", type=Path, help="Write the inspection report to this file.")
    inspect.add_argument("--json", action="store_true", help="Alias for --format json.")

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

    brief = sub.add_parser("brief", help="Build a pack orientation brief or snapshot-directory agent brief.")
    brief.add_argument("target", type=Path)
    brief.add_argument("--max-files", type=int, default=12)
    brief.add_argument("--top-terms", type=int, default=40)
    brief.add_argument("--top-symbols", type=int, default=40)
    brief.add_argument("--timeline-limit", type=int, default=5, help="Snapshot-directory mode: recent snapshots to include.")
    brief.add_argument("--stats-limit", type=int, default=10, help="Snapshot-directory mode: reuse stats rows to include.")
    brief.add_argument("--verify-packs", action="store_true", help="Snapshot-directory mode: run full pack verification during doctor.")
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

    diff_context = sub.add_parser("diff-context", help="Build source-backed changed-files context.")
    diff_context.add_argument("base_pack", type=Path)
    diff_context.add_argument("target_pack", type=Path)
    diff_context.add_argument("question", nargs="?", default="what changed?")
    diff_context.add_argument("--limit", type=int, default=8)
    diff_context.add_argument("--max-files", type=int, help="Alias for --limit.")
    diff_context.add_argument("--snippet-lines", type=int, default=12)
    diff_context.add_argument("--snippets-per-file", type=int, default=2)
    diff_context.add_argument("--max-bytes", type=int, help="Maximum total snippet text bytes.")
    diff_context.add_argument("--no-source", action="store_true", help="Return change metadata without snippets.")
    diff_context.add_argument("--format", choices=("markdown", "json"), default="markdown")
    diff_context.add_argument("--out", type=Path, help="Write the diff context bundle to this file.")

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
            BuildOptions(chunk_size=args.chunk_size, force=args.force, base_pack=args.base),
        )
        _print(result, args.json)
        return 0
    if args.command == "demo":
        report = run_demo(
            args.out,
            force=args.force,
            question=args.question,
            chunk_size=args.chunk_size,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"demo: {report['out_dir']}")
            print(f"status: {report['status']}")
            print(f"pack: {report['summary']['pack_path']}")
            print(f"context: {Path(report['out_dir']) / report['artifacts']['context_markdown']}")
            print(f"config: {report['summary']['config_path']}")
        return 0 if report["status"] == "pass" else 1
    if args.command == "scan":
        report = scan_repository(
            args.repo,
            max_file_bytes=args.max_file_bytes,
            include_hidden=args.include_hidden,
            public_release=args.public_release,
            ignore_codes=args.ignore_code or (),
            baseline=args.baseline,
        )
        if args.write_baseline:
            report["baseline_written"] = write_scan_baseline(report, args.write_baseline)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_scan(report)
        if args.write_baseline:
            return 0
        return 1 if _scan_has_threshold(report, args.fail_on) else 0
    if args.command == "release-check":
        artifacts_dir = args.artifacts_dir
        if args.json and artifacts_dir is None:
            artifacts_dir = args.repo / ".repomori-release-check"
        report = run_release_check(
            args.repo,
            baseline=args.baseline,
            fail_on=args.fail_on,
            public_release=not args.no_public_release,
            run_tests=not args.skip_tests,
            run_demo_smoke=not args.skip_demo,
            demo_out=args.demo_out,
            keep_demo=args.keep_demo,
            tests_dir=args.tests_dir,
            drift_log=args.drift_log,
            drift_policy=args.drift_policy,
            artifacts_dir=artifacts_dir,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_release_check(report)
        return 0 if report["status"] == "pass" else 1
    if args.command == "release-health":
        artifacts_dir = args.artifacts_dir
        if args.json and artifacts_dir is None:
            artifacts_dir = Path(args.repo).resolve() / ".repomori-health"
        report = run_release_health(
            args.repo,
            snapshot_dir=args.snapshot_dir,
            baseline=args.baseline,
            fail_on=args.fail_on,
            public_release=not args.no_public_release,
            run_tests=not args.skip_tests,
            run_demo_smoke=not args.skip_demo,
            demo_out=args.demo_out,
            keep_demo=args.keep_demo,
            tests_dir=args.tests_dir,
            drift_log=args.drift_log,
            drift_policy=args.drift_policy,
            timeline_limit=args.timeline_limit,
            drift_summary_limit=args.drift_summary_limit,
            doctor_verify_packs=args.doctor_verify_packs,
            artifacts_dir=artifacts_dir,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print(report, args.json)
        return 0 if report["status"] != "fail" else 1
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
            incremental=args.incremental,
            compare=not args.no_compare,
            compare_limit=args.compare_limit,
            diff_context=args.diff_context,
            diff_context_question=args.diff_context_question,
            diff_context_limit=args.diff_context_max_files,
            diff_context_snippet_lines=args.diff_context_snippet_lines,
            diff_context_snippets_per_file=args.diff_context_snippets_per_file,
            diff_context_max_bytes=args.diff_context_max_bytes,
            diff_context_include_source=not args.diff_context_no_source,
            anchor_freshness=args.anchor_freshness,
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
            incremental=args.incremental,
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
        status = report.get("summary", {}).get("chain_status")
        if status not in {"pass", "warn", "fail"}:
            status = "warn"
        output = (
            json.dumps(report, indent=2)
            if args.format == "json"
            else format_timeline_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
            if status != "pass" and not getattr(args, "json", False):
                _print_report_status_hint(
                    {
                        "status": status,
                        "errors": report.get("chain", {}).get("errors", []),
                        "warnings": report.get("chain", {}).get("warnings", []),
                    },
                    "timeline",
                )
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0 if status != "fail" else 1
    if args.command == "drift-summary":
        summary = summarize_baseline_drift_log(args.log, limit=args.limit)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"drift summary: {summary['log_path']}")
            print(f"status: {summary['status']}")
            print(f"rows: {summary['count']}")
            print(f"warn rows: {summary['warn_count']}")
            trend = summary.get("trend", {})
            print(
                "trend semi-strict delta: "
                f"{trend.get('semi_strict_delta', 0)}"
            )
            print(
                "trend fallback delta: "
                f"{trend.get('fallback_delta', 0)}"
            )
            print(f"max non-strict ratio: {summary.get('max_non_strict_ratio', 0.0):.2f}")
            print(f"avg non-strict ratio: {summary.get('avg_non_strict_ratio', 0.0):.2f}")
            print(f"ordered: {summary.get('ordered')}")
        return 0 if summary["status"] != "fail" else 1
    if args.command == "stats":
        report = read_snapshot_stats(args.out_dir, limit=args.limit)
        output = (
            json.dumps(report, indent=2)
            if args.format == "json"
            else format_stats_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        return 0
    if args.command == "chain":
        report = verify_snapshot_chain(args.out_dir)
        output_format = "json" if args.json else args.format
        output = (
            json.dumps(report, indent=2)
            if output_format == "json"
            else format_snapshot_chain_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        if report.get("status") != "pass" and not args.json and args.out:
            _print_report_status_hint(report, "chain")
        return 0 if report["status"] != "fail" else 1
    if args.command == "anchor":
        report = build_snapshot_anchor(args.out_dir)
        output_format = "json" if args.json else args.format
        output = (
            json.dumps(report, indent=2)
            if output_format == "json"
            else format_snapshot_anchor_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        if report.get("status") != "pass" and not args.json and args.out:
            _print_report_status_hint(report, "anchor")
        return 0 if report["status"] != "fail" else 1
    if args.command == "verify-anchor":
        report = verify_snapshot_anchor(args.anchor, args.out_dir, check_current=not args.no_current)
        output_format = "json" if args.json else args.format
        output = (
            json.dumps(report, indent=2)
            if output_format == "json"
            else format_snapshot_anchor_verification_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
        if report.get("status") != "pass" and not args.json and args.out:
            _print_report_status_hint(report, "verify-anchor")
        return 0 if report["status"] != "fail" else 1
    if args.command == "doctor":
        result = doctor_snapshot_dir(args.out_dir, verify_packs=args.verify_packs)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
            if result.get("status") != "pass" and not args.json:
                _print_report_status_hint(result, "doctor")
        else:
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
            anchor_out=settings["anchor_out"],
            anchor_verify=settings["anchor_verify"],
            allow_unverified_anchor=settings["allow_unverified_anchor"],
            anchor_freshness=settings["anchor_freshness"],
            anchor_log=settings["anchor_log"],
            no_handoff=settings["no_handoff"],
            keep=settings["keep"],
            prune_apply=settings["prune_apply"],
            verify_packs=settings["verify_packs"],
            timeline_limit=settings["timeline_limit"],
            chunk_size=settings["chunk_size"],
            incremental=settings["incremental"],
            compare=settings["compare"],
            compare_limit=settings["compare_limit"],
            diff_context=settings["diff_context"],
            diff_context_question=settings["diff_context_question"],
            diff_context_limit=settings["diff_context_limit"],
            diff_context_snippet_lines=settings["diff_context_snippet_lines"],
            diff_context_snippets_per_file=settings["diff_context_snippets_per_file"],
            diff_context_max_bytes=settings["diff_context_max_bytes"],
            diff_context_include_source=settings["diff_context_include_source"],
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"memory: {report['out_dir']}")
            print(f"status: {report['status']}")
            print(f"pack: {report['summary']['pack_path']}")
            print(f"handoff: {report['summary']['handoff_dir']}")
            print(f"diff context: {report['summary']['diff_context_status']}")
            print(f"prune applied: {report['summary']['prune_applied']}")
            print(f"timeline snapshots: {report['summary']['timeline_snapshot_count']}")
            if report.get("failure_reasons"):
                print("failure reasons:")
                for reason in report["failure_reasons"]:
                    print(f"- {reason}")
        return 0 if report["status"] != "fail" else 1
    if args.command == "agent":
        return run_agent_bridge(
            sys.stdin,
            sys.stdout,
            config_path=args.config,
            profile=args.profile,
            start_dir=Path.cwd(),
        )
    if args.command == "mcp":
        return run_mcp_bridge(
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
    if args.command == "inspect":
        report = inspect_pack(
            args.pack,
            max_files=args.max_files,
            top_terms=args.top_terms,
            top_symbols=args.top_symbols,
            verify=args.verify,
        )
        output_format = "json" if args.json else args.format
        output = (
            json.dumps(report, indent=2)
            if output_format == "json"
            else format_pack_inspect_markdown(report)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="" if output.endswith("\n") else "\n")
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
        if args.target.is_dir():
            brief_report = build_agent_brief(
                args.target,
                timeline_limit=args.timeline_limit,
                stats_limit=args.stats_limit,
                verify_packs=args.verify_packs,
                max_files=args.max_files,
                top_terms=args.top_terms,
                top_symbols=args.top_symbols,
            )
            markdown = format_agent_brief_markdown(brief_report)
        else:
            brief_report = build_repo_brief(
                args.target,
                max_files=args.max_files,
                top_terms=args.top_terms,
                top_symbols=args.top_symbols,
            )
            markdown = format_brief_markdown(brief_report)
        output = (
            json.dumps(brief_report, indent=2)
            if args.format == "json"
            else markdown
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
    if args.command == "diff-context":
        limit = args.max_files if args.max_files is not None else args.limit
        bundle = build_diff_context_bundle(
            args.base_pack,
            args.target_pack,
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
            else format_diff_context_markdown(bundle)
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


def _print_scan(report: dict) -> None:
    summary = report.get("summary", {})
    print(f"scan: {report.get('repo_path')}")
    print(f"status: {report.get('status')}")
    print(
        "findings: "
        f"{summary.get('findings', 0)} "
        f"ignored={summary.get('ignored_findings', 0)} "
        f"high={summary.get('high', 0)} "
        f"medium={summary.get('medium', 0)} "
        f"low={summary.get('low', 0)} "
        f"info={summary.get('info', 0)}"
    )
    if report.get("baseline_written"):
        print(f"baseline: {report['baseline_written']['path']}")
    for finding in report.get("findings", [])[:20]:
        location = finding.get("path", ".")
        if finding.get("line") is not None:
            location = f"{location}:{finding['line']}"
        print(f"- {finding.get('severity')} {finding.get('code')} {location}: {finding.get('message')}")
    extra = len(report.get("findings", [])) - 20
    if extra > 0:
        print(f"... {extra} more finding(s)")


def _print_report_status_hint(report: dict, title: str) -> None:
    """Emit a compact status hint for CLI commands that wrote structured output to a file."""

    status = report.get("status")
    if status in {None, "pass"}:
        return

    issues = []
    for error in report.get("errors", []):
        if isinstance(error, dict):
            path = str(error.get("path", "")).strip()
            message = str(error.get("message", "")).strip()
            if path and message:
                issues.append(f"{path}: {message}")
            elif message:
                issues.append(message)
    if not issues:
        verification = report.get("verification") if isinstance(report.get("verification"), dict) else None
        if verification is not None:
            for error in verification.get("errors", []):
                if isinstance(error, dict):
                    path = str(error.get("path", "")).strip()
                    message = str(error.get("message", "")).strip()
                    if path and message:
                        issues.append(f"{path}: {message}")
                    elif message:
                        issues.append(message)
        for warning in report.get("warnings", []):
            if isinstance(warning, dict):
                path = str(warning.get("path", "")).strip()
                message = str(warning.get("message", "")).strip()
                if path and message:
                    issues.append(f"{path}: {message}")
                elif message:
                    issues.append(message)

    print(f"{title}: {status}", file=sys.stderr)
    for issue in issues[:5]:
        print(f"- {issue}", file=sys.stderr)


def _scan_has_threshold(report: dict, threshold: str) -> bool:
    minimum = {"info": 0, "low": 1, "medium": 2, "high": 3}[threshold]
    for finding in report.get("findings", []):
        if {"info": 0, "low": 1, "medium": 2, "high": 3}.get(finding.get("severity"), -1) >= minimum:
            return True
    return False


def _print_release_check(report: dict) -> None:
    summary = report.get("summary", {})
    print(f"release-check: {report.get('repo_path')}")
    print(f"status: {report.get('status')}")
    print(f"elapsed: {summary.get('elapsed_seconds')}s")
    failed = summary.get("failed_checks") or []
    print("failed checks: " + (", ".join(failed) if failed else "none"))
    for name, check in report.get("checks", {}).items():
        detail = check.get("status")
        if name == "scan":
            scan_summary = check.get("summary", {})
            detail += (
                f" findings={scan_summary.get('findings', 0)}"
                f" ignored={scan_summary.get('ignored_findings', 0)}"
            )
            drift = check.get("drift_warnings")
            if isinstance(drift, dict):
                detail += (
                    f" drift_ratio={drift.get('non_strict_ratio', 0.0):.2f}"
                    f" drift_status={drift.get('status')}"
                )
        elif name == "tests":
            detail += f" returncode={check.get('returncode')}"
        elif name == "demo":
            detail += f" demo_status={check.get('demo_status', check.get('status'))}"
        print(f"- {name}: {detail}")
    if report.get("failure_reasons"):
        print("failure reasons:")
        for reason in report.get("failure_reasons", []):
            print(f"- {reason}")


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
        "anchor_out": str(args.anchor_out.resolve()) if args.anchor_out is not None else settings.get("anchor_out"),
        "anchor_log": str(args.anchor_log.resolve()) if args.anchor_log is not None else settings.get("anchor_log"),
        "anchor_freshness": _setting(args.anchor_freshness, settings, "anchor_freshness", None),
        "anchor_verify": _setting(args.anchor_verify, settings, "anchor_verify", False),
        "allow_unverified_anchor": _setting(args.allow_unverified_anchor, settings, "allow_unverified_anchor", False),
        "no_handoff": _setting(args.no_handoff, settings, "no_handoff", False),
        "keep": _setting(args.keep, settings, "keep", 20),
        "prune_apply": _setting(args.prune_apply, settings, "prune_apply", False),
        "verify_packs": _setting(args.verify_packs, settings, "verify_packs", False),
        "timeline_limit": _setting(args.timeline_limit, settings, "timeline_limit", 5),
        "chunk_size": _setting(args.chunk_size, settings, "chunk_size", 256 * 1024),
        "incremental": _setting(args.incremental, settings, "incremental", True),
        "compare": _setting(args.compare, settings, "compare", True),
        "compare_limit": _setting(args.compare_limit, settings, "compare_limit", 50),
        "diff_context": _setting(args.diff_context, settings, "diff_context", False),
        "diff_context_question": _setting(args.diff_context_question, settings, "diff_context_question", "what changed?"),
        "diff_context_limit": _setting(args.diff_context_max_files, settings, "diff_context_limit", 8),
        "diff_context_snippet_lines": _setting(args.diff_context_snippet_lines, settings, "diff_context_snippet_lines", 12),
        "diff_context_snippets_per_file": _setting(args.diff_context_snippets_per_file, settings, "diff_context_snippets_per_file", 2),
        "diff_context_max_bytes": _setting(args.diff_context_max_bytes, settings, "diff_context_max_bytes", 8192),
        "diff_context_include_source": _setting(args.diff_context_include_source, settings, "diff_context_include_source", True),
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
