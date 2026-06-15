from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import repomori.cli as cli
import repomori.codec as codec

from repomori.codec import (
    BuildOptions,
    benchmark_repo,
    build_agent_brief,
    build_baseline_drift_report,
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
    format_benchmark_markdown,
    format_brief_markdown,
    format_compare_markdown,
    format_eval_markdown,
    format_pack_inspect_markdown,
    format_context_markdown,
    format_diff_context_markdown,
    format_snapshot_anchor_markdown,
    format_snapshot_anchor_verification_markdown,
    format_snapshot_chain_markdown,
    format_stats_markdown,
    format_snapshot_markdown,
    format_timeline_markdown,
    append_anchor_log,
    get_file_bytes,
    handle_agent_request,
    handle_mcp_request,
    init_config,
    info_pack,
    inspect_pack,
    load_memory_config,
    query_pack,
    prune_snapshots,
    read_snapshot_stats,
    read_snapshot_timeline,
    run_mcp_bridge,
    run_demo,
    run_memory_cycle,
    run_release_check,
    run_release_health,
    append_baseline_drift_log,
    summarize_baseline_drift_log,
    schema_catalog,
    scan_baseline_from_report,
    scan_repository,
    snapshot_repo,
    verify_snapshot_anchor,
    tree_pack,
    verify_snapshot_chain,
    verify_pack,
    write_scan_baseline,
)


