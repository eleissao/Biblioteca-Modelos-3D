from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

SUPPORTED_EXTENSIONS = {
    ".blend": "blend",
    ".glb": "glb",
    ".gltf": "gltf",
}


class InvalidPath(ValueError):
    """Raised when a requested path escapes its allowed directory."""


def natural_key(value: str) -> tuple[object, ...]:
    """Case/accent-insensitive sort key that keeps numbers in numeric order."""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", normalized.casefold())
    )


@dataclass(frozen=True)
class Model:
    project_id: str
    relative_path: str
    name: str
    kind: str
    size: int
    mtime_ns: int

    @property
    def asset_url(self) -> str:
        if self.kind == "blend":
            return (
                "/api/glb?project="
                + quote(self.project_id, safe="")
                + "&path="
                + quote(self.relative_path, safe="")
            )
        return (
            "/assets/"
            + quote(self.project_id, safe="")
            + "/"
            + quote(self.relative_path, safe="/")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.relative_path,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "asset_url": self.asset_url,
        }


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    path: Path
    models: tuple[Model, ...]

    @property
    def model_count(self) -> int:
        return len(self.models)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "model_count": len(self.models),
            "models": [model.to_dict() for model in self.models],
        }


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _scan_models(project_id: str, models_dir: Path) -> list[Model]:
    if not models_dir.is_dir():
        return []

    models_root = models_dir.resolve()
    models: list[Model] = []

    for path in models_dir.rglob("*"):
        try:
            relative = path.relative_to(models_dir)
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        if not path.is_file():
            continue
        kind = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
        if kind is None:
            continue
        try:
            resolved = path.resolve(strict=True)
            stat = path.stat()
        except OSError:
            continue
        if not _is_within(resolved, models_root):
            continue

        rel_posix = relative.as_posix()
        models.append(
            Model(
                project_id=project_id,
                relative_path=rel_posix,
                name=path.stem,
                kind=kind,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        )

    models.sort(key=lambda model: natural_key(model.relative_path))
    return models


def scan_projects(projects_root: Path) -> list[Project]:
    """Scan projects/<project>/models. Projects without models are kept."""
    if not projects_root.is_dir():
        return []

    root = projects_root.resolve()
    projects: list[Project] = []

    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        try:
            resolved = project_dir.resolve(strict=True)
        except OSError:
            continue
        if not _is_within(resolved, root) or resolved == root:
            continue

        models_dir = project_dir / "models"
        projects.append(
            Project(
                id=project_dir.name,
                name=project_dir.name,
                path=project_dir,
                models=tuple(_scan_models(project_dir.name, models_dir)),
            )
        )

    projects.sort(key=lambda project: natural_key(project.name))
    return projects


def resolve_project_dir(projects_root: Path, project_id: str) -> Path:
    if (
        not project_id
        or project_id in {".", ".."}
        or "/" in project_id
        or "\\" in project_id
        or "\x00" in project_id
    ):
        raise InvalidPath("nome de projeto inválido")

    root = projects_root.resolve()
    candidate = (root / project_id).resolve()
    if candidate == root or not _is_within(candidate, root):
        raise InvalidPath("projeto fora da pasta projects")
    return candidate


def resolve_under(base: Path, relative: str) -> Path:
    if not relative or "\x00" in relative or "\\" in relative:
        raise InvalidPath("caminho inválido")

    pure = PurePosixPath(relative)
    if (
        relative in {".", ".."}
        or not pure.parts
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise InvalidPath("caminho inválido")

    base_resolved = base.resolve()
    candidate = base_resolved.joinpath(*pure.parts).resolve()
    if not _is_within(candidate, base_resolved):
        raise InvalidPath("caminho fora da pasta permitida")
    return candidate


def resolve_model_source(project_dir: Path, relative_path: str) -> Path:
    source = resolve_under(project_dir / "models", relative_path)
    if not source.is_file():
        raise FileNotFoundError(relative_path)
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvalidPath("extensão de modelo não suportada")
    return source
