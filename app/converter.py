from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class ConversionError(RuntimeError):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


def resolve_blender(configured: str | None) -> Path | None:
    """Find Blender. BLENDER_BIN wins over config, then PATH."""
    candidates: list[str] = []
    env_value = os.environ.get("BLENDER_BIN")
    if env_value:
        candidates.append(env_value)
    if configured:
        candidates.append(configured)
    which = shutil.which("blender")
    if which:
        candidates.append(which)

    for candidate in candidates:
        path = Path(os.path.expanduser(candidate))
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


class ModelConverter:
    """Converts .blend and .fbx files to Draco-compressed GLB.

    The cache key includes the source mtime/size plus the exporter and Blender
    configuration. FBX uses a separate signature so the warmed .blend cache
    stays valid when the FBX importer changes.
    """

    def __init__(
        self,
        blender_bin: Path | None,
        cache_dir: Path,
        exclude_mesh_names: list[str],
        timeout_seconds: int = 300,
        script_path: Path | None = None,
        fbx_import_script_path: Path | None = None,
    ) -> None:
        self.blender_bin = blender_bin
        self.cache_dir = cache_dir
        self.exclude_mesh_names = list(exclude_mesh_names)
        self.timeout_seconds = timeout_seconds
        root = Path(__file__).resolve().parent.parent
        self.script_path = script_path or root / "blender_export.py"
        self.fbx_import_script_path = (
            fbx_import_script_path or root / "blender_import_fbx.py"
        )
        self.gl_cache_dir = self.cache_dir / "glb"
        self.tmp_dir = self.cache_dir / "tmp"
        self.gl_cache_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.cache_signature = self._config_signature()
        self.fbx_cache_signature = self._fbx_signature()
        self._conversion_slot = threading.Semaphore(1)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @staticmethod
    def _file_signature(path: Path) -> list[int]:
        try:
            stat = path.stat()
            return [stat.st_mtime_ns, stat.st_size]
        except OSError:
            return [0, 0]

    def _config_signature(self) -> str:
        payload = json.dumps(
            {
                "schema": 2,
                "exclude": sorted(
                    (str(name).casefold() for name in self.exclude_mesh_names)
                ),
                "script": self._file_signature(self.script_path),
                "blender": str(self.blender_bin) if self.blender_bin else "",
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]

    def _fbx_signature(self) -> str:
        payload = json.dumps(
            {
                "schema": 1,
                "format": "fbx",
                "exclude": sorted(
                    (str(name).casefold() for name in self.exclude_mesh_names)
                ),
                "export_script": self._file_signature(self.script_path),
                "import_script": self._file_signature(
                    self.fbx_import_script_path
                ),
                "blender": str(self.blender_bin) if self.blender_bin else "",
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _key(
        self, project_id: str, relative_path: str, suffix: str = ".blend"
    ) -> str:
        signature = (
            self.fbx_cache_signature
            if suffix.casefold() == ".fbx"
            else self.cache_signature
        )
        raw = f"{project_id}\x00{relative_path}\x00{signature}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:20]

    def _source_stamp(self, source: Path) -> str:
        stat = source.stat()
        stamp = f"{stat.st_mtime_ns}_{stat.st_size}"
        if source.suffix.lower() != ".fbx":
            return stamp

        # FBX often references external textures. Include the sibling files so
        # replacing a texture invalidates the cached GLB too.
        siblings: list[str] = []
        try:
            entries = list(source.parent.iterdir())
        except OSError:
            entries = []
        for candidate in entries:
            if (
                candidate == source
                or candidate.name.startswith(".")
                or not candidate.is_file()
            ):
                continue
            try:
                sibling = candidate.stat()
            except OSError:
                continue
            siblings.append(
                f"{candidate.name}\x00{sibling.st_mtime_ns}\x00{sibling.st_size}"
            )
        if siblings:
            digest = hashlib.sha256(
                "\x00".join(sorted(siblings)).encode("utf-8")
            ).hexdigest()[:10]
            stamp += f"_{digest}"
        return stamp

    def _cache_path(self, key: str, source: Path) -> Path:
        return self.gl_cache_dir / f"{key}_{self._source_stamp(source)}.glb"

    @staticmethod
    def _valid_glb(path: Path) -> bool:
        try:
            if path.stat().st_size < 20:
                return False
            with path.open("rb") as handle:
                return handle.read(4) == b"glTF"
        except OSError:
            return False

    def ensure_glb(self, project_id: str, relative_path: str, source: Path) -> Path:
        source = Path(source)
        suffix = source.suffix.lower()
        if suffix not in {".blend", ".fbx"}:
            raise ConversionError(
                "Formato não suportado para conversão.",
                f"Use .blend ou .fbx: {source.name}",
            )
        if self.blender_bin is None:
            raise ConversionError(
                "Blender não encontrado.",
                "Defina blender_bin em config.json ou a variável BLENDER_BIN.",
            )
        if not self.script_path.is_file():
            raise ConversionError(
                "Script de exportação ausente.",
                f"Esperado em: {self.script_path}",
            )
        if suffix == ".fbx" and not self.fbx_import_script_path.is_file():
            raise ConversionError(
                "Script de importação FBX ausente.",
                f"Esperado em: {self.fbx_import_script_path}",
            )

        key = self._key(project_id, relative_path, suffix)

        cache_path = self._cache_path(key, source)
        if self._valid_glb(cache_path):
            return cache_path

        with self._lock_for(key):
            cache_path = self._cache_path(key, source)
            if self._valid_glb(cache_path):
                return cache_path

            with self._conversion_slot:
                cache_path = self._cache_path(key, source)
                if self._valid_glb(cache_path):
                    return cache_path
                return self._convert(key, source, cache_path)

    def _convert(self, key: str, source: Path, cache_path: Path) -> Path:
        before = source.stat()
        handle, temp_name = tempfile.mkstemp(
            prefix=f"{key}_", suffix=".glb", dir=self.tmp_dir
        )
        os.close(handle)
        temp_path = Path(temp_name)

        suffix = source.suffix.lower()
        command = [
            str(self.blender_bin),
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "1",
        ]
        environment = None
        if suffix == ".fbx":
            command += ["--python", str(self.fbx_import_script_path)]
            environment = dict(os.environ)
            environment["BIBLIOTECA_FBX_SOURCE"] = str(source)
        else:
            command.append(str(source))
        command += [
            "--python",
            str(self.script_path),
            "--",
            str(temp_path),
            json.dumps(self.exclude_mesh_names, ensure_ascii=False),
        ]

        started = time.monotonic()
        logger.info("Convertendo %s", source)
        try:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                raise ConversionError(
                    f"Tempo limite excedido ao converter {source.name}.",
                    f"Limite: {self.timeout_seconds}s. "
                    "Aumente conversion_timeout_seconds.",
                ) from exc

            if result.returncode != 0 or not self._valid_glb(temp_path):
                detail = (result.stderr or result.stdout or "").strip()
                if len(detail) > 4000:
                    detail = "…" + detail[-4000:]
                raise ConversionError(
                    f"Blender falhou ao converter {source.name}.",
                    detail or f"código de saída {result.returncode}",
                )

            try:
                after = source.stat()
            except OSError as exc:
                raise ConversionError(
                    f"O arquivo {source.name} mudou durante a conversão.", ""
                ) from exc

            if (after.st_mtime_ns, after.st_size) != (
                before.st_mtime_ns,
                before.st_size,
            ):
                raise ConversionError(
                    f"O arquivo {source.name} mudou durante a conversão.",
                    "Recarregue a página para tentar novamente.",
                )

            self.gl_cache_dir.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, cache_path)
        finally:
            temp_path.unlink(missing_ok=True)

        self._cleanup_old(key, cache_path)
        elapsed = time.monotonic() - started
        logger.info(
            "Convertido %s em %.1fs (%.1f MB)",
            source.name,
            elapsed,
            cache_path.stat().st_size / 1_000_000,
        )
        return cache_path

    def _cleanup_old(self, key: str, keep: Path) -> None:
        for old in self.gl_cache_dir.glob(f"{key}_*.glb"):
            if old != keep:
                try:
                    old.unlink()
                except OSError:
                    pass
