from __future__ import annotations

import hashlib
import io
import json
import shutil
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
    append_handoff_health_log,
    benchmark_repo,
    build_agent_brief,
    build_baseline_drift_report,
    build_repo_brief,
    build_snapshot_anchor,
    build_capsule,
    build_context_bundle,
    build_diff_context_bundle,
    build_handoff_health_report,
    build_handoff_health_record,
    build_handoff_package,
    build_pack,
    build_release_candidate_reviewer_handoff,
    build_release_evidence,
    build_release_review_privacy_guard_demo,
    build_release_review_decision_log,
    check_handoff_package,
    check_compatibility,
    check_contract_fixture,
    check_release_candidate_review_bundle,
    check_release_review_decision_log_privacy,
    check_snapshot_restore,
    archive_handoff_package,
    compare_packs,
    diagnose_query,
    doctor_snapshot_dir,
    evaluate_handoff_quality,
    evaluate_release_policy,
    evaluate_pack,
    evaluate_context_quality,
    format_agent_brief_markdown,
    format_benchmark_markdown,
    format_brief_markdown,
    format_compare_markdown,
    format_compat_markdown,
    format_contract_check_markdown,
    format_eval_markdown,
    format_handoff_score_markdown,
    format_handoff_triage_markdown,
    format_handoff_quality_markdown,
    format_handoff_improvement_markdown,
    format_handoff_archive_markdown,
    format_handoff_health_markdown,
    format_handoff_health_summary_markdown,
    format_pack_inspect_diff_markdown,
    format_pack_inspect_markdown,
    format_release_candidate_artifact_index_markdown,
    format_release_candidate_reviewer_handoff_markdown,
    format_release_evidence_markdown,
    format_release_review_privacy_guard_demo_markdown,
    format_release_review_decision_log_markdown,
    format_release_review_checklist_markdown,
    format_release_verify_markdown,
    format_restore_check_markdown,
    format_context_markdown,
    format_context_eval_markdown,
    format_diff_context_markdown,
    format_snapshot_anchor_markdown,
    format_snapshot_anchor_verification_markdown,
    format_snapshot_chain_markdown,
    format_stats_markdown,
    format_snapshot_markdown,
    format_timeline_markdown,
    format_timeline_search_markdown,
    append_anchor_log,
    get_file_bytes,
    handle_agent_request,
    handle_mcp_request,
    init_config,
    info_pack,
    improve_handoff_package,
    inspect_pack_diff,
    inspect_pack,
    load_memory_config,
    query_pack,
    prune_snapshots,
    read_snapshot_stats,
    read_snapshot_timeline,
    search_snapshot_timeline,
    run_mcp_bridge,
    run_demo,
    run_memory_cycle,
    run_release_check,
    run_release_health,
    append_baseline_drift_log,
    summarize_baseline_drift_log,
    summarize_handoff_health_log,
    schema_catalog,
    scan_baseline_from_report,
    scan_repository,
    score_handoff_package,
    snapshot_repo,
    verify_snapshot_anchor,
    tree_pack,
    triage_handoff_score,
    verify_snapshot_chain,
    verify_pack,
    verify_release_package,
    write_scan_baseline,
    write_release_package_artifacts,
)


