"""Blender-side FBX importer invoked by app/converter.py.

Blender runs this script before blender_export.py. The source path arrives in
BIBLIOTECA_FBX_SOURCE so the exporter keeps its existing command-line contract.
"""

from __future__ import annotations

import os

import bpy  # type: ignore[import-not-found]


def main() -> None:
    source = os.environ.get("BIBLIOTECA_FBX_SOURCE")
    if not source:
        raise SystemExit("BIBLIOTECA_FBX_SOURCE não definido")
    if not os.path.isfile(source):
        raise SystemExit(f"FBX não encontrado: {source}")

    # Do not leave the factory startup cube in the scene.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = bpy.ops.import_scene.fbx(filepath=source, use_image_search=True)
    if "FINISHED" not in result:
        raise SystemExit(f"falha ao importar {source}: {sorted(result)}")


main()
