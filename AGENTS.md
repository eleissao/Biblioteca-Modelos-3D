# AGENTS.md

## Run and verify

- `python3 server.py` serves the app at `http://127.0.0.1:8000` and, because the
  default host is `0.0.0.0`, on the machine's LAN addresses. Use
  `--host 127.0.0.1` to restrict it. There is no install step, build step,
  package manager, or Node/npm dependency.
- Configuration lives in `config.json`; `BLENDER_BIN` overrides `blender_bin`,
  and `--blender` overrides both.
- Run all tests with `python3 -m unittest discover -s tests -v`.
- Run one test with
  `python3 -m unittest tests.test_library.LibraryTests.test_scan_projects_and_models -v`.
- The server scans `projects/` on every `/api/projects` request. CRUD actions in
  the UI refetch the list after they finish; manual file changes still need
  `Ctrl+R` because there is no polling/watch mode, by design.

## Architecture

- `app/library.py` scans `projects/<project>/models/` recursively and builds
  URLs. Supported extensions are `.blend`, `.fbx`, `.glb`, `.gltf`; each
  supported file becomes one card, even inside a shared folder. Flat files are
  accepted too. `.blend` and `.fbx` get an `/api/glb` asset URL; `.glb` and
  `.gltf` are served directly. Projects with zero models are included.
- `app/server.py` is a stdlib `ThreadingHTTPServer`. `/assets/<project>/<path>`
  serves files relative to that project's `models/` directory; `.gltf` sidecars
  (`.bin`, textures) resolve through the same route. `/api/glb` converts a
  `.blend` or `.fbx` and serves the cached GLB. The write routes use
  `app/manager.py` for project creation/rename/delete and model
  upload/rename/delete. Uploads are `multipart/form-data`; the UI sends one
  primary model plus optional sidecars.
- `app/manager.py` validates names and paths through `library.py`, writes uploads
  to a hidden temp folder and renames it into place. Renaming a model also
  renames its dedicated folder; deleting the last model removes that folder.
  Shared folders stay in place.
- `app/converter.py` runs Blender headless through `blender_export.py`. FBX is
  imported first by `blender_import_fbx.py` into an empty scene; both scripts
  run in the same Blender process. Conversions are globally serialized because
  Blender is memory-heavy. The cache key includes project/path, source mtime and
  size, excluded mesh names, Blender path, and exporter script mtime/size. FBX
  has its own signature that also covers the importer script, and its cache
  filename includes a digest of sibling files so external texture changes
  invalidate it. `.blend` cache keys stay unchanged when the FBX importer does.
- Frontend is vanilla ES modules in `static/js/`; three.js r187 is vendored in
  `static/vendor/three/`. There is no bundler. Routes are hash-based:
  `#/`, `#/p/<project>`, `#/p/<project>/m/<model>`.
- Closing the dialog uses `history.replaceState` back to the project URL, so the
  dialog close does not re-render/reload every preview card.

## Do not break these

- The configured Blender is
  `/home/eleissa/Display/blender-5.2 (2).3-linux-x64/blender`. The apt Blender
  4.0.2 cannot read the Zstandard-compressed, 4.05 `.blend` files in this
  project and its glTF exporter fails without NumPy. Do not fall back to the
  system `blender` for this corpus.
- Ground filtering matches **exact** mesh names in `exclude_mesh_names`
  (`Plane`, `Ground`, `Chão`, `Piso`), case-insensitively. Do not add
  prefix/suffix matching: `Croata - planta` legitimately contains meshes named
  `Plane.002` and `Plane.004`.
- The one exception is a mesh used as a particle-system instance object: it is
  unlinked from the scene but kept in `bpy.data` so the exporter can emit its
  instances. `Àrvore janipapo` needs this because its leaves use a `Plane`
  template. Do not turn the exception into name-based special cases.
- FBX import must start from an empty scene (`read_factory_settings(use_empty=True)`).
  Otherwise the factory startup cube is exported alongside the model. Keep
  `blender_export.py` unchanged when possible so warmed `.blend` cache keys stay
  valid; put FBX-only import logic in `blender_import_fbx.py`.
- Preview cards use one shared WebGL renderer (`PreviewEngine` in
  `static/js/viewer.js`), not one context per card. Each card owns a 2D canvas
  that receives the last rendered frame. Previews start at 90% visibility and
  pause offscreen, but their scenes stay loaded; do not dispose them on scroll
  or the GLB is downloaded and parsed again. The corpus includes 16 to 20 MB GLBs,
  so keep the visible-only rendering and pause behavior.
- The dialog pauses previews and owns one extra WebGL context. Dispose geometry,
  materials, textures, renderer, controls, and listeners when closing it.
- CRUD paths must keep using `resolve_under`/`resolve_project_dir`; upload names
  cannot contain separators, `..`, or leading dots. Do not join request paths
  directly.
- Deleting `.cache/` is safe and forces re-conversion. It currently contains the
  warmed GLBs; deleting it makes the first `.blend` or `.fbx` open slow again.
- Path helpers intentionally reject absolute paths, `..`, backslashes, and
  symlink escapes. Use `resolve_under`/`resolve_project_dir` rather than joining
  request paths directly.
- The server has no authentication and binds to `0.0.0.0` by default so phones
  on the LAN can reach it. Keep destructive CRUD routes and upload limits in
  mind before exposing it beyond a trusted network.

## Tests and repo state

- Integration tests in `tests/test_converter.py` invoke the configured Blender,
  generate `.blend` and `.fbx` fixtures in temporary directories, and skip if
  Blender is missing. They do not touch `projects/`. `tests/test_manager.py` and
  `tests/test_server.py` cover CRUD, format routing, and traversal rejection.
- This directory is a Git repository. Do not commit without being asked.
- `README.md` is the user-facing setup/conversion guide; keep it in sync with
  changes to config keys, supported formats, or commands.
