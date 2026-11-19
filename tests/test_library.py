from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.library import (
    InvalidPath,
    natural_key,
    resolve_model_source,
    resolve_project_dir,
    resolve_under,
    scan_projects,
)


class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.projects = self.root / "projects"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def touch(self, relative: str, content: bytes = b"x") -> Path:
        path = self.projects / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_scan_projects_and_models(self) -> None:
        self.touch("Alpha/models/one/one.blend")
        self.touch("Alpha/models/two.glb")
        self.touch("Alpha/models/nested/deep/three.gltf")
        self.touch("Alpha/models/four/four.fbx")
        self.touch("Alpha/models/readme.txt")
        self.touch("Alpha/models/.hidden/hidden.glb")
        self.touch("Beta/models/.keep")
        (self.projects / "EmptyProject").mkdir(parents=True)
        self.touch(".hiddenproject/models/x.glb")

        projects = scan_projects(self.projects)
        self.assertEqual([p.name for p in projects], ["Alpha", "Beta", "EmptyProject"])

        alpha = projects[0]
        self.assertEqual([m.relative_path for m in alpha.models], [
            "four/four.fbx",
            "nested/deep/three.gltf",
            "one/one.blend",
            "two.glb",
        ])
        self.assertEqual(
            [m.kind for m in alpha.models], ["fbx", "gltf", "blend", "glb"]
        )
        self.assertEqual(projects[1].model_count, 0)

        fbx = alpha.models[0]
        parsed = parse_qs(urlsplit(fbx.asset_url).query)
        self.assertEqual(parsed["project"], ["Alpha"])
        self.assertEqual(parsed["path"], ["four/four.fbx"])

        blend = alpha.models[2]
        parsed = parse_qs(urlsplit(blend.asset_url).query)
        self.assertEqual(parsed["project"], ["Alpha"])
        self.assertEqual(parsed["path"], ["one/one.blend"])

        glb = alpha.models[3]
        self.assertEqual(glb.asset_url, "/assets/Alpha/two.glb")

    def test_flat_and_nested_files_are_accepted(self) -> None:
        self.touch("P/models/flat.GLB")
        self.touch("P/models/sub/nested.FBX")
        models = scan_projects(self.projects)[0].models
        self.assertEqual([m.kind for m in models], ["glb", "fbx"])

    def test_natural_sort(self) -> None:
        self.assertLess(natural_key("model 2"), natural_key("model 10"))
        self.assertLess(natural_key("Árvore"), natural_key("buriti"))

    def test_symlink_escape_is_skipped(self) -> None:
        outside = self.root / "outside.glb"
        outside.write_bytes(b"x")
        models_dir = self.projects / "P/models"
        models_dir.mkdir(parents=True)
        try:
            os.symlink(outside, models_dir / "escape.glb")
        except OSError:
            self.skipTest("symlinks indisponíveis")
        self.assertEqual(scan_projects(self.projects)[0].model_count, 0)

    def test_resolve_under_rejects_escapes(self) -> None:
        base = self.projects / "P/models"
        base.mkdir(parents=True)
        (base / "ok.glb").write_bytes(b"x")

        self.assertEqual(resolve_under(base, "ok.glb"), (base / "ok.glb").resolve())
        for bad in ["../x.glb", "/etc/passwd", "a/../../x", "a\\b.glb", "", "."]:
            with self.subTest(bad=bad), self.assertRaises(InvalidPath):
                resolve_under(base, bad)

    def test_resolve_project_dir_rejects_invalid_ids(self) -> None:
        self.projects.mkdir(parents=True)
        (self.projects / "P").mkdir()
        self.assertEqual(
            resolve_project_dir(self.projects, "P"),
            (self.projects / "P").resolve(),
        )
        for bad in ["", ".", "..", "a/b", "..\\x", "/tmp"]:
            with self.subTest(bad=bad), self.assertRaises(InvalidPath):
                resolve_project_dir(self.projects, bad)

    def test_resolve_model_source(self) -> None:
        project = self.projects / "P"
        source = project / "models/m/m.blend"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x")
        fbx = project / "models/m/m.fbx"
        fbx.write_bytes(b"x")
        self.assertEqual(
            resolve_model_source(project, "m/m.blend"), source.resolve()
        )
        self.assertEqual(
            resolve_model_source(project, "m/m.fbx"), fbx.resolve()
        )
        with self.assertRaises(FileNotFoundError):
            resolve_model_source(project, "m/missing.blend")
        (project / "models/m/file.txt").write_text("x")
        with self.assertRaises(InvalidPath):
            resolve_model_source(project, "m/file.txt")


if __name__ == "__main__":
    unittest.main()
