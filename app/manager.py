from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .library import (
    SUPPORTED_EXTENSIONS,
    InvalidPath,
    resolve_model_source,
    resolve_project_dir,
    resolve_under,
)

ALLOWED_SIDECAR_EXTENSIONS = {
    ".avif",
    ".basis",
    ".bin",
    ".bmp",
    ".dds",
    ".exr",
    ".gif",
    ".hdr",
    ".jpeg",
    ".jpg",
    ".ktx2",
    ".png",
    ".tga",
    ".tif",
    ".tiff",
    ".webp",
}

MAX_UPLOAD_FILES = 64


class ConflictError(ValueError):
    """Raised when a project or file already exists."""


@dataclass(frozen=True)
class UploadFile:
    filename: str
    data: bytes


def _clean_name(raw: str, label: str) -> str:
    value = (raw or "").strip()
    if (
        not value
        or value in {".", ".."}
        or value.startswith(".")
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or len(value) > 200
    ):
        raise InvalidPath(f"{label} inválido")
    return value


def _clean_filename(raw: str) -> str:
    value = (raw or "").strip()
    if (
        not value
        or value in {".", ".."}
        or value.startswith(".")
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or len(value) > 240
    ):
        raise InvalidPath("nome de arquivo inválido")
    return value


def _models_dir(project_dir: Path, *, create: bool = False) -> Path:
    if not project_dir.is_dir():
        raise FileNotFoundError(str(project_dir))
    models = project_dir / "models"
    if not models.is_dir():
        if not create:
            raise FileNotFoundError(str(models))
        models.mkdir()
    return models.resolve()


def create_project(projects_root: Path, name: str) -> Path:
    project_name = _clean_name(name, "nome do projeto")
    target = resolve_project_dir(projects_root, project_name)
    if target.exists():
        raise ConflictError(f"o projeto {project_name!r} já existe")
    try:
        (target / "models").mkdir(parents=True)
    except FileExistsError as exc:
        raise ConflictError(f"o projeto {project_name!r} já existe") from exc
    return target


def rename_project(projects_root: Path, project_id: str, name: str) -> Path:
    source = resolve_project_dir(projects_root, project_id)
    if not source.is_dir():
        raise FileNotFoundError(project_id)
    new_name = _clean_name(name, "nome do projeto")
    target = resolve_project_dir(projects_root, new_name)
    if target == source:
        return source
    if target.exists():
        raise ConflictError(f"já existe um projeto chamado {new_name!r}")
    source.rename(target)
    return target


def delete_project(projects_root: Path, project_id: str) -> None:
    project = resolve_project_dir(projects_root, project_id)
    if not project.is_dir():
        raise FileNotFoundError(project_id)
    shutil.rmtree(project)


def import_model(project_dir: Path, files: list[UploadFile]) -> str:
    if not files:
        raise InvalidPath("nenhum arquivo enviado")
    if len(files) > MAX_UPLOAD_FILES:
        raise InvalidPath(f"envie no máximo {MAX_UPLOAD_FILES} arquivos por vez")

    cleaned: list[UploadFile] = []
    seen: set[str] = set()
    for uploaded in files:
        filename = _clean_filename(uploaded.filename)
        if filename in seen:
            raise InvalidPath(f"arquivo duplicado: {filename}")
        seen.add(filename)
        cleaned.append(UploadFile(filename=filename, data=uploaded.data))

    primaries = [
        item
        for item in cleaned
        if Path(item.filename).suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if len(primaries) != 1:
        raise InvalidPath(
            "envie exatamente um modelo .blend, .fbx, .glb ou .gltf por vez"
        )

    primary = primaries[0]
    for item in cleaned:
        suffix = Path(item.filename).suffix.lower()
        if item is primary:
            continue
        if suffix not in ALLOWED_SIDECAR_EXTENSIONS:
            raise InvalidPath(
                f"arquivo auxiliar não permitido: {item.filename}"
            )

    models = _models_dir(project_dir, create=True)
    folder_name = _clean_name(Path(primary.filename).stem, "nome do modelo")
    target = resolve_under(models, folder_name)
    if target.exists():
        raise ConflictError(
            f"já existe uma pasta de modelo chamada {folder_name!r}"
        )

    temp_dir = Path(tempfile.mkdtemp(prefix=".upload-", dir=models))
    try:
        for item in cleaned:
            (temp_dir / item.filename).write_bytes(item.data)
        try:
            temp_dir.rename(target)
        except FileExistsError as exc:
            raise ConflictError(
                f"já existe uma pasta de modelo chamada {folder_name!r}"
            ) from exc
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return (PurePosixPath(folder_name) / primary.filename).as_posix()


def _model_files(directory: Path) -> list[Path]:
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    return [
        path
        for path in entries
        if (
            path.is_file()
            and not path.name.startswith(".")
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    ]


def rename_model(project_dir: Path, relative_path: str, name: str) -> str:
    source = resolve_model_source(project_dir, relative_path)
    models = _models_dir(project_dir)
    new_stem = _clean_name(name, "nome do modelo")
    relative = PurePosixPath(relative_path)
    new_relative = relative.with_name(new_stem + source.suffix).as_posix()
    destination = resolve_under(models, new_relative)

    if destination == source:
        return relative_path
    if destination.exists():
        raise ConflictError(f"já existe um arquivo chamado {new_relative!r}")

    dedicated_folder = (
        len(relative.parent.parts) == 1
        and relative.parent.name == source.stem
        and len(_model_files(source.parent)) == 1
    )
    if dedicated_folder:
        new_folder = resolve_under(models, new_stem)
        if new_folder.exists():
            raise ConflictError(
                f"já existe uma pasta de modelo chamada {new_stem!r}"
            )
        source.parent.rename(new_folder)
        try:
            renamed = new_folder / (new_stem + source.suffix)
            (new_folder / source.name).rename(renamed)
        except Exception:
            new_folder.rename(source.parent)
            raise
        return PurePosixPath(new_stem, renamed.name).as_posix()

    source.rename(destination)
    return new_relative


def delete_model(project_dir: Path, relative_path: str) -> None:
    source = resolve_model_source(project_dir, relative_path)
    models = _models_dir(project_dir)
    parent = source.parent
    stem = source.stem
    source.unlink()
    if (
        parent != models
        and parent.name == stem
        and not _model_files(parent)
    ):
        shutil.rmtree(parent)
