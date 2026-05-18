from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repomori.codec import (
    BuildOptions,
    build_capsule,
    build_context_bundle,
    build_handoff_package,
    build_pack,
    check_handoff_package,
    evaluate_pack,
    format_eval_markdown,
    format_context_markdown,
    get_file_bytes,
    info_pack,
    query_pack,
    tree_pack,
    verify_pack,
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
            self.assertIn("context.md", artifacts)
            self.assertIn("capsule.json", artifacts)
            for artifact in artifacts.values():
                artifact_path = out / artifact["path"]
                data = artifact_path.read_bytes()
                self.assertEqual(artifact["size"], len(data))
                self.assertEqual(artifact["sha256"], hashlib.sha256(data).hexdigest())

            for name in ("context.json", "capsule.json", "eval.json", "verify.json"):
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

    def test_check_handoff_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _repo, pack = self._demo_pack(Path(tmp), build=True)
            out = Path(tmp) / "handoff"
            build_handoff_package(pack, "sqlite Store", out)

            clean = check_handoff_package(out)
            self.assertTrue(clean["valid"])
            self.assertEqual(clean["checked_artifacts"], 7)
            self.assertEqual(clean["checked_json"], 4)

            (out / "context.md").write_text("tampered\n", encoding="utf-8")
            broken = check_handoff_package(out)
            self.assertFalse(broken["valid"])
            self.assertGreaterEqual(broken["error_count"], 1)
            self.assertTrue(any(error["scope"] == "artifact" for error in broken["errors"]))

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


if __name__ == "__main__":
    unittest.main()