class RepoMoriCodecTests(unittest.TestCase):
    def _release_policy_package(
        self,
        tmp_path: Path,
        *,
        signed: bool = False,
        evidence_warning_count: int = 0,
    ) -> Path:
        root = tmp_path / ".repomori-release-candidate"
        dist = root / "dist"
        dist.mkdir(parents=True)
        (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
        (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
        write_release_package_artifacts(
            root,
            version="0.2.0",
            commit="abc123",
            ref="main",
            run_id="42",
            repository="Martin123132/RepoMori",
            generated_at=1700000000,
        )
        verify_report = verify_release_package(root)
        (root / "release-verify.json").write_text(json.dumps(verify_report, indent=2), encoding="utf-8")
        (root / "release-verify.md").write_text(format_release_verify_markdown(verify_report), encoding="utf-8")
        if signed:
            for target in ("checksums.txt", "release-provenance.json", "sbom.spdx.json", "release-verify.json"):
                (root / f"{target}.asc").write_text(f"signature for {target}\n", encoding="utf-8")
            (root / "repomori-release-public-key.asc").write_text("public-key\n", encoding="utf-8")

        release_check = tmp_path / "release-check.json"
        release_check.write_text(
            json.dumps(
                {
                    "schema_version": "repomori.release_check.v1",
                    "status": "pass",
                    "summary": {"failed_checks": [], "scan_findings": 0},
                }
            ),
            encoding="utf-8",
        )
        build_release_evidence(root, release_check=release_check, out_dir=root)
        if evidence_warning_count:
            evidence_path = root / "release-evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["status"] = "pass"
            evidence.setdefault("summary", {})["warning_count"] = evidence_warning_count
            evidence["warnings"] = [
                {
                    "code": "fixture_warning",
                    "message": "Synthetic reviewer warning used to test strict release policy thresholds.",
                }
            ]
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        return root

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
            self.assertIn("storage", top["matched_tokens"])
            self.assertIn("alias-symbol", top["match_reasons"])
            self.assertTrue(top["snippet_anchors"])
            self.assertTrue(any(event["field"] == "symbol" for event in top["score_breakdown"]))
            self.assertTrue(report["ranking_notes"])
            self.assertIn("score_delta", report["ranking_notes"][0])

    def test_query_ranking_uses_identifier_terms_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "quality"
            repo.mkdir()
            (repo / "README.md").write_text(
                "# Storage Notes\n\nGeneral product notes for repositories.\n",
                encoding="utf-8",
            )
            (repo / "storage.py").write_text(
                "import sqlite3\n\n"
                "class Repository:\n"
                "    def connect_database(self):\n"
                "        return sqlite3.connect(':memory:')\n",
                encoding="utf-8",
            )
            pack = root / "quality.repomori"
            build_pack(repo, pack, BuildOptions(force=True))

            results = query_pack(pack, "database connection", limit=2)

            self.assertEqual(results[0]["path"], "storage.py")
            self.assertIn("alias-symbol", results[0]["match_reasons"])
            self.assertIn("all-query-terms", results[0]["match_reasons"])
            self.assertEqual(results[0]["matched_terms"], ["connection", "database"])

            camel_results = query_pack(pack, "connectDatabase", limit=1)
            self.assertEqual(camel_results[0]["path"], "storage.py")
            self.assertIn("all-query-terms", camel_results[0]["match_reasons"])
            self.assertEqual(camel_results[0]["matched_terms"], ["connect", "database"])

    def test_context_snippet_prefers_matching_symbol_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "anchors"
            repo.mkdir()
            (repo / "storage.py").write_text(
                "# database connection overview\n"
                "# database connection setup notes\n"
                "# database connection retries\n"
                "# database connection pooling\n\n"
                "import sqlite3\n\n"
                "def connect_database():\n"
                "    return sqlite3.connect(':memory:')\n",
                encoding="utf-8",
            )
            pack = root / "anchors.repomori"
            build_pack(repo, pack, BuildOptions(force=True))

            bundle = build_context_bundle(
                pack,
                "database connection",
                limit=1,
                snippet_lines=3,
                snippets_per_file=1,
            )

            snippet = bundle["sources"][0]["snippets"][0]
            self.assertIn("def connect_database", snippet["text"])
            self.assertIn("symbols:connect_database", snippet["matched"])
            self.assertEqual(bundle["sources"][0]["matched_terms"], ["connection", "database"])

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

    def test_pack_inspect_diff_reports_structural_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, base_pack = self._demo_pack(root)
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
            target_pack = root / "target.repomori"
            build_pack(repo, target_pack, BuildOptions(force=True, base_pack=base_pack))

            report = inspect_pack_diff(base_pack, target_pack, max_files=5, top_terms=10, top_symbols=10, verify=True)

            self.assertEqual(report["schema_version"], "repomori.inspect_diff.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["added_count"], 1)
            self.assertEqual(report["summary"]["removed_count"], 1)
            self.assertGreaterEqual(report["summary"]["changed_count"], 2)
            self.assertEqual(report["verification"]["base"]["status"], "pass")
            self.assertEqual(report["verification"]["target"]["status"], "pass")
            self.assertIn("chunk_count_delta", report["storage_delta"])
            added_symbols = {item["value"] for item in report["vocabulary_delta"]["top_symbols"]["added"]}
            self.assertIn("function:close", added_symbols)
            manifest_paths = {item["path"] for item in report["source_manifest"]}
            self.assertIn("app.py", manifest_paths)
            self.assertIn("new.py", manifest_paths)
            self.assertIn("blob.bin", manifest_paths)

            markdown = format_pack_inspect_diff_markdown(report)
            self.assertIn("# RepoMori Pack Inspect Diff", markdown)
            self.assertIn("## Storage Delta", markdown)
            self.assertIn("function:close", markdown)

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
            self.assertEqual(brief["summary"]["inspect_diff_status"], "pass")
            self.assertEqual(brief["summary"]["inspect_diff_changed_count"], 1)
            self.assertEqual(brief["latest_inspect_diff"]["summary"]["changed_count"], 1)
            self.assertEqual(brief["latest_diff_context"]["summary"]["changed_count"], 1)
            self.assertEqual(brief["repo_brief"]["schema_version"], "repomori.brief.v1")

            artifact_kinds = {item["kind"] for item in brief["artifacts"]}
            self.assertIn("latest_pack", artifact_kinds)
            self.assertIn("handoff_dir", artifact_kinds)
            self.assertIn("inspect_diff_json", artifact_kinds)
            self.assertIn("diff_context_json", artifact_kinds)
            self.assertTrue(any("context" in item["command"] for item in brief["recommended_commands"]))

            markdown = format_agent_brief_markdown(brief)
            self.assertIn("# RepoMori Agent Brief", markdown)
            self.assertIn("## Latest Inspect Diff", markdown)
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

    def test_context_quality_eval_report_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            report = evaluate_context_quality(
                pack,
                [
                    {
                        "id": "sqlite-store",
                        "question": "How does the sqlite Store connect?",
                        "expected_paths": ["app.py"],
                        "required_snippets": ["sqlite3.connect"],
                        "required_terms": ["sqlite", "store"],
                        "max_rank": 1,
                        "min_snippets": 1,
                    }
                ],
                limit=3,
                snippet_lines=6,
            )

            self.assertEqual(report["schema_version"], "repomori.context_eval.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["passed_cases"], 1)
            self.assertEqual(report["cases"][0]["result"]["top_path"], "app.py")
            self.assertTrue(all(check["status"] == "pass" for check in report["cases"][0]["checks"]))

            markdown = format_context_eval_markdown(report)
            self.assertIn("# RepoMori Context Quality Eval", markdown)
            self.assertIn("sqlite-store", markdown)
            self.assertIn("No failing context quality cases", markdown)

    def test_context_quality_eval_reports_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)

            report = evaluate_context_quality(
                pack,
                [
                    {
                        "id": "missing-path",
                        "question": "sqlite Store",
                        "expected_paths": ["missing.py"],
                        "required_snippets": ["not in source"],
                    }
                ],
                limit=2,
                snippet_lines=4,
            )

            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["summary"]["failed_cases"], 1)
            self.assertEqual(report["failures"][0]["case_id"], "missing-path")
            failed_ids = {check["id"] for check in report["failures"][0]["failed_checks"]}
            self.assertIn("expected_path:missing.py", failed_ids)
            self.assertTrue(any(check_id.startswith("required_snippet:") for check_id in failed_ids))

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
            self.assertTrue((out / "inspect-diff.json").exists())
            self.assertTrue((out / "inspect-diff.md").exists())
            artifacts = {artifact["path"]: artifact for artifact in manifest["artifacts"]}
            self.assertEqual(artifacts["compare.json"]["kind"], "compare_json")
            self.assertEqual(artifacts["inspect-diff.json"]["kind"], "inspect_diff_json")
            compare = json.loads((out / "compare.json").read_text(encoding="utf-8"))
            self.assertEqual(compare["schema_version"], "repomori.compare.v1")
            self.assertGreaterEqual(compare["summary"]["changed_count"], 1)
            inspect_diff = json.loads((out / "inspect-diff.json").read_text(encoding="utf-8"))
            self.assertEqual(inspect_diff["schema_version"], "repomori.inspect_diff.v1")
            self.assertGreaterEqual(inspect_diff["summary"]["changed_count"], 1)

            check = check_handoff_package(out)
            self.assertTrue(check["valid"])
            self.assertEqual(check["checked_json"], 7)

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

    def test_check_compatibility_validates_pack_handoff_agent_and_mcp_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            handoff_dir = Path(tmp) / "handoff"
            build_handoff_package(pack, "sqlite Store", handoff_dir)

            report = check_compatibility(pack, handoff=handoff_dir, verify_pack_contents=True)

            self.assertEqual(report["schema_version"], "repomori.compat.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["pack_schema"], codec.SCHEMA_VERSION)
            self.assertTrue(report["summary"]["handoff_valid"])
            self.assertTrue(report["pack_verification"]["verified"])
            check_statuses = {item["id"]: item["status"] for item in report["checks"]}
            self.assertEqual(check_statuses["pack_schema"], "pass")
            self.assertEqual(check_statuses["pack_verification"], "pass")
            self.assertEqual(check_statuses["handoff_integrity"], "pass")
            self.assertEqual(check_statuses["handoff_schemas"], "pass")
            self.assertEqual(check_statuses["schema_catalog"], "pass")
            self.assertEqual(check_statuses["agent_methods"], "pass")
            self.assertEqual(check_statuses["mcp_tools"], "pass")
            self.assertEqual(
                [item["id"] for item in report["checks"]],
                self._compat_contract_fixture()["full_compat_check_ids"],
            )
            self.assertIn("repomori.compat.v1", report["schema_catalog"]["required_schemas"])
            self.assertIn("compat.check", report["schema_catalog"]["required_agent_methods"])
            self.assertIn("repomori_compat_check", report["schema_catalog"]["required_mcp_tools"])

            markdown = format_compat_markdown(report)
            self.assertIn("# RepoMori Compatibility", markdown)
            self.assertIn("handoff_schemas", markdown)

    def test_check_compatibility_missing_inputs_warns_not_fails(self) -> None:
        report = check_compatibility()

        self.assertEqual(report["schema_version"], "repomori.compat.v1")
        self.assertEqual(report["status"], "warn")
        self.assertTrue(any(item["id"] == "pack_input" for item in report["checks"]))
        self.assertTrue(any(item["id"] == "handoff_input" for item in report["checks"]))
        self.assertTrue(any(warning["code"] == "pack_missing" for warning in report["warnings"]))
        self.assertTrue(any(warning["code"] == "handoff_missing" for warning in report["warnings"]))

    def test_check_compatibility_detects_handoff_schema_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            handoff_dir = Path(tmp) / "handoff"
            build_handoff_package(pack, "sqlite Store", handoff_dir)

            context_path = handoff_dir / "context.json"
            context = json.loads(context_path.read_text(encoding="utf-8"))
            context["schema_version"] = "repomori.context.v0"
            context_path.write_text(json.dumps(context), encoding="utf-8")

            report = check_compatibility(pack, handoff=handoff_dir)

            self.assertEqual(report["status"], "fail")
            self.assertTrue(any(error["code"] == "handoff_integrity_failed" for error in report["errors"]))
            self.assertTrue(any(error["code"] == "handoff_schema_mismatch" for error in report["errors"]))
            schema_checks = {item["path"]: item for item in report["handoff_schemas"]}
            self.assertEqual(schema_checks["context.json"]["status"], "fail")
            self.assertEqual(schema_checks["context.json"]["actual"], "repomori.context.v0")

    def test_score_handoff_package_reports_quality_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff-score"
            build_handoff_package(pack, "sqlite Store", out)

            report = score_handoff_package(out)

            self.assertEqual(report["schema_version"], "repomori.handoff_score.v1")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["summary"]["valid"])
            self.assertGreaterEqual(report["summary"]["score_percent"], 85)
            check_ids = {item["id"] for item in report["checks"]}
            self.assertIn("source_context", check_ids)
            self.assertIn("machine_state", check_ids)
            self.assertGreaterEqual(report["summary"]["context_snippet_count"], 1)

            markdown = format_handoff_score_markdown(report)
            self.assertIn("# RepoMori Handoff Score", markdown)
            self.assertIn("source_context", markdown)

    def test_score_handoff_package_detects_invalid_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff-score-invalid"
            build_handoff_package(pack, "sqlite Store", out)
            (out / "context.md").write_text("tampered\n", encoding="utf-8")

            report = score_handoff_package(out)

            self.assertEqual(report["status"], "fail")
            self.assertFalse(report["summary"]["valid"])
            self.assertIn("integrity", report["summary"]["failed_checks"])
            self.assertTrue(any(error["code"] == "handoff_validation_failed" for error in report["errors"]))

    def test_score_handoff_package_rewards_base_pack_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, base_pack = self._demo_pack(Path(tmp))
            build_pack(repo, base_pack, BuildOptions(force=True))
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            target_pack = Path(tmp) / "target.repomori"
            build_pack(repo, target_pack, BuildOptions(force=True))
            out = Path(tmp) / "handoff-score-delta"
            build_handoff_package(target_pack, "sqlite Store", out, base_pack=base_pack)

            report = score_handoff_package(out)

            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["summary"]["base_pack_present"])
            self.assertTrue(report["summary"]["compare_present"])
            self.assertTrue(report["summary"]["inspect_diff_present"])
            delta = next(item for item in report["checks"] if item["id"] == "delta_context")
            self.assertEqual(delta["status"], "pass")

    def test_handoff_triage_reports_no_urgent_actions_for_clean_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff-triage-clean"
            build_handoff_package(pack, "sqlite Store", out)
            score = score_handoff_package(out)
            score = json.loads(json.dumps(score))
            score["warnings"] = []
            score["summary"]["warned_checks"] = []
            score["summary"]["score"] = score["summary"]["max_score"]
            score["summary"]["score_percent"] = 100.0
            for check in score["checks"]:
                check["status"] = "pass"
                check["points"] = check["max_points"]

            report = triage_handoff_score(score)

            self.assertEqual(report["schema_version"], "repomori.handoff_triage.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["action_count"], 0)
            markdown = format_handoff_triage_markdown(report)
            self.assertIn("# RepoMori Handoff Triage", markdown)
            self.assertIn("No urgent handoff fixes", markdown)

    def test_handoff_triage_turns_weak_score_into_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            memory_dir = Path(tmp) / "memory"
            memory = run_memory_cycle(repo, memory_dir)
            handoff_dir = Path(memory["summary"]["handoff_dir"])

            report = triage_handoff_score(handoff_dir)

            self.assertEqual(report["schema_version"], "repomori.handoff_triage.v1")
            self.assertEqual(report["status"], "fail")
            self.assertGreaterEqual(report["summary"]["high_priority_count"], 1)
            action_ids = {item["id"] for item in report["actions"]}
            self.assertIn("improve-source-context", action_ids)
            self.assertIn("tighten-eval-questions", action_ids)
            self.assertIn("rescore-after-fixes", action_ids)
            self.assertEqual(report["source"]["type"], "handoff_dir_score")

    def test_handoff_triage_prioritizes_invalid_handoff_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff-triage-invalid"
            build_handoff_package(pack, "sqlite Store", out)
            (out / "context.md").write_text("tampered\n", encoding="utf-8")

            report = triage_handoff_score(out)

            self.assertEqual(report["status"], "fail")
            self.assertGreaterEqual(report["summary"]["high_priority_count"], 1)
            self.assertEqual(report["actions"][0]["id"], "fix-integrity")
            self.assertIn("check-handoff", report["actions"][0]["command"])

    def test_handoff_quality_profiles_warn_and_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            memory_dir = Path(tmp) / "memory"
            memory = run_memory_cycle(repo, memory_dir)
            handoff_dir = Path(memory["summary"]["handoff_dir"])

            safe = evaluate_handoff_quality(handoff_dir, profile="safe")
            strict = evaluate_handoff_quality(handoff_dir, profile="strict")

            self.assertEqual(safe["schema_version"], "repomori.handoff_quality.v1")
            self.assertEqual(safe["status"], "warn")
            self.assertEqual(strict["status"], "fail")
            self.assertTrue(strict["failures"])
            markdown = format_handoff_quality_markdown(strict)
            self.assertIn("# RepoMori Handoff Quality", markdown)
            self.assertIn("Profile", markdown)

    def test_improve_handoff_package_writes_quality_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "improved-handoff"

            report = improve_handoff_package(
                pack,
                "sqlite Store",
                out,
                target_score=90,
                max_attempts=2,
            )

            self.assertEqual(report["schema_version"], "repomori.handoff_improvement.v1")
            self.assertIn(report["status"], {"pass", "warn"})
            self.assertTrue((out / "manifest.json").exists())
            self.assertTrue((out / "handoff-improvement.json").exists())
            self.assertTrue((out / "handoff-improvement.md").exists())
            self.assertTrue((out / "handoff-score-before.json").exists())
            self.assertTrue((out / "handoff-score-after.json").exists())
            self.assertTrue((out / "handoff-quality.json").exists())
            self.assertGreaterEqual(report["summary"]["final_score_percent"], 90)
            markdown = format_handoff_improvement_markdown(report)
            self.assertIn("# RepoMori Handoff Improvement", markdown)

    def test_archive_handoff_package_writes_zip_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            handoff_dir = Path(tmp) / "handoff"
            archive_path = Path(tmp) / "handoff-portable.zip"
            build_handoff_package(pack, "sqlite Store", handoff_dir)

            report = archive_handoff_package(handoff_dir, archive_path)

            self.assertEqual(report["schema_version"], "repomori.handoff_archive.v1")
            self.assertTrue(archive_path.exists())
            self.assertGreater(report["archive"]["size"], 0)
            self.assertEqual(report["archive"]["sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest())
            markdown = format_handoff_archive_markdown(report)
            self.assertIn("# RepoMori Handoff Archive", markdown)

    def test_handoff_health_report_wraps_quality_and_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            handoff_dir = Path(tmp) / "handoff-health"
            artifacts_dir = Path(tmp) / "health-artifacts"
            build_handoff_package(pack, "sqlite Store", handoff_dir)

            report = build_handoff_health_report(
                handoff_dir,
                profile="safe",
                artifacts_dir=artifacts_dir,
            )

            self.assertEqual(report["schema_version"], "repomori.handoff_health.v1")
            self.assertIn(report["status"], {"pass", "warn"})
            self.assertEqual(report["summary"]["active_handoff_dir"], str(handoff_dir.resolve()))
            self.assertEqual(report["score"]["schema_version"], "repomori.handoff_score.v1")
            self.assertEqual(report["triage"]["schema_version"], "repomori.handoff_triage.v1")
            self.assertEqual(report["quality"]["schema_version"], "repomori.handoff_quality.v1")
            self.assertTrue((artifacts_dir / "handoff-health.json").exists())
            self.assertTrue((artifacts_dir / "handoff-health.md").exists())
            markdown = format_handoff_health_markdown(report)
            self.assertIn("# RepoMori Handoff Health", markdown)
            self.assertIn("Score", markdown)

    def test_handoff_health_strict_fails_weak_memory_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            memory_dir = Path(tmp) / "memory"
            memory = run_memory_cycle(repo, memory_dir)
            handoff_dir = Path(memory["summary"]["handoff_dir"])

            report = build_handoff_health_report(handoff_dir, profile="strict")

            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["summary"]["quality_status"], "fail")
            self.assertGreaterEqual(report["summary"]["triage_high_priority_count"], 1)

    def test_handoff_health_can_improve_and_archive_active_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            memory_dir = Path(tmp) / "memory"
            memory = run_memory_cycle(repo, memory_dir)
            handoff_dir = Path(memory["summary"]["handoff_dir"])
            improved_dir = Path(tmp) / "handoff-improved"
            archive_path = Path(tmp) / "handoff-health.zip"

            report = build_handoff_health_report(
                handoff_dir,
                profile="safe",
                target_score=90,
                improve_pack=memory["summary"]["pack_path"],
                question="continue this repo",
                improve_out=improved_dir,
                archive=True,
                archive_out=archive_path,
                max_attempts=2,
                force=True,
            )

            self.assertEqual(report["schema_version"], "repomori.handoff_health.v1")
            self.assertTrue(report["summary"]["improved"])
            self.assertTrue((improved_dir / "manifest.json").exists())
            self.assertTrue(report["summary"]["archived"])
            self.assertTrue(archive_path.exists())
            self.assertEqual(report["summary"]["archive_sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest())
            self.assertEqual(report["archive"]["schema_version"], "repomori.handoff_archive.v1")

    def test_handoff_health_log_appends_and_summarizes_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            handoff_dir = Path(tmp) / "handoff-health-log"
            log_path = Path(tmp) / "logs" / "handoff-health.jsonl"
            build_handoff_package(pack, "sqlite Store", handoff_dir)
            base_report = build_handoff_health_report(handoff_dir, profile="safe")
            first = json.loads(json.dumps(base_report))
            second = json.loads(json.dumps(base_report))
            first["status"] = "pass"
            first["summary"]["final_score_percent"] = 94.0
            first["summary"]["triage_action_count"] = 0
            first["summary"]["triage_high_priority_count"] = 0
            second["status"] = "warn"
            second["summary"]["final_score_percent"] = 82.0
            second["summary"]["triage_action_count"] = 3
            second["summary"]["triage_high_priority_count"] = 1
            second["summary"]["improved"] = True
            second["summary"]["archived"] = True
            second["summary"]["archive_sha256"] = "abc123"

            record = build_handoff_health_record(first, run_meta={"run_ts": 10, "run_id": "a"})
            self.assertEqual(record["schema_version"], "repomori.handoff_health_record.v1")
            self.assertEqual(record["score_percent"], first["summary"]["score_percent"])
            append_handoff_health_log(first, log_path, run_meta={"run_ts": 10, "run_id": "a"})
            append_handoff_health_log(second, log_path, run_meta={"run_ts": 11, "run_id": "b"})

            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["schema_version"], "repomori.handoff_health_record.v1")
            self.assertEqual(rows[1]["archive_sha256"], "abc123")
            summary = summarize_handoff_health_log(log_path, limit=2)
            self.assertEqual(summary["schema_version"], "repomori.handoff_health_summary.v1")
            self.assertEqual(summary["count"], 2)
            self.assertEqual(summary["warn_count"], 1)
            self.assertEqual(summary["improvement_count"], 1)
            self.assertEqual(summary["archive_count"], 1)
            self.assertEqual(summary["trend"]["score_percent_delta"], -12.0)
            self.assertEqual(summary["trend"]["triage_action_delta"], 3)
            markdown = format_handoff_health_summary_markdown(summary)
            self.assertIn("# RepoMori Handoff Health Summary", markdown)

    def test_handoff_health_report_can_append_health_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            handoff_dir = Path(tmp) / "handoff-health-log-builder"
            log_path = Path(tmp) / "handoff-health.jsonl"
            artifacts_dir = Path(tmp) / "health-artifacts"
            build_handoff_package(pack, "sqlite Store", handoff_dir)

            report = build_handoff_health_report(
                handoff_dir,
                profile="safe",
                health_log=log_path,
                run_meta={"run_ts": 100, "run_id": "builder"},
                artifacts_dir=artifacts_dir,
            )

            self.assertEqual(report["health_log"]["status"], "appended")
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "builder")
            artifact_payload = json.loads((artifacts_dir / "handoff-health.json").read_text(encoding="utf-8"))
            self.assertIn("health_log", artifact_payload)

    def test_benchmark_repo_outputs_reports_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "bench"

            report = benchmark_repo(repo, out, question="sqlite Store")

            self.assertEqual(report["schema_version"], "repomori.bench.v1")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["summary"]["verify_passed"])
            self.assertTrue(report["summary"]["handoff_passed"])
            self.assertIn(report["summary"]["handoff_score_status"], {"pass", "warn"})
            self.assertGreaterEqual(report["summary"]["handoff_score_percent"], 85)
            self.assertTrue((out / "bench.json").exists())
            self.assertTrue((out / "bench.md").exists())
            self.assertTrue((out / "brief.json").exists())
            self.assertTrue((out / "brief.md").exists())
            self.assertTrue((out / "handoff" / "manifest.json").exists())
            self.assertTrue((out / "handoff" / "brief.json").exists())
            self.assertTrue((out / "handoff" / "handoff-score.json").exists())
            self.assertTrue((out / "handoff" / "handoff-triage.json").exists())
            self.assertEqual(report["handoff_score"]["schema_version"], "repomori.handoff_score.v1")
            self.assertEqual(report["handoff_triage"]["schema_version"], "repomori.handoff_triage.v1")
            self.assertEqual(report["summary"]["handoff_triage_status"], "warn")
            self.assertGreaterEqual(report["summary"]["handoff_triage_action_count"], 1)
            self.assertIn("handoff_triage_json", report["artifacts"])
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
            privacy_demo = report["checks"]["privacy_guard_demo"]
            self.assertTrue(privacy_demo["ok"])
            self.assertEqual(privacy_demo["status"], "pass")
            self.assertEqual(report["summary"]["privacy_guard_demo_status"], "pass")
            self.assertEqual(privacy_demo["summary"]["clean_guard_status"], "pass")
            self.assertEqual(privacy_demo["summary"]["failing_guard_status"], "fail")
            self.assertEqual(privacy_demo["summary"]["leaked_marker_codes"], [])
            observed_codes = set(privacy_demo["summary"]["observed_issue_counts_by_code"])
            self.assertIn("secret_like_value", observed_codes)
            self.assertIn("raw_dump_key", observed_codes)
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
        repo_root = Path(__file__).resolve().parents[1]
        workflow = (repo_root / ".github/workflows/release-health.yml").read_text(encoding="utf-8")
        release_health_doc = (repo_root / "docs/release-health.md").read_text(encoding="utf-8")
        release_check_doc = (repo_root / "docs/release-check.md").read_text(encoding="utf-8")
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        fixture = self._compat_contract_fixture()

        self.assertIn("required_artifacts=(", workflow)
        self.assertIn("release-health.json", workflow)
        self.assertIn("release-health.md", workflow)
        for artifact in fixture["release_health_compat_artifacts"]:
            self.assertIn(artifact, workflow)
            self.assertIn(f"${{{{ steps.run.outputs.artifacts_dir }}}}/{artifact}", workflow)
        self.assertIn("contract_fixture", workflow)
        self.assertIn("--contract-fixture", workflow)
        self.assertIn("--drift-log", workflow)
        self.assertIn("\"$DRIFT_LOG\"", workflow)
        self.assertIn("${{ steps.run.outputs.drift_log }}", workflow)
        self.assertIn("${{ steps.run.outputs.artifacts_dir }}/release-health.json", workflow)
        self.assertIn("${{ steps.run.outputs.artifacts_dir }}/release-health.md", workflow)
        self.assertIn("${{ steps.run.outputs.artifacts_dir }}/compat.json", workflow)
        self.assertIn("${{ steps.run.outputs.artifacts_dir }}/compat.md", workflow)
        self.assertIn("if [ -n \"$DRIFT_LOG\" ] && [ ! -f \"$DRIFT_LOG\" ]", workflow)
        for doc in (release_health_doc, release_check_doc, readme):
            self.assertIn("python -m unittest discover -s tests", doc)
        self.assertIn("privacy-guard demo preflight", release_check_doc)
        self.assertIn("without echoing synthetic paths", release_check_doc)
        self.assertIn("privacy-guard demo preflight", readme)
        self.assertIn("--skip-demo --json", release_health_doc)
        self.assertIn("--skip-demo --json", release_check_doc)

    def test_workflow_contracts_for_release_candidate(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow = (repo_root / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
        pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
        changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")
        release_doc = (repo_root / "docs/release-candidate.md").read_text(encoding="utf-8")
        release_notes = (repo_root / "docs/releases/0.2.0.md").read_text(encoding="utf-8")
        readme = (repo_root / "README.md").read_text(encoding="utf-8")

        self.assertIn('version = "0.2.0"', pyproject)
        self.assertIn("## 0.2.0", changelog)
        self.assertIn("The `0.2.0` release line used validated candidate `0.2.0rc1`", release_doc)
        self.assertIn("for the next candidate by substituting the version", release_doc)
        self.assertIn("# RepoMori 0.2.0", release_notes)
        self.assertIn("Release record: [`v0.2.0`", readme)
        self.assertNotIn("Latest release:", readme)
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("v*", workflow)
        self.assertIn("release_policy", workflow)
        self.assertIn("tests/fixtures/release-policy-basic.json", workflow)
        self.assertIn("Validate release version", workflow)
        self.assertIn("python -m repomori release-check", workflow)
        self.assertIn("python -m pip wheel . --no-deps", workflow)
        self.assertIn("repomori contract-check --fixture", workflow)
        self.assertIn("repomori demo --out", workflow)
        self.assertIn("repomori.release_candidate.v1", workflow)
        self.assertIn("include-hidden-files: true", workflow)
        self.assertIn("release-candidate.json", workflow)
        self.assertIn("release-candidate.md", workflow)
        self.assertIn("write_release_package_artifacts", workflow)
        self.assertIn("checksums.txt", workflow)
        self.assertIn("release-provenance.json", workflow)
        self.assertIn("sbom.spdx.json", workflow)
        self.assertIn("Verify release package", workflow)
        self.assertIn("python -m repomori verify-release .repomori-release-candidate --json", workflow)
        self.assertIn("release-verify.json", workflow)
        self.assertIn("release-verify.md", workflow)
        self.assertIn("repomori.release_verify.v1", workflow)
        self.assertIn("Write release evidence package", workflow)
        self.assertIn("python -m repomori release-evidence .repomori-release-candidate", workflow)
        self.assertIn("release-evidence.json", workflow)
        self.assertIn("release-evidence.md", workflow)
        self.assertIn("repomori.release_evidence.v1", workflow)
        self.assertIn("Verify release policy gate", workflow)
        self.assertIn("python -m repomori verify-release .repomori-release-candidate \\\n            --policy \"$RELEASE_POLICY\"", workflow)
        self.assertIn("release-verify-policy.json", workflow)
        self.assertIn("release-verify-policy.md", workflow)
        self.assertIn("release-review-checklist.md", workflow)
        self.assertIn("release-artifact-index.md", workflow)
        self.assertIn("release-bundle-completeness.json", workflow)
        self.assertIn("release-review-handoff.json", workflow)
        self.assertIn("release-review-handoff.md", workflow)
        self.assertIn("release-review-decision-log.json", workflow)
        self.assertIn("release-review-decision-log.md", workflow)
        self.assertIn("Provisional completeness", workflow)
        self.assertIn("Final fail-fast completeness", workflow)
        self.assertIn("repomori.release_policy.v1", workflow)
        self.assertIn("repomori.release_review_bundle.v1", workflow)
        self.assertIn("repomori.release_review_handoff.v1", workflow)
        self.assertIn("repomori.release_review_decision_log.v1", workflow)
        self.assertIn("repomori.release_review_privacy_guard.v1", workflow)
        self.assertIn("build_release_candidate_reviewer_handoff", workflow)
        self.assertIn("build_release_review_decision_log", workflow)
        self.assertIn("check_release_review_decision_log_privacy", workflow)
        self.assertIn("check_release_candidate_review_bundle", workflow)
        self.assertIn("format_release_candidate_artifact_index_markdown", workflow)
        self.assertIn("format_release_candidate_reviewer_handoff_markdown", workflow)
        self.assertIn("format_release_review_decision_log_markdown", workflow)
        self.assertIn("format_release_review_checklist_markdown", workflow)
        self.assertIn("require_handoff=False", workflow)
        self.assertIn("bundle[\"status\"] == \"pass\"", workflow)
        self.assertIn("handoff[\"status\"] == \"pass\"", workflow)
        self.assertIn("decision_log[\"status\"] == \"pass\"", workflow)
        self.assertIn("privacy_guard[\"status\"] == \"pass\"", workflow)
        self.assertIn("decision_log[\"privacy_guard\"][\"status\"] == \"pass\"", workflow)
        self.assertIn("issue_counts_by_code\"] == {}", workflow)
        self.assertIn("decision_log[\"reviewer_outcome\"][\"decision\"] == \"pending\"", workflow)
        self.assertIn("report[\"policy\"][\"review\"][\"decision\"] == \"reviewable\"", workflow)
        self.assertIn("report[\"policy\"][\"review\"][\"profile\"]", workflow)
        self.assertIn("Review decision: `reviewable`", workflow)
        self.assertIn("### Policy Profile Preflight", workflow)
        self.assertIn("docs/release-policy-selection.md", workflow)
        self.assertIn("docs/release-policy-matrix.md", workflow)
        self.assertIn("docs/release-policy.md#policy-diagnostics", workflow)
        self.assertIn("### Reviewer Next Steps", workflow)
        self.assertIn("## Final Reviewer Decision", workflow)
        self.assertIn("Privacy Guard Demo Preflight", workflow)
        self.assertIn("privacy-guard-demo --mode clean", workflow)
        self.assertIn("privacy-guard-demo --mode fail", workflow)
        self.assertIn("Privacy-guard demo clean/fail expectations were run", workflow)
        self.assertIn("Final reviewer decision: `pending`", workflow)
        self.assertIn("RepoMori Release Candidate Artifact Index", workflow)
        self.assertIn("Diagnostics References", workflow)
        self.assertIn("Public-Safety And Privacy Confirmations", workflow)
        self.assertIn("Privacy Guard", workflow)
        self.assertIn("release-review-checklist.md", release_doc)
        self.assertIn("release-artifact-index.md", release_doc)
        self.assertIn("release-bundle-completeness.json", release_doc)
        self.assertIn("release-review-handoff.md", release_doc)
        self.assertIn("release-review-decision-log.md", release_doc)
        self.assertIn("repomori.release_review_handoff.v1", release_doc)
        self.assertIn("repomori.release_review_decision_log.v1", release_doc)
        self.assertIn("privacy guard", release_doc)
        self.assertIn("Redacted Privacy Guard Failure Example", release_doc)
        self.assertIn("issue_counts_by_code", release_doc)
        self.assertIn("local_absolute_path", release_doc)
        self.assertIn("If a failure summary includes the actual matched value", release_doc)
        self.assertIn("privacy-guard-demo --mode clean --json", release_doc)
        self.assertIn("privacy-guard-demo --mode fail --json", release_doc)
        self.assertIn("The clean run should report top-level `status: \"pass\"`", release_doc)
        self.assertIn("run should still report top-level `status: \"pass\"`", release_doc)
        synthetic_private_host = "internal" + ".example"
        self.assertNotIn(synthetic_private_host, release_doc)
        self.assertNotIn("api" + "_key=", release_doc)
        self.assertIn("completeness feeds the handoff", release_doc)
        self.assertIn("final fail-fast completeness requires", release_doc)
        self.assertIn("Bundle Completeness Remediation", release_doc)
        self.assertIn("failed completeness reports include remediation guidance", readme)
        self.assertIn("selected profile", release_doc)
        self.assertIn("diagnostics references", release_doc)
        self.assertIn("fill-in reviewer decision log", release_doc)
        self.assertIn("clean/fail demo expectations", readme)
        self.assertIn("first-stop reviewer map", release_doc)
        self.assertIn("Sign release integrity artifacts", workflow)
        self.assertIn("REPOMORI_RELEASE_GPG_PRIVATE_KEY", workflow)
        self.assertIn("REPOMORI_RELEASE_GPG_PASSPHRASE", workflow)
        self.assertIn("REPOMORI_RELEASE_GPG_PUBLIC_KEY", workflow)
        self.assertIn("repomori-release-public-key.asc", workflow)
        self.assertIn("release public key fingerprint does not match signing key fingerprint", workflow)
        self.assertIn("for artifact in checksums.txt release-provenance.json sbom.spdx.json release-verify.json", workflow)
        self.assertIn("${artifact}.asc", workflow)
        self.assertIn(".repomori-release-candidate/*.asc", workflow)

    def test_workflow_contracts_for_publish_release(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        workflow = (repo_root / ".github/workflows/publish-release.yml").read_text(encoding="utf-8")
        publish_doc = (repo_root / "docs/release-publishing.md").read_text(encoding="utf-8")

        self.assertIn("name: publish-release", workflow)
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("release_policy", workflow)
        self.assertIn("tests/fixtures/release-policy-basic.json", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("Validate publish inputs", workflow)
        self.assertIn("release version mismatch", workflow)
        self.assertIn("python -m repomori release-check", workflow)
        self.assertIn("python -m pip wheel . --no-deps", workflow)
        self.assertIn("repomori contract-check --fixture", workflow)
        self.assertIn("repomori demo --out", workflow)
        self.assertIn("write_release_package_artifacts", workflow)
        self.assertIn('workflow="publish-release.yml"', workflow)
        self.assertIn("python -m repomori verify-release .repomori-release-candidate --json", workflow)
        self.assertIn("create_args=(release create", workflow)
        self.assertIn("--draft", workflow)
        self.assertIn("--target \"$GITHUB_SHA\"", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("--clobber", workflow)
        self.assertIn("Refusing to overwrite a published release", workflow)
        self.assertIn("Sign release integrity artifacts", workflow)
        self.assertIn("REPOMORI_RELEASE_GPG_PRIVATE_KEY", workflow)
        self.assertIn("REPOMORI_RELEASE_GPG_PASSPHRASE", workflow)
        self.assertIn("REPOMORI_RELEASE_GPG_PUBLIC_KEY", workflow)
        self.assertIn("repomori-release-public-key.asc", workflow)
        self.assertIn("release public key fingerprint does not match signing key fingerprint", workflow)
        self.assertIn("for artifact in checksums.txt release-provenance.json sbom.spdx.json release-verify.json", workflow)
        self.assertIn("${artifact}.asc", workflow)
        self.assertIn(".repomori-release-candidate/*.asc", workflow)
        self.assertIn("release-verify.json", workflow)
        self.assertIn("release-verify.md", workflow)
        self.assertIn("Write release evidence package", workflow)
        self.assertIn("python -m repomori release-evidence .repomori-release-candidate", workflow)
        self.assertIn("release-evidence.json", workflow)
        self.assertIn("release-evidence.md", workflow)
        self.assertIn("repomori.release_evidence.v1", workflow)
        self.assertIn("Verify release policy gate", workflow)
        self.assertIn("python -m repomori verify-release .repomori-release-candidate \\\n            --policy \"$RELEASE_POLICY\"", workflow)
        self.assertIn("release-verify-policy.json", workflow)
        self.assertIn("release-verify-policy.md", workflow)
        self.assertIn("release-verify-policy.*", workflow)
        self.assertIn("release-review-checklist.md", workflow)
        self.assertIn("release-artifact-index", workflow)
        self.assertIn("release-bundle-completeness", workflow)
        self.assertIn("release-review-handoff", workflow)
        self.assertIn("release-review-decision-log", workflow)
        self.assertIn("Provisional completeness", workflow)
        self.assertIn("Final fail-fast completeness", workflow)
        self.assertIn("repomori.release_policy.v1", workflow)
        self.assertIn("repomori.release_review_bundle.v1", workflow)
        self.assertIn("repomori.release_review_handoff.v1", workflow)
        self.assertIn("repomori.release_review_decision_log.v1", workflow)
        self.assertIn("repomori.release_review_privacy_guard.v1", workflow)
        self.assertIn("build_release_candidate_reviewer_handoff", workflow)
        self.assertIn("build_release_review_decision_log", workflow)
        self.assertIn("check_release_review_decision_log_privacy", workflow)
        self.assertIn("check_release_candidate_review_bundle", workflow)
        self.assertIn("format_release_candidate_artifact_index_markdown", workflow)
        self.assertIn("format_release_candidate_reviewer_handoff_markdown", workflow)
        self.assertIn("format_release_review_decision_log_markdown", workflow)
        self.assertIn("format_release_review_checklist_markdown", workflow)
        self.assertIn("require_handoff=False", workflow)
        self.assertIn("bundle[\"status\"] == \"pass\"", workflow)
        self.assertIn("handoff[\"status\"] == \"pass\"", workflow)
        self.assertIn("decision_log[\"status\"] == \"pass\"", workflow)
        self.assertIn("privacy_guard[\"status\"] == \"pass\"", workflow)
        self.assertIn("decision_log[\"privacy_guard\"][\"status\"] == \"pass\"", workflow)
        self.assertIn("issue_counts_by_code\"] == {}", workflow)
        self.assertIn("decision_log[\"reviewer_outcome\"][\"decision\"] == \"pending\"", workflow)
        self.assertIn("report[\"policy\"][\"review\"][\"decision\"] == \"reviewable\"", workflow)
        self.assertIn("report[\"policy\"][\"review\"][\"profile\"]", workflow)
        self.assertIn("Review decision: `reviewable`", workflow)
        self.assertIn("### Policy Profile Preflight", workflow)
        self.assertIn("docs/release-policy-selection.md", workflow)
        self.assertIn("docs/release-policy-matrix.md", workflow)
        self.assertIn("docs/release-policy.md#policy-diagnostics", workflow)
        self.assertIn("### Reviewer Next Steps", workflow)
        self.assertIn("## Final Reviewer Decision", workflow)
        self.assertIn("Privacy Guard Demo Preflight", workflow)
        self.assertIn("privacy-guard-demo --mode clean", workflow)
        self.assertIn("privacy-guard-demo --mode fail", workflow)
        self.assertIn("Privacy-guard demo clean/fail expectations were run", workflow)
        self.assertIn("Final reviewer decision: `pending`", workflow)
        self.assertIn("RepoMori Release Candidate Artifact Index", workflow)
        self.assertIn("Diagnostics References", workflow)
        self.assertIn("Public-Safety And Privacy Confirmations", workflow)
        self.assertIn("Privacy Guard", workflow)
        self.assertIn("release-candidate.json", workflow)
        self.assertIn("release-candidate.md", workflow)
        self.assertIn("include-hidden-files: true", workflow)
        self.assertIn("Draft Release Assets", publish_doc)
        self.assertIn("release-bundle-completeness.json", publish_doc)
        self.assertIn("release-review-handoff.md", publish_doc)
        self.assertIn("release-review-decision-log.md", publish_doc)
        self.assertIn("Existing published releases are never overwritten.", publish_doc)
        self.assertIn("REPOMORI_RELEASE_GPG_PRIVATE_KEY", publish_doc)
        self.assertIn("REPOMORI_RELEASE_GPG_PUBLIC_KEY", publish_doc)

    def test_write_release_package_artifacts_outputs_integrity_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            wheel = dist / "repomori-0.2.0-py3-none-any.whl"
            source = dist / "repomori-0.2.0-source.zip"
            wheel.write_bytes(b"wheel-bytes")
            source.write_bytes(b"source-bytes")

            manifest = write_release_package_artifacts(
                root,
                version="0.2.0",
                commit="abc123",
                ref="main",
                run_id="42",
                repository="Martin123132/RepoMori",
                generated_at=1700000000,
            )

            self.assertEqual(manifest["schema_version"], "repomori.release_candidate.v1")
            self.assertEqual(manifest["integrity"]["checksums"]["path"], "checksums.txt")
            self.assertEqual(manifest["integrity"]["provenance"]["path"], "release-provenance.json")
            self.assertEqual(manifest["integrity"]["sbom"]["path"], "sbom.spdx.json")
            self.assertEqual(json.loads((root / "release-candidate.json").read_text(encoding="utf-8")), manifest)

            provenance = json.loads((root / "release-provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance["schema_version"], "repomori.release_provenance.v1")
            self.assertEqual(provenance["version"], "0.2.0")
            self.assertEqual(provenance["repository"], "Martin123132/RepoMori")
            provenance_paths = {artifact["path"] for artifact in provenance["artifacts"]}
            self.assertEqual(
                provenance_paths,
                {
                    "dist/repomori-0.2.0-py3-none-any.whl",
                    "dist/repomori-0.2.0-source.zip",
                    "sbom.spdx.json",
                },
            )

            sbom = json.loads((root / "sbom.spdx.json").read_text(encoding="utf-8"))
            self.assertEqual(sbom["spdxVersion"], "SPDX-2.3")
            self.assertTrue(any(package["name"] == "repomori" for package in sbom["packages"]))
            self.assertIn("LicenseRef-PolyForm-Noncommercial-1.0.0", json.dumps(sbom))

            checksum_lines = (root / "checksums.txt").read_text(encoding="utf-8").splitlines()
            checksum_map = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in checksum_lines}
            expected_paths = {
                "dist/repomori-0.2.0-py3-none-any.whl",
                "dist/repomori-0.2.0-source.zip",
                "sbom.spdx.json",
                "release-provenance.json",
            }
            self.assertEqual(set(checksum_map), expected_paths)
            for relative, digest in checksum_map.items():
                self.assertEqual(digest, hashlib.sha256((root / relative).read_bytes()).hexdigest())

            manifest_md = (root / "release-candidate.md").read_text(encoding="utf-8")
            self.assertIn("## Integrity", manifest_md)
            self.assertIn("checksums.txt", manifest_md)

    def test_verify_release_package_passes_for_integrity_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(
                root,
                version="0.2.0",
                commit="abc123",
                ref="main",
                run_id="42",
                repository="Martin123132/RepoMori",
                generated_at=1700000000,
            )

            report = verify_release_package(root)

            self.assertEqual(report["schema_version"], "repomori.release_verify.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["checked_files"], 5)
            self.assertEqual(report["summary"]["manifest_version"], "0.2.0")
            self.assertTrue(report["summary"]["wheel_present"])
            self.assertTrue(report["summary"]["source_archive_present"])
            self.assertEqual(report["summary"]["mismatched_files"], 0)
            self.assertTrue(any(item["path"] == "checksums.txt" for item in report["artifacts"]))

            markdown = format_release_verify_markdown(report)
            self.assertIn("# RepoMori Release Verification", markdown)
            self.assertIn("checksums.txt", markdown)
            self.assertIn("release-provenance.json", markdown)
            self.assertIn("sbom.spdx.json", markdown)

    def test_verify_release_package_policy_passes_with_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(
                root,
                version="0.2.0",
                commit="abc123",
                ref="main",
                run_id="42",
                repository="Martin123132/RepoMori",
                generated_at=1700000000,
            )
            verify_report = verify_release_package(root)
            (root / "release-verify.json").write_text(json.dumps(verify_report, indent=2), encoding="utf-8")
            (root / "release-verify.md").write_text(format_release_verify_markdown(verify_report), encoding="utf-8")
            release_check = tmp_path / "release-check.json"
            release_check.write_text(
                json.dumps(
                    {
                        "schema_version": "repomori.release_check.v1",
                        "status": "pass",
                        "summary": {"failed_checks": [], "scan_findings": 0},
                    }
                ),
                encoding="utf-8",
            )
            build_release_evidence(root, release_check=release_check, out_dir=root)
            policy = {
                "schema_version": "repomori.release_policy.v1",
                "require": {
                    "checksums": True,
                    "provenance": True,
                    "sbom": True,
                    "release_verify_report": True,
                    "release_evidence": True,
                    "release_check": True,
                },
                "allowed_statuses": {
                    "release_verify": ["pass"],
                    "release_evidence": ["pass"],
                    "release_check": ["pass"],
                    "signatures": ["unsigned"],
                },
                "required_schemas": {
                    "release_candidate": "repomori.release_candidate.v1",
                    "provenance": "repomori.release_provenance.v1",
                    "sbom": "SPDX-2.3",
                    "release_verify": "repomori.release_verify.v1",
                    "release_evidence": "repomori.release_evidence.v1",
                    "release_check": "repomori.release_check.v1",
                },
                "max_warnings": 0,
                "max_errors": 0,
            }

            report = verify_release_package(root, policy=policy)

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["policy"]["schema_version"], "repomori.release_policy.v1")
            self.assertEqual(report["policy"]["status"], "pass")
            self.assertEqual(report["summary"]["policy_status"], "pass")
            self.assertTrue(any(check["id"] == "release_policy" and check["status"] == "pass" for check in report["checks"]))
            direct = evaluate_release_policy(root, verify_report, policy)
            self.assertEqual(direct["status"], "pass")

    def test_verify_release_package_policy_fails_missing_required_evidence_and_signatures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(root, version="0.2.0", generated_at=1700000000)
            policy = {
                "schema_version": "repomori.release_policy.v1",
                "require": {
                    "release_evidence": True,
                    "release_check": True,
                    "signatures": True,
                    "public_key": True,
                },
                "allowed_statuses": {
                    "release_verify": ["pass"],
                    "release_evidence": ["pass"],
                    "signatures": ["signed"],
                },
            }

            report = verify_release_package(root, policy=policy)

            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["policy"]["status"], "fail")
            codes = {violation["code"] for violation in report["policy"]["violations"]}
            self.assertIn("release_policy_required_file_missing", codes)
            self.assertIn("release_policy_required_report_missing", codes)
            self.assertIn("release_policy_signatures_missing", codes)
            self.assertIn("release_policy_status_not_allowed", codes)

    def test_release_policy_example_fixtures_are_documented_and_valid(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        release_policy_doc = (repo_root / "docs/release-policy.md").read_text(encoding="utf-8")
        selection_doc = (repo_root / "docs/release-policy-selection.md").read_text(encoding="utf-8")
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        fixture_names = (
            "release-policy-basic.json",
            "release-policy-dev-unsigned.json",
            "release-policy-enterprise-signed.json",
            "release-policy-strict-no-warnings.json",
        )

        for name in fixture_names:
            with self.subTest(name=name):
                policy = json.loads((repo_root / "tests" / "fixtures" / name).read_text(encoding="utf-8"))
                self.assertEqual(policy["schema_version"], "repomori.release_policy.v1")
                self.assertIsInstance(policy.get("profile"), str)
                self.assertIsInstance(policy.get("description"), str)
                self.assertIn(name, release_policy_doc)
                self.assertIn(name, selection_doc)
                self.assertIn(name, readme)

    def test_release_policy_selection_guide_links_matrix_and_diagnostics(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        selection_doc = (repo_root / "docs/release-policy-selection.md").read_text(encoding="utf-8")
        release_policy_doc = (repo_root / "docs/release-policy.md").read_text(encoding="utf-8")
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        enterprise = (repo_root / "docs/enterprise-readiness.md").read_text(encoding="utf-8")

        self.assertIn("release-policy-selection.md", readme)
        self.assertIn("release-policy-selection.md", enterprise)
        self.assertIn("release-policy-selection.md", release_policy_doc)
        self.assertIn("release-policy-matrix.md", selection_doc)
        self.assertIn("release-policy.md#policy-diagnostics", selection_doc)
        self.assertIn("## Policy Diagnostics", release_policy_doc)
        for phrase in (
            "When in doubt, start with `release-policy-basic.json`",
            "OSS/dev candidate before signing is configured",
            "enterprise signed review should use `enterprise_signed`",
            "strict_no_warnings",
        ):
            self.assertIn(phrase, selection_doc)

    def test_release_policy_dev_unsigned_example_accepts_unsigned_package(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-dev-unsigned.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), signed=False)

            report = verify_release_package(root, policy=policy)

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["policy"]["status"], "pass")
            self.assertEqual(report["policy"]["profile"], "dev_unsigned")
            self.assertEqual(report["policy"]["review"]["decision"], "reviewable")
            self.assertIn("Unsigned packages are allowed", report["policy"]["review"]["next_steps"][1])
            self.assertEqual(report["policy"]["diagnostics"]["outcome"], "policy_passed")
            self.assertEqual(report["policy"]["diagnostics"]["reasons"], [])
            self.assertEqual(report["policy"]["summary"]["signature_status"], "unsigned")

            markdown = format_release_verify_markdown(report)
            self.assertIn("Profile: `dev_unsigned`", markdown)
            self.assertIn("Review decision: `reviewable`", markdown)
            self.assertIn("### Policy Profile Preflight", markdown)
            self.assertIn("docs/release-policy-selection.md", markdown)
            self.assertIn("docs/release-policy-matrix.md", markdown)
            self.assertIn("docs/release-policy.md#policy-diagnostics", markdown)
            self.assertIn("start with `basic` or `dev_unsigned`", markdown)
            self.assertIn("use `enterprise_signed` only when detached signatures", markdown)
            self.assertIn("Policy gate passed", markdown)

    def test_release_review_checklist_markdown_records_decision_inputs(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-dev-unsigned.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), signed=False)
            report = verify_release_package(root, policy=policy)
            evidence = json.loads((root / "release-evidence.json").read_text(encoding="utf-8"))

            markdown = format_release_review_checklist_markdown(report, evidence)

            self.assertIn("# RepoMori Release Candidate Review Checklist", markdown)
            self.assertIn("Selected profile: `dev_unsigned`", markdown)
            self.assertIn("Policy outcome: `policy_passed`", markdown)
            self.assertIn("Review decision: `reviewable`", markdown)
            self.assertIn("## Artifact Hash And Provenance Checks", markdown)
            self.assertIn("Checksum file hashes: `pass`", markdown)
            self.assertIn("Provenance artifacts: `pass`", markdown)
            self.assertIn("No policy diagnostic reasons were reported.", markdown)
            self.assertIn("## Privacy Guard Demo Preflight", markdown)
            self.assertIn("privacy-guard-demo --mode clean --json", markdown)
            self.assertIn("privacy-guard-demo --mode fail --json", markdown)
            self.assertIn("Clean expectation: top-level `status` is `pass`", markdown)
            self.assertIn("Failing expectation: top-level `status` is `pass`", markdown)
            self.assertIn("only redacted category/count summaries are shown", markdown)
            self.assertIn("- [ ] Selected policy profile matches the release situation", markdown)
            self.assertIn("Privacy-guard demo clean/fail expectations were run", markdown)
            self.assertIn("Final reviewer decision: `pending`", markdown)

    def test_release_candidate_artifact_index_markdown_lists_reviewer_artifacts(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-dev-unsigned.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), signed=False)
            report = verify_release_package(root, policy=policy)
            evidence = json.loads((root / "release-evidence.json").read_text(encoding="utf-8"))

            markdown = format_release_candidate_artifact_index_markdown(report, evidence)

            self.assertIn("# RepoMori Release Candidate Artifact Index", markdown)
            self.assertIn("Selected policy profile: `dev_unsigned`", markdown)
            self.assertIn("Policy outcome: `policy_passed`", markdown)
            self.assertIn("release-verify-policy.md", markdown)
            self.assertIn("release-review-checklist.md", markdown)
            self.assertIn("release-artifact-index.md", markdown)
            self.assertIn("release-review-handoff.json", markdown)
            self.assertIn("release-review-handoff.md", markdown)
            self.assertIn("release-bundle-completeness.json", markdown)
            self.assertIn("release-review-decision-log.json", markdown)
            self.assertIn("release-review-decision-log.md", markdown)
            self.assertIn("release-evidence.json", markdown)
            self.assertIn("checksums.txt", markdown)
            self.assertIn("release-provenance.json", markdown)
            self.assertIn("## Generation Order", markdown)
            self.assertIn("Provisional completeness runs with `require_handoff=False`", markdown)
            self.assertIn("Final fail-fast completeness writes `release-bundle-completeness.json`", markdown)
            self.assertIn("record reviewed artifacts, gate statuses, public-safety confirmations", markdown)
            self.assertIn("docs/release-policy-selection.md", markdown)
            self.assertIn("docs/release-policy-matrix.md", markdown)
            self.assertIn("docs/release-policy.md#policy-diagnostics", markdown)

    def test_release_candidate_review_bundle_completeness_passes_and_fails_fast(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-dev-unsigned.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), signed=False)
            report = verify_release_package(root, policy=policy)
            evidence = json.loads((root / "release-evidence.json").read_text(encoding="utf-8"))
            (root / "release-verify-policy.json").write_text(
                json.dumps(report, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "release-verify-policy.md").write_text(
                format_release_verify_markdown(report),
                encoding="utf-8",
            )
            (root / "release-review-checklist.md").write_text(
                format_release_review_checklist_markdown(report, evidence),
                encoding="utf-8",
            )
            (root / "release-artifact-index.md").write_text(
                format_release_candidate_artifact_index_markdown(report, evidence),
                encoding="utf-8",
            )
            provisional = check_release_candidate_review_bundle(root, require_handoff=False)
            handoff = build_release_candidate_reviewer_handoff(report, evidence, provisional)
            (root / "release-review-handoff.json").write_text(
                json.dumps(handoff, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "release-review-handoff.md").write_text(
                format_release_candidate_reviewer_handoff_markdown(handoff),
                encoding="utf-8",
            )

            complete = check_release_candidate_review_bundle(root)

            self.assertEqual(complete["schema_version"], "repomori.release_review_bundle.v1")
            self.assertEqual(complete["status"], "pass")
            self.assertEqual(complete["summary"]["selected_profile"], "dev_unsigned")
            self.assertEqual(complete["summary"]["policy_outcome"], "policy_passed")
            self.assertEqual(complete["summary"]["remediation_count"], 0)
            self.assertEqual(complete["errors"], [])
            self.assertEqual(complete["remediation"], [])

            (root / "release-review-handoff.md").unlink()
            missing_handoff = check_release_candidate_review_bundle(root)
            handoff_error = next(item for item in missing_handoff["errors"] if item["code"] == "artifact:release-review-handoff.md")
            self.assertEqual(handoff_error["remediation"]["category"], "reviewer handoff")
            self.assertIn("Regenerate release-review-handoff.json", handoff_error["remediation"]["next_step"])
            (root / "release-review-handoff.md").write_text(
                format_release_candidate_reviewer_handoff_markdown(handoff),
                encoding="utf-8",
            )

            stale_handoff = dict(handoff)
            stale_handoff["profile"] = "old_profile"
            (root / "release-review-handoff.json").write_text(
                json.dumps(stale_handoff, indent=2) + "\n",
                encoding="utf-8",
            )
            stale = check_release_candidate_review_bundle(root)
            stale_error = next(item for item in stale["errors"] if item["code"] == "content:handoff_profile")
            self.assertEqual(stale_error["remediation"]["category"], "reviewer handoff")
            self.assertEqual(stale_error["expected"], "dev_unsigned")
            self.assertEqual(stale_error["actual"], "old_profile")
            (root / "release-review-handoff.json").write_text(
                json.dumps(handoff, indent=2) + "\n",
                encoding="utf-8",
            )

            (root / "release-artifact-index.md").unlink()
            failed = check_release_candidate_review_bundle(root)

            self.assertEqual(failed["status"], "fail")
            self.assertGreater(failed["summary"]["error_count"], 0)
            artifact_error = next(item for item in failed["errors"] if item["code"] == "artifact:release-artifact-index.md")
            self.assertEqual(artifact_error["remediation"]["category"], "artifact index and diagnostics references")
            self.assertIn("Regenerate release-artifact-index.md", artifact_error["remediation"]["next_step"])
            self.assertGreater(failed["summary"]["remediation_count"], 0)

            (root / "release-artifact-index.md").write_text("# RepoMori Release Candidate Artifact Index\n", encoding="utf-8")
            diagnostics_failed = check_release_candidate_review_bundle(root)
            diagnostics_error = next(item for item in diagnostics_failed["errors"] if item["code"] == "content:artifact_index")
            self.assertEqual(
                diagnostics_error["remediation"]["category"],
                "artifact index and diagnostics references",
            )
            self.assertIn("docs/release-policy.md#policy-diagnostics", diagnostics_error["remediation"]["docs"])

            (root / "release-artifact-index.md").write_text(
                format_release_candidate_artifact_index_markdown(report, evidence),
                encoding="utf-8",
            )
            (root / "release-review-handoff.json").write_text(
                json.dumps(handoff, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "release-review-handoff.md").write_text(
                format_release_candidate_reviewer_handoff_markdown(handoff),
                encoding="utf-8",
            )
            policy_payload = json.loads((root / "release-verify-policy.json").read_text(encoding="utf-8"))
            policy_payload["policy"].pop("profile", None)
            (root / "release-verify-policy.json").write_text(json.dumps(policy_payload, indent=2) + "\n", encoding="utf-8")
            profile_failed = check_release_candidate_review_bundle(root)
            profile_error = next(item for item in profile_failed["errors"] if item["code"] == "content:selected_profile")
            self.assertEqual(profile_error["remediation"]["category"], "selected profile")
            self.assertIn("release-policy-selection.md", profile_error["remediation"]["docs"][0])

    def test_release_candidate_reviewer_handoff_summarizes_bundle_review(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-dev-unsigned.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), signed=False)
            report = verify_release_package(root, policy=policy)
            evidence = json.loads((root / "release-evidence.json").read_text(encoding="utf-8"))
            (root / "release-verify-policy.json").write_text(
                json.dumps(report, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "release-verify-policy.md").write_text(
                format_release_verify_markdown(report),
                encoding="utf-8",
            )
            (root / "release-review-checklist.md").write_text(
                format_release_review_checklist_markdown(report, evidence),
                encoding="utf-8",
            )
            (root / "release-artifact-index.md").write_text(
                format_release_candidate_artifact_index_markdown(report, evidence),
                encoding="utf-8",
            )
            bundle = check_release_candidate_review_bundle(root, require_handoff=False)

            handoff = build_release_candidate_reviewer_handoff(report, evidence, bundle)
            markdown = format_release_candidate_reviewer_handoff_markdown(handoff)

            self.assertEqual(handoff["schema_version"], "repomori.release_review_handoff.v1")
            self.assertEqual(handoff["status"], "pass")
            self.assertEqual(handoff["profile"], "dev_unsigned")
            self.assertEqual(handoff["policy"]["outcome"], "policy_passed")
            self.assertEqual(handoff["completeness"]["status"], "pass")
            self.assertEqual(handoff["remediation"], [])
            artifact_paths = {artifact["path"] for artifact in handoff["artifacts"]}
            self.assertIn("release-artifact-index.md", artifact_paths)
            self.assertIn("release-review-checklist.md", artifact_paths)
            self.assertIn("release-bundle-completeness.json", artifact_paths)
            self.assertIn("docs/release-policy.md#policy-diagnostics", handoff["diagnostics_references"])
            self.assertIn("# RepoMori Release Candidate Reviewer Handoff", markdown)
            self.assertIn("Selected policy profile: `dev_unsigned`", markdown)
            self.assertIn("Policy outcome: `policy_passed`", markdown)
            self.assertIn("Completeness status: `pass`", markdown)
            self.assertIn("release-artifact-index.md", markdown)
            self.assertIn("release-review-checklist.md", markdown)
            self.assertIn("No bundle completeness remediation is currently reported.", markdown)

            (root / "release-artifact-index.md").unlink()
            failed_bundle = check_release_candidate_review_bundle(root)
            failed_handoff = build_release_candidate_reviewer_handoff(report, evidence, failed_bundle)
            failed_markdown = format_release_candidate_reviewer_handoff_markdown(failed_handoff)

            self.assertEqual(failed_handoff["status"], "fail")
            self.assertGreater(failed_handoff["completeness"]["remediation_count"], 0)
            self.assertTrue(failed_handoff["remediation"])
            self.assertIn("Resolve the remediation list", failed_handoff["next_steps"][0])
            self.assertIn("Regenerate release-artifact-index.md", failed_markdown)

    def test_release_review_decision_log_records_evidence_trail(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-dev-unsigned.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), signed=False)
            report = verify_release_package(root, policy=policy)
            evidence = json.loads((root / "release-evidence.json").read_text(encoding="utf-8"))
            (root / "release-verify-policy.json").write_text(
                json.dumps(report, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "release-verify-policy.md").write_text(
                format_release_verify_markdown(report),
                encoding="utf-8",
            )
            (root / "release-review-checklist.md").write_text(
                format_release_review_checklist_markdown(report, evidence),
                encoding="utf-8",
            )
            (root / "release-artifact-index.md").write_text(
                format_release_candidate_artifact_index_markdown(report, evidence),
                encoding="utf-8",
            )
            provisional = check_release_candidate_review_bundle(root, require_handoff=False)
            handoff = build_release_candidate_reviewer_handoff(report, evidence, provisional)
            (root / "release-review-handoff.json").write_text(
                json.dumps(handoff, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "release-review-handoff.md").write_text(
                format_release_candidate_reviewer_handoff_markdown(handoff),
                encoding="utf-8",
            )
            bundle = check_release_candidate_review_bundle(root)

            decision_log = build_release_review_decision_log(report, evidence, bundle, handoff)
            markdown = format_release_review_decision_log_markdown(decision_log)

            self.assertEqual(decision_log["schema_version"], "repomori.release_review_decision_log.v1")
            self.assertEqual(decision_log["status"], "pass")
            self.assertEqual(decision_log["summary"]["selected_profile"], "dev_unsigned")
            self.assertEqual(decision_log["summary"]["policy_outcome"], "policy_passed")
            self.assertEqual(decision_log["summary"]["completeness_status"], "pass")
            self.assertEqual(decision_log["summary"]["handoff_status"], "pass")
            self.assertEqual(decision_log["summary"]["public_safety_status"], "pass")
            self.assertEqual(decision_log["summary"]["privacy_guard_status"], "pass")
            self.assertEqual(decision_log["privacy_guard"]["schema_version"], "repomori.release_review_privacy_guard.v1")
            self.assertEqual(decision_log["privacy_guard"]["status"], "pass")
            self.assertEqual(decision_log["privacy_guard"]["summary"]["failed_check_count"], 0)
            self.assertEqual(decision_log["privacy_guard"]["summary"]["issue_counts_by_code"], {})
            self.assertEqual(decision_log["reviewer_outcome"]["decision"], "pending")
            artifact_paths = {artifact["path"] for artifact in decision_log["reviewed_artifacts"]}
            self.assertIn("release-review-handoff.md", artifact_paths)
            self.assertIn("release-bundle-completeness.json", artifact_paths)
            self.assertIn("release-artifact-index.md", artifact_paths)
            gate_statuses = {gate["id"]: gate["status"] for gate in decision_log["gate_results"]}
            self.assertEqual(gate_statuses["bundle_completeness"], "pass")
            self.assertEqual(gate_statuses["reviewer_handoff"], "pass")
            self.assertEqual(gate_statuses["public_safety_privacy"], "pass")
            confirmation_statuses = {
                item["id"]: item["status"]
                for item in decision_log["public_safety"]["confirmations"]
            }
            self.assertEqual(confirmation_statuses["active_scan_findings"], "pass")
            self.assertEqual(confirmation_statuses["decision_log_generation"], "pass")
            self.assertIn("# RepoMori Release Review Decision Log", markdown)
            self.assertIn("Public-Safety And Privacy Confirmations", markdown)
            self.assertIn("Privacy Guard", markdown)
            self.assertIn("No local absolute paths, temp directories, secret-like values", markdown)
            self.assertIn("release-review-handoff.md", markdown)
            self.assertIn("release-bundle-completeness.json", markdown)
            self.assertIn("Final reviewer decision: `pending`", markdown)

    def test_release_review_decision_log_privacy_guard_blocks_leaks(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-dev-unsigned.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), signed=False)
            report = verify_release_package(root, policy=policy)
            evidence = json.loads((root / "release-evidence.json").read_text(encoding="utf-8"))
            (root / "release-verify-policy.json").write_text(
                json.dumps(report, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "release-verify-policy.md").write_text(
                format_release_verify_markdown(report),
                encoding="utf-8",
            )
            (root / "release-review-checklist.md").write_text(
                format_release_review_checklist_markdown(report, evidence),
                encoding="utf-8",
            )
            (root / "release-artifact-index.md").write_text(
                format_release_candidate_artifact_index_markdown(report, evidence),
                encoding="utf-8",
            )
            provisional = check_release_candidate_review_bundle(root, require_handoff=False)
            handoff = build_release_candidate_reviewer_handoff(report, evidence, provisional)
            (root / "release-review-handoff.json").write_text(
                json.dumps(handoff, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "release-review-handoff.md").write_text(
                format_release_candidate_reviewer_handoff_markdown(handoff),
                encoding="utf-8",
            )
            bundle = check_release_candidate_review_bundle(root)
            clean = build_release_review_decision_log(report, evidence, bundle, handoff)
            clean_markdown = format_release_review_decision_log_markdown(clean)

            clean_guard = check_release_review_decision_log_privacy(clean, clean_markdown)

            self.assertEqual(clean_guard["status"], "pass")
            leaky = json.loads(json.dumps(clean))
            fake_secret_value = "s" + "k-" + "thisisnotrealbutlookslikesecret"
            local_path = "C:" + "\\Users\\ollet\\AppData\\Local\\Temp\\private.txt "
            private_url = "https://" + "internal" + ".example" + ".local/private"
            secret_assignment = "api" + "_key=" + fake_secret_value
            leaky["reviewed_artifacts"][0]["evidence_point"] = local_path + secret_assignment
            leaky["reports"] = {"release_check": {"findings": ["raw evidence dump"]}}
            leaky_markdown = clean_markdown + f"\n{private_url}\n"

            guard = check_release_review_decision_log_privacy(leaky, leaky_markdown)

            self.assertEqual(guard["schema_version"], "repomori.release_review_privacy_guard.v1")
            self.assertEqual(guard["status"], "fail")
            self.assertEqual(guard["summary"]["failed_check_count"], 5)
            codes = {issue["code"] for issue in guard["issues"]}
            self.assertIn("local_absolute_path", codes)
            self.assertIn("temp_directory", codes)
            self.assertIn("secret_like_value", codes)
            self.assertIn("private_url", codes)
            self.assertIn("raw_dump_key", codes)
            counts = guard["summary"]["issue_counts_by_code"]
            self.assertGreaterEqual(counts["local_absolute_path"], 1)
            self.assertGreaterEqual(counts["temp_directory"], 1)
            self.assertGreaterEqual(counts["secret_like_value"], 1)
            self.assertGreaterEqual(counts["private_url"], 1)
            self.assertGreaterEqual(counts["raw_dump_key"], 1)
            serialized_guard = json.dumps(guard)
            self.assertNotIn(fake_secret_value, serialized_guard)
            self.assertNotIn(local_path, serialized_guard)
            self.assertNotIn(private_url, serialized_guard)
            redacted_failure_log = json.loads(json.dumps(clean))
            redacted_failure_log["privacy_guard"] = guard
            redacted_failure_log["summary"]["privacy_guard_status"] = "fail"
            redacted_markdown = format_release_review_decision_log_markdown(redacted_failure_log)
            self.assertIn("Issue Counts By Category", redacted_markdown)
            self.assertIn("`secret_like_value`", redacted_markdown)
            self.assertIn("`raw_dump_key`", redacted_markdown)
            self.assertNotIn(fake_secret_value, redacted_markdown)
            self.assertNotIn(local_path, redacted_markdown)
            self.assertNotIn(private_url, redacted_markdown)

    def test_release_review_privacy_guard_demo_reports_clean_and_redacted_failure(self) -> None:
        clean = build_release_review_privacy_guard_demo(mode="clean")

        self.assertEqual(clean["schema_version"], "repomori.release_review_privacy_guard_demo.v1")
        self.assertEqual(clean["status"], "pass")
        self.assertEqual(clean["expected_guard_status"], "pass")
        self.assertEqual(clean["privacy_guard"]["status"], "pass")
        self.assertEqual(clean["summary"]["issue_counts_by_code"], {})
        clean_markdown = format_release_review_privacy_guard_demo_markdown(clean)
        self.assertIn("# RepoMori Release Review Privacy Guard Demo", clean_markdown)
        self.assertIn("Mode: `clean`", clean_markdown)

        failing = build_release_review_privacy_guard_demo(mode="fail")

        self.assertEqual(failing["status"], "pass")
        self.assertEqual(failing["expected_guard_status"], "fail")
        self.assertEqual(failing["privacy_guard"]["status"], "fail")
        counts = failing["summary"]["issue_counts_by_code"]
        self.assertGreaterEqual(counts["local_absolute_path"], 1)
        self.assertGreaterEqual(counts["temp_directory"], 1)
        self.assertGreaterEqual(counts["secret_like_value"], 1)
        self.assertGreaterEqual(counts["private_url"], 1)
        self.assertGreaterEqual(counts["proprietary_marker"], 1)
        self.assertGreaterEqual(counts["raw_dump_key"], 1)
        failing_markdown = format_release_review_privacy_guard_demo_markdown(failing)
        self.assertIn("Mode: `fail`", failing_markdown)
        self.assertIn("Issue Counts By Category", failing_markdown)
        self.assertIn("`secret_like_value`", failing_markdown)
        self.assertIn("`raw_dump_key`", failing_markdown)

        serialized = json.dumps(failing, sort_keys=True)
        raw_values = [
            "C:" + "\\" + "Users" + "\\" + "reviewer" + "\\" + "Temp" + "\\" + "SYNTHETIC_PATH.txt",
            "api" + "_key=" + "s" + "k-" + "syntheticplaceholdernotreal",
            "https://" + "internal" + ".example" + ".local/synthetic",
            "proprietary" + " source",
            "SYNTHETIC_RAW_DUMP_PLACEHOLDER",
        ]
        for raw_value in raw_values:
            self.assertNotIn(raw_value, serialized)
            self.assertNotIn(raw_value, failing_markdown)

    def test_cli_privacy_guard_demo_json_and_markdown_are_safe(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        output = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "repomori",
                "privacy-guard-demo",
                "--mode",
                "fail",
                "--json",
            ],
            cwd=repo,
            text=True,
        )
        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], "repomori.release_review_privacy_guard_demo.v1")
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["privacy_guard"]["status"], "fail")
        self.assertGreaterEqual(payload["summary"]["issue_counts_by_code"]["secret_like_value"], 1)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "privacy-guard-demo.md"
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "privacy-guard-demo",
                    "--mode",
                    "fail",
                    "--format",
                    "markdown",
                    "--out",
                    str(out),
                ],
                cwd=repo,
            )
            markdown = out.read_text(encoding="utf-8")

        self.assertIn("# RepoMori Release Review Privacy Guard Demo", markdown)
        self.assertIn("Observed guard status: `fail`", markdown)
        secret_assignment = "api" + "_key=" + "s" + "k-" + "syntheticplaceholdernotreal"
        private_url = "https://" + "internal" + ".example" + ".local/synthetic"
        local_path = "C:" + "\\" + "Users" + "\\" + "reviewer" + "\\" + "Temp" + "\\" + "SYNTHETIC_PATH.txt"
        self.assertNotIn(secret_assignment, output)
        self.assertNotIn(secret_assignment, markdown)
        self.assertNotIn(private_url, output)
        self.assertNotIn(private_url, markdown)
        self.assertNotIn(local_path, output)
        self.assertNotIn(local_path, markdown)

    def test_release_policy_enterprise_signed_example_requires_and_accepts_signatures(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-enterprise-signed.json"
        with tempfile.TemporaryDirectory() as tmp:
            unsigned_root = self._release_policy_package(Path(tmp) / "unsigned", signed=False)

            unsigned_report = verify_release_package(unsigned_root, policy=policy)

            self.assertEqual(unsigned_report["status"], "fail")
            self.assertEqual(unsigned_report["policy"]["profile"], "enterprise_signed")
            self.assertEqual(unsigned_report["policy"]["review"]["decision"], "blocked")
            self.assertEqual(unsigned_report["policy"]["diagnostics"]["outcome"], "signature_requirements_not_met")
            unsigned_codes = {violation["code"] for violation in unsigned_report["policy"]["violations"]}
            self.assertIn("release_policy_required_file_missing", unsigned_codes)
            self.assertIn("release_policy_signatures_missing", unsigned_codes)
            self.assertIn("release_policy_status_not_allowed", unsigned_codes)
            diagnostic_codes = {reason["code"] for reason in unsigned_report["policy"]["diagnostics"]["reasons"]}
            self.assertIn("release_policy_signatures_missing", diagnostic_codes)
            self.assertIn("release_policy_status_not_allowed", diagnostic_codes)

            unsigned_markdown = format_release_verify_markdown(unsigned_report)
            self.assertIn("Profile: `enterprise_signed`", unsigned_markdown)
            self.assertIn("Review decision: `blocked`", unsigned_markdown)
            self.assertIn("do not approve", unsigned_markdown)
            self.assertIn("### Policy Diagnostics", unsigned_markdown)
            self.assertIn("signature_requirements_not_met", unsigned_markdown)
            self.assertIn("Configure release signing", unsigned_markdown)

            signed_root = self._release_policy_package(Path(tmp) / "signed", signed=True)
            signed_report = verify_release_package(signed_root, policy=policy)

            self.assertEqual(signed_report["status"], "pass")
            self.assertEqual(signed_report["policy"]["status"], "pass")
            self.assertEqual(signed_report["policy"]["review"]["decision"], "reviewable")
            self.assertEqual(signed_report["policy"]["summary"]["signature_status"], "signed")
            self.assertEqual(signed_report["policy"]["summary"]["public_key_status"], "present")

    def test_release_policy_strict_no_warnings_example_blocks_warning_drift(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-strict-no-warnings.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), evidence_warning_count=1)

            report = verify_release_package(root, policy=policy)

            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["policy"]["status"], "fail")
            self.assertEqual(report["policy"]["profile"], "strict_no_warnings")
            self.assertEqual(report["policy"]["review"]["decision"], "blocked")
            self.assertEqual(report["policy"]["diagnostics"]["outcome"], "warning_or_error_threshold_exceeded")
            violations = report["policy"]["violations"]
            self.assertTrue(
                any(
                    item["code"] == "release_policy_threshold_exceeded"
                    and item["expected"] == 0
                    and item["actual"] == 1
                    for item in violations
                )
            )
            markdown = format_release_verify_markdown(report)
            self.assertIn("### Policy Diagnostics", markdown)
            self.assertIn("warning_or_error_threshold_exceeded", markdown)
            self.assertIn("Resolve the warnings or errors", markdown)

    def test_release_policy_profile_matrix_matches_documented_outcomes(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        matrix_doc = (repo_root / "docs/release-policy-matrix.md").read_text(encoding="utf-8")
        policies = {
            "basic": repo_root / "tests/fixtures/release-policy-basic.json",
            "dev_unsigned": repo_root / "tests/fixtures/release-policy-dev-unsigned.json",
            "enterprise_signed": repo_root / "tests/fixtures/release-policy-enterprise-signed.json",
            "strict_no_warnings": repo_root / "tests/fixtures/release-policy-strict-no-warnings.json",
        }
        expected = {
            ("unsigned_clean", "basic"): ("reviewable", "policy_passed", set()),
            ("unsigned_clean", "dev_unsigned"): ("reviewable", "policy_passed", set()),
            (
                "unsigned_clean",
                "enterprise_signed",
            ): (
                "blocked",
                "signature_requirements_not_met",
                {
                    "release_policy_required_file_missing",
                    "release_policy_signatures_missing",
                    "release_policy_status_not_allowed",
                },
            ),
            ("unsigned_clean", "strict_no_warnings"): ("reviewable", "policy_passed", set()),
            ("signed_clean", "basic"): ("reviewable", "policy_passed", set()),
            ("signed_clean", "dev_unsigned"): ("reviewable", "policy_passed", set()),
            ("signed_clean", "enterprise_signed"): ("reviewable", "policy_passed", set()),
            ("signed_clean", "strict_no_warnings"): ("reviewable", "policy_passed", set()),
            ("signed_warning", "basic"): ("reviewable", "policy_passed", set()),
            ("signed_warning", "dev_unsigned"): ("reviewable", "policy_passed", set()),
            ("signed_warning", "enterprise_signed"): ("reviewable", "policy_passed", set()),
            (
                "signed_warning",
                "strict_no_warnings",
            ): ("blocked", "warning_or_error_threshold_exceeded", {"release_policy_threshold_exceeded"}),
        }
        for text in (
            "unsigned_clean",
            "signed_clean",
            "signed_warning",
            "signature_requirements_not_met",
            "warning_or_error_threshold_exceeded",
            "release_policy_threshold_exceeded",
        ):
            self.assertIn(text, matrix_doc)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            states = {
                "unsigned_clean": self._release_policy_package(tmp_path / "unsigned", signed=False),
                "signed_clean": self._release_policy_package(tmp_path / "signed", signed=True),
                "signed_warning": self._release_policy_package(
                    tmp_path / "signed-warning",
                    signed=True,
                    evidence_warning_count=1,
                ),
            }
            for (state_name, profile), (decision, outcome, reason_codes) in expected.items():
                with self.subTest(state=state_name, profile=profile):
                    report = verify_release_package(states[state_name], policy=policies[profile])
                    policy_report = report["policy"]
                    self.assertEqual(policy_report["review"]["decision"], decision)
                    self.assertEqual(policy_report["diagnostics"]["outcome"], outcome)
                    actual_reason_codes = {
                        reason["code"]
                        for reason in policy_report["diagnostics"]["reasons"]
                    }
                    self.assertTrue(reason_codes.issubset(actual_reason_codes))

    def test_build_release_evidence_outputs_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(
                root,
                version="0.2.0",
                commit="abc123",
                ref="main",
                run_id="42",
                repository="Martin123132/RepoMori",
                generated_at=1700000000,
            )
            verify_report = verify_release_package(root)
            (root / "release-verify.json").write_text(json.dumps(verify_report, indent=2) + "\n", encoding="utf-8")
            (root / "release-verify.md").write_text(format_release_verify_markdown(verify_report), encoding="utf-8")
            for target in ("checksums.txt", "release-provenance.json", "sbom.spdx.json", "release-verify.json"):
                (root / f"{target}.asc").write_text("signature\n", encoding="utf-8")
            (root / "repomori-release-public-key.asc").write_text("public-key\n", encoding="utf-8")

            release_check_dir = tmp_path / ".repomori-release-check"
            release_check_dir.mkdir()
            release_check = {
                "schema_version": "repomori.release_check.v1",
                "status": "pass",
                "summary": {"failed_checks": [], "scan_findings": 0},
            }
            release_check_path = release_check_dir / "release-check.json"
            release_check_path.write_text(json.dumps(release_check), encoding="utf-8")
            out_dir = tmp_path / "evidence"

            report = build_release_evidence(
                root,
                repo=tmp_path,
                release_check=release_check_path,
                out_dir=out_dir,
            )

            self.assertEqual(report["schema_version"], "repomori.release_evidence.v1")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["version"], "0.2.0")
            self.assertEqual(report["checks"]["release_verify"]["status"], "pass")
            self.assertEqual(report["checks"]["release_check"]["status"], "pass")
            self.assertEqual(report["checks"]["signatures"]["status"], "signed")
            self.assertEqual(report["checks"]["signatures"]["public_key_status"], "present")
            self.assertEqual(report["release"]["run_url"], "https://github.com/Martin123132/RepoMori/actions/runs/42")
            self.assertTrue((out_dir / "release-evidence.json").is_file())
            self.assertTrue((out_dir / "release-evidence.md").is_file())

            markdown = format_release_evidence_markdown(report)
            self.assertIn("# RepoMori Release Evidence", markdown)
            self.assertIn("release-provenance.json", markdown)
            self.assertIn("signatures", markdown)

    def test_build_release_evidence_fails_without_release_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(root, version="0.2.0", generated_at=1700000000)

            report = build_release_evidence(root)

            self.assertEqual(report["schema_version"], "repomori.release_evidence.v1")
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any(error["code"] == "report_missing" for error in report["errors"]))

    def test_verify_release_package_detects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            wheel = dist / "repomori-0.2.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(root, version="0.2.0", generated_at=1700000000)

            wheel.write_bytes(b"tampered")
            report = verify_release_package(root)

            self.assertEqual(report["status"], "fail")
            self.assertGreater(report["summary"]["mismatched_files"], 0)
            self.assertTrue(any(error["code"] == "artifact_hash_mismatch" for error in report["errors"]))

    def test_verify_release_package_fails_when_integrity_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(root, version="0.2.0", generated_at=1700000000)

            (root / "release-provenance.json").unlink()
            report = verify_release_package(root)

            self.assertEqual(report["status"], "fail")
            self.assertTrue(any(error["code"] == "required_file_missing" for error in report["errors"]))
            self.assertTrue(any(check["id"] == "required_files" and check["status"] == "fail" for check in report["checks"]))

    def test_verify_release_package_resolves_single_nested_artifact_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "downloaded-artifact"
            root = parent / "repomori-release-candidate-0.2.0" / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(root, version="0.2.0", generated_at=1700000000)

            report = verify_release_package(parent)

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["resolved_root"], str(root.resolve()))
            self.assertTrue(any(warning["code"] == "package_root_resolved_nested" for warning in report["warnings"]))

    def test_workflow_contracts_for_handoff_health(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/handoff-health.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("workflow_call", workflow)
        self.assertIn("handoff-health", workflow)
        self.assertIn("--health-log", workflow)
        self.assertIn("handoff-health.json", workflow)
        self.assertIn("handoff-health.md", workflow)
        self.assertIn("handoff-health.jsonl", workflow)
        self.assertIn("include-hidden-files: true", workflow)

    def test_workflow_contracts_for_tests_preflight(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml").read_text(encoding="utf-8")

        self.assertIn("package-smoke:", workflow)
        self.assertIn("python -m pip install .", workflow)
        self.assertIn("repomori commands --json", workflow)
        self.assertIn("repomori demo --out", workflow)
        self.assertIn("repomori contract-check --fixture", workflow)
        self.assertIn('generated_dirs = {', workflow)
        self.assertIn("release-check preflight blocked by visible top-level artifacts:", workflow)
        self.assertIn("release-health compat JSON artifact was not created", workflow)
        self.assertIn("release-health contract-check JSON artifact was not created", workflow)
        self.assertIn("repomori.contract_check.v1", workflow)
        self.assertIn("Move generated outputs under hidden directories for this repo, for example:", workflow)
        self.assertIn("  - .repomori-packs", workflow)
        self.assertIn("  - .repomori-release-check", workflow)
        self.assertIn("  - .repomori-release-health", workflow)
        self.assertIn("  - .repomori-health", workflow)

    def test_gitignore_covers_documented_hidden_outputs(self) -> None:
        gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")

        for pattern in (
            ".repomori-packs/",
            ".repomori-release-check/",
            ".repomori-release-health/",
            ".repomori-release-candidate/",
            ".repomori-health/",
            ".repomori-smoke/",
            ".repomori-handoff-health/",
            ".repomori-baseline-drift.jsonl",
        ):
            self.assertIn(pattern, gitignore)

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
            self.assertIn("compat", report["checks"])
            self.assertIn("contract", report["checks"])
            self.assertEqual(report["checks"]["compat"]["status"], "pass")
            self.assertEqual(report["checks"]["contract"]["status"], "pass")
            self.assertEqual(report["summary"]["compat_status"], "pass")
            self.assertEqual(report["summary"]["contract_status"], "pass")
            self.assertEqual(report["artifacts"]["json"], str((health_dir / "release-health.json")))
            self.assertEqual(report["artifacts"]["markdown"], str((health_dir / "release-health.md")))
            self.assertEqual(report["artifacts"]["compat_json"], str((health_dir / "compat.json")))
            self.assertEqual(report["artifacts"]["compat_markdown"], str((health_dir / "compat.md")))
            self.assertEqual(report["artifacts"]["contract_json"], str((health_dir / "contract-check.json")))
            self.assertEqual(report["artifacts"]["contract_markdown"], str((health_dir / "contract-check.md")))
            self.assertTrue((health_dir / "release-health.json").exists())
            self.assertTrue((health_dir / "release-health.md").exists())
            self.assertTrue((health_dir / "compat.json").exists())
            self.assertTrue((health_dir / "compat.md").exists())
            self.assertTrue((health_dir / "contract-check.json").exists())
            self.assertTrue((health_dir / "contract-check.md").exists())
            compat_artifact = json.loads((health_dir / "compat.json").read_text(encoding="utf-8"))
            self.assertEqual(compat_artifact["schema_version"], "repomori.compat.v1")
            self.assertEqual(compat_artifact["status"], "pass")
            contract_artifact = json.loads((health_dir / "contract-check.json").read_text(encoding="utf-8"))
            self.assertEqual(contract_artifact["schema_version"], "repomori.contract_check.v1")
            self.assertEqual(contract_artifact["status"], "pass")
            self.assertIn("pack_schema=", (health_dir / "release-health.md").read_text(encoding="utf-8"))
            self.assertIn("contract:", (health_dir / "release-health.md").read_text(encoding="utf-8"))

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
            self.assertEqual(report["checks"]["compat"]["status"], "warn")

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

            timeline_search = search_snapshot_timeline(out, "sqlite", limit=2)
            self.assertEqual(timeline_search["schema_version"], "repomori.timeline_search.v1")
            self.assertEqual(timeline_search["status"], "pass")
            self.assertEqual(timeline_search["summary"]["matched_snapshot_count"], 2)
            self.assertGreaterEqual(timeline_search["summary"]["matched_file_count"], 1)
            self.assertEqual(timeline_search["matches"][0]["pack_path"], second["summary"]["pack_path"])
            timeline_search_markdown = format_timeline_search_markdown(timeline_search)
            self.assertIn("# RepoMori Timeline Search", timeline_search_markdown)
            self.assertIn("File History", timeline_search_markdown)

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
            self.assertEqual(second["summary"]["handoff_score_status"], "pass")
            self.assertGreaterEqual(second["summary"]["handoff_score_percent"], 85)
            handoff_dir = Path(second["summary"]["handoff_dir"])
            self.assertTrue((handoff_dir / "manifest.json").exists())
            self.assertTrue((handoff_dir / "compare.json").exists())
            self.assertTrue((handoff_dir / "inspect-diff.json").exists())
            self.assertTrue((handoff_dir / "handoff-score.json").exists())
            self.assertTrue((handoff_dir / "handoff-score.md").exists())
            self.assertTrue((handoff_dir / "handoff-triage.json").exists())
            self.assertTrue((handoff_dir / "handoff-triage.md").exists())
            self.assertEqual(second["handoff_score"]["schema_version"], "repomori.handoff_score.v1")
            self.assertEqual(second["handoff_triage"]["schema_version"], "repomori.handoff_triage.v1")
            self.assertEqual(second["summary"]["handoff_triage_status"], "warn")
            self.assertGreaterEqual(second["summary"]["handoff_triage_action_count"], 1)
            manifest = second["handoff"]
            self.assertEqual(manifest["settings"]["base_pack"], first["summary"]["pack_path"])
            self.assertEqual(second["handoff_check"]["checked_json"], 7)
            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["latest"]["handoff_dir"], str(handoff_dir))
            self.assertTrue(index["latest"]["handoff_passed"])
            self.assertEqual(index["latest"]["handoff_score_status"], "pass")
            self.assertTrue(Path(index["latest"]["handoff_score_json"]).exists())
            self.assertEqual(index["latest"]["handoff_triage_status"], "warn")
            self.assertTrue(Path(index["latest"]["handoff_triage_json"]).exists())

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
            self.assertIn(report["summary"]["handoff_score_status"], {"pass", "warn"})
            self.assertGreaterEqual(report["summary"]["handoff_score_percent"], 60)
            self.assertTrue((handoff_dir / "handoff-score.json").exists())
            self.assertTrue((handoff_dir / "handoff-triage.json").exists())
            self.assertEqual(report["summary"]["handoff_triage_status"], "fail")
            self.assertGreaterEqual(report["summary"]["handoff_triage_action_count"], 1)
            self.assertGreaterEqual(report["summary"]["handoff_triage_high_priority_count"], 1)
            self.assertIn("handoff_score_json", report["artifacts"])
            self.assertIn("handoff_triage_json", report["artifacts"])
            self.assertEqual(report["snapshot"]["handoff_score"]["schema_version"], "repomori.handoff_score.v1")
            self.assertEqual(report["snapshot"]["handoff_triage"]["schema_version"], "repomori.handoff_triage.v1")
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
            self.assertIsNone(report["summary"]["handoff_score_status"])
            self.assertIsNone(report["summary"]["handoff_triage_status"])
            self.assertNotIn("handoff", report["artifacts"])
            self.assertNotIn("handoff_score_json", report["artifacts"])
            self.assertNotIn("handoff_triage_json", report["artifacts"])
            self.assertIsNone(report["snapshot"]["handoff"])
            self.assertIsNone(report["snapshot"]["handoff_score"])
            self.assertIsNone(report["snapshot"]["handoff_triage"])

    def test_run_memory_cycle_handoff_quality_profile_can_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "memory-quality"

            report = run_memory_cycle(repo, out, handoff_quality_profile="strict")

            self.assertEqual(report["schema_version"], "repomori.memory.v1")
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["summary"]["handoff_quality_status"], "fail")
            self.assertEqual(report["summary"]["handoff_quality_profile"], "strict")
            self.assertIsNotNone(report["handoff_quality"])
            self.assertTrue(any("handoff-quality:" in reason for reason in report["failure_reasons"]))

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
            self.assertEqual(second["summary"]["inspect_diff_status"], "pass")
            self.assertEqual(second["inspect_diff"]["schema_version"], "repomori.inspect_diff.v1")
            self.assertEqual(second["inspect_diff"]["summary"]["changed_count"], 1)
            self.assertIn("inspect_diff_json", second["artifacts"])
            self.assertIn("diff_context_json", second["artifacts"])
            inspect_diff_json = out / second["artifacts"]["inspect_diff_json"]
            inspect_diff_md = out / second["artifacts"]["inspect_diff_markdown"]
            diff_json = out / second["artifacts"]["diff_context_json"]
            diff_md = out / second["artifacts"]["diff_context_markdown"]
            self.assertTrue(inspect_diff_json.exists())
            self.assertTrue(inspect_diff_md.exists())
            self.assertTrue(diff_json.exists())
            self.assertTrue(diff_md.exists())
            self.assertIn("# RepoMori Pack Inspect Diff", inspect_diff_md.read_text(encoding="utf-8"))
            self.assertIn("def close", diff_md.read_text(encoding="utf-8"))

            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            self.assertEqual(index["latest"]["inspect_diff_json"], inspect_diff_json.name)
            self.assertEqual(index["latest"]["inspect_diff_status"], "pass")
            self.assertEqual(index["latest"]["diff_context_json"], diff_json.name)
            self.assertEqual(index["latest"]["diff_context_status"], "written")
            doctor = doctor_snapshot_dir(out)
            self.assertEqual(doctor["status"], "pass")

            (repo / "next.py").write_text("def next_step():\n    return 'next'\n", encoding="utf-8")
            third = run_memory_cycle(repo, out, no_handoff=True, diff_context=True, keep=1, prune_apply=True)
            self.assertEqual(third["summary"]["diff_context_status"], "written")
            self.assertFalse(inspect_diff_json.exists())
            self.assertFalse(inspect_diff_md.exists())
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
            self.assertIn("inspect_diff.build", help_response["result"]["methods"])
            self.assertIn("compat.check", help_response["result"]["methods"])

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

            compat_response = handle_agent_request(
                {"id": "compat", "method": "compat.check", "params": {"require_handoff": False}},
                config_path=config,
            )
            self.assertTrue(compat_response["ok"])
            self.assertEqual(compat_response["result"]["schema_version"], "repomori.compat.v1")
            self.assertEqual(compat_response["result"]["status"], "pass")

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

            inspect_diff_response = handle_agent_request(
                {"id": "inspect-diff", "method": "inspect_diff.build", "params": {"max_files": 2}},
                config_path=config,
            )
            self.assertTrue(inspect_diff_response["ok"])
            self.assertEqual(inspect_diff_response["result"]["schema_version"], "repomori.inspect_diff.v1")
            self.assertEqual(inspect_diff_response["result"]["summary"]["added_count"], 1)

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

    def test_agent_and_mcp_handoff_quality_search_and_archive_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "packs"
            config = Path(tmp) / "repomori.toml"
            init_config(repo, out, config_path=config, no_handoff=True)
            run_memory_cycle(repo, out, no_handoff=True)
            handoff_dir = Path(tmp) / "handoff-agent"
            build_handoff_package(pack, "sqlite Store", handoff_dir)

            quality_response = handle_agent_request(
                {
                    "id": "quality",
                    "method": "handoff.quality",
                    "params": {"score_or_handoff": str(handoff_dir), "profile": "safe"},
                },
                config_path=config,
            )
            self.assertTrue(quality_response["ok"])
            self.assertEqual(quality_response["result"]["schema_version"], "repomori.handoff_quality.v1")

            health_response = handle_agent_request(
                {
                    "id": "health",
                    "method": "handoff.health",
                    "params": {"handoff_dir": str(handoff_dir), "profile": "safe"},
                },
                config_path=config,
            )
            self.assertTrue(health_response["ok"])
            self.assertEqual(health_response["result"]["schema_version"], "repomori.handoff_health.v1")

            search_response = handle_agent_request(
                {"id": "search", "method": "timeline.search", "params": {"text": "sqlite", "limit": 1}},
                config_path=config,
            )
            self.assertTrue(search_response["ok"])
            self.assertEqual(search_response["result"]["schema_version"], "repomori.timeline_search.v1")

            archive_path = Path(tmp) / "handoff-agent.zip"
            archive_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "archive",
                    "method": "tools/call",
                    "params": {
                        "name": "repomori_handoff_archive",
                        "arguments": {"handoff_dir": str(handoff_dir), "out": str(archive_path)},
                    },
                },
                config_path=config,
            )
            self.assertFalse(archive_response["result"]["isError"])
            self.assertEqual(archive_response["result"]["structuredContent"]["schema_version"], "repomori.handoff_archive.v1")
            self.assertTrue(archive_path.exists())

            health_mcp_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "health",
                    "method": "tools/call",
                    "params": {
                        "name": "repomori_handoff_health",
                        "arguments": {"handoff_dir": str(handoff_dir), "profile": "safe"},
                    },
                },
                config_path=config,
            )
            self.assertFalse(health_mcp_response["result"]["isError"])
            self.assertEqual(health_mcp_response["result"]["structuredContent"]["schema_version"], "repomori.handoff_health.v1")

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
        self.assertIn("repomori_timeline_search", first_names)
        self.assertIn("repomori_diff_context_build", first_names)
        self.assertIn("repomori_pack_inspect", first_names)
        self.assertIn("repomori_pack_inspect_diff", first_names)
        self.assertIn("repomori_handoff_score", first_names)
        self.assertIn("repomori_handoff_triage", first_names)
        self.assertIn("repomori_handoff_quality", first_names)
        self.assertIn("repomori_handoff_improve", first_names)
        self.assertIn("repomori_handoff_archive", first_names)
        self.assertIn("repomori_handoff_health", first_names)
        self.assertIn("repomori_compat_check", first_names)
        self.assertIn("repomori_stats_read", first_names)
        self.assertIn("repomori_schema_list", first_names)
        memory_tool = next(tool for tool in first_list["result"]["tools"] if tool["name"] == "repomori_memory_run")
        self.assertIn("incremental", memory_tool["inputSchema"]["properties"])
        self.assertIn("diff_context", memory_tool["inputSchema"]["properties"])
        self.assertIn("anchor_out", memory_tool["inputSchema"]["properties"])
        self.assertIn("anchor_verify", memory_tool["inputSchema"]["properties"])
        self.assertIn("allow_unverified_anchor", memory_tool["inputSchema"]["properties"])
        self.assertIn("anchor_log", memory_tool["inputSchema"]["properties"])
        self.assertIn("handoff_quality_profile", memory_tool["inputSchema"]["properties"])

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

            inspect_diff_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "inspect-diff",
                    "method": "tools/call",
                    "params": {
                        "name": "repomori_pack_inspect_diff",
                        "arguments": {"max_files": 2},
                    },
                },
                config_path=config,
            )
            self.assertFalse(inspect_diff_response["result"]["isError"])
            self.assertEqual(inspect_diff_response["result"]["structuredContent"]["schema_version"], "repomori.inspect_diff.v1")
            self.assertEqual(inspect_diff_response["result"]["structuredContent"]["summary"]["added_count"], 1)
            self.assertIn("changed:", inspect_diff_response["result"]["content"][0]["text"])

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

            compat_response = handle_mcp_request(
                {
                    "jsonrpc": "2.0",
                    "id": "compat",
                    "method": "tools/call",
                    "params": {
                        "name": "repomori_compat_check",
                        "arguments": {"require_handoff": False},
                    },
                },
                config_path=config,
            )
            self.assertFalse(compat_response["result"]["isError"])
            self.assertEqual(compat_response["result"]["structuredContent"]["schema_version"], "repomori.compat.v1")
            self.assertEqual(compat_response["result"]["structuredContent"]["status"], "pass")
            self.assertIn("checks:", compat_response["result"]["content"][0]["text"])

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
        self.assertIn("repomori.release_candidate.v1", schema_versions)
        self.assertIn("repomori.release_provenance.v1", schema_versions)
        self.assertIn("repomori.release_evidence.v1", schema_versions)
        self.assertIn("repomori.release_policy.v1", schema_versions)
        self.assertIn("repomori.release_review_bundle.v1", schema_versions)
        self.assertIn("repomori.release_review_handoff.v1", schema_versions)
        self.assertIn("repomori.release_review_decision_log.v1", schema_versions)
        self.assertIn("repomori.release_review_privacy_guard.v1", schema_versions)
        self.assertIn("repomori.release_review_privacy_guard_demo.v1", schema_versions)
        self.assertIn("repomori.health.v1", schema_versions)
        self.assertIn("repomori.agent.response.v1", schema_versions)
        self.assertIn("repomori.agent_brief.v1", schema_versions)
        self.assertIn("repomori.brief.v1", schema_versions)
        self.assertIn("repomori.handoff_score.v1", schema_versions)
        self.assertIn("repomori.handoff_triage.v1", schema_versions)
        self.assertIn("repomori.handoff_quality.v1", schema_versions)
        self.assertIn("repomori.handoff_improvement.v1", schema_versions)
        self.assertIn("repomori.handoff_archive.v1", schema_versions)
        self.assertIn("repomori.handoff_health.v1", schema_versions)
        self.assertIn("repomori.handoff_health_record.v1", schema_versions)
        self.assertIn("repomori.handoff_health_summary.v1", schema_versions)
        self.assertIn("repomori.compat.v1", schema_versions)
        self.assertIn("repomori.contract_check.v1", schema_versions)
        self.assertIn("repomori.cli_commands.v1", schema_versions)
        self.assertIn("repomori.timeline_search.v1", schema_versions)
        self.assertIn("repomori.inspect.v1", schema_versions)
        self.assertIn("repomori.compare.v1", schema_versions)
        self.assertIn("repomori.inspect_diff.v1", schema_versions)
        self.assertIn("repomori.verify.v1", schema_versions)
        self.assertIn("repomori.eval.v1", schema_versions)
        self.assertIn("repomori.context_eval.v1", schema_versions)
        self.assertIn("repomori.snapshot_chain.v1", schema_versions)
        self.assertIn("repomori.snapshot_anchor.v1", schema_versions)
        self.assertIn("repomori.snapshot_anchor.verify.v1", schema_versions)
        self.assertIn("repomori.restore_check.v1", schema_versions)
        self.assertIn("repomori.stats.v1", schema_versions)
        self.assertIn("repomori.diff_context.v1", schema_versions)
        self.assertIn("anchor.build", catalog["agent_methods"])
        self.assertIn("anchor.verify", catalog["agent_methods"])
        self.assertIn("brief.build", catalog["agent_methods"])
        self.assertIn("chain.verify", catalog["agent_methods"])
        self.assertIn("context.build", catalog["agent_methods"])
        self.assertIn("diff_context.build", catalog["agent_methods"])
        self.assertIn("inspect.build", catalog["agent_methods"])
        self.assertIn("inspect_diff.build", catalog["agent_methods"])
        self.assertIn("handoff.score", catalog["agent_methods"])
        self.assertIn("handoff.triage", catalog["agent_methods"])
        self.assertIn("handoff.quality", catalog["agent_methods"])
        self.assertIn("handoff.improve", catalog["agent_methods"])
        self.assertIn("handoff.archive", catalog["agent_methods"])
        self.assertIn("handoff.health", catalog["agent_methods"])
        self.assertIn("compat.check", catalog["agent_methods"])
        self.assertIn("timeline.search", catalog["agent_methods"])
        self.assertIn("stats.read", catalog["agent_methods"])
        self.assertIn("schema.list", catalog["agent_methods"])
        self.assertIn("repomori_anchor_build", catalog["mcp_tools"])
        self.assertIn("repomori_anchor_verify", catalog["mcp_tools"])
        self.assertIn("repomori_brief_build", catalog["mcp_tools"])
        self.assertIn("repomori_chain_verify", catalog["mcp_tools"])
        self.assertIn("repomori_diff_context_build", catalog["mcp_tools"])
        self.assertIn("repomori_pack_inspect", catalog["mcp_tools"])
        self.assertIn("repomori_pack_inspect_diff", catalog["mcp_tools"])
        self.assertIn("repomori_handoff_quality", catalog["mcp_tools"])
        self.assertIn("repomori_handoff_improve", catalog["mcp_tools"])
        self.assertIn("repomori_handoff_archive", catalog["mcp_tools"])
        self.assertIn("repomori_handoff_health", catalog["mcp_tools"])
        self.assertIn("repomori_compat_check", catalog["mcp_tools"])
        self.assertIn("repomori_timeline_search", catalog["mcp_tools"])
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

        inspect_diff_schema = schema_catalog("repomori.inspect_diff.v1")
        self.assertEqual(inspect_diff_schema["selected"], "repomori.inspect_diff.v1")
        self.assertEqual(inspect_diff_schema["schema"]["producer"], "inspect_pack_diff")

        release_review_bundle_schema = schema_catalog("repomori.release_review_bundle.v1")
        self.assertEqual(release_review_bundle_schema["selected"], "repomori.release_review_bundle.v1")
        self.assertEqual(
            release_review_bundle_schema["schema"]["producer"],
            "check_release_candidate_review_bundle",
        )

        release_review_handoff_schema = schema_catalog("repomori.release_review_handoff.v1")
        self.assertEqual(release_review_handoff_schema["selected"], "repomori.release_review_handoff.v1")
        self.assertEqual(
            release_review_handoff_schema["schema"]["producer"],
            "build_release_candidate_reviewer_handoff",
        )

        release_review_decision_log_schema = schema_catalog("repomori.release_review_decision_log.v1")
        self.assertEqual(
            release_review_decision_log_schema["selected"],
            "repomori.release_review_decision_log.v1",
        )
        self.assertEqual(
            release_review_decision_log_schema["schema"]["producer"],
            "build_release_review_decision_log",
        )
        release_review_privacy_guard_schema = schema_catalog("repomori.release_review_privacy_guard.v1")
        self.assertEqual(
            release_review_privacy_guard_schema["selected"],
            "repomori.release_review_privacy_guard.v1",
        )
        self.assertEqual(
            release_review_privacy_guard_schema["schema"]["producer"],
            "check_release_review_decision_log_privacy",
        )
        release_review_privacy_guard_demo_schema = schema_catalog("repomori.release_review_privacy_guard_demo.v1")
        self.assertEqual(
            release_review_privacy_guard_demo_schema["selected"],
            "repomori.release_review_privacy_guard_demo.v1",
        )
        self.assertEqual(
            release_review_privacy_guard_demo_schema["schema"]["producer"],
            "build_release_review_privacy_guard_demo",
        )

        verify_schema = schema_catalog("repomori.verify.v1")
        self.assertEqual(verify_schema["selected"], "repomori.verify.v1")
        self.assertEqual(verify_schema["schema"]["producer"], "verify_pack")

        eval_schema = schema_catalog("repomori.eval.v1")
        self.assertEqual(eval_schema["selected"], "repomori.eval.v1")
        self.assertEqual(eval_schema["schema"]["producer"], "evaluate_pack")

        context_eval_schema = schema_catalog("repomori.context_eval.v1")
        self.assertEqual(context_eval_schema["selected"], "repomori.context_eval.v1")
        self.assertEqual(context_eval_schema["schema"]["producer"], "evaluate_context_quality")

        release_candidate_schema = schema_catalog("repomori.release_candidate.v1")
        self.assertEqual(release_candidate_schema["selected"], "repomori.release_candidate.v1")
        self.assertEqual(release_candidate_schema["schema"]["producer"], ".github/workflows/release-candidate.yml")

        release_provenance_schema = schema_catalog("repomori.release_provenance.v1")
        self.assertEqual(release_provenance_schema["selected"], "repomori.release_provenance.v1")
        self.assertEqual(release_provenance_schema["schema"]["producer"], "write_release_package_artifacts")

        release_evidence_schema = schema_catalog("repomori.release_evidence.v1")
        self.assertEqual(release_evidence_schema["selected"], "repomori.release_evidence.v1")
        self.assertEqual(release_evidence_schema["schema"]["producer"], "build_release_evidence")
        release_policy_schema = schema_catalog("repomori.release_policy.v1")
        self.assertEqual(release_policy_schema["selected"], "repomori.release_policy.v1")
        self.assertEqual(release_policy_schema["schema"]["producer"], "evaluate_release_policy")

        agent_brief = schema_catalog("repomori.agent_brief.v1")
        self.assertEqual(agent_brief["selected"], "repomori.agent_brief.v1")
        self.assertEqual(agent_brief["schema"]["producer"], "build_agent_brief")

        compat = schema_catalog("repomori.compat.v1")
        self.assertEqual(compat["selected"], "repomori.compat.v1")
        self.assertEqual(compat["schema"]["producer"], "check_compatibility")

        contract = schema_catalog("repomori.contract_check.v1")
        self.assertEqual(contract["selected"], "repomori.contract_check.v1")
        self.assertEqual(contract["schema"]["producer"], "check_contract_fixture")

        cli_commands = schema_catalog("repomori.cli_commands.v1")
        self.assertEqual(cli_commands["selected"], "repomori.cli_commands.v1")
        self.assertEqual(cli_commands["schema"]["producer"], "build_cli_command_inventory")

        chain = schema_catalog("repomori.snapshot_chain.v1")
        self.assertEqual(chain["selected"], "repomori.snapshot_chain.v1")
        self.assertEqual(chain["schema"]["producer"], "verify_snapshot_chain")

        anchor = schema_catalog("repomori.snapshot_anchor.v1")
        self.assertEqual(anchor["selected"], "repomori.snapshot_anchor.v1")
        self.assertEqual(anchor["schema"]["producer"], "build_snapshot_anchor")

        anchor_verify = schema_catalog("repomori.snapshot_anchor.verify.v1")
        self.assertEqual(anchor_verify["selected"], "repomori.snapshot_anchor.verify.v1")
        self.assertEqual(anchor_verify["schema"]["producer"], "verify_snapshot_anchor")
        restore_check = schema_catalog("repomori.restore_check.v1")
        self.assertEqual(restore_check["selected"], "repomori.restore_check.v1")
        self.assertEqual(restore_check["schema"]["producer"], "check_snapshot_restore")

        handoff_score_schema = schema_catalog("repomori.handoff_score.v1")
        self.assertEqual(handoff_score_schema["selected"], "repomori.handoff_score.v1")
        self.assertEqual(handoff_score_schema["schema"]["producer"], "score_handoff_package")

        handoff_triage_schema = schema_catalog("repomori.handoff_triage.v1")
        self.assertEqual(handoff_triage_schema["selected"], "repomori.handoff_triage.v1")
        self.assertEqual(handoff_triage_schema["schema"]["producer"], "triage_handoff_score")

        handoff_quality_schema = schema_catalog("repomori.handoff_quality.v1")
        self.assertEqual(handoff_quality_schema["selected"], "repomori.handoff_quality.v1")
        self.assertEqual(handoff_quality_schema["schema"]["producer"], "evaluate_handoff_quality")

        handoff_health_schema = schema_catalog("repomori.handoff_health.v1")
        self.assertEqual(handoff_health_schema["selected"], "repomori.handoff_health.v1")
        self.assertEqual(handoff_health_schema["schema"]["producer"], "build_handoff_health_report")

        handoff_health_record_schema = schema_catalog("repomori.handoff_health_record.v1")
        self.assertEqual(handoff_health_record_schema["selected"], "repomori.handoff_health_record.v1")
        self.assertEqual(handoff_health_record_schema["schema"]["producer"], "append_handoff_health_log")

        handoff_health_summary_schema = schema_catalog("repomori.handoff_health_summary.v1")
        self.assertEqual(handoff_health_summary_schema["selected"], "repomori.handoff_health_summary.v1")
        self.assertEqual(handoff_health_summary_schema["schema"]["producer"], "summarize_handoff_health_log")

        timeline_search_schema = schema_catalog("repomori.timeline_search.v1")
        self.assertEqual(timeline_search_schema["selected"], "repomori.timeline_search.v1")
        self.assertEqual(timeline_search_schema["schema"]["producer"], "search_snapshot_timeline")

    def test_compat_contract_fixture_matches_schema_agent_and_mcp_contracts(self) -> None:
        fixture = self._compat_contract_fixture()
        catalog = schema_catalog()

        self.assertEqual(
            sorted(item["schema_version"] for item in catalog["schemas"]),
            fixture["schema_versions"],
        )
        self.assertEqual(catalog["agent_methods"], fixture["agent_methods"])
        self.assertEqual(catalog["mcp_tools"], fixture["mcp_tools"])
        self.assertEqual(
            fixture["release_health_compat_artifacts"],
            ["compat.json", "compat.md", "contract-check.json", "contract-check.md"],
        )

        agent_help = handle_agent_request({"id": "help", "method": "agent.help"})
        self.assertTrue(agent_help["ok"])
        self.assertEqual(agent_help["result"]["methods"], fixture["agent_methods"])

        mcp_tools = handle_mcp_request({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
        self.assertEqual(mcp_tools["result"]["schema_version"], "repomori.mcp.tools.v1")
        self.assertEqual(
            [tool["name"] for tool in mcp_tools["result"]["tools"]],
            fixture["mcp_tools"],
        )

    def test_contract_check_fixture_reports_pass_and_markdown(self) -> None:
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "compat-contracts.json"

        report = check_contract_fixture(fixture_path)

        self.assertEqual(report["schema_version"], "repomori.contract_check.v1")
        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["summary"]["skipped"])
        self.assertEqual(report["summary"]["change_count"], 0)
        self.assertEqual(report["diffs"]["schema_versions"]["status"], "pass")
        self.assertEqual(report["diffs"]["agent_methods"]["status"], "pass")
        self.assertEqual(report["diffs"]["mcp_tools"]["status"], "pass")

        markdown = format_contract_check_markdown(report)
        self.assertIn("# RepoMori Contract Check", markdown)
        self.assertIn("Contract fixture matches", markdown)

    def test_contract_check_fixture_reports_added_removed_and_order_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._compat_contract_fixture()
            fixture["schema_versions"] = [
                item for item in fixture["schema_versions"] if item != "repomori.compat.v1"
            ]
            fixture["agent_methods"] = list(reversed(fixture["agent_methods"]))
            fixture["mcp_tools"].append("repomori_future_tool")
            fixture_path = Path(tmp) / "contract.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

            report = check_contract_fixture(fixture_path)

            self.assertEqual(report["status"], "fail")
            self.assertIn("repomori.compat.v1", report["diffs"]["schema_versions"]["added"])
            self.assertIn("repomori_future_tool", report["diffs"]["mcp_tools"]["removed"])
            self.assertTrue(report["diffs"]["agent_methods"]["order_changed"])
            self.assertGreaterEqual(report["summary"]["change_count"], 3)
            self.assertTrue(any(error["code"] == "contract_drift" for error in report["errors"]))

    def test_contract_check_missing_optional_fixture_skips(self) -> None:
        report = check_contract_fixture(None, required=False)

        self.assertEqual(report["schema_version"], "repomori.contract_check.v1")
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["summary"]["skipped"])
        self.assertTrue(any(warning["code"] == "fixture_skipped" for warning in report["warnings"]))

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
            handoff_score = score_handoff_package(handoff_dir)
            handoff_triage = triage_handoff_score(handoff_score)
            handoff_health = build_handoff_health_report(handoff_dir)
            memory = run_memory_cycle(repo, memory_dir, no_handoff=True)
            inspect_diff = inspect_pack_diff(pack, memory["summary"]["pack_path"], max_files=2)
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
                "inspect_diff": (
                    inspect_diff,
                    "repomori.inspect_diff.v1",
                    {"schema_version", "status", "base_pack", "target_pack", "summary", "comparison", "storage_delta", "vocabulary_delta"},
                ),
                "handoff": (
                    handoff,
                    "repomori.handoff.v1",
                    {"schema_version", "status", "question", "out_dir", "artifacts", "verification"},
                ),
                "handoff_score": (
                    handoff_score,
                    "repomori.handoff_score.v1",
                    {"schema_version", "status", "handoff_dir", "summary", "checks", "validation"},
                ),
                "handoff_triage": (
                    handoff_triage,
                    "repomori.handoff_triage.v1",
                    {"schema_version", "status", "source", "summary", "actions"},
                ),
                "handoff_health": (
                    handoff_health,
                    "repomori.handoff_health.v1",
                    {"schema_version", "status", "handoff_dir", "profile", "summary", "check", "score", "triage", "quality"},
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
            self.assertEqual(handoff_score["status"], "pass")
            self.assertIn(handoff_triage["status"], {"pass", "warn"})
            self.assertEqual(inspect_diff["summary"]["changed_count"], 0)
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

    def test_cli_inspect_diff_json_is_parseable(self) -> None:
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
                    "inspect-diff",
                    str(base_pack),
                    str(target_pack),
                    "--json",
                    "--max-files",
                    "2",
                    "--top-symbols",
                    "10",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.inspect_diff.v1")
            self.assertEqual(payload["summary"]["changed_count"], 1)
            self.assertEqual(payload["source_manifest"][0]["path"], "app.py")

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

    def test_cli_context_eval_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            cases = Path(tmp) / "context-eval-cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "id": "sqlite-store",
                                "question": "sqlite Store",
                                "expected_paths": ["app.py"],
                                "required_snippets": ["sqlite3.connect"],
                                "max_rank": 1,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "context-eval",
                    str(pack),
                    "--cases",
                    str(cases),
                    "--format",
                    "json",
                    "--max-files",
                    "2",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.context_eval.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["cases"][0]["result"]["top_path"], "app.py")

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

    def test_cli_score_handoff_json_and_markdown_are_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff-score-cli"
            markdown_out = Path(tmp) / "handoff-score.md"
            build_handoff_package(pack, "sqlite Store", out)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "score-handoff",
                    str(out),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.handoff_score.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertGreaterEqual(payload["summary"]["score_percent"], 85)

            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "score-handoff",
                    str(out),
                    "--out",
                    str(markdown_out),
                ],
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertIn("# RepoMori Handoff Score", markdown_out.read_text(encoding="utf-8"))

    def test_cli_handoff_triage_json_and_markdown_are_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            memory_dir = Path(tmp) / "memory"
            markdown_out = Path(tmp) / "handoff-triage.md"
            memory = run_memory_cycle(repo, memory_dir)
            handoff_dir = Path(memory["summary"]["handoff_dir"])
            score_path = handoff_dir / "handoff-score.json"
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "handoff-triage",
                    str(score_path),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.handoff_triage.v1")
            self.assertIn(payload["status"], {"pass", "warn", "fail"})
            self.assertEqual(payload["source"]["type"], "score_file")

            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "handoff-triage",
                    str(handoff_dir),
                    "--out",
                    str(markdown_out),
                ],
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertIn("# RepoMori Handoff Triage", markdown_out.read_text(encoding="utf-8"))

    def test_cli_handoff_quality_improve_and_archive_json_are_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            handoff_dir = Path(tmp) / "handoff-quality-cli"
            improved_dir = Path(tmp) / "improved-cli"
            archive_path = Path(tmp) / "handoff-cli.zip"
            build_handoff_package(pack, "sqlite Store", handoff_dir)

            quality_output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "handoff-quality",
                    str(handoff_dir),
                    "--profile",
                    "safe",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            quality = json.loads(quality_output)
            self.assertEqual(quality["schema_version"], "repomori.handoff_quality.v1")

            health_artifacts = Path(tmp) / "handoff-health-artifacts"
            health_log = Path(tmp) / "handoff-health.jsonl"
            health_output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "handoff-health",
                    str(handoff_dir),
                    "--profile",
                    "safe",
                    "--artifacts-dir",
                    str(health_artifacts),
                    "--health-log",
                    str(health_log),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            health = json.loads(health_output)
            self.assertEqual(health["schema_version"], "repomori.handoff_health.v1")
            self.assertTrue((health_artifacts / "handoff-health.json").exists())
            self.assertTrue((health_artifacts / "handoff-health.md").exists())
            self.assertTrue(health_log.exists())

            health_summary_output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "handoff-health-summary",
                    str(health_log),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            health_summary = json.loads(health_summary_output)
            self.assertEqual(health_summary["schema_version"], "repomori.handoff_health_summary.v1")
            self.assertEqual(health_summary["count"], 1)

            improve_output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "improve-handoff",
                    str(pack),
                    "sqlite Store",
                    "--out",
                    str(improved_dir),
                    "--target-score",
                    "90",
                    "--max-attempts",
                    "2",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            improved = json.loads(improve_output)
            self.assertEqual(improved["schema_version"], "repomori.handoff_improvement.v1")
            self.assertTrue((improved_dir / "handoff-improvement.json").exists())

            archive_output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "archive-handoff",
                    str(improved_dir),
                    "--out",
                    str(archive_path),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )
            archive = json.loads(archive_output)
            self.assertEqual(archive["schema_version"], "repomori.handoff_archive.v1")
            self.assertTrue(archive_path.exists())

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
            self.assertEqual(payload["summary"]["handoff_score_status"], "pass")
            self.assertEqual(payload["summary"]["handoff_triage_status"], "warn")
            self.assertTrue((Path(payload["summary"]["handoff_dir"]) / "compare.json").exists())
            self.assertTrue((Path(payload["summary"]["handoff_dir"]) / "handoff-score.json").exists())
            self.assertTrue((Path(payload["summary"]["handoff_dir"]) / "handoff-triage.json").exists())

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

    def test_cli_timeline_search_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "timeline-search-cli"
            snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'sqlite'\n", encoding="utf-8")
            snapshot_repo(repo, out)
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "timeline-search",
                    str(out),
                    "sqlite",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.timeline_search.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertGreaterEqual(payload["summary"]["matched_snapshot_count"], 1)

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

    def test_check_snapshot_restore_passes_for_snapshot_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "restore-source"
            snapshot_repo(repo, out)
            (repo / "new.py").write_text("def added():\n    return 'new'\n", encoding="utf-8")
            snapshot_repo(repo, out)

            report = check_snapshot_restore(out, verify_packs=True, timeline_limit=1)

            self.assertEqual(report["schema_version"], "repomori.restore_check.v1")
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["summary"]["restore_ready"])
            self.assertEqual(report["summary"]["snapshot_count"], 2)
            self.assertEqual(report["summary"]["doctor_status"], "pass")
            self.assertEqual(report["summary"]["chain_status"], "pass")
            self.assertEqual(report["checks"]["timeline"]["returned_count"], 1)
            markdown = format_restore_check_markdown(report)
            self.assertIn("# RepoMori Restore Check", markdown)
            self.assertIn("Backup Contents", markdown)

    def test_check_snapshot_restore_warns_for_relocated_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            source = Path(tmp) / "restore-source"
            restored = Path(tmp) / "restore-target"
            snapshot_repo(repo, source)
            shutil.copytree(source, restored)

            report = check_snapshot_restore(restored)

            self.assertEqual(report["schema_version"], "repomori.restore_check.v1")
            self.assertEqual(report["status"], "warn")
            self.assertFalse(report["summary"]["restore_ready"])
            self.assertTrue(report["summary"]["usable_with_warnings"])
            self.assertFalse(report["errors"])
            self.assertTrue(any(warning.get("check") == "portability" for warning in report["warnings"]))

    def test_check_snapshot_restore_verifies_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "restore-anchor"
            snapshot_repo(repo, out)
            anchor = Path(tmp) / "timeline-anchor.json"
            anchor.write_text(json.dumps(build_snapshot_anchor(out), indent=2), encoding="utf-8")

            report = check_snapshot_restore(out, anchor=anchor)

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["anchor_status"], "pass")
            self.assertTrue(report["summary"]["anchor_hash_valid"])
            self.assertTrue(report["summary"]["anchor_chain_head_matches"])

    def test_check_snapshot_restore_fails_for_missing_indexed_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "restore-missing-pack"
            snapshot_repo(repo, out)
            index = json.loads((out / "snapshots.json").read_text(encoding="utf-8"))
            missing = Path(index["latest"]["pack_path"])
            missing.unlink()

            report = check_snapshot_restore(out)

            self.assertEqual(report["status"], "fail")
            self.assertFalse(report["summary"]["restore_ready"])
            self.assertGreater(report["summary"]["error_count"], 0)
            self.assertTrue(any("does not exist" in error["message"] for error in report["errors"]))

    def test_cli_restore_check_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, _pack = self._demo_pack(Path(tmp))
            out = Path(tmp) / "restore-check-cli"
            snapshot_repo(repo, out)
            anchor = Path(tmp) / "restore-anchor.json"
            anchor.write_text(json.dumps(build_snapshot_anchor(out), indent=2), encoding="utf-8")
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "restore-check",
                    str(out),
                    "--anchor",
                    str(anchor),
                    "--verify-packs",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
            )

            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "repomori.restore_check.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["summary"]["anchor_status"], "pass")

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
        self.assertTrue(any(item["schema_version"] == "repomori.release_verify.v1" for item in payload["schemas"]))

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

    def test_cli_command_inventory_matches_parser(self) -> None:
        parser = cli.build_parser()
        inventory = cli.build_cli_command_inventory()
        command_names = inventory["summary"]["commands"]

        self.assertEqual(inventory["schema_version"], "repomori.cli_commands.v1")
        self.assertEqual(inventory["status"], "pass")
        self.assertEqual(inventory["summary"]["command_count"], len(command_names))
        self.assertIn("commands", command_names)
        self.assertIn("memory", command_names)
        self.assertIn("release-health", command_names)
        self.assertIn("verify-release", command_names)
        self.assertIn("privacy-guard-demo", command_names)
        self.assertIn("contract-check", command_names)
        self.assertEqual(parser.prog, inventory["prog"])

        memory = next(command for command in inventory["commands"] if command["name"] == "memory")
        memory_options = {
            option
            for argument in memory["arguments"]
            for option in argument.get("option_strings", [])
        }
        self.assertIn("--anchor-freshness", memory_options)
        self.assertIn("--diff-context", memory_options)

        privacy_demo = next(command for command in inventory["commands"] if command["name"] == "privacy-guard-demo")
        privacy_demo_options = {
            option
            for argument in privacy_demo["arguments"]
            for option in argument.get("option_strings", [])
        }
        self.assertIn("--mode", privacy_demo_options)
        self.assertIn("--format", privacy_demo_options)

    def test_cli_commands_json_is_parseable(self) -> None:
        output = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "repomori",
                "commands",
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
        )

        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], "repomori.cli_commands.v1")
        self.assertIn("commands", payload["summary"]["commands"])
        self.assertTrue(any(command["name"] == "context" for command in payload["commands"]))

    def test_cli_reference_markdown_is_current(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        expected = cli.format_cli_reference_markdown(cli.build_cli_command_inventory())
        actual = (repo / "docs" / "cli-reference.md").read_text(encoding="utf-8")

        self.assertEqual(actual, expected)

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
            self.assertEqual(payload["checks"]["privacy_guard_demo"]["status"], "pass")
            self.assertEqual(payload["checks"]["privacy_guard_demo"]["summary"]["failing_guard_status"], "fail")
            self.assertEqual(payload["checks"]["privacy_guard_demo"]["summary"]["leaked_marker_codes"], [])

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
            self.assertIn("compat", payload["checks"])
            self.assertEqual(payload["checks"]["release_check"]["schema_version"], "repomori.release_check.v1")
            self.assertEqual(payload["checks"]["compat"]["schema_version"], "repomori.compat.v1")
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
            self.assertEqual(payload["checks"]["compat"]["status"], "warn")

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
            self.assertTrue(Path(payload["artifacts"]["compat_json"]).exists())
            self.assertTrue(Path(payload["artifacts"]["compat_markdown"]).exists())
            self.assertTrue(Path(payload["artifacts"]["contract_json"]).exists())
            self.assertTrue(Path(payload["artifacts"]["contract_markdown"]).exists())
            self.assertEqual(payload["artifacts"]["json"], str(artifacts_dir / "release-health.json"))
            self.assertEqual(payload["artifacts"]["markdown"], str(artifacts_dir / "release-health.md"))
            self.assertEqual(payload["artifacts"]["compat_json"], str(artifacts_dir / "compat.json"))
            self.assertEqual(payload["artifacts"]["compat_markdown"], str(artifacts_dir / "compat.md"))
            self.assertEqual(payload["artifacts"]["contract_json"], str(artifacts_dir / "contract-check.json"))
            self.assertEqual(payload["artifacts"]["contract_markdown"], str(artifacts_dir / "contract-check.md"))
            compat_artifact = json.loads((artifacts_dir / "compat.json").read_text(encoding="utf-8"))
            self.assertEqual(compat_artifact["schema_version"], "repomori.compat.v1")
            contract_artifact = json.loads((artifacts_dir / "contract-check.json").read_text(encoding="utf-8"))
            self.assertEqual(contract_artifact["schema_version"], "repomori.contract_check.v1")
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

    def test_cli_verify_release_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(root, version="0.2.0", generated_at=1700000000)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "verify-release",
                    str(root),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.release_verify.v1")
            self.assertEqual(payload["status"], "pass")

    def test_cli_verify_release_policy_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(root, version="0.2.0", generated_at=1700000000)
            policy = Path(tmp) / "release-policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema_version": "repomori.release_policy.v1",
                        "require": {"checksums": True, "provenance": True, "sbom": True},
                        "allowed_statuses": {"release_verify": ["pass"]},
                        "required_schemas": {
                            "release_candidate": "repomori.release_candidate.v1",
                            "provenance": "repomori.release_provenance.v1",
                            "sbom": "SPDX-2.3",
                            "release_verify": "repomori.release_verify.v1",
                        },
                        "max_errors": 0,
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "verify-release",
                    str(root),
                    "--policy",
                    str(policy),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.release_verify.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["policy"]["status"], "pass")
            self.assertEqual(payload["summary"]["policy_status"], "pass")

    def test_cli_verify_release_policy_writes_reviewer_artifacts(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        policy = repo_root / "tests/fixtures/release-policy-dev-unsigned.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._release_policy_package(Path(tmp), signed=False)
            json_out = root / "release-verify-policy.json"
            markdown_out = root / "release-verify-policy.md"

            json_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "verify-release",
                    str(root),
                    "--policy",
                    str(policy),
                    "--json",
                    "--out",
                    str(json_out),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            markdown_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "verify-release",
                    str(root),
                    "--policy",
                    str(policy),
                    "--format",
                    "markdown",
                    "--out",
                    str(markdown_out),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(json_result.returncode, 0, json_result.stderr)
            self.assertEqual(markdown_result.returncode, 0, markdown_result.stderr)
            self.assertTrue(json_out.is_file())
            self.assertTrue(markdown_out.is_file())
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            markdown = markdown_out.read_text(encoding="utf-8")
            self.assertEqual(payload["schema_version"], "repomori.release_verify.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["policy"]["profile"], "dev_unsigned")
            self.assertEqual(payload["policy"]["review"]["decision"], "reviewable")
            self.assertIn("Profile: `dev_unsigned`", markdown)
            self.assertIn("Review decision: `reviewable`", markdown)
            self.assertIn("### Policy Profile Preflight", markdown)
            self.assertIn("docs/release-policy-selection.md", markdown)
            self.assertIn("docs/release-policy-matrix.md", markdown)
            self.assertIn("docs/release-policy.md#policy-diagnostics", markdown)
            self.assertIn("### Reviewer Next Steps", markdown)

    def test_cli_release_evidence_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            (dist / "repomori-0.2.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(
                root,
                version="0.2.0",
                commit="abc123",
                ref="main",
                run_id="42",
                repository="Martin123132/RepoMori",
                generated_at=1700000000,
            )
            release_check_dir = tmp_path / ".repomori-release-check"
            release_check_dir.mkdir()
            release_check_path = release_check_dir / "release-check.json"
            release_check_path.write_text(
                json.dumps(
                    {
                        "schema_version": "repomori.release_check.v1",
                        "status": "pass",
                        "summary": {"failed_checks": []},
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "release-evidence",
                    str(root),
                    "--release-check",
                    str(release_check_path),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.release_evidence.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["checks"]["release_verify"]["status"], "pass")
            self.assertEqual(payload["checks"]["signatures"]["status"], "unsigned")

    def test_cli_verify_release_fails_for_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".repomori-release-candidate"
            dist = root / "dist"
            dist.mkdir(parents=True)
            wheel = dist / "repomori-0.2.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel-bytes")
            (dist / "repomori-0.2.0-source.zip").write_bytes(b"source-bytes")
            write_release_package_artifacts(root, version="0.2.0", generated_at=1700000000)
            wheel.write_bytes(b"tampered")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "verify-release",
                    str(root),
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.release_verify.v1")
            self.assertEqual(payload["status"], "fail")
            self.assertTrue(any(error["code"] == "artifact_hash_mismatch" for error in payload["errors"]))

    def test_cli_compat_json_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            handoff_dir = Path(tmp) / "handoff"
            build_handoff_package(pack, "sqlite Store", handoff_dir)

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "repomori",
                    "compat",
                    str(pack),
                    "--handoff",
                    str(handoff_dir),
                    "--verify-pack",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema_version"], "repomori.compat.v1")
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(payload["pack_verification"]["verified"])
            self.assertEqual(payload["summary"]["pack_schema"], codec.SCHEMA_VERSION)

    def test_cli_contract_check_json_is_parseable(self) -> None:
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "compat-contracts.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "repomori",
                "contract-check",
                "--fixture",
                str(fixture_path),
                "--json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], "repomori.contract_check.v1")
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["summary"]["change_count"], 0)

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

    def _compat_contract_fixture(self) -> dict:
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "compat-contracts.json"
        return json.loads(fixture_path.read_text(encoding="utf-8"))

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
