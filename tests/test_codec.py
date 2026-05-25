from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repomori.codec import (
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
    format_benchmark_markdown,
    format_brief_markdown,
    format_compare_markdown,
    format_eval_markdown,
    format_context_markdown,
    format_snapshot_markdown,
    format_timeline_markdown,
    get_file_bytes,
    handle_agent_request,
    handle_mcp_request,
    init_config,
    info_pack,
    load_memory_config,
    query_pack,
    prune_snapshots,
    read_snapshot_timeline,
    run_mcp_bridge,
    run_demo,
    run_memory_cycle,
    run_release_check,
    schema_catalog,
    scan_baseline_from_report,
    scan_repository,
    snapshot_repo,
    tree_pack,
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
            self.assertEqual(report["summary"]["mcp_context_schema"], "repomori.context.v1")
            self.assertIn("repomori_context_build", report["mcp"]["tool_names"])
            self.assertTrue((out / "demo-repo" / "app.py").exists())
            self.assertTrue((out / "demo.repomori").exists())
            self.assertTrue((out / "context.md").exists())
            self.assertTrue((out / "repomori.toml").exists())
            self.assertTrue((out / "packs" / "latest.repomori").exists())
            self.assertEqual(json.loads((out / "demo.json").read_text(encoding="utf-8")), report)
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

            baseline_path = Path(tmp) / "scan-baseline.json"
            written = write_scan_baseline(original, baseline_path)
            self.assertEqual(written["schema_version"], "repomori.scan.baseline.write.v1")
            self.assertEqual(written["ignored_count"], 1)
            ignored_by_file = scan_repository(repo, public_release=True, baseline=baseline_path)
            self.assertEqual(ignored_by_file["status"], "pass")

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
            self.assertFalse(loaded["settings"]["prune_apply"])

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

            doctor_response = handle_agent_request({"id": "doctor", "method": "doctor.run"}, config_path=config)
            self.assertTrue(doctor_response["ok"])
            self.assertEqual(doctor_response["result"]["schema_version"], "repomori.doctor.v1")

            timeline_response = handle_agent_request(
                {"id": "timeline", "method": "timeline.read", "params": {"limit": 1}},
                config_path=config,
            )
            self.assertTrue(timeline_response["ok"])
            self.assertEqual(timeline_response["result"]["schema_version"], "repomori.timeline.v1")

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
        self.assertIn("repomori_schema_list", first_names)
        memory_tool = next(tool for tool in first_list["result"]["tools"] if tool["name"] == "repomori_memory_run")
        self.assertIn("incremental", memory_tool["inputSchema"]["properties"])

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
        self.assertIn("repomori.agent.response.v1", schema_versions)
        self.assertIn("context.build", catalog["agent_methods"])
        self.assertIn("schema.list", catalog["agent_methods"])
        self.assertIn("repomori_schema_list", catalog["mcp_tools"])

        memory = schema_catalog("repomori.memory.v1")
        self.assertEqual(memory["selected"], "repomori.memory.v1")
        self.assertEqual(memory["schema"]["producer"], "run_memory_cycle")
        self.assertIn("timeline", memory["schema"]["required_fields"])

    def test_golden_fixture_core_output_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, pack = self._demo_pack(root, build=True)
            handoff_dir = root / "handoff"
            memory_dir = root / "memory"

            context = build_context_bundle(pack, "sqlite Store", limit=1, max_bytes=200)
            capsule = build_capsule(pack, max_files=2)
            handoff = build_handoff_package(pack, "sqlite Store", handoff_dir)
            memory = run_memory_cycle(repo, memory_dir, no_handoff=True)
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
                "handoff": (
                    handoff,
                    "repomori.handoff.v1",
                    {"schema_version", "status", "question", "out_dir", "artifacts", "verification"},
                ),
                "memory": (
                    memory,
                    "repomori.memory.v1",
                    {"schema_version", "status", "repo_path", "out_dir", "settings", "summary", "snapshot", "doctor", "prune", "timeline"},
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
            self.assertEqual(memory["status"], "pass")
            self.assertEqual(scan["status"], "warn")
            self.assertIn("memory.run", agent_help["result"]["methods"])

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
            self.assertTrue(config.exists())
            self.assertIn("incremental = true", config.read_text(encoding="utf-8"))
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