class RepoMoriCodecTests(unittest.TestCase):
    def test_build_query_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, pack = self._demo_pack(Path(tmp))

            summary = build_pack(repo, pack, BuildOptions(force=True))
            self.assertEqual(summary["file_count"], 3)

            info = info_pack(pack)
            self.assertEqual(info["counts"]["files"], 3)
            self.assertGreaterEqual(info["counts"]["symbols"], 2)

            results = query_pack(pack, "sqlite Store", limit=3)
            self.assertEqual(results[0]["path"], "app.py")
            self.assertIn("symbol", results[0]["why"])
            self.assertIn("exact-symbol", results[0]["why"])
            self.assertIn("all-query-terms", results[0]["why"])

            path_results = query_pack(pack, "readme", limit=1)
            self.assertEqual(path_results[0]["path"], "README.md")
            self.assertIn("exact-basename", path_results[0]["why"])

            restored = get_file_bytes(pack, "app.py")
            self.assertEqual(restored, (repo / "app.py").read_bytes())

            tree = tree_pack(pack)
            self.assertEqual([row["path"] for row in tree], ["README.md", "app.py", "blob.bin"])

    def test_pack_inspector_report_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            report = inspect_pack(pack, max_files=2, top_terms=5, top_symbols=5, verify=True)
            self.assertEqual(report["schema_version"], "repomori.inspect.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["pack"]["schema_version"], "repomori.pack.v1")
            self.assertEqual(report["summary"]["file_count"], 3)
            self.assertEqual(report["summary"]["text_files"], 2)
            self.assertEqual(report["summary"]["binary_files"], 1)
            self.assertEqual(report["verification"]["status"], "pass")
            self.assertTrue(report["verification"]["verified"])
            self.assertTrue(report["languages"])
            self.assertTrue(report["storage"]["chunks"]["count"])
            self.assertLessEqual(len(report["files"]["largest"]), 2)
            self.assertTrue(any(item["path"] == "app.py" for item in report["files"]["key"]))
            self.assertTrue(any(item["path"] == "blob.bin" for item in report["files"]["binary"]))
            self.assertTrue(all("sha256" in item for item in report["source_manifest"]))

            markdown = format_pack_inspect_markdown(report)
            self.assertIn("# RepoMori Pack Inspector", markdown)
            self.assertIn("Pack SHA-256", markdown)
            self.assertIn("## Storage", markdown)
            self.assertIn("app.py", markdown)

    def test_build_with_base_reuses_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, base_pack = self._demo_pack(root)
            build_pack(repo, base_pack, BuildOptions(force=True))
            (repo / "app.py").write_text(
                "import sqlite3\n\n"
                "class Store:\n"
                "    def connect(self):\n"
                "        return sqlite3.connect(':memory:')\n"
                "    def close(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            target_pack = root / "target.repomori"

            summary = build_pack(repo, target_pack, BuildOptions(force=True, base_pack=base_pack))

            self.assertTrue(summary["incremental"])
            self.assertEqual(summary["base_pack_path"], str(base_pack.resolve()))
            self.assertEqual(summary["file_count"], 3)
            self.assertEqual(summary["reused_file_count"], 2)
            self.assertEqual(summary["rebuilt_file_count"], 1)
            self.assertGreaterEqual(summary["reused_chunk_count"], 2)
            self.assertTrue(verify_pack(target_pack)["verified"])
            self.assertEqual(get_file_bytes(target_pack, "app.py"), (repo / "app.py").read_bytes())
            self.assertEqual(query_pack(target_pack, "close Store", limit=1)[0]["path"], "app.py")

    def test_diagnose_query_explains_ranking_and_snippets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            report = diagnose_query(pack, "storage sqlite Store", limit=3, snippet_lines=6)
            self.assertEqual(report["schema_version"], "repomori.diagnose.v1")
            self.assertEqual(report["question"], "storage sqlite Store")
            self.assertIn("storage", report["query"]["tokens"])
            self.assertIn("sqlite", report["query"]["tokens"])

            self.assertGreaterEqual(len(report["selected_files"]), 2)
            top = report["selected_files"][0]
            self.assertEqual(top["path"], "app.py")
            self.assertIn("symbol", top["why"])
            self.assertIn("store", top["matched_tokens"])
            self.assertIn("storage", top["missed_tokens"])
            self.assertTrue(top["snippet_anchors"])
            self.assertTrue(any(event["field"] == "symbol" for event in top["score_breakdown"]))
            self.assertTrue(report["ranking_notes"])
            self.assertIn("score_delta", report["ranking_notes"][0])

    def test_diagnose_binary_file_skips_snippet_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            report = diagnose_query(pack, "blob", limit=1)
            source = report["selected_files"][0]
            self.assertEqual(source["path"], "blob.bin")
            self.assertEqual(source["snippet_status"], "binary_or_undecodable")
            self.assertEqual(source["snippet_anchors"], [])
            self.assertEqual(source["snippets"], [])

    def test_compare_packs_reports_added_changed_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base_pack = self._demo_pack(Path(tmp))
            build_pack(repo, base_pack, BuildOptions(force=True))

            (repo / "README.md").write_text("# Demo\n\nStorage engine changed.\n", encoding="utf-8")
            (repo / "app.py").write_text(
                "import sqlite3\n\n"
                "class Store:\n"
                "    def connect(self):\n"
                "        return sqlite3.connect(':memory:')\n"
                "    def close(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            (repo / "blob.bin").unlink()
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            target_pack = Path(tmp) / "target.repomori"
            build_pack(repo, target_pack, BuildOptions(force=True))

            report = compare_packs(base_pack, target_pack)
            self.assertEqual(report["schema_version"], "repomori.compare.v1")
            self.assertEqual(report["summary"]["added_count"], 1)
            self.assertEqual(report["summary"]["removed_count"], 1)
            self.assertGreaterEqual(report["summary"]["changed_count"], 2)
            self.assertEqual(report["files"]["added"][0]["path"], "new.py")
            self.assertEqual(report["files"]["removed"][0]["path"], "blob.bin")

            changed_paths = {item["path"] for item in report["files"]["changed"]}
            self.assertIn("app.py", changed_paths)
            app_change = next(item for item in report["files"]["changed"] if item["path"] == "app.py")
            self.assertIn("sha256", app_change["change_reasons"])
            self.assertIn("function:close", app_change["summary_delta"]["added_symbols"])

            markdown = format_compare_markdown(report)
            self.assertIn("# RepoMori Pack Compare", markdown)
            self.assertIn("## Changed Files", markdown)
            self.assertIn("new.py", markdown)
            self.assertIn("blob.bin", markdown)

    def test_repo_brief_summarizes_pack_orientation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            brief = build_repo_brief(pack, max_files=3)
            self.assertEqual(brief["schema_version"], "repomori.brief.v1")
            self.assertEqual(brief["summary"]["file_count"], 3)
            languages = {item["language"]: item["count"] for item in brief["summary"]["language_counts"]}
            self.assertEqual(languages["python"], 1)

            key_paths = [item["path"] for item in brief["orientation"]["key_files"]]
            self.assertIn("README.md", key_paths)
            self.assertIn("app.py", key_paths)
            symbols = [item["symbol"] for item in brief["vocabulary"]["top_symbols"]]
            self.assertIn("class:Store", symbols)
            self.assertTrue(brief["source_manifest"])

            markdown = format_brief_markdown(brief)
            self.assertIn("# RepoMori Repo Brief", markdown)
            self.assertIn("## Entrypoints", markdown)
            self.assertIn("app.py", markdown)

    def test_agent_brief_summarizes_latest_memory_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"

            run_memory_cycle(repo, out, no_handoff=True, diff_context=True)
            (repo / "app.py").write_text(
                "import sqlite3\n\n"
                "class Store:\n"
                "    def connect(self):\n"
                "        return sqlite3.connect(':memory:')\n"
                "    def close(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            second = run_memory_cycle(
                repo,
                out,
                handoff_question="continue this repo",
                diff_context=True,
                diff_context_question="close Store",
            )

            brief = build_agent_brief(out, timeline_limit=3, stats_limit=3, max_files=4)
            self.assertEqual(brief["schema_version"], "repomori.agent_brief.v1")
            self.assertEqual(brief["status"], "pass")
            self.assertEqual(brief["summary"]["latest_pack_path"], second["summary"]["pack_path"])
            self.assertEqual(brief["summary"]["doctor_status"], "pass")
            self.assertEqual(brief["summary"]["diff_context_status"], "written")
            self.assertEqual(brief["latest_diff_context"]["summary"]["changed_count"], 1)
            self.assertEqual(brief["repo_brief"]["schema_version"], "repomori.brief.v1")

            artifact_kinds = {item["kind"] for item in brief["artifacts"]}
            self.assertIn("latest_pack", artifact_kinds)
            self.assertIn("handoff_dir", artifact_kinds)
            self.assertIn("diff_context_json", artifact_kinds)
            self.assertTrue(any("context" in item["command"] for item in brief["recommended_commands"]))

            markdown = format_agent_brief_markdown(brief)
            self.assertIn("# RepoMori Agent Brief", markdown)
            self.assertIn("## Latest Diff Context", markdown)
            self.assertIn("app.py", markdown)
            self.assertIn("python -m repomori context", markdown)

    def test_context_bundle_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, pack = self._demo_pack(Path(tmp), build=True)

            bundle = build_context_bundle(pack, "How does the sqlite Store connect?", limit=3, snippet_lines=6)
            self.assertEqual(bundle["schema_version"], "repomori.context.v1")
            self.assertEqual(bundle["question"], "How does the sqlite Store connect?")
            self.assertEqual(bundle["sources"][0]["path"], "app.py")
            self.assertEqual(bundle["source_manifest"][0]["path"], "app.py")
            self.assertEqual(bundle["source_manifest"][0]["sha256"], bundle["sources"][0]["sha256"])

            snippets = bundle["sources"][0]["snippets"]
            self.assertGreaterEqual(len(snippets), 1)
            self.assertEqual(snippets[0]["start_line"], 1)
            self.assertIn("class Store", snippets[0]["text"])
            self.assertIn("sqlite3.connect", snippets[0]["text"])

            markdown = format_context_markdown(bundle)
            self.assertIn("# RepoMori Agent Context", markdown)
            self.assertIn("### app.py", markdown)
            self.assertIn("SHA-256:", markdown)
            self.assertIn("Score:", markdown)
            self.assertIn("Source bytes:", markdown)
            self.assertIn("Lines 1-", markdown)
            self.assertIn("sqlite3.connect", markdown)

    def test_context_size_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            bundle = build_context_bundle(
                pack,
                "sqlite Store",
                limit=3,
                snippet_lines=6,
                max_bytes=20,
                snippets_per_file=1,
            )
            self.assertEqual(bundle["selection"]["max_bytes"], 20)
            self.assertEqual(bundle["selection"]["snippets_per_file"], 1)
            self.assertLessEqual(bundle["selection"]["source_bytes"], 20)
            for source in bundle["sources"]:
                self.assertLessEqual(len(source["snippets"]), 1)

            metadata_only = build_context_bundle(pack, "sqlite Store", include_source=False)
            self.assertEqual(metadata_only["selection"]["include_source"], False)
            self.assertTrue(all(source["snippet_status"] == "source_omitted" for source in metadata_only["sources"]))

    def test_binary_context_source_has_metadata_without_snippets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            bundle = build_context_bundle(pack, "blob", limit=1)
            self.assertEqual(bundle["sources"][0]["path"], "blob.bin")
            self.assertEqual(bundle["sources"][0]["snippet_status"], "binary_or_undecodable")
            self.assertEqual(bundle["sources"][0]["snippets"], [])
            self.assertEqual(bundle["source_manifest"][0]["snippet_count"], 0)

    def test_diff_context_includes_changed_added_and_removed_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, base_pack = self._demo_pack(root)
            build_pack(repo, base_pack, BuildOptions(force=True))
            (repo / "app.py").write_text(
                "import sqlite3\n\n"
                "class Store:\n"
                "    def connect(self):\n"
                "        return sqlite3.connect(':memory:')\n"
                "    def close(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            (repo / "blob.bin").unlink()
            target_pack = root / "target.repomori"
            build_pack(repo, target_pack, BuildOptions(force=True, base_pack=base_pack))

            bundle = build_diff_context_bundle(
                base_pack,
                target_pack,
                "close added blob",
                limit=4,
                snippet_lines=4,
                snippets_per_file=2,
            )

            self.assertEqual(bundle["schema_version"], "repomori.diff_context.v1")
            self.assertEqual(bundle["summary"]["changed_count"], 1)
            self.assertEqual(bundle["summary"]["added_count"], 1)
            self.assertEqual(bundle["summary"]["removed_count"], 1)
            sources = {source["path"]: source for source in bundle["sources"]}
            self.assertEqual(sources["app.py"]["change_type"], "changed")
            self.assertEqual(sources["app.py"]["source_pack"], "target")
            self.assertTrue(any("def close" in snippet["text"] for snippet in sources["app.py"]["snippets"]))
            self.assertEqual(sources["new.py"]["change_type"], "added")
            self.assertIn("return 'new'", sources["new.py"]["snippets"][0]["text"])
            self.assertEqual(sources["blob.bin"]["change_type"], "removed")
            self.assertEqual(sources["blob.bin"]["source_pack"], "base")
            self.assertEqual(sources["blob.bin"]["snippet_status"], "binary_or_undecodable")
            self.assertTrue(any(item["source_pack"] == "base" for item in bundle["source_manifest"]))

            markdown = format_diff_context_markdown(bundle)
            self.assertIn("# RepoMori Diff Context", markdown)
            self.assertIn("Change type", markdown)
            self.assertIn("def close", markdown)

    def test_verify_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            result = verify_pack(pack)
            self.assertTrue(result["verified"])
            self.assertEqual(result["schema_version"], "repomori.verify.v1")
            self.assertEqual(result["checked_files"], 3)
            self.assertEqual(result["error_count"], 0)

    def test_eval_report_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            report = evaluate_pack(pack, questions=["sqlite Store", "missingzz"], limit=2)
            self.assertEqual(report["schema_version"], "repomori.eval.v1")
            self.assertEqual(report["summary"]["question_count"], 2)
            self.assertEqual(report["summary"]["passed_questions"], 1)
            self.assertEqual(report["summary"]["weak_questions"], 1)
            self.assertEqual(report["questions"][0]["selected_sources"][0]["path"], "app.py")
            self.assertIn("no_sources", report["questions"][1]["weak_signals"])
            self.assertGreaterEqual(report["coverage"]["unique_file_count"], 1)
            self.assertTrue(report["suggested_improvements"])

            markdown = format_eval_markdown(report)
            self.assertIn("# RepoMori Evaluation", markdown)
            self.assertIn("sqlite Store", markdown)
            self.assertIn("Suggested Improvements", markdown)

    def test_capsule_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            capsule = build_capsule(pack, max_files=2, top_terms=5)
            self.assertEqual(capsule["schema_version"], "repomori.capsule.v1")
            self.assertEqual(capsule["selection"]["included_files"], 2)
            self.assertEqual(capsule["selection"]["total_files"], 3)
            self.assertTrue(capsule["selection"]["truncated"])
            self.assertIn("terms", capsule["dictionary"])
            self.assertEqual(len(capsule["manifest"]), 2)

            app_record = next(item for item in capsule["files"] if item["p"] == "app.py")
            self.assertEqual(app_record["l"], "python")
            self.assertIn("s", app_record)
            self.assertIn("i", app_record)
            self.assertNotIn("text", app_record)
            self.assertNotIn("snippets", app_record)

    def test_handoff_package_outputs_manifest_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff"

            manifest = build_handoff_package(pack, "sqlite Store", out)

            self.assertEqual(manifest["schema_version"], "repomori.handoff.v1")
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["question"], "sqlite Store")
            self.assertTrue(manifest["verification"]["verified"])
            self.assertEqual(json.loads((out / "manifest.json").read_text(encoding="utf-8")), manifest)

            expected = {
                "README.md",
                "brief.json",
                "brief.md",
                "capsule.json",
                "context.json",
                "context.md",
                "eval.json",
                "eval.md",
                "manifest.json",
                "verify.json",
            }
            self.assertTrue(expected.issubset({path.name for path in out.iterdir()}))

            artifacts = {artifact["path"]: artifact for artifact in manifest["artifacts"]}
            self.assertIn("brief.md", artifacts)
            self.assertIn("brief.json", artifacts)
            self.assertIn("context.md", artifacts)
            self.assertIn("capsule.json", artifacts)
            for artifact in artifacts.values():
                artifact_path = out / artifact["path"]
                data = artifact_path.read_bytes()
                self.assertEqual(artifact["size"], len(data))
                self.assertEqual(artifact["sha256"], hashlib.sha256(data).hexdigest())

            for name in ("context.json", "brief.json", "capsule.json", "eval.json", "verify.json"):
                json.loads((out / name).read_text(encoding="utf-8"))

    def test_handoff_force_and_copy_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff"

            build_handoff_package(pack, "sqlite Store", out)
            with self.assertRaises(FileExistsError):
                build_handoff_package(pack, "sqlite Store", out)

            forced = build_handoff_package(pack, "sqlite Store", out, force=True)
            self.assertEqual(forced["status"], "complete")

            copy_out = Path(tmp) / "handoff-copy"
            copied = build_handoff_package(pack, "sqlite Store", copy_out, copy_pack=True)
            pack_copy = copy_out / pack.name
            self.assertEqual(pack_copy.read_bytes(), pack.read_bytes())
            self.assertTrue(any(artifact["kind"] == "pack_copy" for artifact in copied["artifacts"]))

            check = check_handoff_package(copy_out)
            self.assertTrue(check["valid"])
            self.assertTrue(check["copied_pack"]["verified"])

    def test_handoff_with_base_pack_includes_compare_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base_pack = self._demo_pack(Path(tmp))
            build_pack(repo, base_pack, BuildOptions(force=True))
            (repo / "app.py").write_text(
                "import sqlite3\n\n"
                "class Store:\n"
                "    def connect(self):\n"
                "        return sqlite3.connect(':memory:')\n"
                "    def close(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            target_pack = Path(tmp) / "target.repomori"
            build_pack(repo, target_pack, BuildOptions(force=True))
            out = Path(tmp) / "handoff-compare"

            manifest = build_handoff_package(target_pack, "sqlite Store", out, base_pack=base_pack)

            self.assertEqual(manifest["schema_version"], "repomori.handoff.v1")
            self.assertIn("base_pack", manifest)
            self.assertEqual(manifest["settings"]["base_pack"], str(base_pack.resolve()))
            self.assertTrue((out / "compare.json").exists())
            self.assertTrue((out / "compare.md").exists())
            artifacts = {artifact["path"]: artifact for artifact in manifest["artifacts"]}
            self.assertEqual(artifacts["compare.json"]["kind"], "compare_json")
            compare = json.loads((out / "compare.json").read_text(encoding="utf-8"))
            self.assertEqual(compare["schema_version"], "repomori.compare.v1")
            self.assertGreaterEqual(compare["summary"]["changed_count"], 1)

            check = check_handoff_package(out)
            self.assertTrue(check["valid"])
            self.assertEqual(check["checked_json"], 6)

    def test_check_handoff_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff"
            build_handoff_package(pack, "sqlite Store", out)

            clean = check_handoff_package(out)
            self.assertTrue(clean["valid"])
            self.assertEqual(clean["checked_artifacts"], 9)
            self.assertEqual(clean["checked_json"], 5)

            (out / "context.md").write_text("tampered\n", encoding="utf-8")
            broken = check_handoff_package(out)
            self.assertFalse(broken["valid"])
            self.assertGreaterEqual(broken["error_count"], 1)
            self.assertTrue(any(error["scope"] == "artifact" for error in broken["errors"]))

    def test_benchmark_repo_outputs_reports_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "bench"

            report = benchmark_repo(repo, out, question="sqlite Store")

            self.assertEqual(report["schema_version"], "repomori.bench.v1")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["summary"]["verify_passed"])
            self.assertTrue(report["summary"]["handoff_passed"])
            self.assertTrue((out / "bench.json").exists())
            self.assertTrue((out / "bench.md").exists())
            self.assertTrue((out / "brief.json").exists())
            self.assertTrue((out / "brief.md").exists())
            self.assertTrue((out / "handoff" / "manifest.json").exists())
            self.assertTrue((out / "handoff" / "brief.json").exists())
            self.assertEqual(json.loads((out / "bench.json").read_text(encoding="utf-8")), report)

            markdown = format_benchmark_markdown(report)
            self.assertIn("# RepoMori Benchmark", markdown)
            self.assertIn("sqlite Store", markdown)
            self.assertIn("Brief key files", markdown)

            with self.assertRaises(FileExistsError):
                benchmark_repo(repo, out, question="sqlite Store")
            forced = benchmark_repo(repo, out, question="sqlite Store", force=True)
            self.assertEqual(forced["status"], "pass")

    def test_run_demo_outputs_quickstart_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo-run"

            report = run_demo(out)

            self.assertEqual(report["schema_version"], "repomori.demo.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["query_top_path"], "app.py")
            self.assertEqual(report["summary"]["inspect_status"], "pass")
            self.assertEqual(report["summary"]["inspect_schema"], "repomori.inspect.v1")
            self.assertEqual(report["summary"]["mcp_context_schema"], "repomori.context.v1")
            self.assertIn("repomori_context_build", report["mcp"]["tool_names"])
            self.assertIn("repomori_pack_inspect", report["mcp"]["tool_names"])
            self.assertIn("inspect_json", report["artifacts"])
            self.assertIn("inspect_markdown", report["artifacts"])
            self.assertTrue((out / "demo-repo" / "app.py").exists())
            self.assertTrue((out / "demo.repomori").exists())
            self.assertTrue((out / "inspect.json").exists())
            self.assertTrue((out / "inspect.md").exists())
            self.assertTrue((out / "context.md").exists())
            self.assertTrue((out / "repomori.toml").exists())
            self.assertTrue((out / "packs" / "latest.repomori").exists())
            self.assertEqual(json.loads((out / "inspect.json").read_text(encoding="utf-8")), report["inspect"])
            self.assertIn("# RepoMori Pack Inspector", (out / "inspect.md").read_text(encoding="utf-8"))
            self.assertEqual(json.loads((out / "demo.json").read_text(encoding="utf-8")), report)
            self.assertIn("python -m repomori inspect", (out / "README.md").read_text(encoding="utf-8"))
            self.assertIn("python -m repomori mcp", (out / "README.md").read_text(encoding="utf-8"))

            with self.assertRaises(FileExistsError):
                run_demo(out)
            forced = run_demo(out, force=True)
            self.assertEqual(forced["status"], "pass")

    def test_scan_repository_clean_public_release_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "clean"
            self._public_ready_repo(repo)

            report = scan_repository(repo, public_release=True)

            self.assertEqual(report["schema_version"], "repomori.scan.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["findings"], 0)
            self.assertTrue(report["public_release"]["required_files"]["LICENSE.md"])

    def test_scan_repository_detects_secrets_artifacts_noise_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "risky"
            self._public_ready_repo(repo)
            fake_key = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz1234567890ABCDE"
            (repo / ".env").write_text(f"OPENAI_API_KEY={fake_key}\n", encoding="utf-8")
            local_path = "C:\\Users\\ollet" + "\\" + "OneDrive" + "\\private"
            (repo / "local.py").write_text(f'HOME = "{local_path}"\n', encoding="utf-8")
            (repo / "packs").mkdir()
            (repo / "packs" / "latest.repomori").write_bytes(b"pack")
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / "leftpad.js").write_text("module.exports = 1;\n", encoding="utf-8")
            (repo / "large.bin").write_bytes(b"\x00" * 128)

            report = scan_repository(repo, max_file_bytes=96)
            codes = {finding["code"] for finding in report["findings"]}

            self.assertEqual(report["status"], "fail")
            self.assertIn("openai_api_key", codes)
            self.assertIn("risky_secret_filename", codes)
            self.assertIn("generated_artifact_dir", codes)
            self.assertIn("repomori_pack_artifact", codes)
            self.assertIn("dependency_or_build_noise", codes)
            self.assertIn("windows_user_path", codes)
            self.assertIn("large_file", codes)
            self.assertNotIn(fake_key, json.dumps(report))

    def test_scan_repository_license_posture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "private-meta"
            repo.mkdir()
            (repo / "README.md").write_text("# Private metadata\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text(
                "[project]\nname = \"demo\"\nlicense = { text = \"Private\" }\n",
                encoding="utf-8",
            )

            report = scan_repository(repo)
            codes = {finding["code"] for finding in report["findings"]}

            self.assertEqual(report["status"], "warn")
            self.assertIn("missing_license", codes)
            self.assertIn("private_license_metadata", codes)

    def test_scan_repository_ignore_code_and_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "paths"
            self._public_ready_repo(repo)
            (repo / "README.md").write_text("Use D:\\Temp\\repomori-demo for examples.\n", encoding="utf-8")

            original = scan_repository(repo, public_release=True)
            self.assertEqual(original["status"], "warn")
            self.assertEqual(original["summary"]["findings"], 1)
            self.assertEqual(original["findings"][0]["code"], "temp_drive_path")

            ignored_by_code = scan_repository(repo, public_release=True, ignore_codes=["temp_drive_path"])
            self.assertEqual(ignored_by_code["status"], "pass")
            self.assertEqual(ignored_by_code["summary"]["findings"], 0)
            self.assertEqual(ignored_by_code["summary"]["ignored_findings"], 1)
            self.assertEqual(ignored_by_code["ignored_findings"][0]["ignored_reason"], "ignore_code")

            baseline_payload = scan_baseline_from_report(original)
            ignored_by_baseline = scan_repository(repo, public_release=True, baseline=baseline_payload)
            self.assertEqual(ignored_by_baseline["status"], "pass")
            self.assertEqual(ignored_by_baseline["summary"]["findings"], 0)
            self.assertEqual(ignored_by_baseline["ignored_findings"][0]["ignored_reason"], "baseline")
            self.assertEqual(ignored_by_baseline["ignored_findings"][0]["baseline_match"], "strict")

            baseline_path = Path(tmp) / "scan-baseline.json"
            written = write_scan_baseline(original, baseline_path)
            self.assertEqual(written["schema_version"], "repomori.scan.baseline.write.v1")
            self.assertEqual(written["ignored_count"], 1)
            ignored_by_file = scan_repository(repo, public_release=True, baseline=baseline_path)
            self.assertEqual(ignored_by_file["status"], "pass")

    def test_scan_repository_baseline_tolerates_line_shift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "paths-shift"
            self._public_ready_repo(repo)
            (repo / "README.md").write_text("Use D:\\Temp\\repomori-demo for examples.\n", encoding="utf-8")

            original = scan_repository(repo, public_release=True)
            baseline_payload = scan_baseline_from_report(original)

            (repo / "README.md").write_text(
                "# Heading\n\nUse D:\\Temp\\repomori-demo for examples.\n",
                encoding="utf-8",
            )

            shifted = scan_repository(repo, public_release=True, baseline=baseline_payload)
            self.assertEqual(shifted["status"], "pass")
            self.assertEqual(shifted["summary"]["findings"], 0)
            self.assertEqual(shifted["summary"]["ignored_findings"], 1)
            self.assertEqual(shifted["ignored_findings"][0]["baseline_match"], "semi_strict")

    def test_scan_repository_baseline_fallback_is_conservative(self) -> None:
        repo_baseline = {
            "schema_version": "repomori.scan.baseline.v1",
            "source_schema_version": "repomori.scan.v1",
            "repo_path": "",
            "created_at": 0,
            "ignore": [
                {
                    "severity": "low",
                    "code": "temp_drive_path",
                    "path": "README.md",
                    "message": "D-drive temp path appears in source.",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "paths-ambiguous"
            repo.mkdir()
            (repo / "README.md").write_text(
                "Use D:\\Temp\\repomori-demo for examples.\nUse D:\\Temp\\repomori-demo again.\n",
                encoding="utf-8",
            )

            result = scan_repository(repo, public_release=True, baseline=repo_baseline)
            temp_findings = [item for item in result["findings"] if item["code"] == "temp_drive_path"]
            temp_ignored = [
                item
                for item in result["ignored_findings"]
                if item.get("code") == "temp_drive_path"
            ]
            self.assertEqual(len(temp_findings), 2)
            self.assertEqual(len(temp_ignored), 0)
            self.assertFalse(any(item.get("baseline_match") == "fallback" for item in result["ignored_findings"]))

    def test_run_release_check_schema_scan_and_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release"
            self._public_ready_repo(repo)
            demo_out = repo / ".release-check-demo"

            report = run_release_check(
                repo,
                run_tests=False,
                run_demo_smoke=True,
                demo_out=demo_out,
            )

            self.assertEqual(report["schema_version"], "repomori.release_check.v1")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["checks"]["schema"]["ok"])
            self.assertTrue(report["checks"]["scan"]["ok"])
            self.assertEqual(report["checks"]["tests"]["status"], "skipped")
            self.assertEqual(report["checks"]["demo"]["demo_status"], "pass")
            self.assertFalse(demo_out.exists())

    def test_run_release_check_fails_on_workspace_generated_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-block"
            self._public_ready_repo(repo)
            (repo / "packs").mkdir()
            (repo / "benchmarks").mkdir()
            (repo / "handoff").mkdir()
            (repo / "tmp.repomori").write_text("stale artifact", encoding="utf-8")

            report = run_release_check(
                repo,
                run_tests=False,
                run_demo_smoke=False,
                fail_on="low",
            )

            self.assertEqual(report["status"], "fail")
            self.assertIn("workspace", report["summary"]["failed_checks"])
            self.assertFalse(report["checks"]["workspace"]["ok"])
            self.assertEqual(report["checks"]["workspace"]["status"], "fail")
            self.assertGreaterEqual(report["checks"]["workspace"]["count"], 3)
            self.assertTrue(any("workspace:" in row for row in report["failure_reasons"]))
            self.assertTrue(any("packs" in row.lower() for row in report["failure_reasons"]))

    def test_run_release_check_allows_hidden_workspace_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-clean"
            self._public_ready_repo(repo)
            hidden_artifacts = repo / ".repomori-release-check"
            hidden_artifacts.mkdir()
            (hidden_artifacts / "release-check.repomori").write_text("keep-hidden", encoding="utf-8")
            (repo / ".repomori-packs").mkdir()

            report = run_release_check(
                repo,
                run_tests=False,
                run_demo_smoke=False,
                fail_on="low",
                artifacts_dir=hidden_artifacts,
            )

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["failed_checks"], [])
            self.assertTrue(report["checks"]["workspace"]["ok"])
            self.assertEqual(report["checks"]["workspace"]["status"], "pass")

    def test_run_release_check_reports_baseline_drift_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-drift"
            repo.mkdir()
            (repo / "README.md").write_text("D:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            (repo / "DOCS.md").write_text("D:\\Temp\\repomori-demo\\doc.txt\n", encoding="utf-8")
            (repo / "LICENSE.md").write_text("License text.\n", encoding="utf-8")
            initial_scan = scan_repository(repo, public_release=False)
            baseline_path = Path(tmp) / "scan-baseline.json"
            write_scan_baseline(initial_scan, baseline_path)

            (repo / "README.md").write_text("# heading\nD:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            (repo / "DOCS.md").write_text("D:\\Temp\\repomori-demo\\doc.txt\n", encoding="utf-8")

            report = run_release_check(
                repo,
                public_release=False,
                baseline=baseline_path,
                run_tests=False,
                run_demo_smoke=False,
                fail_on="low",
            )

            self.assertEqual(report["status"], "pass")
            drift = report["checks"]["scan"]["drift_warnings"]
            self.assertEqual(drift["strict_count"], 1)
            self.assertEqual(drift["semi_strict_count"], 1)
            self.assertEqual(drift["fallback_count"], 0)
            self.assertEqual(drift["ignored_total"], 2)
            self.assertEqual(drift["non_strict_count"], 1)
            self.assertAlmostEqual(drift["non_strict_ratio"], 0.5)
            self.assertTrue(drift["downgraded_from_line_match"])
            self.assertFalse(drift["downgraded_from_message_match"])
            self.assertEqual(drift["status"], "warn")
            self.assertIn("line-based strict baseline matches were downgraded to semi-strict by line drift", drift["warnings"])

    def test_workflow_contracts_for_memory_anchor_smoke(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow = (repo_root / ".github/workflows/memory-anchor-smoke.yml").read_text(encoding="utf-8")
        docs_snippet = (repo_root / "docs/memory-anchor-reusable.md").read_text(encoding="utf-8")

        for mode in ("strict", "safe", "legacy"):
            self.assertIn(f"memory-anchor-${{mode}}.json", workflow)
            self.assertIn(f"memory-anchor-${{mode}}.json", docs_snippet)

        self.assertIn("MODES=(strict safe legacy)", workflow)
        self.assertIn('for mode in "${MODES[@]}"', workflow)
        self.assertIn("timeline-anchor.json", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn(
            "${{ steps.run.outputs.artifact_dir }}/memory-anchor-strict.json",
            workflow,
        )
        self.assertIn(
            "${{ steps.run.outputs.artifact_dir }}/memory-anchor-safe.json",
            workflow,
        )
        self.assertIn(
            "${{ steps.run.outputs.artifact_dir }}/memory-anchor-legacy.json",
            workflow,
        )

        self.assertIn('echo "artifact_dir=$BASE_DIR" >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn("timeline-anchor.json", workflow)

    def test_workflow_contracts_for_release_health(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/release-health.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("required_artifacts=(", workflow)
        self.assertIn("release-health.json", workflow)
        self.assertIn("release-health.md", workflow)
        self.assertIn("--drift-log", workflow)
        self.assertIn("\"$DRIFT_LOG\"", workflow)
        self.assertIn("${{ steps.run.outputs.drift_log }}", workflow)
        self.assertIn("${{ steps.run.outputs.artifacts_dir }}/release-health.json", workflow)
        self.assertIn("${{ steps.run.outputs.artifacts_dir }}/release-health.md", workflow)
        self.assertIn("if [ -n \"$DRIFT_LOG\" ] && [ ! -f \"$DRIFT_LOG\" ]", workflow)

    def test_workflow_contracts_for_tests_preflight(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml").read_text(encoding="utf-8")

        self.assertIn('generated_dirs = {', workflow)
        self.assertIn("release-check preflight blocked by visible top-level artifacts:", workflow)
        self.assertIn("Move generated outputs under hidden directories for this repo, for example:", workflow)
        self.assertIn("  - .repomori-packs", workflow)
        self.assertIn("  - .repomori-release-check", workflow)
        self.assertIn("  - .repomori-release-health", workflow)
        self.assertIn("  - .repomori-health", workflow)

    def test_run_release_health_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health"
            self._public_ready_repo(repo)
            out = Path(tmp) / "snapshots"
            run_memory_cycle(repo, out, no_handoff=True)

            health_dir = Path(tmp) / "health-artifacts"
            report = run_release_health(
                repo,
                snapshot_dir=out,
                run_tests=False,
                run_demo_smoke=False,
                artifacts_dir=health_dir,
            )

            self.assertEqual(report["schema_version"], "repomori.health.v1")
            self.assertEqual(report["status"], "pass")
            self.assertIn("release_check", report["checks"])
            self.assertIn("doctor", report["checks"])
            self.assertIn("chain", report["checks"])
            self.assertIn("timeline", report["checks"])
            self.assertIn("drift_summary", report["checks"])
            self.assertEqual(report["artifacts"]["json"], str((health_dir / "release-health.json")))
            self.assertEqual(report["artifacts"]["markdown"], str((health_dir / "release-health.md")))
            self.assertTrue((health_dir / "release-health.json").exists())
            self.assertTrue((health_dir / "release-health.md").exists())

    def test_run_release_health_obeys_drift_policy_without_failing_on_fail_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health-policy"
            repo.mkdir()
            (repo / "README.md").write_text(
                "D:\\Temp\\repomori-demo\\one\\file.py\n",
                encoding="utf-8",
            )
            initial = scan_repository(repo, public_release=False)
            baseline = Path(tmp) / "scan-baseline.json"
            write_scan_baseline(initial, baseline)

            (repo / "README.md").write_text(
                "# heading\nD:\\Temp\\repomori-demo\\one\\file.py\n",
                encoding="utf-8",
            )
            out = Path(tmp) / "snapshots"
            run_memory_cycle(repo, out, no_handoff=True)

            drift_log = Path(tmp) / "drift.log"
            policy = Path(tmp) / "drift-policy.json"
            policy.write_text(json.dumps({
                "non_strict_ratio": {"warn-at": 0.2, "fail-at": 1.0},
            }), encoding="utf-8")

            report = run_release_health(
                repo,
                snapshot_dir=out,
                baseline=baseline,
                public_release=False,
                run_tests=False,
                run_demo_smoke=False,
                drift_policy=policy,
                drift_log=drift_log,
                artifacts_dir=Path(tmp) / "health-policy-artifacts",
            )

            self.assertEqual(report["schema_version"], "repomori.health.v1")
            self.assertEqual(report["checks"]["release_check"]["summary"]["drift_policy_status"], "warn")
            self.assertEqual(report["status"], "warn")
            self.assertTrue(drift_log.exists())

    def test_run_release_health_without_snapshot_history_warns_not_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health-no-snap"
            self._public_ready_repo(repo)

            report = run_release_health(
                repo,
                run_tests=False,
                run_demo_smoke=False,
            )

            self.assertEqual(report["schema_version"], "repomori.health.v1")
            self.assertEqual(report["status"], "warn")
            self.assertEqual(report["checks"]["doctor"]["status"], "warn")
            self.assertEqual(report["checks"]["chain"]["status"], "warn")
            self.assertEqual(report["checks"]["timeline"]["status"], "warn")

    def test_run_release_health_policy_can_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health-policy-fail"
            repo.mkdir()
            (repo / "README.md").write_text("D:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            initial = scan_repository(repo, public_release=False)
            baseline = Path(tmp) / "scan-baseline.json"
            write_scan_baseline(initial, baseline)

            (repo / "README.md").write_text("# heading\nD:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            out = Path(tmp) / "snapshots"
            run_memory_cycle(repo, out, no_handoff=True)

            policy = Path(tmp) / "drift-policy.json"
            policy.write_text(json.dumps({"non_strict_ratio": {"warn-at": 0.2, "fail-at": 0.9}}), encoding="utf-8")

            report = run_release_health(
                repo,
                snapshot_dir=out,
                baseline=baseline,
                public_release=False,
                run_tests=False,
                run_demo_smoke=False,
                drift_policy=policy,
                artifacts_dir=Path(tmp) / "health-policy-fail-artifacts",
            )

            self.assertEqual(report["schema_version"], "repomori.health.v1")
            self.assertEqual(report["checks"]["release_check"]["summary"]["drift_policy_status"], "fail")
            self.assertEqual(report["status"], "fail")

    def test_baseline_drift_warning_math(self) -> None:
        conservative = build_baseline_drift_report(
            {"summary": {"baseline_match_counts": {"strict": 5, "semi_strict": 0, "fallback": 0}}},
            investigate_threshold=0.1,
        )
        self.assertEqual(conservative["non_strict_ratio"], 0.0)
        self.assertFalse(conservative["investigate"])
        self.assertEqual(conservative["status"], "pass")

        noisy = build_baseline_drift_report(
            {"summary": {"baseline_match_counts": {"strict": 1, "semi_strict": 3, "fallback": 1}}},
            investigate_threshold=0.2,
        )
        self.assertEqual(noisy["non_strict_ratio"], 0.8)
        self.assertEqual(noisy["non_strict_count"], 4)
        self.assertTrue(noisy["investigate"])
        self.assertTrue(noisy["downgraded_from_line_match"])
        self.assertTrue(noisy["downgraded_from_message_match"])

    def test_build_baseline_drift_report_modes(self) -> None:
        strict = build_baseline_drift_report(
            {"summary": {"baseline_match_counts": {"strict": 2, "semi_strict": 0, "fallback": 0}}},
            run_meta={"run_id": "strict-run"},
        )
        self.assertEqual(strict["strict_count"], 2)
        self.assertEqual(strict["semi_strict_count"], 0)
        self.assertEqual(strict["fallback_count"], 0)
        self.assertEqual(strict["status"], "pass")
        self.assertFalse(strict["downgraded_from_line_match"])
        self.assertFalse(strict["downgraded_from_message_match"])

        mixed = build_baseline_drift_report(
            {"summary": {"baseline_match_counts": {"strict": 3, "semi_strict": 1, "fallback": 0}}},
            run_meta={"run_id": "mixed-run"},
        )
        self.assertEqual(mixed["non_strict_ratio"], 0.25)
        self.assertTrue(mixed["investigate"])
        self.assertEqual(mixed["run_id"], "mixed-run")

        fallback = build_baseline_drift_report(
            {"summary": {"baseline_match_counts": {"strict": 1, "semi_strict": 0, "fallback": 2}}},
            run_meta={"run_id": "fallback-run"},
        )
        self.assertEqual(fallback["fallback_count"], 2)
        self.assertTrue(fallback["downgraded_from_message_match"])
        self.assertEqual(fallback["status"], "warn")

        with_scan_meta = build_baseline_drift_report(
            {
                "repo_path": "D:/Temp/demo",
                "settings": {"baseline_path": "D:/Temp/demo/.repomori-scan-baseline.json"},
                "summary": {"baseline_match_counts": {"strict": 1, "semi_strict": 0, "fallback": 0}},
            },
        )
        self.assertEqual(with_scan_meta["repo_path"], "D:/Temp/demo")
        self.assertEqual(with_scan_meta["baseline_path"], "D:/Temp/demo/.repomori-scan-baseline.json")

    def test_snapshot_repo_builds_latest_and_compares_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            first = snapshot_repo(repo, out)
            self.assertEqual(first["schema_version"], "repomori.snapshot.v1")
            self.assertEqual(first["status"], "pass")
            self.assertIsNone(first["comparison"])
            self.assertTrue(first["settings"]["incremental"])
            self.assertFalse(first["summary"]["incremental"])
            self.assertIsNone(first["summary"]["incremental_base_pack"])
            self.assertTrue((out / "latest.repomori").exists())
            self.assertTrue((out / first["artifacts"]["snapshot_json"]).exists())
            self.assertTrue((out / first["artifacts"]["snapshot_markdown"]).exists())
            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["schema_version"], "repomori.snapshots.v1")
            self.assertEqual(index["snapshot_count"], 1)
            self.assertEqual(index["latest"]["pack_path"], first["summary"]["pack_path"])
            self.assertEqual(index["latest"]["pack_sha256"], hashlib.sha256(Path(first["summary"]["pack_path"]).read_bytes()).hexdigest())

            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            second = snapshot_repo(repo, out)
            self.assertEqual(second["schema_version"], "repomori.snapshot.v1")
            self.assertEqual(second["status"], "pass")
            self.assertTrue(second["summary"]["incremental"])
            self.assertEqual(second["summary"]["incremental_base_pack"], first["summary"]["pack_path"])
            self.assertEqual(second["summary"]["reused_file_count"], 3)
            self.assertEqual(second["summary"]["rebuilt_file_count"], 1)
            self.assertIsNotNone(second["comparison"])
            self.assertEqual(second["comparison"]["summary"]["added_count"], 1)
            self.assertIn("compare_json", second["artifacts"])
            self.assertIn("compare_markdown", second["artifacts"])
            self.assertEqual(second["artifacts"]["snapshot_index"], "snapshots.json")
            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["snapshot_count"], 2)
            self.assertEqual(index["latest"]["pack_path"], second["summary"]["pack_path"])
            self.assertEqual(index["latest"]["added_count"], 1)
            self.assertTrue(index["latest"]["incremental"])
            self.assertEqual(index["latest"]["reused_file_count"], 3)

            timeline = read_snapshot_timeline(out, limit=1)
            self.assertEqual(timeline["schema_version"], "repomori.timeline.v1")
            self.assertEqual(timeline["snapshot_count"], 2)
            self.assertEqual(timeline["returned_count"], 1)
            self.assertEqual(timeline["snapshots"][0]["pack_path"], second["summary"]["pack_path"])
            self.assertEqual(timeline["summary"]["total_added"], 1)
            timeline_markdown = format_timeline_markdown(timeline)
            self.assertIn("# RepoMori Snapshot Timeline", timeline_markdown)
            self.assertIn("Recent Snapshots", timeline_markdown)

            markdown = format_snapshot_markdown(second)
            self.assertIn("# RepoMori Snapshot", markdown)
            self.assertIn("Incremental", markdown)
            self.assertIn("Reused files", markdown)
            self.assertIn("## Comparison", markdown)
            self.assertIn("Added", markdown)

    def test_snapshot_chain_passes_and_exposes_timeline_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            first = snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            second = snapshot_repo(repo, out)

            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["chain"]["chain_version"], "repomori.snapshot_chain.v1")
            self.assertEqual(index["chain"]["snapshot_count"], 2)
            self.assertEqual(index["latest"]["chain_hash"], index["chain"]["head_chain_hash"])
            self.assertEqual(index["snapshots"][0]["chain_index"], 0)
            self.assertIsNone(index["snapshots"][0]["previous_chain_hash"])
            self.assertEqual(index["snapshots"][1]["previous_chain_hash"], index["snapshots"][0]["chain_hash"])
            self.assertEqual(index["snapshots"][1]["pack_path"], second["summary"]["pack_path"])

            report = verify_snapshot_chain(out)
            self.assertEqual(report["schema_version"], "repomori.snapshot_chain.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["checked_count"], 2)
            self.assertEqual(report["summary"]["head_chain_hash"], index["chain"]["head_chain_hash"])

            timeline = read_snapshot_timeline(out)
            self.assertEqual(timeline["summary"]["chain_status"], "pass")
            self.assertEqual(timeline["summary"]["chain_head_hash"], index["chain"]["head_chain_hash"])
            self.assertEqual(timeline["chain"]["status"], "pass")
            timeline_markdown = format_timeline_markdown(timeline)
            self.assertIn("Chain status", timeline_markdown)

            doctor = doctor_snapshot_dir(out)
            self.assertEqual(doctor["status"], "pass")
            self.assertEqual(doctor["summary"]["chain_status"], "pass")

            brief = build_agent_brief(out)
            self.assertEqual(brief["summary"]["chain_status"], "pass")
            self.assertEqual(brief["chain"]["summary"]["head_chain_hash"], index["chain"]["head_chain_hash"])

            markdown = format_snapshot_chain_markdown(report)
            self.assertIn("# RepoMori Snapshot Chain", markdown)
            self.assertIn("Head hash", markdown)
            self.assertIn("Checked", markdown)

    def test_snapshot_chain_detects_metadata_tamper_reorder_and_middle_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out)
            (repo / "next.py").write_text("def next_step():\n    return 'next'\n", encoding="utf-8")
            snapshot_repo(repo, out)

            index_path = out / "snapshots.json"
            clean = json.loads(index_path.read_text(encoding="utf-8"))

            tampered = json.loads(json.dumps(clean))
            tampered["snapshots"][0]["repo_path"] = "tampered"
            index_path.write_text(json.dumps(tampered, indent=2) + "\n", encoding="utf-8")
            report = verify_snapshot_chain(out)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any("entry hash" in error["message"] for error in report["errors"]))

            reordered = json.loads(json.dumps(clean))
            reordered["snapshots"][1], reordered["snapshots"][2] = reordered["snapshots"][2], reordered["snapshots"][1]
            index_path.write_text(json.dumps(reordered, indent=2) + "\n", encoding="utf-8")
            report = verify_snapshot_chain(out)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any("previous chain hash" in error["message"] or "chain index" in error["message"] for error in report["errors"]))

            deleted = json.loads(json.dumps(clean))
            deleted["snapshots"] = [deleted["snapshots"][0], deleted["snapshots"][2]]
            deleted["snapshot_count"] = 2
            deleted["chain"]["snapshot_count"] = 2
            index_path.write_text(json.dumps(deleted, indent=2) + "\n", encoding="utf-8")
            report = verify_snapshot_chain(out)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any("previous chain hash" in error["message"] for error in report["errors"]))

    def test_snapshot_chain_warns_for_legacy_unchained_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            snapshot_repo(repo, out)
            index_path = out / "snapshots.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index.pop("chain", None)
            for snapshot in index["snapshots"]:
                for field in ("chain_version", "chain_index", "previous_chain_hash", "entry_hash", "chain_hash"):
                    snapshot.pop(field, None)
            for field in ("chain_version", "chain_index", "previous_chain_hash", "entry_hash", "chain_hash"):
                index["latest"].pop(field, None)
            index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

            report = verify_snapshot_chain(out)
            self.assertEqual(report["status"], "warn")
            self.assertTrue(report["summary"]["legacy_unchained"])
            doctor = doctor_snapshot_dir(out)
            self.assertEqual(doctor["status"], "warn")
            self.assertEqual(doctor["summary"]["chain_status"], "warn")

    def test_snapshot_anchor_exports_current_chain_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            first = snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            second = snapshot_repo(repo, out)

            anchor = build_snapshot_anchor(out)
            chain = verify_snapshot_chain(out)
            self.assertEqual(anchor["schema_version"], "repomori.snapshot_anchor.v1")
            self.assertEqual(anchor["status"], "pass")
            self.assertEqual(anchor["chain"]["head_chain_hash"], chain["summary"]["head_chain_hash"])
            self.assertEqual(anchor["chain"]["snapshot_count"], 2)
            self.assertEqual(anchor["latest_snapshot"]["pack_path"], second["summary"]["pack_path"])
            self.assertEqual(
                anchor["latest_snapshot"]["pack_sha256"],
                hashlib.sha256(Path(second["summary"]["pack_path"]).read_bytes()).hexdigest(),
            )
            self.assertEqual(anchor["latest_snapshot"]["chain_hash"], anchor["chain"]["head_chain_hash"])
            self.assertEqual(anchor["verification"]["status"], "pass")
            anchor_without_hash = dict(anchor)
            anchor_hash = anchor_without_hash.pop("anchor_hash")
            expected_hash = hashlib.sha256(
                json.dumps(anchor_without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            self.assertEqual(anchor_hash, expected_hash)

            markdown = format_snapshot_anchor_markdown(anchor)
            self.assertIn("# RepoMori Snapshot Anchor", markdown)
            self.assertIn("Anchor hash", markdown)
            self.assertIn(Path(first["summary"]["pack_path"]).parent.name, markdown)

    def test_snapshot_anchor_records_failed_chain_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            snapshot_repo(repo, out)
            index_path = out / "snapshots.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["snapshots"][0]["repo_path"] = "tampered"
            index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

            anchor = build_snapshot_anchor(out)
            self.assertEqual(anchor["status"], "fail")
            self.assertGreaterEqual(anchor["verification"]["error_count"], 1)
            self.assertTrue(anchor["verification"]["errors"])
            self.assertTrue(anchor["anchor_hash"])

    def test_verify_snapshot_anchor_passes_and_detects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            snapshot_repo(repo, out)
            anchor = build_snapshot_anchor(out)
            report = verify_snapshot_anchor(anchor, out)

            self.assertEqual(report["schema_version"], "repomori.snapshot_anchor.verify.v1")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["summary"]["anchor_hash_valid"])
            self.assertTrue(report["summary"]["chain_head_matches"])
            self.assertTrue(report["summary"]["latest_snapshot_matches"])
            self.assertTrue(report["summary"]["current_pack_hash_matches"])

            tampered = dict(anchor)
            tampered["created_at"] = int(tampered["created_at"]) + 1
            tampered_report = verify_snapshot_anchor(tampered, out, check_current=False)
            self.assertEqual(tampered_report["status"], "fail")
            self.assertFalse(tampered_report["summary"]["anchor_hash_valid"])
            self.assertTrue(any("Anchor hash" in error["message"] for error in tampered_report["errors"]))

            markdown = format_snapshot_anchor_verification_markdown(report)
            self.assertIn("# RepoMori Snapshot Anchor Verification", markdown)
            self.assertIn("Anchor hash valid", markdown)

    def test_verify_snapshot_anchor_detects_current_timeline_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            snapshot_repo(repo, out)
            anchor = build_snapshot_anchor(out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out)

            report = verify_snapshot_anchor(anchor, out)
            self.assertEqual(report["status"], "fail")
            self.assertFalse(report["summary"]["chain_head_matches"])
            self.assertFalse(report["summary"]["latest_snapshot_matches"])
            self.assertTrue(any("chain head" in error["message"] for error in report["errors"]))

    def test_read_snapshot_stats_reports_incremental_savings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            first = snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            second = snapshot_repo(repo, out)
            (repo / "app.py").write_text(
                "import sqlite3\n\n"
                "class Store:\n"
                "    def connect(self):\n"
                "        return sqlite3.connect(':memory:')\n"
                "    def close(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            third = snapshot_repo(repo, out)

            stats = read_snapshot_stats(out, limit=2)

            self.assertEqual(stats["schema_version"], "repomori.stats.v1")
            self.assertEqual(stats["snapshot_count"], 3)
            self.assertEqual(stats["returned_count"], 2)
            self.assertEqual(stats["summary"]["incremental_snapshot_count"], 2)
            self.assertEqual(stats["summary"]["full_snapshot_count"], 1)
            self.assertEqual(stats["summary"]["total_reused_files"], 6)
            self.assertEqual(stats["summary"]["total_rebuilt_files"], 5)
            self.assertGreater(stats["summary"]["reuse_percent"], 50)
            self.assertEqual(stats["latest"]["pack_path"], third["summary"]["pack_path"])
            self.assertEqual(stats["latest"]["incremental_base_pack"], second["summary"]["pack_path"])
            self.assertEqual(stats["snapshots"][0]["pack_path"], third["summary"]["pack_path"])
            self.assertEqual(stats["top_reuse"][0]["reused_file_count"], 3)

            timeline = read_snapshot_timeline(out)
            self.assertEqual(timeline["summary"]["total_reused_files"], 6)
            self.assertEqual(timeline["summary"]["incremental_snapshot_count"], 2)

            markdown = format_stats_markdown(stats)
            self.assertIn("# RepoMori Snapshot Stats", markdown)
            self.assertIn("Reuse percent", markdown)
            self.assertIn(Path(third["summary"]["pack_path"]).name, markdown)

    def test_snapshot_repo_can_disable_incremental_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            first = snapshot_repo(repo, out)
            second = snapshot_repo(repo, out, incremental=False)

            self.assertFalse(second["settings"]["incremental"])
            self.assertFalse(second["build"]["incremental"])
            self.assertIsNone(second["summary"]["incremental_base_pack"])
            self.assertEqual(second["summary"]["previous_latest_pack"], first["summary"]["pack_path"])
            self.assertEqual(second["summary"]["reused_file_count"], 0)
            self.assertEqual(second["summary"]["rebuilt_file_count"], 3)
            self.assertEqual(second["comparison"]["summary"]["unchanged_count"], 3)

    def test_snapshot_repo_can_build_handoff_with_previous_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            first = snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            second = snapshot_repo(repo, out, handoff_question="sqlite Store")

            self.assertEqual(second["schema_version"], "repomori.snapshot.v1")
            self.assertIsNotNone(second["handoff"])
            self.assertTrue(second["summary"]["handoff_passed"])
            handoff_dir = Path(second["summary"]["handoff_dir"])
            self.assertTrue((handoff_dir / "manifest.json").exists())
            self.assertTrue((handoff_dir / "compare.json").exists())
            manifest = second["handoff"]
            self.assertEqual(manifest["settings"]["base_pack"], first["summary"]["pack_path"])
            self.assertEqual(second["handoff_check"]["checked_json"], 6)
            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["latest"]["handoff_dir"], str(handoff_dir))
            self.assertTrue(index["latest"]["handoff_passed"])

    def test_doctor_snapshot_dir_passes_clean_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out, handoff_question="sqlite Store")

            report = doctor_snapshot_dir(out, verify_packs=True)
            self.assertEqual(report["schema_version"], "repomori.doctor.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["error_count"], 0)
            self.assertEqual(report["warning_count"], 0)
            self.assertEqual(report["summary"]["snapshot_count"], 2)
            self.assertEqual(report["summary"]["checked_packs"], 2)
            self.assertEqual(report["summary"]["verified_packs"], 2)
            self.assertEqual(report["summary"]["checked_handoffs"], 1)

    def test_doctor_detects_missing_pack_and_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            first = snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            second = snapshot_repo(repo, out)

            Path(first["summary"]["pack_path"]).unlink()
            second_pack = Path(second["summary"]["pack_path"])
            second_pack.write_bytes(second_pack.read_bytes() + b"tampered")

            report = doctor_snapshot_dir(out)
            self.assertEqual(report["status"], "fail")
            self.assertGreaterEqual(report["error_count"], 2)
            messages = [error["message"] for error in report["errors"]]
            self.assertTrue(any("does not exist" in message for message in messages))
            self.assertTrue(any("SHA-256" in message for message in messages))

    def test_doctor_validates_snapshot_handoff_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            report = snapshot_repo(repo, out, handoff_question="sqlite Store")
            handoff_dir = Path(report["summary"]["handoff_dir"])
            (handoff_dir / "context.md").unlink()

            doctor = doctor_snapshot_dir(out)
            self.assertEqual(doctor["status"], "fail")
            self.assertTrue(any(error["scope"] == "handoff" for error in doctor["errors"]))

    def test_prune_snapshots_dry_run_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"

            first = snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            second = snapshot_repo(repo, out, handoff_question="sqlite Store")
            (repo / "next.py").write_text("def next_step():\n    return 'next'\n", encoding="utf-8")
            third = snapshot_repo(repo, out)
            index_before = (out / "snapshots.json").read_text(encoding="utf-8")

            dry_run = prune_snapshots(out, keep=1)
            self.assertFalse(dry_run["applied"])
            self.assertEqual(len(dry_run["retained"]), 1)
            self.assertEqual(len(dry_run["candidates"]), 2)
            self.assertEqual((out / "snapshots.json").read_text(encoding="utf-8"), index_before)
            self.assertTrue(Path(first["summary"]["pack_path"]).exists())
            self.assertTrue(Path(second["summary"]["handoff_dir"]).exists())

            applied = prune_snapshots(out, keep=1, apply=True)
            self.assertTrue(applied["applied"])
            self.assertFalse(applied["errors"])
            self.assertTrue((out / "latest.repomori").exists())
            self.assertTrue((out / "snapshots.json").exists())
            self.assertTrue(Path(third["summary"]["pack_path"]).exists())
            self.assertFalse(Path(first["summary"]["pack_path"]).exists())
            self.assertFalse(Path(second["summary"]["handoff_dir"]).exists())
            updated = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["snapshot_count"], 1)
            self.assertEqual(updated["latest"]["pack_path"], third["summary"]["pack_path"])
            self.assertTrue(updated["chain"]["anchored_to_pruned_history"])
            chain = verify_snapshot_chain(out)
            self.assertEqual(chain["status"], "pass")
            self.assertTrue(chain["summary"]["anchored_to_pruned_history"])

    def test_prune_skips_external_handoff_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshots"
            external_handoff = Path(tmp) / "external-handoff"

            snapshot_repo(repo, out, handoff_question="sqlite Store", handoff_out_dir=external_handoff)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out)
            (repo / "next.py").write_text("def next_step():\n    return 'next'\n", encoding="utf-8")
            snapshot_repo(repo, out)

            report = prune_snapshots(out, keep=1, apply=True)
            self.assertFalse(report["errors"])
            self.assertTrue(external_handoff.exists())
            self.assertTrue(
                any(
                    item["reason"] == "skipped_external" and item["path"] == str(external_handoff.resolve())
                    for item in report["skipped"]
                )
            )

    def test_run_memory_cycle_creates_handoff_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"

            report = run_memory_cycle(repo, out)

            self.assertEqual(report["schema_version"], "repomori.memory.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["settings"]["handoff_question"], "continue this repo")
            self.assertTrue(report["settings"]["incremental"])
            self.assertFalse(report["summary"]["incremental"])
            self.assertEqual(report["snapshot"]["schema_version"], "repomori.snapshot.v1")
            self.assertEqual(report["doctor"]["schema_version"], "repomori.doctor.v1")
            self.assertEqual(report["prune"]["schema_version"], "repomori.prune.v1")
            self.assertEqual(report["timeline"]["schema_version"], "repomori.timeline.v1")
            self.assertFalse(report["prune"]["applied"])
            handoff_dir = Path(report["summary"]["handoff_dir"])
            self.assertTrue((handoff_dir / "manifest.json").exists())
            self.assertTrue(report["summary"]["handoff_passed"])
            self.assertEqual(report["timeline"]["returned_count"], 1)

    def test_run_memory_cycle_can_skip_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"

            report = run_memory_cycle(repo, out, no_handoff=True)

            self.assertEqual(report["schema_version"], "repomori.memory.v1")
            self.assertEqual(report["status"], "pass")
            self.assertIsNone(report["settings"]["handoff_question"])
            self.assertIsNone(report["summary"]["handoff_dir"])
            self.assertNotIn("handoff", report["artifacts"])
            self.assertIsNone(report["snapshot"]["handoff"])

    def test_run_memory_cycle_prune_dry_run_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"

            first = run_memory_cycle(repo, out, no_handoff=True, keep=1)
            first_pack = Path(first["summary"]["pack_path"])
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            dry_run = run_memory_cycle(repo, out, no_handoff=True, keep=1)

            self.assertTrue(dry_run["summary"]["incremental"])
            self.assertEqual(dry_run["summary"]["incremental_base_pack"], first["summary"]["pack_path"])
            self.assertEqual(dry_run["summary"]["reused_file_count"], 3)
            self.assertEqual(dry_run["summary"]["rebuilt_file_count"], 1)
            self.assertFalse(dry_run["prune"]["applied"])
            self.assertEqual(len(dry_run["prune"]["candidates"]), 1)
            self.assertTrue(first_pack.exists())

            second_pack = Path(dry_run["summary"]["pack_path"])
            (repo / "next.py").write_text("def next_step():\n    return 'next'\n", encoding="utf-8")
            applied = run_memory_cycle(repo, out, no_handoff=True, keep=1, prune_apply=True)

            self.assertTrue(applied["prune"]["applied"])
            self.assertFalse(applied["prune"]["errors"])
            self.assertFalse(first_pack.exists())
            self.assertFalse(second_pack.exists())
            self.assertTrue(Path(applied["summary"]["pack_path"]).exists())
            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["snapshot_count"], 1)

    def test_run_memory_cycle_can_disable_incremental_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"

            first = run_memory_cycle(repo, out, no_handoff=True)
            second = run_memory_cycle(repo, out, no_handoff=True, incremental=False)

            self.assertFalse(second["settings"]["incremental"])
            self.assertFalse(second["snapshot"]["build"]["incremental"])
            self.assertEqual(second["summary"]["incremental_base_pack"], None)
            self.assertEqual(second["snapshot"]["summary"]["previous_latest_pack"], first["summary"]["pack_path"])
            self.assertEqual(second["summary"]["reused_file_count"], 0)
            self.assertEqual(second["summary"]["rebuilt_file_count"], 3)

    def test_run_memory_cycle_can_write_diff_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"

            first = run_memory_cycle(repo, out, no_handoff=True, diff_context=True)
            self.assertEqual(first["summary"]["diff_context_status"], "skipped_no_previous_pack")
            self.assertIsNone(first["diff_context"])

            (repo / "app.py").write_text(
                "import sqlite3\n\n"
                "class Store:\n"
                "    def connect(self):\n"
                "        return sqlite3.connect(':memory:')\n"
                "    def close(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            second = run_memory_cycle(
                repo,
                out,
                no_handoff=True,
                diff_context=True,
                diff_context_question="close Store",
                diff_context_limit=3,
            )

            self.assertEqual(second["summary"]["diff_context_status"], "written")
            self.assertEqual(second["diff_context"]["schema_version"], "repomori.diff_context.v1")
            self.assertEqual(second["diff_context"]["summary"]["changed_count"], 1)
            self.assertIn("diff_context_json", second["artifacts"])
            diff_json = out / second["artifacts"]["diff_context_json"]
            diff_md = out / second["artifacts"]["diff_context_markdown"]
            self.assertTrue(diff_json.exists())
            self.assertTrue(diff_md.exists())
            self.assertIn("def close", diff_md.read_text(encoding="utf-8"))

            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["latest"]["diff_context_json"], diff_json.name)
            self.assertEqual(index["latest"]["diff_context_status"], "written")
            doctor = doctor_snapshot_dir(out)
            self.assertEqual(doctor["status"], "pass")

            (repo / "next.py").write_text("def next_step():\n    return 'next'\n", encoding="utf-8")
            third = run_memory_cycle(repo, out, no_handoff=True, diff_context=True, keep=1, prune_apply=True)
            self.assertEqual(third["summary"]["diff_context_status"], "written")
            self.assertFalse(diff_json.exists())
            self.assertFalse(diff_md.exists())

    def test_run_memory_cycle_can_write_anchor_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"
            anchor_path = out / "snapshot-anchor.json"

            report = run_memory_cycle(
                repo,
                out,
                no_handoff=True,
                anchor_out=str(anchor_path),
            )

            self.assertEqual(report["schema_version"], "repomori.memory.v1")
            self.assertEqual(report["summary"]["anchor_status"], "pass")
            self.assertIsNone(report["summary"]["anchor_verification_status"])
            self.assertEqual(report["summary"]["anchor_path"], str(anchor_path.resolve()))
            self.assertIn("anchor", report["artifacts"])
            self.assertTrue(anchor_path.exists())
            anchor_record = report["artifacts"]["anchor"]
            anchor_file = out / anchor_record["path"] if not Path(anchor_record["path"]).is_absolute() else Path(anchor_record["path"])
            self.assertEqual(anchor_record["kind"], "anchor_json")
            self.assertEqual(anchor_file, anchor_path)
            self.assertEqual(anchor_record["sha256"], hashlib.sha256(anchor_path.read_bytes()).hexdigest())

    def test_run_memory_cycle_can_verify_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"
            anchor_path = out / "snapshot-anchor.json"
            log_path = out / "anchor-log.jsonl"

            report = run_memory_cycle(
                repo,
                out,
                no_handoff=True,
                anchor_out=str(anchor_path),
                anchor_verify=True,
                anchor_log=str(log_path),
            )

            self.assertEqual(report["schema_version"], "repomori.memory.v1")
            self.assertEqual(report["summary"]["anchor_status"], "pass")
            self.assertEqual(report["summary"]["anchor_verification_status"], "pass")
            self.assertEqual(report["anchor_verification"]["status"], "pass")
            self.assertIsNotNone(report["anchor_verification"])
            self.assertIsNotNone(report["anchor_log"])
            self.assertTrue(anchor_path.exists())
            self.assertTrue(log_path.exists())
            audit_rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(audit_rows), 1)
            self.assertEqual(audit_rows[0]["anchor_schema_version"], "repomori.snapshot_anchor.verify.v1")
            self.assertEqual(audit_rows[0]["anchor_status"], "pass")
            self.assertIsNotNone(audit_rows[0]["chain_head_hash"])
            self.assertEqual(audit_rows[0]["snapshot_count"], 1)

    def test_run_memory_cycle_anchor_verification_failures_and_allow_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"
            anchor_path = out / "snapshot-anchor.json"

            def force_failure(*_args: object, **_kwargs: object) -> dict[str, object]:
                return {
                    "schema_version": "repomori.snapshot_anchor.verify.v1",
                    "status": "fail",
                    "anchor_path": str(anchor_path),
                    "out_dir": str(out),
                    "summary": {
                        "error_count": 1,
                        "warning_count": 0,
                        "anchor_hash_valid": False,
                        "chain_head_matches": False,
                        "latest_snapshot_matches": False,
                        "current_pack_hash_matches": False,
                    },
                    "errors": [{"scope": "anchor", "message": "forced failure"}],
                    "warnings": [],
                }

            with patch.object(codec, "verify_snapshot_anchor", side_effect=force_failure):
                failed = run_memory_cycle(
                    repo,
                    out,
                    no_handoff=True,
                    anchor_out=str(anchor_path),
                    anchor_verify=True,
                    allow_unverified_anchor=False,
                )
                self.assertEqual(failed["summary"]["anchor_verification_status"], "fail")
                self.assertEqual(failed["status"], "fail")

                allowed = run_memory_cycle(
                    repo,
                    out,
                    no_handoff=True,
                    anchor_out=str(anchor_path),
                    anchor_verify=True,
                    allow_unverified_anchor=True,
                )
                self.assertEqual(allowed["summary"]["anchor_verification_status"], "fail")
                self.assertEqual(allowed["status"], "warn")
                self.assertTrue(
                    any("anchor verification failed" in reason.lower() for reason in allowed["failure_reasons"])
                )

            def doctor_warning(*_args: object, **_kwargs: object) -> dict[str, object]:
                return {
                    "schema_version": "repomori.doctor.v1",
                    "status": "warn",
                    "error_count": 0,
                    "warning_count": 1,
                    "summary": {},
                    "errors": [],
                    "warnings": [{"scope": "chain", "message": "forced warning"}],
                }

            with (
                patch.object(codec, "verify_snapshot_anchor", side_effect=force_failure),
                patch.object(codec, "doctor_snapshot_dir", side_effect=doctor_warning),
            ):
                failed_with_doctor_warning = run_memory_cycle(
                    repo,
                    out,
                    no_handoff=True,
                    anchor_out=str(anchor_path),
                    anchor_verify=True,
                    allow_unverified_anchor=False,
                )
                self.assertEqual(failed_with_doctor_warning["summary"]["doctor_status"], "warn")
                self.assertEqual(failed_with_doctor_warning["summary"]["anchor_verification_status"], "fail")
                self.assertEqual(failed_with_doctor_warning["status"], "fail")
                self.assertTrue(any(reason.startswith("doctor:") for reason in failed_with_doctor_warning["failure_reasons"]))

    def test_run_memory_cycle_anchor_freshness_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-anchor-profile"
            anchor_path = out / "timeline-anchor.json"
            checks: list[bool] = []

            def verify_profile_fail(
                _anchor: Path | str,
                _out: Path | str,
                check_current: bool = True,
            ) -> dict[str, object]:
                checks.append(check_current)
                return {
                    "schema_version": "repomori.snapshot_anchor.verify.v1",
                    "status": "fail",
                    "anchor_path": str(_anchor),
                    "out_dir": str(_out),
                    "summary": {
                        "error_count": 1,
                        "warning_count": 0,
                        "anchor_hash_valid": False,
                        "chain_head_matches": False,
                        "latest_snapshot_matches": False,
                        "current_pack_hash_matches": False,
                    },
                    "errors": [{"scope": "anchor", "message": "forced profile failure"}],
                    "warnings": [],
                }

            with patch.object(codec, "verify_snapshot_anchor", side_effect=verify_profile_fail):
                strict = run_memory_cycle(
                    repo,
                    out,
                    no_handoff=True,
                    anchor_out=str(anchor_path),
                    anchor_freshness="strict",
                )
                safe = run_memory_cycle(
                    repo,
                    out,
                    no_handoff=True,
                    anchor_out=str(anchor_path),
                    anchor_freshness="safe",
                )
                legacy = run_memory_cycle(
                    repo,
                    out,
                    no_handoff=True,
                    anchor_out=str(anchor_path),
                    anchor_freshness="legacy",
                )

            self.assertEqual(checks, [True, True, False])
            self.assertEqual(strict["summary"]["anchor_freshness"], "strict")
            self.assertEqual(safe["summary"]["anchor_freshness"], "safe")
            self.assertEqual(legacy["summary"]["anchor_freshness"], "legacy")
            self.assertEqual(strict["status"], "fail")
            self.assertEqual(safe["status"], "warn")
            self.assertEqual(legacy["status"], "warn")
            self.assertTrue(any("forced profile failure" in reason for reason in strict["failure_reasons"]))
            self.assertTrue(any("forced profile failure" in reason for reason in safe["failure_reasons"]))
            self.assertTrue(any("forced profile failure" in reason for reason in legacy["failure_reasons"]))

    def test_run_memory_cycle_reports_doctor_missing_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory"

            def doctor_missing_timeline(*_args: object, **_kwargs: object) -> dict[str, object]:
                return {
                    "schema_version": "repomori.doctor.v1",
                    "status": "fail",
                    "error_count": 1,
                    "warning_count": 0,
                    "summary": {},
                    "errors": [{"scope": "latest", "path": str(out / "snapshots.json"), "message": "latest.repomori does not exist."}],
                    "warnings": [],
                }

            with patch.object(codec, "doctor_snapshot_dir", side_effect=doctor_missing_timeline):
                report = run_memory_cycle(
                    repo,
                    out,
                    no_handoff=True,
                )
            self.assertEqual(report["status"], "fail")
            self.assertIn("doctor:", " ".join(report["failure_reasons"]))
            self.assertTrue(any("latest.repomori does not exist" in reason for reason in report["failure_reasons"]))

    def test_append_anchor_log_appends_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "anchor-log.jsonl"
            anchor_payload = {
                "schema_version": "repomori.snapshot_anchor.v1",
                "status": "pass",
                "chain": {
                    "head_chain_hash": "abc",
                    "snapshot_count": 4,
                },
                "summary": {"error_count": 0, "warning_count": 0},
            }

            report = append_anchor_log(anchor_payload, log_path)
            self.assertEqual(report["status"], "appended")
            self.assertEqual(report["log_path"], str(log_path))
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["anchor_schema_version"], "repomori.snapshot_anchor.v1")
            self.assertEqual(row["anchor_status"], "pass")
            self.assertEqual(row["chain_head_hash"], "abc")
            self.assertEqual(row["snapshot_count"], 4)
            self.assertEqual(row["out_dir"], "")

    def test_append_baseline_drift_log_appends_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "drift-log.jsonl"
            first = build_baseline_drift_report(
                {
                    "summary": {
                        "baseline_match_counts": {"strict": 5, "semi_strict": 0, "fallback": 0},
                    },
                    "repo_path": "D:/Temp/demo",
                },
                run_meta={"repo_path": "D:/Temp/demo", "run_ts": 1000, "run_id": "first"},
            )
            second = build_baseline_drift_report(
                {
                    "summary": {
                        "baseline_match_counts": {"strict": 4, "semi_strict": 2, "fallback": 1},
                    },
                    "repo_path": "D:/Temp/demo",
                },
                run_meta={"repo_path": "D:/Temp/demo", "run_ts": 1001, "run_id": "second"},
            )

            first_append = append_baseline_drift_log(first, log_path)
            second_append = append_baseline_drift_log(second, log_path)

            self.assertEqual(first_append["status"], "appended")
            self.assertEqual(second_append["status"], "appended")
            self.assertEqual(first_append["log_path"], str(log_path))
            self.assertEqual(second_append["log_path"], str(log_path))

            rows = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["schema_version"], "repomori.baseline_drift_record.v1")
            self.assertEqual(rows[1]["schema_version"], "repomori.baseline_drift_record.v1")
            self.assertEqual(rows[0]["non_strict_count"], 0)
            self.assertEqual(rows[1]["non_strict_count"], 3)
            self.assertEqual(rows[0]["run_id"], "first")
            self.assertEqual(rows[1]["run_id"], "second")
            self.assertLess(rows[0]["run_ts"], rows[1]["run_ts"])

    def test_summarize_baseline_drift_log_computes_trend_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "drift-summary.jsonl"
            append_baseline_drift_log(
                build_baseline_drift_report(
                    {"summary": {"baseline_match_counts": {"strict": 2, "semi_strict": 0, "fallback": 0}}, "repo_path": "D:/Temp/demo"},
                    run_meta={"repo_path": "D:/Temp/demo", "run_id": "run-1", "run_ts": 1010},
                ),
                log_path,
            )
            append_baseline_drift_log(
                build_baseline_drift_report(
                    {"summary": {"baseline_match_counts": {"strict": 1, "semi_strict": 3, "fallback": 1}}, "repo_path": "D:/Temp/demo"},
                    run_meta={"repo_path": "D:/Temp/demo", "run_id": "run-2", "run_ts": 1011},
                ),
                log_path,
            )

            summary = summarize_baseline_drift_log(log_path, limit=2)
            self.assertEqual(summary["schema_version"], "repomori.baseline_drift_summary.v1")
            self.assertEqual(summary["count"], 2)
            self.assertEqual(summary["warn_count"], 1)
            self.assertEqual(summary["trend"]["semi_strict_delta"], 3)
            self.assertEqual(summary["trend"]["fallback_delta"], 1)
            self.assertEqual(summary["trend"]["non_strict_delta"], 4)
            self.assertAlmostEqual(summary["max_non_strict_ratio"], 0.8)
            self.assertAlmostEqual(summary["avg_non_strict_ratio"], 0.4)

    def test_init_and_load_memory_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"

            result = init_config(repo, out)
            config_path = Path(result["config_path"])
            self.assertEqual(result["schema_version"], "repomori.config.init.v1")
            self.assertTrue(config_path.exists())
            self.assertIn("[profiles.default]", config_path.read_text(encoding="utf-8"))

            loaded = load_memory_config(config_path)
            self.assertEqual(loaded["schema_version"], "repomori.config.v1")
            self.assertEqual(loaded["profile"], "default")
            self.assertEqual(loaded["settings"]["repo"], str(repo.resolve()))
            self.assertEqual(loaded["settings"]["out_dir"], str(out.resolve()))
            self.assertEqual(loaded["settings"]["keep"], 20)
            self.assertTrue(loaded["settings"]["incremental"])
            self.assertFalse(loaded["settings"]["diff_context"])
            self.assertFalse(loaded["settings"]["prune_apply"])
            self.assertIsNone(loaded["settings"]["anchor_out"])
            self.assertFalse(loaded["settings"]["anchor_verify"])
            self.assertFalse(loaded["settings"]["allow_unverified_anchor"])
            self.assertIsNone(loaded["settings"]["anchor_log"])

    def test_load_memory_config_resolves_anchor_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config_path = Path(tmp) / "repomori.toml"
            init_config(
                repo,
                out,
                config_path=config_path,
                keep=2,
                no_handoff=True,
            )
            # Add anchor settings relative to the config file directory.
            config_text = config_path.read_text(encoding="utf-8")
            config_text += (
                '\nanchor_out = "anchor-memory.json"\n'
                "anchor_verify = true\n"
                "allow_unverified_anchor = true\n"
                'anchor_log = "logs/anchor-log.jsonl"\n'
            )
            config_path.write_text(config_text, encoding="utf-8")

            loaded = load_memory_config(config_path)

            self.assertEqual(loaded["settings"]["anchor_out"], str((Path(tmp) / "anchor-memory.json").resolve()))
            self.assertEqual(loaded["settings"]["anchor_log"], str((Path(tmp) / "logs" / "anchor-log.jsonl").resolve()))
            self.assertTrue(loaded["settings"]["anchor_verify"])
            self.assertTrue(loaded["settings"]["allow_unverified_anchor"])

    def test_load_memory_config_supports_named_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            config_path = Path(tmp) / "repomori.toml"
            init_config(
                repo,
                Path(tmp) / "packs",
                config_path=config_path,
                profile="nightly",
                keep=2,
                prune_apply=True,
                no_handoff=True,
            )

            loaded = load_memory_config(config_path, profile="nightly")
            self.assertEqual(loaded["profile"], "nightly")
            self.assertEqual(loaded["settings"]["keep"], 2)
            self.assertTrue(loaded["settings"]["prune_apply"])
            self.assertTrue(loaded["settings"]["no_handoff"])

    def test_agent_bridge_help_query_context_and_file_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(repo, out, config_path=config)
            run_memory_cycle(repo, out, no_handoff=True)

            help_response = handle_agent_request(
                {"id": 1, "method": "agent.help"},
                config_path=config,
            )
            self.assertTrue(help_response["ok"])
            self.assertIn("query.run", help_response["result"]["methods"])

            query_response = handle_agent_request(
                {"id": 2, "method": "query.run", "params": {"text": "sqlite Store", "limit": 1}},
                config_path=config,
            )
            self.assertTrue(query_response["ok"])
            self.assertEqual(query_response["result"]["schema_version"], "repomori.agent.query.v1")
            self.assertEqual(query_response["result"]["results"][0]["path"], "app.py")

            context_response = handle_agent_request(
                {
                    "id": 3,
                    "method": "context.build",
                    "params": {"question": "How does Store connect?", "max_files": 1, "max_bytes": 200},
                },
                config_path=config,
            )
            self.assertTrue(context_response["ok"])
            self.assertEqual(context_response["result"]["schema_version"], "repomori.context.v1")
            self.assertEqual(context_response["result"]["sources"][0]["path"], "app.py")

            inspect_response = handle_agent_request(
                {"id": "inspect", "method": "inspect.build", "params": {"max_files": 2, "verify": True}},
                config_path=config,
            )
            self.assertTrue(inspect_response["ok"])
            self.assertEqual(inspect_response["result"]["schema_version"], "repomori.inspect.v1")
            self.assertEqual(inspect_response["result"]["verification"]["status"], "pass")

            file_response = handle_agent_request(
                {"id": 4, "method": "file.get", "params": {"path": "app.py"}},
                config_path=config,
            )
            self.assertTrue(file_response["ok"])
            self.assertEqual(file_response["result"]["schema_version"], "repomori.agent.file.v1")
            self.assertTrue(file_response["result"]["is_text"])
            self.assertIn("sqlite3.connect", file_response["result"]["text"])

    def test_agent_bridge_memory_doctor_timeline_capsule_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(repo, out, config_path=config, no_handoff=True)

            memory_response = handle_agent_request(
                {"id": "memory", "method": "memory.run", "params": {"keep": 1}},
                config_path=config,
            )
            self.assertTrue(memory_response["ok"])
            self.assertEqual(memory_response["result"]["schema_version"], "repomori.memory.v1")
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            second_memory = handle_agent_request(
                {"id": "memory2", "method": "memory.run", "params": {"keep": 2}},
                config_path=config,
            )
            self.assertTrue(second_memory["ok"])

            doctor_response = handle_agent_request({"id": "doctor", "method": "doctor.run"}, config_path=config)
            self.assertTrue(doctor_response["ok"])
            self.assertEqual(doctor_response["result"]["schema_version"], "repomori.doctor.v1")

            timeline_response = handle_agent_request(
                {"id": "timeline", "method": "timeline.read", "params": {"limit": 1}},
                config_path=config,
            )
            self.assertTrue(timeline_response["ok"])
            self.assertEqual(timeline_response["result"]["schema_version"], "repomori.timeline.v1")
            self.assertEqual(timeline_response["result"]["summary"]["chain_status"], "pass")

            chain_response = handle_agent_request({"id": "chain", "method": "chain.verify"}, config_path=config)
            self.assertTrue(chain_response["ok"])
            self.assertEqual(chain_response["result"]["schema_version"], "repomori.snapshot_chain.v1")
            self.assertEqual(chain_response["result"]["status"], "pass")

            anchor_response = handle_agent_request({"id": "anchor", "method": "anchor.build"}, config_path=config)
            self.assertTrue(anchor_response["ok"])
            self.assertEqual(anchor_response["result"]["schema_version"], "repomori.snapshot_anchor.v1")
            self.assertEqual(anchor_response["result"]["status"], "pass")
            self.assertEqual(
                anchor_response["result"]["chain"]["head_chain_hash"],
                chain_response["result"]["summary"]["head_chain_hash"],
            )
            anchor_file = Path(tmp) / "agent-anchor.json"
            anchor_file.write_text(json.dumps(anchor_response["result"], indent=2), encoding="utf-8")
            anchor_verify_response = handle_agent_request(
                {"id": "anchor-verify", "method": "anchor.verify", "params": {"anchor": str(anchor_file)}},
                config_path=config,
            )
            self.assertTrue(anchor_verify_response["ok"])
            self.assertEqual(anchor_verify_response["result"]["schema_version"], "repomori.snapshot_anchor.verify.v1")
            self.assertEqual(anchor_verify_response["result"]["status"], "pass")

            stats_response = handle_agent_request(
                {"id": "stats", "method": "stats.read", "params": {"limit": 1}},
                config_path=config,
            )
            self.assertTrue(stats_response["ok"])
            self.assertEqual(stats_response["result"]["schema_version"], "repomori.stats.v1")

            diff_response = handle_agent_request(
                {"id": "diff", "method": "diff_context.build", "params": {"question": "added", "max_files": 2}},
                config_path=config,
            )
            self.assertTrue(diff_response["ok"])
            self.assertEqual(diff_response["result"]["schema_version"], "repomori.diff_context.v1")
            self.assertEqual(diff_response["result"]["summary"]["added_count"], 1)

            capsule_response = handle_agent_request(
                {"id": "capsule", "method": "capsule.build", "params": {"max_files": 1}},
                config_path=config,
            )
            self.assertTrue(capsule_response["ok"])
            self.assertEqual(capsule_response["result"]["schema_version"], "repomori.capsule.v1")

            bad_response = handle_agent_request({"id": "bad", "method": "missing.method"}, config_path=config)
            self.assertFalse(bad_response["ok"])
            self.assertEqual(bad_response["error"]["code"], "method_not_found")

            invalid_response = handle_agent_request(["not", "an", "object"])  # type: ignore[arg-type]
            self.assertFalse(invalid_response["ok"])
            self.assertEqual(invalid_response["error"]["code"], "invalid_request")

    def test_mcp_bridge_initialize_tools_and_errors(self) -> None:
        init_response = handle_mcp_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        self.assertEqual(init_response["jsonrpc"], "2.0")
        self.assertEqual(init_response["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("tools", init_response["result"]["capabilities"])

        initialized = handle_mcp_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(initialized)

        first_list = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        second_list = handle_mcp_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual(first_list["result"]["schema_version"], "repomori.mcp.tools.v1")
        first_names = [tool["name"] for tool in first_list["result"]["tools"]]
        second_names = [tool["name"] for tool in second_list["result"]["tools"]]
        self.assertEqual(first_names, second_names)
        self.assertIn("repomori_context_build", first_names)
        self.assertIn("repomori_brief_build", first_names)
        self.assertIn("repomori_chain_verify", first_names)
        self.assertIn("repomori_anchor_build", first_names)
        self.assertIn("repomori_anchor_verify", first_names)
        self.assertIn("repomori_diff_context_build", first_names)
        self.assertIn("repomori_pack_inspect", first_names)
        self.assertIn("repomori_stats_read", first_names)
        self.assertIn("repomori_schema_list", first_names)
        memory_tool = next(tool for tool in first_list["result"]["tools"] if tool["name"] == "repomori_memory_run")
        self.assertIn("incremental", memory_tool["inputSchema"]["properties"])
        self.assertIn("diff_context", memory_tool["inputSchema"]["properties"])
        self.assertIn("anchor_out", memory_tool["inputSchema"]["properties"])
        self.assertIn("anchor_verify", memory_tool["inputSchema"]["properties"])
        self.assertIn("allow_unverified_anchor", memory_tool["inputSchema"]["properties"])
        self.assertIn("anchor_log", memory_tool["inputSchema"]["properties"])

        unknown_method = handle_mcp_request({"jsonrpc": "2.0", "id": 4, "method": "missing.method"})
        self.assertEqual(unknown_method["error"]["code"], -32601)

        unknown_tool = handle_mcp_request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "repomori_missing", "arguments": {}},
            }
        )
        self.assertEqual(unknown_tool["error"]["code"], -32602)

        tool_failure = handle_mcp_request(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "repomori_context_build", "arguments": {}},
            }
        )
        self.assertTrue(tool_failure["result"]["isError"])
        self.assertEqual(tool_failure["result"]["structuredContent"]["schema_version"], "repomori.mcp.tool_error.v1")

        input_stream = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
        output_stream = io.StringIO()
        status = run_mcp_bridge(input_stream, output_stream)
        self.assertEqual(status, 0)
        ping = json.loads(output_stream.getvalue())
        self.assertEqual(ping["result"], {})

    def test_mcp_tools_wrap_agent_query_context_doctor_timeline_schema_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(repo, out, config_path=config, no_handoff=True)
            run_memory_cycle(repo, out, no_handoff=True)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            run_memory_cycle(repo, out, no_handoff=True)

            query_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "query",
                    "method": "tools/call",
                    "params": {
                        "name": "repomori_query_run",
                        "arguments": {"text": "sqlite Store", "limit": 1},
                    },
                },
                config_path=config,
            )
            self.assertFalse(query_response["result"]["isError"])
            self.assertEqual(query_response["result"]["structuredContent"]["schema_version"], "repomori.agent.query.v1")
            self.assertEqual(query_response["result"]["structuredContent"]["results"][0]["path"], "app.py")
            self.assertIn("app.py", query_response["result"]["content"][0]["text"])

            inspect_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "inspect",
                    "method": "tools/call",
                    "params": {
                        "name": "repomori_pack_inspect",
                        "arguments": {"max_files": 2, "verify": True},
                    },
                },
                config_path=config,
            )
            self.assertFalse(inspect_response["result"]["isError"])
            self.assertEqual(inspect_response["result"]["structuredContent"]["schema_version"], "repomori.inspect.v1")
            self.assertEqual(inspect_response["result"]["structuredContent"]["verification"]["status"], "pass")
            self.assertIn("files:", inspect_response["result"]["content"][0]["text"])

            context_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "context",
                    "method": "tools/call",
                    "params": {
                        "name": "repomori_context_build",
                        "arguments": {"question": "How does Store connect?", "max_files": 1, "max_bytes": 200},
                    },
                },
                config_path=config,
            )
            self.assertFalse(context_response["result"]["isError"])
            self.assertEqual(context_response["result"]["structuredContent"]["schema_version"], "repomori.context.v1")
            self.assertEqual(context_response["result"]["structuredContent"]["sources"][0]["path"], "app.py")

            diff_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "diff",
                    "method": "tools/call",
                    "params": {
                        "name": "repomori_diff_context_build",
                        "arguments": {"question": "added", "max_files": 2},
                    },
                },
                config_path=config,
            )
            self.assertFalse(diff_response["result"]["isError"])
            self.assertEqual(diff_response["result"]["structuredContent"]["schema_version"], "repomori.diff_context.v1")
            self.assertEqual(diff_response["result"]["structuredContent"]["summary"]["added_count"], 1)
            self.assertIn("added", diff_response["result"]["content"][0]["text"])

            doctor_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "doctor",
                    "method": "tools/call",
                    "params": {"name": "repomori_doctor_run", "arguments": {}},
                },
                config_path=config,
            )
            self.assertEqual(doctor_response["result"]["structuredContent"]["schema_version"], "repomori.doctor.v1")
            self.assertEqual(doctor_response["result"]["structuredContent"]["status"], "pass")

            timeline_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "timeline",
                    "method": "tools/call",
                    "params": {"name": "repomori_timeline_read", "arguments": {"limit": 1}},
                },
                config_path=config,
            )
            self.assertEqual(timeline_response["result"]["structuredContent"]["schema_version"], "repomori.timeline.v1")
            self.assertEqual(timeline_response["result"]["structuredContent"]["returned_count"], 1)

            stats_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "stats",
                    "method": "tools/call",
                    "params": {"name": "repomori_stats_read", "arguments": {"limit": 1}},
                },
                config_path=config,
            )
            self.assertEqual(stats_response["result"]["structuredContent"]["schema_version"], "repomori.stats.v1")
            self.assertEqual(stats_response["result"]["structuredContent"]["returned_count"], 1)

            brief_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "brief",
                    "method": "tools/call",
                    "params": {"name": "repomori_brief_build", "arguments": {"timeline_limit": 2}},
                },
                config_path=config,
            )
            self.assertEqual(brief_response["result"]["structuredContent"]["schema_version"], "repomori.agent_brief.v1")
            self.assertEqual(brief_response["result"]["structuredContent"]["summary"]["snapshot_count"], 2)
            self.assertIn("latest_pack", brief_response["result"]["content"][0]["text"])

            chain_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "chain",
                    "method": "tools/call",
                    "params": {"name": "repomori_chain_verify", "arguments": {}},
                },
                config_path=config,
            )
            self.assertEqual(chain_response["result"]["structuredContent"]["schema_version"], "repomori.snapshot_chain.v1")
            self.assertEqual(chain_response["result"]["structuredContent"]["status"], "pass")
            self.assertIn("checked", chain_response["result"]["content"][0]["text"])

            anchor_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "anchor",
                    "method": "tools/call",
                    "params": {"name": "repomori_anchor_build", "arguments": {}},
                },
                config_path=config,
            )
            self.assertEqual(anchor_response["result"]["structuredContent"]["schema_version"], "repomori.snapshot_anchor.v1")
            self.assertEqual(anchor_response["result"]["structuredContent"]["status"], "pass")
            self.assertIn("anchor_hash", anchor_response["result"]["content"][0]["text"])

            anchor_file = Path(tmp) / "mcp-anchor.json"
            anchor_file.write_text(json.dumps(anchor_response["result"]["structuredContent"], indent=2), encoding="utf-8")
            anchor_verify_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "anchor-verify",
                    "method": "tools/call",
                    "params": {"name": "repomori_anchor_verify", "arguments": {"anchor": str(anchor_file)}},
                },
                config_path=config,
            )
            self.assertEqual(anchor_verify_response["result"]["structuredContent"]["schema_version"], "repomori.snapshot_anchor.verify.v1")
            self.assertEqual(anchor_verify_response["result"]["structuredContent"]["status"], "pass")
            self.assertIn("anchor_hash_valid", anchor_verify_response["result"]["content"][0]["text"])

            schema_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "schema",
                    "method": "tools/call",
                    "params": {"name": "repomori_schema_list", "arguments": {}},
                },
                config_path=config,
            )
            self.assertEqual(schema_response["result"]["structuredContent"]["schema_version"], "repomori.schema.catalog.v1")
            self.assertIn("repomori_context_build", schema_response["result"]["structuredContent"]["mcp_tools"])

            file_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "file",
                    "method": "tools/call",
                    "params": {"name": "repomori_file_get", "arguments": {"path": "app.py"}},
                },
                config_path=config,
            )
            self.assertEqual(file_response["result"]["structuredContent"]["schema_version"], "repomori.agent.file.v1")
            self.assertTrue(file_response["result"]["structuredContent"]["is_text"])
            self.assertIn("sqlite3.connect", file_response["result"]["structuredContent"]["text"])

    def test_schema_catalog_lists_contracts_and_methods(self) -> None:
        catalog = schema_catalog()
        self.assertEqual(catalog["schema_version"], "repomori.schema.catalog.v1")
        schema_versions = {item["schema_version"] for item in catalog["schemas"]}
        self.assertIn("repomori.memory.v1", schema_versions)
        self.assertIn("repomori.scan.v1", schema_versions)
        self.assertIn("repomori.scan.baseline.v1", schema_versions)
        self.assertIn("repomori.release_check.v1", schema_versions)
        self.assertIn("repomori.health.v1", schema_versions)
        self.assertIn("repomori.agent.response.v1", schema_versions)
        self.assertIn("repomori.agent_brief.v1", schema_versions)
        self.assertIn("repomori.brief.v1", schema_versions)
        self.assertIn("repomori.inspect.v1", schema_versions)
        self.assertIn("repomori.snapshot_chain.v1", schema_versions)
        self.assertIn("repomori.snapshot_anchor.v1", schema_versions)
        self.assertIn("repomori.snapshot_anchor.verify.v1", schema_versions)
        self.assertIn("repomori.stats.v1", schema_versions)
        self.assertIn("repomori.diff_context.v1", schema_versions)
        self.assertIn("anchor.build", catalog["agent_methods"])
        self.assertIn("anchor.verify", catalog["agent_methods"])
        self.assertIn("brief.build", catalog["agent_methods"])
        self.assertIn("chain.verify", catalog["agent_methods"])
        self.assertIn("context.build", catalog["agent_methods"])
        self.assertIn("diff_context.build", catalog["agent_methods"])
        self.assertIn("inspect.build", catalog["agent_methods"])
        self.assertIn("stats.read", catalog["agent_methods"])
        self.assertIn("schema.list", catalog["agent_methods"])
        self.assertIn("repomori_anchor_build", catalog["mcp_tools"])
        self.assertIn("repomori_anchor_verify", catalog["mcp_tools"])
        self.assertIn("repomori_brief_build", catalog["mcp_tools"])
        self.assertIn("repomori_chain_verify", catalog["mcp_tools"])
        self.assertIn("repomori_diff_context_build", catalog["mcp_tools"])
        self.assertIn("repomori_pack_inspect", catalog["mcp_tools"])
        self.assertIn("repomori_stats_read", catalog["mcp_tools"])
        self.assertIn("repomori_schema_list", catalog["mcp_tools"])

        memory = schema_catalog("repomori.memory.v1")
        self.assertEqual(memory["selected"], "repomori.memory.v1")
        self.assertEqual(memory["schema"]["producer"], "run_memory_cycle")
        self.assertIn("timeline", memory["schema"]["required_fields"])

        stats = schema_catalog("repomori.stats.v1")
        self.assertEqual(stats["selected"], "repomori.stats.v1")
        self.assertEqual(stats["schema"]["producer"], "read_snapshot_stats")

        diff_context = schema_catalog("repomori.diff_context.v1")
        self.assertEqual(diff_context["selected"], "repomori.diff_context.v1")
        self.assertEqual(diff_context["schema"]["producer"], "build_diff_context_bundle")

        inspect_schema = schema_catalog("repomori.inspect.v1")
        self.assertEqual(inspect_schema["selected"], "repomori.inspect.v1")
        self.assertEqual(inspect_schema["schema"]["producer"], "inspect_pack")

        agent_brief = schema_catalog("repomori.agent_brief.v1")
        self.assertEqual(agent_brief["selected"], "repomori.agent_brief.v1")
        self.assertEqual(agent_brief["schema"]["producer"], "build_agent_brief")

        chain = schema_catalog("repomori.snapshot_chain.v1")
        self.assertEqual(chain["selected"], "repomori.snapshot_chain.v1")
        self.assertEqual(chain["schema"]["producer"], "verify_snapshot_chain")

        anchor = schema_catalog("repomori.snapshot_anchor.v1")
        self.assertEqual(anchor["selected"], "repomori.snapshot_anchor.v1")
        self.assertEqual(anchor["schema"]["producer"], "build_snapshot_anchor")

        anchor_verify = schema_catalog("repomori.snapshot_anchor.verify.v1")
        self.assertEqual(anchor_verify["selected"], "repomori.snapshot_anchor.verify.v1")
        self.assertEqual(anchor_verify["schema"]["producer"], "verify_snapshot_anchor")

    def test_golden_fixture_core_output_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, pack = self._demo_pack(root, build=True)
            handoff_dir = root / "handoff"
            memory_dir = root / "memory"

            context = build_context_bundle(pack, "sqlite Store", limit=1, max_bytes=200)
            inspect_report = inspect_pack(pack, max_files=2, top_terms=5, top_symbols=5)
            capsule = build_capsule(pack, max_files=2)
            handoff = build_handoff_package(pack, "sqlite Store", handoff_dir)
            memory = run_memory_cycle(repo, memory_dir, no_handoff=True)
            diff_context = build_diff_context_bundle(pack, memory["summary"]["pack_path"])
            agent_brief = build_agent_brief(memory_dir)
            chain = verify_snapshot_chain(memory_dir)
            anchor = build_snapshot_anchor(memory_dir)
            anchor_verify = verify_snapshot_anchor(anchor, memory_dir)
            stats = read_snapshot_stats(memory_dir)
            scan = scan_repository(repo)
            agent_help = handle_agent_request({"id": 1, "method": "agent.help"})

            golden_shapes = {
                "context": (
                    context,
                    "repomori.context.v1",
                    {"schema_version", "question", "pack", "selection", "sources", "source_manifest"},
                ),
                "capsule": (
                    capsule,
                    "repomori.capsule.v1",
                    {"schema_version", "key", "pack", "selection", "files", "dictionary", "manifest"},
                ),
                "inspect": (
                    inspect_report,
                    "repomori.inspect.v1",
                    {"schema_version", "status", "pack", "summary", "storage", "files", "vocabulary"},
                ),
                "handoff": (
                    handoff,
                    "repomori.handoff.v1",
                    {"schema_version", "status", "question", "out_dir", "artifacts", "verification"},
                ),
                "diff_context": (
                    diff_context,
                    "repomori.diff_context.v1",
                    {"schema_version", "question", "base_pack", "target_pack", "summary", "selection", "sources", "source_manifest"},
                ),
                "memory": (
                    memory,
                    "repomori.memory.v1",
                    {"schema_version", "status", "repo_path", "out_dir", "settings", "summary", "snapshot", "doctor", "prune", "timeline"},
                ),
                "agent_brief": (
                    agent_brief,
                    "repomori.agent_brief.v1",
                    {"schema_version", "status", "out_dir", "summary", "latest_snapshot", "artifacts", "recommended_commands"},
                ),
                "chain": (
                    chain,
                    "repomori.snapshot_chain.v1",
                    {"schema_version", "status", "out_dir", "summary", "errors", "warnings"},
                ),
                "anchor": (
                    anchor,
                    "repomori.snapshot_anchor.v1",
                    {"schema_version", "status", "out_dir", "created_at", "chain", "latest_snapshot", "verification", "anchor_hash"},
                ),
                "anchor_verify": (
                    anchor_verify,
                    "repomori.snapshot_anchor.verify.v1",
                    {"schema_version", "status", "anchor_path", "out_dir", "summary", "errors", "warnings"},
                ),
                "stats": (
                    stats,
                    "repomori.stats.v1",
                    {"schema_version", "out_dir", "snapshot_count", "returned_count", "summary", "latest", "snapshots", "top_reuse"},
                ),
                "scan": (
                    scan,
                    "repomori.scan.v1",
                    {"schema_version", "status", "repo_path", "settings", "summary", "findings"},
                ),
                "agent_help": (
                    agent_help["result"],
                    "repomori.agent.help.v1",
                    {"schema_version", "protocol", "request", "response", "methods"},
                ),
            }
            for _name, (payload, schema_version, required_keys) in golden_shapes.items():
                self.assertEqual(payload["schema_version"], schema_version)
                self.assertTrue(required_keys.issubset(payload.keys()))

            self.assertEqual(context["sources"][0]["path"], "app.py")
            self.assertTrue(capsule["files"])
            self.assertEqual(handoff["status"], "complete")
            self.assertEqual(diff_context["summary"]["changed_count"], 0)
            self.assertEqual(memory["status"], "pass")
            self.assertEqual(stats["snapshot_count"], 1)
            self.assertEqual(scan["status"], "warn")
            self.assertIn("memory.run", agent_help["result"]["methods"])

    def test_cli_inspect_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "inspect",
                    str(pack),
                    "--json",
                    "--max-files",
                    "2",
                    "--top-terms",
                    "5",
                    "--verify",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.inspect.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"]["file_count"], 3)
            self.assertEqual(payload["verification"]["status"], "pass")
            self.assertTrue(payload["source_manifest"])

    def test_cli_context_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "context",
                    str(pack),
                    "sqlite Store",
                    "--format",
                    "json",
                    "--max-files",
                    "1",
                    "--max-bytes",
                    "40",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["question"], "sqlite Store")
            self.assertEqual(payload["sources"][0]["path"], "app.py")
            self.assertEqual(payload["selection"]["limit"], 1)
            self.assertLessEqual(payload["selection"]["source_bytes"], 40)
            self.assertIn("source_manifest", payload)

    def test_cli_diff_context_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, base_pack = self._demo_pack(root)
            build_pack(repo, base_pack, BuildOptions(force=True))
            (repo / "app.py").write_text(
                "import sqlite3\n\n"
                "class Store:\n"
                "    def connect(self):\n"
                "        return sqlite3.connect(':memory:')\n"
                "    def close(self):\n"
                "        return None\n",
                encoding="utf-8",
            )
            target_pack = root / "target.repomori"
            build_pack(repo, target_pack, BuildOptions(force=True, base_pack=base_pack))

            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "diff-context",
                    str(base_pack),
                    str(target_pack),
                    "close Store",
                    "--format",
                    "json",
                    "--max-files",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.diff_context.v1")
            self.assertEqual(payload["summary"]["changed_count"], 1)
            self.assertEqual(payload["sources"][0]["path"], "app.py")
            self.assertIn("source_manifest", payload)

    def test_cli_build_base_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, base_pack = self._demo_pack(root)
            build_pack(repo, base_pack, BuildOptions(force=True))
            (repo / "README.md").write_text("# Demo\n\nStorage engine notes unchanged.\n", encoding="utf-8")
            target_pack = root / "target.repomori"

            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "build",
                    str(repo),
                    str(target_pack),
                    "--base",
                    str(base_pack),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.pack.v1")
            self.assertTrue(payload["incremental"])
            self.assertEqual(payload["reused_file_count"], 2)
            self.assertEqual(payload["rebuilt_file_count"], 1)
            self.assertTrue(verify_pack(target_pack)["verified"])

    def test_cli_diagnose_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "diagnose",
                    str(pack),
                    "sqlite Store",
                    "--json",
                    "--max-files",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.diagnose.v1")
            self.assertEqual(payload["selected_files"][0]["path"], "app.py")
            self.assertTrue(payload["selected_files"][0]["score_breakdown"])
            self.assertIn("snippet_anchors", payload["selected_files"][0])

    def test_cli_compare_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base_pack = self._demo_pack(Path(tmp))
            build_pack(repo, base_pack, BuildOptions(force=True))
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            target_pack = Path(tmp) / "target.repomori"
            build_pack(repo, target_pack, BuildOptions(force=True))
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "compare",
                    str(base_pack),
                    str(target_pack),
                    "--format",
                    "json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.compare.v1")
            self.assertEqual(payload["summary"]["added_count"], 1)
            self.assertEqual(payload["files"]["added"][0]["path"], "new.py")

    def test_cli_brief_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "brief",
                    str(pack),
                    "--format",
                    "json",
                    "--max-files",
                    "2",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.brief.v1")
            self.assertEqual(payload["summary"]["file_count"], 3)
            self.assertLessEqual(len(payload["orientation"]["key_files"]), 2)

    def test_cli_brief_snapshot_dir_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            run_memory_cycle(repo, out, no_handoff=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "brief",
                    str(out),
                    "--format",
                    "json",
                    "--timeline-limit",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.agent_brief.v1")
            self.assertEqual(payload["summary"]["snapshot_count"], 1)
            self.assertEqual(payload["doctor"]["status"], "pass")
            self.assertIn("latest_pack", {item["kind"] for item in payload["artifacts"]})

    def test_cli_verify_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "verify",
                    str(pack),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertTrue(payload["verified"])
            self.assertEqual(payload["error_count"], 0)

    def test_cli_eval_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "eval",
                    str(pack),
                    "--question",
                    "sqlite Store",
                    "--format",
                    "json",
                    "--max-files",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.eval.v1")
            self.assertEqual(payload["summary"]["question_count"], 1)
            self.assertEqual(payload["questions"][0]["selected_sources"][0]["path"], "app.py")

    def test_cli_capsule_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "capsule",
                    str(pack),
                    "--max-files",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.capsule.v1")
            self.assertEqual(payload["selection"]["included_files"], 1)
            self.assertIn("key", payload)

    def test_cli_handoff_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff-cli"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "handoff",
                    str(pack),
                    "sqlite Store",
                    "--out",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.handoff.v1")
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["question"], "sqlite Store")
            self.assertTrue((out / "manifest.json").exists())
            self.assertTrue((out / "context.md").exists())

    def test_cli_handoff_base_pack_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base_pack = self._demo_pack(Path(tmp))
            build_pack(repo, base_pack, BuildOptions(force=True))
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            target_pack = Path(tmp) / "target.repomori"
            build_pack(repo, target_pack, BuildOptions(force=True))
            out = Path(tmp) / "handoff-base-cli"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "handoff",
                    str(target_pack),
                    "sqlite Store",
                    "--base-pack",
                    str(base_pack),
                    "--out",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.handoff.v1")
            self.assertIn("base_pack", payload)
            self.assertTrue((out / "compare.json").exists())
            compare = json.loads((out / "compare.json").read_text(encoding="utf-8"))
            self.assertEqual(compare["summary"]["added_count"], 1)

    def test_cli_check_handoff_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff-check-cli"
            build_handoff_package(pack, "sqlite Store", out)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "check-handoff",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.handoff.check.v1")
            self.assertTrue(payload["valid"])
            self.assertEqual(payload["error_count"], 0)

    def test_cli_bench_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "bench-cli"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "bench",
                    str(repo),
                    "--out",
                    str(out),
                    "--question",
                    "sqlite Store",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.bench.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertTrue((out / "bench.json").exists())
            self.assertTrue((out / "handoff" / "manifest.json").exists())

    def test_cli_demo_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo-cli"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "demo",
                    "--out",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.demo.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"]["query_top_path"], "app.py")
            self.assertTrue((out / "demo.json").exists())
            self.assertTrue((out / "demo-repo" / "app.py").exists())
            self.assertTrue((out / "packs" / "latest.repomori").exists())

    def test_cli_snapshot_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshot-cli"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "snapshot",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.snapshot.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(payload["settings"]["incremental"])
            self.assertFalse(payload["build"]["incremental"])
            self.assertTrue((out / "latest.repomori").exists())
            self.assertTrue((out / payload["artifacts"]["snapshot_json"]).exists())
            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["schema_version"], "repomori.snapshots.v1")
            self.assertEqual(index["snapshot_count"], 1)

    def test_cli_snapshot_no_incremental_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshot-no-incremental-cli"
            subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "snapshot",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "snapshot",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--no-incremental",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.snapshot.v1")
            self.assertFalse(payload["settings"]["incremental"])
            self.assertFalse(payload["build"]["incremental"])
            self.assertEqual(payload["summary"]["reused_file_count"], 0)

    def test_cli_snapshot_handoff_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "snapshot-handoff-cli"
            subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "snapshot",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "snapshot",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--handoff",
                    "sqlite Store",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.snapshot.v1")
            self.assertTrue(payload["summary"]["handoff_passed"])
            self.assertTrue((Path(payload["summary"]["handoff_dir"]) / "compare.json").exists())

    def test_cli_timeline_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "timeline-cli"
            snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "timeline",
                    str(out),
                    "--format",
                    "json",
                    "--limit",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.timeline.v1")
            self.assertEqual(payload["snapshot_count"], 2)
            self.assertEqual(payload["returned_count"], 1)
            self.assertEqual(payload["summary"]["chain_status"], "pass")

    def test_cli_timeline_out_fail_shows_stderr_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing-snapshots"
            timeline_md = Path(tmp) / "timeline.md"
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "timeline",
                    str(missing),
                    "--format",
                    "markdown",
                    "--out",
                    str(timeline_md),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("timeline:", process.stderr)
            self.assertTrue(timeline_md.exists())
            self.assertIn("snapshot directory does not exist", process.stderr.lower())

    def test_cli_chain_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "chain-cli"
            snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "chain",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.snapshot_chain.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"]["checked_count"], 2)
            markdown = format_snapshot_chain_markdown(payload)
            self.assertIn("Head hash", markdown)

    def test_cli_chain_out_fail_shows_stderr_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing-snapshots"
            chain_md = Path(tmp) / "chain.md"
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "chain",
                    str(missing),
                    "--format",
                    "markdown",
                    "--out",
                    str(chain_md),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("chain:", process.stderr)
            self.assertTrue(chain_md.exists())
            self.assertIn("snapshot directory does not exist", process.stderr.lower())

    def test_cli_anchor_json_and_markdown_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "anchor-cli"
            anchor_md = Path(tmp) / "anchor.md"
            snapshot_repo(repo, out)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "anchor",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.snapshot_anchor.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(payload["anchor_hash"])
            self.assertEqual(payload["latest_snapshot"]["chain_hash"], payload["chain"]["head_chain_hash"])

            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "anchor",
                    str(out),
                    "--format",
                    "markdown",
                    "--out",
                    str(anchor_md),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            self.assertIn("# RepoMori Snapshot Anchor", anchor_md.read_text(encoding="utf-8"))

    def test_cli_anchor_out_and_fail_writes_hint_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "missing-snapshots"
            anchor_md = Path(tmp) / "anchor.md"
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "anchor",
                    str(out),
                    "--format",
                    "markdown",
                    "--out",
                    str(anchor_md),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("anchor:", process.stderr)
            self.assertTrue(anchor_md.exists())
            self.assertIn("snapshot directory does not exist", process.stderr.lower())

    def test_cli_verify_anchor_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "verify-anchor-cli"
            anchor_path = Path(tmp) / "anchor.json"
            snapshot_repo(repo, out)
            anchor_path.write_text(json.dumps(build_snapshot_anchor(out), indent=2), encoding="utf-8")
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "verify-anchor",
                    str(anchor_path),
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.snapshot_anchor.verify.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(payload["summary"]["anchor_hash_valid"])
            self.assertTrue(payload["summary"]["chain_head_matches"])

    def test_cli_verify_anchor_out_fail_shows_stderr_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = self._demo_pack(Path(tmp))
            out = Path(tmp) / "verify-anchor-base"
            missing_out = out / "does-not-exist"
            anchor_file = Path(tmp) / "anchor.json"
            snapshot_repo(repo, out)
            anchor_file.write_text(json.dumps(build_snapshot_anchor(out), indent=2), encoding="utf-8")

            anchor_verify_output = Path(tmp) / "verify-anchor.md"
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "verify-anchor",
                    str(anchor_file),
                    str(missing_out),
                    "--format",
                    "markdown",
                    "--out",
                    str(anchor_verify_output),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("verify-anchor:", process.stderr)
            self.assertIn("anchor chain head does not match current snapshot timeline head", process.stderr.lower())

    def test_cli_stats_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "stats-cli"
            snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "stats",
                    str(out),
                    "--format",
                    "json",
                    "--limit",
                    "1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.stats.v1")
            self.assertEqual(payload["snapshot_count"], 2)
            self.assertEqual(payload["returned_count"], 1)
            self.assertEqual(payload["summary"]["incremental_snapshot_count"], 1)
            self.assertEqual(payload["summary"]["total_reused_files"], 3)

    def test_cli_doctor_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "doctor-cli"
            snapshot_repo(repo, out)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "doctor",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.doctor.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["error_count"], 0)

    def test_cli_doctor_out_fail_shows_stderr_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "doctor-missing"
            report = Path(tmp) / "doctor.md"
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "doctor",
                    str(out),
                    "--out",
                    str(report),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("doctor:", process.stderr)
            self.assertTrue(report.exists())
            self.assertIn("snapshot directory does not exist", process.stderr.lower())

    def test_cli_prune_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "prune-cli"
            first = snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out)

            dry_run_output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "prune",
                    str(out),
                    "--keep",
                    "1",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            dry_run = json.loads(dry_run_output)
            self.assertEqual(dry_run["schema_version"], "repomori.prune.v1")
            self.assertFalse(dry_run["applied"])
            self.assertEqual(len(dry_run["candidates"]), 1)
            self.assertTrue(Path(first["summary"]["pack_path"]).exists())

            apply_output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "prune",
                    str(out),
                    "--keep",
                    "1",
                    "--apply",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            applied = json.loads(apply_output)
            self.assertTrue(applied["applied"])
            self.assertFalse(applied["errors"])
            self.assertFalse(Path(first["summary"]["pack_path"]).exists())

    def test_cli_memory_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-cli"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertTrue((Path(payload["summary"]["handoff_dir"]) / "manifest.json").exists())
            self.assertEqual(payload["timeline"]["returned_count"], 1)

    def test_cli_memory_anchor_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-anchor-cli"
            anchor = Path(tmp) / "memory-anchor.json"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--no-handoff",
                    "--anchor-out",
                    str(anchor),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"]["anchor_path"], str(anchor.resolve()))
            self.assertTrue(Path(payload["summary"]["anchor_path"]).exists())
            self.assertEqual(payload["anchor"]["schema_version"], "repomori.snapshot_anchor.v1")

    def test_cli_memory_anchor_freshness_profiles_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-anchor-profile-cli"
            anchor = Path(tmp) / "memory-anchor.json"

            for mode in ("strict", "safe", "legacy"):
                payload = json.loads(
                    subprocess.check_output(
                        [
                            sys.executable,
                            "-m",
                            "repomori",
                            "memory",
                            str(repo),
                            "--out-dir",
                            str(out),
                            "--no-handoff",
                            "--anchor-out",
                            str(anchor),
                            "--anchor-freshness",
                            mode,
                            "--json",
                        ],
                        cwd=Path(__file__).resolve().parents[1],
                        text=True,
                    )
                )

            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"]["anchor_freshness"], mode)
            self.assertIsNotNone(payload["anchor_verification"])
            self.assertEqual(payload["anchor_verification"]["status"], "pass")

    def test_cli_memory_anchor_freshness_profiles_map_to_cycle_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-anchor-profile-cli-flags"
            anchor = Path(tmp) / "memory-anchor.json"
            calls: list[tuple[str, str, dict[str, object]]] = []

            def fake_run_memory_cycle(
                repo_path,
                out_dir,
                **kwargs: object,
            ) -> dict[str, object]:
                normalized: dict[str, object] = dict(kwargs)
                freshness = normalized.get("anchor_freshness")
                anchor_verify = bool(normalized.get("anchor_verify", False))
                allow_unverified_anchor = bool(normalized.get("allow_unverified_anchor", False))
                if isinstance(freshness, str):
                    if freshness == "strict":
                        anchor_verify = True
                        allow_unverified_anchor = False
                    elif freshness in {"safe", "legacy"}:
                        anchor_verify = True
                        allow_unverified_anchor = True
                normalized["anchor_verify"] = anchor_verify
                normalized["allow_unverified_anchor"] = allow_unverified_anchor
                calls.append((str(Path(repo_path).resolve()), str(Path(out_dir).resolve()), dict(normalized)))

                return {
                    "schema_version": "repomori.memory.v1",
                    "status": "pass",
                    "repo_path": str(Path(repo_path).resolve()),
                    "out_dir": str(Path(out_dir).resolve()),
                    "summary": {
                        "anchor_freshness": kwargs.get("anchor_freshness"),
                        "anchor_verification_status": "pass",
                        "pack_path": str(Path(out_dir).resolve() / "pass.repomori"),
                        "handoff_dir": None,
                    },
                    "settings": {
                        "anchor_out": kwargs.get("anchor_out"),
                        "anchor_verify": kwargs.get("anchor_verify"),
                        "allow_unverified_anchor": kwargs.get("allow_unverified_anchor"),
                    },
                    "timeline": {"status": "pass", "returned_count": 1},
                    "failure_reasons": [],
                    "artifacts": {},
                }

            with patch.object(cli, "run_memory_cycle", side_effect=fake_run_memory_cycle):
                for idx, mode in enumerate(("strict", "safe", "legacy"), start=1):
                    rc = cli.main(
                        [
                            "memory",
                            str(repo),
                            "--out-dir",
                            str(out),
                            "--no-handoff",
                            "--anchor-out",
                            str(anchor),
                            "--anchor-freshness",
                            mode,
                            "--json",
                        ]
                    )
                    self.assertEqual(rc, 0)
                    self.assertEqual(len(calls), idx)

                    _repo_arg, _out_arg, kwargs = calls[-1]
                    self.assertEqual(kwargs["anchor_out"], str(anchor.resolve()))
                    self.assertEqual(kwargs["anchor_freshness"], mode)
                    self.assertTrue(kwargs["anchor_verify"], f"{mode} should imply anchor verification")
                    if mode == "strict":
                        self.assertFalse(kwargs["allow_unverified_anchor"], f"{mode} should not allow unverified anchor by default")
                    else:
                        self.assertTrue(kwargs["allow_unverified_anchor"], f"{mode} should allow unverified anchor by default")

            self.assertEqual(len(calls), 3)

    def test_cli_memory_anchor_verify_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-anchor-verify-cli"
            anchor = Path(tmp) / "memory-anchor.json"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--no-handoff",
                    "--anchor-out",
                    str(anchor),
                    "--anchor-verify",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertIsNotNone(payload["anchor_verification"])
            self.assertEqual(payload["anchor_verification"]["status"], "pass")

    def test_cli_memory_anchor_log_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-anchor-log-cli"
            anchor = Path(tmp) / "memory-anchor.json"
            log = Path(tmp) / "memory-anchor.log"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--no-handoff",
                    "--anchor-out",
                    str(anchor),
                    "--anchor-log",
                    str(log),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(log.exists())
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["anchor_schema_version"], "repomori.snapshot_anchor.v1")

    def test_cli_memory_no_handoff_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-no-handoff-cli"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--no-handoff",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertIsNone(payload["summary"]["handoff_dir"])
            self.assertNotIn("handoff", payload["artifacts"])

    def test_cli_memory_no_incremental_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-no-incremental-cli"
            run_memory_cycle(repo, out, no_handoff=True)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--no-handoff",
                    "--no-incremental",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertFalse(payload["settings"]["incremental"])
            self.assertFalse(payload["snapshot"]["build"]["incremental"])
            self.assertEqual(payload["summary"]["reused_file_count"], 0)

    def test_cli_memory_prune_apply_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-prune-cli"
            run_memory_cycle(repo, out, no_handoff=True)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")

            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--no-handoff",
                    "--keep",
                    "1",
                    "--prune-apply",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertTrue(payload["prune"]["applied"])
            self.assertFalse(payload["prune"]["errors"])
            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["snapshot_count"], 1)

    def test_cli_memory_diff_context_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-diff-context-cli"
            run_memory_cycle(repo, out, no_handoff=True)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")

            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--no-handoff",
                    "--diff-context",
                    "--diff-context-question",
                    "added",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["summary"]["diff_context_status"], "written")
            self.assertEqual(payload["diff_context"]["schema_version"], "repomori.diff_context.v1")
            self.assertTrue((out / payload["artifacts"]["diff_context_json"]).exists())

    def test_cli_init_json_writes_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "init",
                    str(repo),
                    "--out-dir",
                    str(out),
                    "--config",
                    str(config),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.config.init.v1")
            self.assertEqual(payload["config_path"], str(config.resolve()))
            self.assertTrue(payload["settings"]["incremental"])
            self.assertFalse(payload["settings"]["diff_context"])
            self.assertTrue(config.exists())
            self.assertIn("incremental = true", config.read_text(encoding="utf-8"))
            self.assertIn("diff_context = false", config.read_text(encoding="utf-8"))
            self.assertIn("repomori.config.v1", config.read_text(encoding="utf-8"))

    def test_cli_memory_uses_config_without_repo_or_out_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(repo, out, config_path=config)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    "--config",
                    str(config),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["repo_path"], str(repo.resolve()))
            self.assertEqual(payload["out_dir"], str(out.resolve()))
            self.assertTrue((Path(payload["summary"]["handoff_dir"]) / "manifest.json").exists())

    def test_cli_memory_flags_override_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(repo, out, config_path=config, no_handoff=True, keep=5)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    "--config",
                    str(config),
                    "--with-handoff",
                    "--no-incremental",
                    "--keep",
                    "1",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertFalse(payload["settings"]["no_handoff"])
            self.assertFalse(payload["settings"]["incremental"])
            self.assertEqual(payload["settings"]["keep"], 1)
            self.assertTrue((Path(payload["summary"]["handoff_dir"]) / "manifest.json").exists())

    def test_cli_memory_relative_anchor_out_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(
                repo,
                out,
                config_path=config,
                no_handoff=True,
            )
            config_path = config.resolve()
            config_lines = config_path.read_text(encoding="utf-8").splitlines()
            config_lines.append('anchor_out = "memory-anchor.json"')
            config_lines.append("anchor_verify = false")
            config_lines.append("allow_unverified_anchor = false")
            config_lines.append('anchor_log = "memory-anchor.log"')
            config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")

            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "memory",
                    "--config",
                    str(config_path),
                    "--no-handoff",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.memory.v1")
            self.assertEqual(payload["summary"]["anchor_path"], str((config_path.parent / "memory-anchor.json").resolve()))
            self.assertEqual(payload["settings"]["anchor_out"], str((config_path.parent / "memory-anchor.json").resolve()))
            self.assertFalse(payload["settings"]["anchor_verify"])
            self.assertTrue(Path(payload["summary"]["anchor_path"]).exists())

    def test_cli_agent_json_lines_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(repo, out, config_path=config, no_handoff=True)
            run_memory_cycle(repo, out, no_handoff=True)
            requests = "\n".join(
                [
                    json.dumps({"id": 1, "method": "ping"}),
                    json.dumps({"id": 2, "method": "query.run", "params": {"text": "sqlite Store", "limit": 1}}),
                    "",
                ]
            )
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "agent",
                    "--config",
                    str(config),
                ],
                input=requests,
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            responses = [json.loads(line) for line in output.splitlines()]
            self.assertEqual(len(responses), 2)
            self.assertTrue(responses[0]["ok"])
            self.assertEqual(responses[0]["result"]["status"], "ok")
            self.assertTrue(responses[1]["ok"])
            self.assertEqual(responses[1]["result"]["results"][0]["path"], "app.py")

    def test_cli_agent_invalid_json_reports_error(self) -> None:
        output = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "repomori",
                "agent",
            ],
            input="{not json}\n",
            cwd=Path(__file__).resolve().parents[1],
            text=True,
        )

        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_json")

    def test_cli_mcp_stdio_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(repo, out, config_path=config, no_handoff=True)
            run_memory_cycle(repo, out, no_handoff=True)
            requests = "\n".join(
                [
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-11-25",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"},
                            },
                        }
                    ),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {
                                "name": "repomori_query_run",
                                "arguments": {"text": "sqlite Store", "limit": 1},
                            },
                        }
                    ),
                    "",
                ]
            )
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "mcp",
                    "--config",
                    str(config),
                ],
                input=requests,
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            responses = [json.loads(line) for line in output.splitlines()]
            self.assertEqual(len(responses), 3)
            self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")
            self.assertIn("repomori_query_run", [tool["name"] for tool in responses[1]["result"]["tools"]])
            self.assertEqual(responses[2]["result"]["structuredContent"]["results"][0]["path"], "app.py")

    def test_cli_schema_json_is_parseable(self) -> None:
        output = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "repomori",
                "schema",
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
        )

        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], "repomori.schema.catalog.v1")
        self.assertIn("context.build", payload["agent_methods"])
        self.assertTrue(any(item["schema_version"] == "repomori.memory.v1" for item in payload["schemas"]))

    def test_cli_schema_specific_json_is_parseable(self) -> None:
        output = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "repomori",
                "schema",
                "repomori.memory.v1",
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
        )

        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], "repomori.schema.catalog.v1")
        self.assertEqual(payload["selected"], "repomori.memory.v1")
        self.assertEqual(payload["schema"]["producer"], "run_memory_cycle")

    def test_cli_scan_json_and_fail_on_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "scan-cli"
            repo.mkdir()
            (repo / "README.md").write_text("# Scan CLI\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "scan",
                    str(repo),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["schema_version"], "repomori.scan.v1")
            self.assertEqual(payload["status"], "warn")
            self.assertTrue(any(finding["code"] == "missing_license" for finding in payload["findings"]))

            strict = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "scan",
                    str(repo),
                    "--fail-on",
                    "medium",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(strict.returncode, 1)
            self.assertEqual(json.loads(strict.stdout)["schema_version"], "repomori.scan.v1")

    def test_cli_scan_baseline_and_ignore_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "scan-baseline-cli"
            self._public_ready_repo(repo)
            (repo / "README.md").write_text("Use D:\\Temp\\repomori-demo for examples.\n", encoding="utf-8")
            baseline = Path(tmp) / "scan-baseline.json"

            write_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "scan",
                    str(repo),
                    "--public-release",
                    "--fail-on",
                    "low",
                    "--write-baseline",
                    str(baseline),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(write_result.returncode, 0, write_result.stderr)
            write_payload = json.loads(write_result.stdout)
            self.assertTrue(baseline.exists())
            self.assertEqual(write_payload["baseline_written"]["ignored_count"], 1)

            baseline_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "scan",
                    str(repo),
                    "--public-release",
                    "--fail-on",
                    "low",
                    "--baseline",
                    str(baseline),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(baseline_result.returncode, 0, baseline_result.stderr)
            baseline_payload = json.loads(baseline_result.stdout)
            self.assertEqual(baseline_payload["summary"]["findings"], 0)
            self.assertEqual(baseline_payload["summary"]["ignored_findings"], 1)

            (repo / "README.md").write_text(
                "# Heading\\n\\nUse D:\\Temp\\repomori-demo for examples.\\n",
                encoding="utf-8",
            )

            shifted_baseline_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "scan",
                    str(repo),
                    "--public-release",
                    "--fail-on",
                    "low",
                    "--baseline",
                    str(baseline),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(shifted_baseline_result.returncode, 0, shifted_baseline_result.stderr)
            shifted_payload = json.loads(shifted_baseline_result.stdout)
            self.assertEqual(shifted_payload["summary"]["findings"], 0)
            self.assertEqual(shifted_payload["summary"]["ignored_findings"], 1)

            ignore_code_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "scan",
                    str(repo),
                    "--public-release",
                    "--fail-on",
                    "low",
                    "--ignore-code",
                    "temp_drive_path",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(ignore_code_result.returncode, 0, ignore_code_result.stderr)
            self.assertEqual(json.loads(ignore_code_result.stdout)["summary"]["ignored_findings"], 1)

    def test_cli_release_check_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-cli"
            self._public_ready_repo(repo)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-check",
                    str(repo),
                    "--skip-tests",
                    "--skip-demo",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.release_check.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["checks"]["tests"]["status"], "skipped")
            self.assertEqual(payload["checks"]["demo"]["status"], "skipped")

    def test_cli_release_check_fails_with_workspace_generated_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-cli-block"
            self._public_ready_repo(repo)
            (repo / "packs").mkdir()
            (repo / "tmp.repomori").write_text("stale artifact", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-check",
                    str(repo),
                    "--skip-tests",
                    "--skip-demo",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "fail")
            self.assertIn("workspace", payload["summary"]["failed_checks"])
            self.assertFalse(payload["checks"]["workspace"]["ok"])
            self.assertTrue(any("workspace:" in row for row in payload["failure_reasons"]))
            self.assertTrue(any("packs" in row.lower() or ".repomori" in row.lower() for row in payload["failure_reasons"]))

    def test_cli_release_check_reports_scan_failure_reason_for_build_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-cli-scan-noise"
            self._public_ready_repo(repo)
            noise = repo / ".pytest_cache"
            noise.mkdir()
            (noise / "cache_file.txt").write_text("noop", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-check",
                    str(repo),
                    "--skip-tests",
                    "--skip-demo",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "fail")
            self.assertEqual(payload["checks"]["scan"]["status"], "fail")
            self.assertTrue(any("scan:" in row and ".pytest_cache" in row for row in payload["failure_reasons"]))

    def test_cli_release_check_allows_hidden_workspace_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-cli-clean"
            self._public_ready_repo(repo)
            hidden_dir = repo / ".repomori-release-check"
            hidden_dir.mkdir()
            (hidden_dir / "release-check.repomori").write_text("keep hidden", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-check",
                    str(repo),
                    "--skip-tests",
                    "--skip-demo",
                    "--artifacts-dir",
                    str(hidden_dir),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"]["failed_checks"], [])
            self.assertEqual(payload["checks"]["workspace"]["status"], "pass")

    def test_cli_release_check_includes_drift_warning_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-drift"
            repo.mkdir()
            (repo / "README.md").write_text("D:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            (repo / "DOCS.md").write_text("D:\\Temp\\repomori-demo\\doc.txt\n", encoding="utf-8")
            (repo / "LICENSE.md").write_text("License text.\n", encoding="utf-8")
            initial_scan = scan_repository(repo, public_release=False)
            baseline_path = Path(tmp) / "scan-baseline.json"
            write_scan_baseline(initial_scan, baseline_path)

            (repo / "README.md").write_text("# heading\nD:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            (repo / "DOCS.md").write_text("D:\\Temp\\repomori-demo\\doc.txt\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-check",
                    str(repo),
                    "--skip-tests",
                    "--skip-demo",
                    "--no-public-release",
                    "--baseline",
                    str(baseline_path),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "pass")
            scan_block = payload["checks"]["scan"]
            self.assertIn("drift_warnings", scan_block)
            drift = scan_block["drift_warnings"]
            self.assertEqual(drift["strict_count"], 1)
            self.assertEqual(drift["semi_strict_count"], 1)
            self.assertEqual(drift["ignored_total"], 2)
            self.assertIn("non_strict_ratio", drift)

    def test_cli_release_check_json_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-artifacts"
            self._public_ready_repo(repo)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-check",
                    str(repo),
                    "--skip-tests",
                    "--skip-demo",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.release_check.v1")
            artifact_dir = repo / ".repomori-release-check"
            self.assertTrue(Path(payload["artifacts"]["json"]).exists())
            self.assertTrue(Path(payload["artifacts"]["markdown"]).exists())
            self.assertEqual(payload["artifacts"]["json"], str(artifact_dir / "release-check.json"))
            self.assertEqual(payload["artifacts"]["markdown"], str(artifact_dir / "release-check.md"))
            if payload["checks"]["scan"]["drift_log"] is not None:
                self.assertTrue(Path(payload["checks"]["scan"]["drift_log"]["log_path"]).exists())
                self.assertEqual(payload["checks"]["scan"]["drift_log"]["status"], "appended")

    def test_cli_release_health_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health-cli"
            self._public_ready_repo(repo)
            out = Path(tmp) / "snapshots"
            run_memory_cycle(repo, out, no_handoff=True)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-health",
                    str(repo),
                    "--snapshot-dir",
                    str(out),
                    "--skip-tests",
                    "--skip-demo",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.health.v1")
            self.assertIn("release_check", payload["checks"])
            self.assertIn("timeline", payload["checks"])
            self.assertIn("chain", payload["checks"])
            self.assertIn("drift_summary", payload["checks"])
            self.assertEqual(payload["checks"]["release_check"]["schema_version"], "repomori.release_check.v1")
            self.assertEqual(payload["status"], "pass")

    def test_cli_release_health_no_snapshot_dir_is_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health-cli-nosnap"
            self._public_ready_repo(repo)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-health",
                    str(repo),
                    "--skip-tests",
                    "--skip-demo",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.health.v1")
            self.assertEqual(payload["status"], "warn")
            self.assertEqual(payload["checks"]["doctor"]["status"], "warn")
            self.assertEqual(payload["checks"]["chain"]["status"], "warn")

    def test_cli_release_health_artifacts_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health-cli-artifacts"
            self._public_ready_repo(repo)
            out = Path(tmp) / "snapshots"
            run_memory_cycle(repo, out, no_handoff=True)

            artifacts_dir = Path(tmp) / "release-health-artifacts"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-health",
                    str(repo),
                    "--snapshot-dir",
                    str(out),
                    "--json",
                    "--artifacts-dir",
                    str(artifacts_dir),
                    "--skip-tests",
                    "--skip-demo",
                    "--no-public-release",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.health.v1")
            self.assertTrue(Path(payload["artifacts"]["json"]).exists())
            self.assertTrue(Path(payload["artifacts"]["markdown"]).exists())
            self.assertEqual(payload["artifacts"]["json"], str(artifacts_dir / "release-health.json"))
            self.assertEqual(payload["artifacts"]["markdown"], str(artifacts_dir / "release-health.md"))
            release_check = payload["checks"]["release_check"]
            self.assertEqual(release_check["artifacts"]["drift_log"], str(artifacts_dir / "baseline-drift.jsonl"))
            self.assertTrue(Path(release_check["artifacts"]["drift_log"]).exists())
            self.assertTrue(
                Path(release_check["checks"]["scan"]["drift_log"]["log_path"]).exists()
            )

    def test_cli_release_health_drift_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health-cli-policy"
            repo.mkdir()
            (repo / "README.md").write_text("D:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            initial = scan_repository(repo, public_release=False)
            baseline = Path(tmp) / "scan-baseline.json"
            write_scan_baseline(initial, baseline)

            (repo / "README.md").write_text("# heading\nD:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            out = Path(tmp) / "snapshots"
            run_memory_cycle(repo, out, no_handoff=True)

            drift_log = Path(tmp) / "release-health-drift.log"
            policy = Path(tmp) / "drift-policy.json"
            policy.write_text(json.dumps({"non_strict_ratio": {"warn-at": 0.2}}), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-health",
                    str(repo),
                    "--snapshot-dir",
                    str(out),
                    "--baseline",
                    str(baseline),
                    "--drift-policy",
                    str(policy),
                    "--drift-log",
                    str(drift_log),
                    "--skip-tests",
                    "--skip-demo",
                    "--no-public-release",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["checks"]["release_check"]["summary"]["drift_policy_status"], "warn")
            self.assertEqual(payload["status"], "warn")
            self.assertTrue(drift_log.exists())

    def test_cli_release_health_drift_policy_can_fail_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-health-cli-policy-fail"
            repo.mkdir()
            (repo / "README.md").write_text("D:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            initial = scan_repository(repo, public_release=False)
            baseline = Path(tmp) / "scan-baseline.json"
            write_scan_baseline(initial, baseline)

            (repo / "README.md").write_text("# heading\nD:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            out = Path(tmp) / "snapshots"
            run_memory_cycle(repo, out, no_handoff=True)

            policy = Path(tmp) / "drift-policy.json"
            policy.write_text(json.dumps({"non_strict_ratio": {"warn-at": 0.2, "fail-at": 0.9}}), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-health",
                    str(repo),
                    "--snapshot-dir",
                    str(out),
                    "--baseline",
                    str(baseline),
                    "--drift-policy",
                    str(policy),
                    "--skip-tests",
                    "--skip-demo",
                    "--no-public-release",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["checks"]["release_check"]["summary"]["drift_policy_status"], "fail")
            self.assertEqual(payload["status"], "fail")

    def test_release_check_drift_policy_with_bom_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-bom-policy"
            repo.mkdir()
            (repo / "README.md").write_text("D:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            baseline = Path(tmp) / "scan-baseline.json"
            write_scan_baseline(scan_repository(repo, public_release=False), baseline)

            policy = Path(tmp) / "drift-policy.json"
            policy.write_bytes(
                json.dumps(
                    {"non_strict_ratio": {"warn-at": 0.2, "fail-at": 1.0}},
                    indent=2,
                ).encode("utf-8-sig")
            )

            report = run_release_check(
                repo,
                baseline=baseline,
                run_tests=False,
                run_demo_smoke=False,
                drift_policy=policy,
            )
            self.assertEqual(report["checks"]["scan"]["drift_policy"]["status"], "warn")

    def test_cli_release_check_drift_log_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "release-check-drift-log"
            repo.mkdir()
            (repo / "README.md").write_text("D:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            (repo / "LICENSE.md").write_text("License text.\n", encoding="utf-8")
            initial_scan = scan_repository(repo, public_release=False)
            baseline_path = Path(tmp) / "scan-baseline.json"
            write_scan_baseline(initial_scan, baseline_path)

            (repo / "README.md").write_text("# heading\nD:\\Temp\\repomori-demo\\one\\file.py\n", encoding="utf-8")
            drift_log = Path(tmp) / "release-drift.log"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-check",
                    str(repo),
                    "--skip-tests",
                    "--skip-demo",
                    "--no-public-release",
                    "--baseline",
                    str(baseline_path),
                    "--drift-log",
                    str(drift_log),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "pass")
            scan_block = payload["checks"]["scan"]["drift_log"]
            self.assertEqual(scan_block["status"], "appended")
            self.assertEqual(scan_block["log_path"], str(drift_log.resolve()))
            rows = [
                json.loads(line)
                for line in drift_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["schema_version"], "repomori.baseline_drift_record.v1")
            self.assertEqual(row["status"], "warn")
            self.assertGreater(row["semi_strict_count"], 0)

    def test_cli_drift_summary_command_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "release-drift.log"
            append_baseline_drift_log(
                build_baseline_drift_report(
                    {"summary": {"baseline_match_counts": {"strict": 2, "semi_strict": 0, "fallback": 0}}, "repo_path": "D:/Temp/demo"},
                    run_meta={"repo_path": "D:/Temp/demo", "run_ts": 10, "run_id": "a"},
                ),
                log_path,
            )
            append_baseline_drift_log(
                build_baseline_drift_report(
                    {"summary": {"baseline_match_counts": {"strict": 1, "semi_strict": 1, "fallback": 1}}, "repo_path": "D:/Temp/demo"},
                    run_meta={"repo_path": "D:/Temp/demo", "run_ts": 11, "run_id": "b"},
                ),
                log_path,
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "drift-summary",
                    str(log_path),
                    "--limit",
                    "2",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.baseline_drift_summary.v1")
            self.assertEqual(payload["count"], 2)
            self.assertEqual(payload["warn_count"], 1)
            self.assertEqual(payload["trend"]["non_strict_delta"], 2)

    def _demo_pack(self, root: Path, *, build: bool = False) -> tuple[Path, Path]:
        repo = root / "demo"
        repo.mkdir()
        (repo / "README.md").write_text("# Demo\n\nStorage engine notes.\n", encoding="utf-8")
        (repo / "app.py").write_text(
            "import sqlite3\n\n"
            "class Store:\n"
            "    def connect(self):\n"
            "        return sqlite3.connect(':memory:')\n",
            encoding="utf-8",
        )
        (repo / "blob.bin").write_bytes(b"\x00\x01\x02" * 10)
        pack = root / "demo.repomori"
        if build:
            build_pack(repo, pack, BuildOptions(force=True))
        return repo, pack

    def _public_ready_repo(self, repo: Path) -> None:
        repo.mkdir()
        (repo / "README.md").write_text("# Ready\n\nSource-available demo.\n", encoding="utf-8")
        (repo / "LICENSE.md").write_text("PolyForm Noncommercial License 1.0.0\n", encoding="utf-8")
        (repo / "NOTICE.md").write_text("Copyright TWO HANDS NETWORK LTD\n", encoding="utf-8")
        (repo / "COMMERCIAL-LICENSE.md").write_text("Commercial use requires written permission.\n", encoding="utf-8")
        (repo / "CONTRIBUTING.md").write_text("Contributions are accepted under project terms.\n", encoding="utf-8")
        (repo / "PUBLIC_RELEASE_CHECKLIST.md").write_text("- Confirm public release posture.\n", encoding="utf-8")
        (repo / "pyproject.toml").write_text(
            "[project]\nname = \"ready\"\nlicense = { file = \"LICENSE.md\" }\n",
            encoding="utf-8",
        )
        (repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
