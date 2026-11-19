from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.library import InvalidPath
from app.manager import (
    ConflictError,
    UploadFile,
    create_project,
    delete_model,
    delete_project,
    import_model,
    rename_model,
    rename_project,
)


class ManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.projects = self.root / "projects"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_project_lifecycle(self) -> None:
        project = create_project(self.projects, "Meu projeto")
        self.assertTrue((project / "models").is_dir())

        with self.assertRaises(ConflictError):
            create_project(self.projects, "Meu projeto")

        renamed = rename_project(self.projects, "Meu projeto", "Outro nome")
        self.assertTrue(renamed.is_dir())
        self.assertFalse(project.exists())

        delete_project(self.projects, "Outro nome")
        self.assertFalse(renamed.exists())

    def test_project_names_reject_traversal(self) -> None:
        for name in ["../fora", "a/b", "a\\b", ".oculto", ".."]:
            with self.subTest(name=name):
                with self.assertRaises(InvalidPath):
                    create_project(self.projects, name)

    def test_import_model_creates_folder_with_sidecars(self) -> None:
        project = create_project(self.projects, "Plantas")
        relative = import_model(
            project,
            [
                UploadFile("samambaia.gltf", b"{}"),
                UploadFile("samambaia.bin", b"bin"),
                UploadFile("textura.png", b"png"),
            ],
        )

        self.assertEqual(relative, "samambaia/samambaia.gltf")
        self.assertEqual(
            (project / "models" / relative).read_bytes(),
            b"{}",
        )
        self.assertTrue(
            (project / "models/samambaia/textura.png").is_file()
        )

    def test_import_fbx_accepts_external_textures(self) -> None:
        project = create_project(self.projects, "Árvores")
        relative = import_model(
            project,
            [
                UploadFile("buriti.fbx", b"fbx"),
                UploadFile("buriti.tga", b"tga"),
            ],
        )

        self.assertEqual(relative, "buriti/buriti.fbx")
        self.assertTrue((project / "models/buriti/buriti.tga").is_file())

    def test_import_model_creates_missing_models_folder(self) -> None:
        project = self.projects / "Projeto manual"
        project.mkdir(parents=True)

        relative = import_model(project, [UploadFile("a.glb", b"a")])

        self.assertEqual(relative, "a/a.glb")
        self.assertTrue((project / "models/a/a.glb").is_file())

    def test_import_model_rejects_invalid_uploads(self) -> None:
        project = create_project(self.projects, "Plantas")

        with self.assertRaises(InvalidPath):
            import_model(project, [])
        with self.assertRaises(InvalidPath):
            import_model(
                project,
                [
                    UploadFile("a.glb", b"a"),
                    UploadFile("b.glb", b"b"),
                ],
            )
        with self.assertRaises(InvalidPath):
            import_model(project, [UploadFile("../fora.glb", b"a")])
        with self.assertRaises(InvalidPath):
            import_model(
                project,
                [
                    UploadFile("a.glb", b"a"),
                    UploadFile("script.exe", b"x"),
                ],
            )
        import_model(project, [UploadFile("a.glb", b"a")])
        with self.assertRaises(ConflictError):
            import_model(project, [UploadFile("a.glb", b"a")])

    def test_rename_model_renames_dedicated_folder(self) -> None:
        project = create_project(self.projects, "Plantas")
        models = project / "models"
        source = models / "samambaia/samambaia.glb"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"glb")
        (source.parent / "textura.png").write_bytes(b"png")

        relative = rename_model(project, "samambaia/samambaia.glb", "costela")

        self.assertEqual(relative, "costela/costela.glb")
        self.assertFalse((models / "samambaia").exists())
        self.assertTrue((models / "costela/costela.glb").is_file())
        self.assertTrue((models / "costela/textura.png").is_file())

    def test_rename_model_keeps_shared_folder(self) -> None:
        project = create_project(self.projects, "Plantas")
        models = project / "models"
        (models / "compartilhado").mkdir(parents=True)
        (models / "compartilhado/a.glb").write_bytes(b"a")
        (models / "compartilhado/b.glb").write_bytes(b"b")

        relative = rename_model(project, "compartilhado/a.glb", "c")

        self.assertEqual(relative, "compartilhado/c.glb")
        self.assertTrue((models / "compartilhado/b.glb").is_file())
        self.assertTrue((models / "compartilhado/c.glb").is_file())

    def test_delete_model_removes_empty_dedicated_folder(self) -> None:
        project = create_project(self.projects, "Plantas")
        models = project / "models"
        source = models / "samambaia/samambaia.glb"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"glb")
        (source.parent / "textura.png").write_bytes(b"png")

        delete_model(project, "samambaia/samambaia.glb")

        self.assertFalse((models / "samambaia").exists())

    def test_delete_model_keeps_shared_folder(self) -> None:
        project = create_project(self.projects, "Plantas")
        models = project / "models"
        (models / "compartilhado").mkdir(parents=True)
        (models / "compartilhado/a.glb").write_bytes(b"a")
        (models / "compartilhado/b.glb").write_bytes(b"b")

        delete_model(project, "compartilhado/a.glb")

        self.assertFalse((models / "compartilhado/a.glb").exists())
        self.assertTrue((models / "compartilhado/b.glb").is_file())

    def test_model_paths_reject_traversal(self) -> None:
        project = create_project(self.projects, "Plantas")
        (self.root / "secret.glb").write_bytes(b"secret")

        with self.assertRaises(InvalidPath):
            rename_model(project, "../secret.glb", "x")
        with self.assertRaises(InvalidPath):
            delete_model(project, "../secret.glb")


if __name__ == "__main__":
    unittest.main()
