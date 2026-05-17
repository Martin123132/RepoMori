from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from repomori.codec import BuildOptions, build_pack, get_file_bytes, info_pack, query_pack, tree_pack


class RepoMoriCodecTests(unittest.TestCase):
    def test_build_query_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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

            summary = build_pack(repo, pack, BuildOptions(force=True))
            self.assertEqual(summary["file_count"], 3)

            info = info_pack(pack)
            self.assertEqual(info["counts"]["files"], 3)
            self.assertGreaterEqual(info["counts"]["symbols"], 2)

            results = query_pack(pack, "sqlite Store", limit=3)
            self.assertEqual(results[0]["path"], "app.py")
            self.assertIn("symbol", results[0]["why"])

            restored = get_file_bytes(pack, "app.py")
            self.assertEqual(restored, (repo / "app.py").read_bytes())

            tree = tree_pack(pack)
            self.assertEqual([row["path"] for row in tree], ["README.md", "app.py", "blob.bin"])


if __name__ == "__main__":
    unittest.main()
