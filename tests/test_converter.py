from __future__ import annotations

import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.converter import ModelConverter, resolve_blender

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"


def configured_blender() -> Path | None:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return resolve_blender(str(data.get("blender_bin") or ""))


def glb_json(path: Path) -> dict:
    with path.open("rb") as handle:
        magic, version, _ = struct.unpack("<III", handle.read(12))
        if magic != 0x46546C67:
            raise AssertionError("GLB inválido")
        chunk_length, _ = struct.unpack("<II", handle.read(8))
        return json.loads(handle.read(chunk_length))


class ConverterCacheTests(unittest.TestCase):
    def test_fbx_cache_includes_sibling_textures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fbx = tmp / "model.fbx"
            fbx.write_bytes(b"fbx")
            texture = tmp / "texture.png"
            texture.write_bytes(b"first")
            converter = ModelConverter(
                blender_bin=None,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane"],
                script_path=ROOT / "blender_export.py",
            )

            key = converter._key("P", "model/model.fbx", ".fbx")
            first = converter._cache_path(key, fbx)
            self.assertEqual(converter._cache_path(key, fbx), first)

            texture.write_bytes(b"second version")
            self.assertNotEqual(converter._cache_path(key, fbx), first)

    def test_blend_cache_ignores_sibling_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            blend = tmp / "model.blend"
            blend.write_bytes(b"blend")
            texture = tmp / "texture.png"
            texture.write_bytes(b"first")
            converter = ModelConverter(
                blender_bin=None,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane"],
                script_path=ROOT / "blender_export.py",
            )

            key = converter._key("P", "model/model.blend")
            first = converter._cache_path(key, blend)
            texture.write_bytes(b"second version")
            self.assertEqual(converter._cache_path(key, blend), first)

    def test_fbx_importer_does_not_invalidate_blend_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            importer = tmp / "import_fbx.py"
            importer.write_text("first", encoding="utf-8")
            base = ModelConverter(
                blender_bin=None,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane"],
                script_path=ROOT / "blender_export.py",
                fbx_import_script_path=importer,
            )
            before_blend = base.cache_signature
            before_fbx = base.fbx_cache_signature

            importer.write_text("second version", encoding="utf-8")
            changed = ModelConverter(
                blender_bin=None,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane"],
                script_path=ROOT / "blender_export.py",
                fbx_import_script_path=importer,
            )
            self.assertEqual(before_blend, changed.cache_signature)
            self.assertNotEqual(before_fbx, changed.fbx_cache_signature)


class ConverterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.blender = configured_blender()
        if cls.blender is None:
            raise unittest.SkipTest("Blender configurado não encontrado")

    def test_blend_to_glb_filters_ground_and_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            blend = tmp / "projects/P/models/model/model.blend"
            blend.parent.mkdir(parents=True)
            make_script = tmp / "make_fixture.py"
            make_script.write_text(
                "import bpy\n"
                "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
                "bpy.ops.mesh.primitive_uv_sphere_add(radius=1)\n"
                "bpy.ops.mesh.primitive_plane_add(size=20)\n"
                f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    str(self.blender),
                    "--background",
                    "--factory-startup",
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(make_script),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

            converter = ModelConverter(
                blender_bin=self.blender,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane"],
                timeout_seconds=120,
                script_path=ROOT / "blender_export.py",
            )

            first = converter.ensure_glb("P", "model/model.blend", blend)
            self.assertTrue(first.is_file())
            document = glb_json(first)
            nodes = [node.get("name", "") for node in document.get("nodes", [])]
            self.assertIn("Sphere", nodes)
            self.assertNotIn("Plane", nodes)

            second = converter.ensure_glb("P", "model/model.blend", blend)
            self.assertEqual(first, second)

    def test_fbx_to_glb_filters_ground_and_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fbx = tmp / "projects/P/models/model/model.fbx"
            fbx.parent.mkdir(parents=True)
            make_script = tmp / "make_fbx.py"
            make_script.write_text(
                "import bpy\n"
                "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
                "bpy.ops.mesh.primitive_uv_sphere_add(radius=1)\n"
                "bpy.context.object.name = 'Sphere'\n"
                "bpy.ops.mesh.primitive_plane_add(size=20)\n"
                "bpy.context.object.name = 'Plane'\n"
                f"bpy.ops.export_scene.fbx(filepath={str(fbx)!r}, "
                "use_selection=False)\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    str(self.blender),
                    "--background",
                    "--factory-startup",
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(make_script),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

            converter = ModelConverter(
                blender_bin=self.blender,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane"],
                timeout_seconds=120,
                script_path=ROOT / "blender_export.py",
                fbx_import_script_path=ROOT / "blender_import_fbx.py",
            )

            first = converter.ensure_glb("P", "model/model.fbx", fbx)
            self.assertTrue(first.is_file())
            document = glb_json(first)
            nodes = [node.get("name", "") for node in document.get("nodes", [])]
            self.assertIn("Sphere", nodes)
            self.assertNotIn("Plane", nodes)

            second = converter.ensure_glb("P", "model/model.fbx", fbx)
            self.assertEqual(first, second)

    def test_particle_instance_source_survives_ground_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            blend = tmp / "projects/P/models/leaves/leaves.blend"
            blend.parent.mkdir(parents=True)
            make_script = tmp / "make_particles.py"
            make_script.write_text(
                "import bpy\n"
                "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
                "bpy.ops.mesh.primitive_ico_sphere_add(radius=1)\n"
                "emitter = bpy.context.object\n"
                "emitter.name = 'Emitter'\n"
                "bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -3))\n"
                "ground = bpy.context.object\n"
                "ground.name = 'Ground'\n"
                "bpy.ops.mesh.primitive_plane_add(size=0.5, location=(4, 0, 0))\n"
                "leaf = bpy.context.object\n"
                "leaf.name = 'Plane'\n"
                "modifier = emitter.modifiers.new('Leaves', 'PARTICLE_SYSTEM')\n"
                "settings = emitter.particle_systems[0].settings\n"
                "settings.type = 'HAIR'\n"
                "settings.count = 7\n"
                "settings.render_type = 'OBJECT'\n"
                "settings.instance_object = leaf\n"
                "settings.emit_from = 'FACE'\n"
                "settings.hair_length = 1.5\n"
                f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    str(self.blender),
                    "--background",
                    "--factory-startup",
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(make_script),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

            converter = ModelConverter(
                blender_bin=self.blender,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane", "Ground"],
                timeout_seconds=120,
                script_path=ROOT / "blender_export.py",
            )
            output = converter.ensure_glb("P", "leaves/leaves.blend", blend)
            document = glb_json(output)
            nodes = document.get("nodes", [])
            roots = document["scenes"][0]["nodes"]
            self.assertEqual(
                [nodes[index].get("name") for index in roots],
                ["Emitter"],
            )
            self.assertEqual(
                sum(1 for node in nodes if node.get("name") == "Plane"),
                7,
            )
            self.assertNotIn(
                "Ground", [node.get("name") for node in nodes]
            )

    def test_cache_signature_changes_with_filter(self) -> None:
        blender = self.blender
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            base = ModelConverter(
                blender_bin=blender,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane"],
                script_path=ROOT / "blender_export.py",
            )
            changed = ModelConverter(
                blender_bin=blender,
                cache_dir=tmp / "cache",
                exclude_mesh_names=["Plane", "Ground"],
                script_path=ROOT / "blender_export.py",
            )
            self.assertNotEqual(
                base._key("P", "m/m.blend"),
                changed._key("P", "m/m.blend"),
            )
            self.assertNotEqual(
                base._key("P", "m/m.fbx", ".fbx"),
                changed._key("P", "m/m.fbx", ".fbx"),
            )




if __name__ == "__main__":
    unittest.main()
