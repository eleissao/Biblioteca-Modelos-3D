"""Blender-side exporter invoked by app/converter.py.

Usage:
    blender -b file.blend --python blender_export.py -- out.glb '["Plane"]'
"""

from __future__ import annotations

import json
import sys

import bpy  # type: ignore[import-not-found]


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :]
    if not argv:
        raise SystemExit("uso: blender_export.py -- <saida.glb> [exclusões-json]")

    output = argv[0]
    excluded_names = set()
    if len(argv) > 1:
        excluded_names = {
            str(name).casefold() for name in json.loads(argv[1])
        }

    removed: list[str] = []
    kept_for_particles: list[str] = []
    particle_sources = {
        particle_system.settings.instance_object
        for owner in bpy.context.scene.objects
        for particle_system in owner.particle_systems
        if particle_system.settings.instance_object is not None
    }
    for obj in list(bpy.context.scene.objects):
        if obj.type == "MESH" and obj.name.casefold() in excluded_names:
            if obj in particle_sources:
                # The object is a template for particle instances (for example,
                # a leaf card). Remove only the template from the scene; the
                # glTF exporter still needs its mesh to build the instances.
                kept_for_particles.append(obj.name)
                for collection in list(obj.users_collection):
                    collection.objects.unlink(obj)
            else:
                removed.append(obj.name)
                bpy.data.objects.remove(obj, do_unlink=True)

    renderable = [
        obj
        for obj in bpy.context.scene.objects
        if obj.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}
    ]
    if not renderable:
        raise SystemExit("nenhuma malha renderizável após o filtro")

    print("EXCLUDED_OBJECTS", removed)
    print("KEPT_PARTICLE_SOURCES", kept_for_particles)
    print("EXPORTED_OBJECTS", len(renderable))

    bpy.ops.export_scene.gltf(
        filepath=output,
        export_format="GLB",
        export_cameras=False,
        export_lights=False,
        export_apply=True,
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6,
        export_draco_position_quantization=14,
        export_draco_normal_quantization=10,
        export_draco_texcoord_quantization=12,
    )


main()
