from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import shutil
import socket
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .converter import ConversionError, ModelConverter, resolve_blender
from .library import (
    InvalidPath,
    scan_projects,
    resolve_model_source,
    resolve_project_dir,
    resolve_under,
)
from .manager import (
    ConflictError,
    UploadFile,
    create_project,
    delete_model,
    delete_project,
    import_model,
    rename_model,
    rename_project,
)

logger = logging.getLogger("biblioteca")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.json"
MAX_JSON_BODY = 1024 * 1024
MAX_UPLOAD_BODY = 512 * 1024 * 1024

MIME_OVERRIDES = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".bin": "application/octet-stream",
    ".blend": "application/octet-stream",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
}


@dataclass
class AppConfig:
    root: Path
    host: str
    port: int
    projects_dir: Path
    static_dir: Path
    cache_dir: Path
    blender_bin: Path | None
    exclude_mesh_names: list[str]
    conversion_timeout_seconds: int


@dataclass
class AppContext:
    config: AppConfig
    converter: ModelConverter


def load_config(config_path: Path, overrides: dict[str, Any]) -> AppConfig:
    data: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": 8000,
        "projects_dir": "projects",
        "static_dir": "static",
        "cache_dir": ".cache",
        "blender_bin": "",
        "exclude_mesh_names": ["Plane", "Ground", "Chão", "Piso"],
        "conversion_timeout_seconds": 300,
    }

    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise SystemExit(f"config inválido: {config_path}")
        data.update(loaded)

    for key, value in overrides.items():
        if value is not None:
            data[key] = value

    def resolve_path(value: str | Path) -> Path:
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else ROOT / path

    projects_dir = resolve_path(data["projects_dir"]).resolve()
    static_dir = resolve_path(data["static_dir"]).resolve()
    cache_dir = resolve_path(data["cache_dir"]).resolve()

    blender = resolve_blender(str(data.get("blender_bin") or ""))

    exclude = data.get("exclude_mesh_names") or []
    if not isinstance(exclude, list):
        raise SystemExit("exclude_mesh_names deve ser uma lista")

    try:
        timeout = int(data.get("conversion_timeout_seconds", 300))
        port = int(data.get("port", 8000))
    except (TypeError, ValueError) as exc:
        raise SystemExit("port/conversion_timeout_seconds inválidos") from exc

    return AppConfig(
        root=ROOT,
        host=str(data.get("host", "127.0.0.1")),
        port=port,
        projects_dir=projects_dir,
        static_dir=static_dir,
        cache_dir=cache_dir,
        blender_bin=blender,
        exclude_mesh_names=[str(item) for item in exclude],
        conversion_timeout_seconds=timeout,
    )


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], context: AppContext) -> None:
        super().__init__(address, AppRequestHandler)
        self.context = context


class AppRequestHandler(BaseHTTPRequestHandler):
    server_version = "BibliotecaModelos/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def context(self) -> AppContext:
        return self.server.context  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("%s %s", self.address_string(), fmt % args)

    def do_HEAD(self) -> None:
        self._handle(head=True)

    def do_GET(self) -> None:
        self._handle(head=False)

    def do_POST(self) -> None:
        self._handle(head=False)

    def _handle(self, head: bool) -> None:
        try:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            self._dispatch(path, query, head, self.command)
        except ConflictError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except InvalidPath as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except FileNotFoundError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "arquivo não encontrado"})
        except ConversionError as exc:
            logger.error("%s | %s", exc, exc.detail)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc), "detail": exc.detail},
            )
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("erro não tratado em %s", self.path)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "erro interno no servidor"},
            )

    def _dispatch(
        self,
        path: str,
        query: dict[str, list[str]],
        head: bool,
        method: str,
    ) -> None:
        if path in {"/", "/index.html"}:
            self._send_file(self.context.config.static_dir / "index.html", head=head)
            return

        if path == "/favicon.ico":
            favicon = self.context.config.static_dir / "favicon.svg"
            if favicon.is_file():
                self._send_file(favicon, head=head)
            else:
                self._send_empty(HTTPStatus.NO_CONTENT)
            return

        if path.startswith("/static/"):
            relative = path[len("/static/") :]
            target = resolve_under(self.context.config.static_dir, relative)
            self._send_file(target, head=head)
            return

        if path.startswith("/assets/"):
            self._send_asset(path[len("/assets/") :], head=head)
            return

        if path in {"/api/health", "/health"}:
            if head:
                self._send_empty(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            blender = self.context.config.blender_bin
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "blender": str(blender) if blender else None,
                    "projects_dir": str(self.context.config.projects_dir),
                    "cache_dir": str(self.context.config.cache_dir),
                },
            )
            return

        if path == "/api/projects":
            if method == "POST":
                self._create_project()
                return
            if method != "GET":
                self._send_empty(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            projects = scan_projects(self.context.config.projects_dir)
            self._send_json(
                HTTPStatus.OK,
                {"projects": [project.to_dict() for project in projects]},
            )
            return

        if path == "/api/projects/rename":
            if method != "POST":
                self._send_empty(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            self._rename_project()
            return

        if path == "/api/projects/delete":
            if method != "POST":
                self._send_empty(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            self._delete_project()
            return

        if path == "/api/models/upload":
            if method != "POST":
                self._send_empty(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            self._upload_model(query)
            return

        if path == "/api/models/rename":
            if method != "POST":
                self._send_empty(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            self._rename_model()
            return

        if path == "/api/models/delete":
            if method != "POST":
                self._send_empty(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            self._delete_model()
            return

        if path == "/api/glb":
            if head:
                self._send_empty(HTTPStatus.METHOD_NOT_ALLOWED)
                return
            self._send_converted_glb(query)
            return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "rota não encontrada", "path": path},
        )

    def _read_body(self, max_bytes: int) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise InvalidPath("Content-Length é obrigatório")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise InvalidPath("Content-Length inválido") from exc
        if length < 0 or length > max_bytes:
            raise InvalidPath("corpo da requisição grande demais")
        body = self.rfile.read(length)
        if len(body) != length:
            raise InvalidPath("corpo da requisição incompleto")
        return body

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise InvalidPath("envie JSON no corpo da requisição")
        body = self._read_body(MAX_JSON_BODY)
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidPath("JSON inválido") from exc
        if not isinstance(data, dict):
            raise InvalidPath("JSON deve ser um objeto")
        return data

    def _read_multipart(self) -> list[UploadFile]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise InvalidPath("envie multipart/form-data no upload")
        body = self._read_body(MAX_UPLOAD_BODY)
        header = (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode("utf-8")
        try:
            message = BytesParser(policy=email_policy).parsebytes(header + body)
        except Exception as exc:
            raise InvalidPath("multipart inválido") from exc
        if not message.is_multipart():
            raise InvalidPath("multipart inválido")
        files: list[UploadFile] = []
        for part in message.iter_parts():
            filename = part.get_filename()
            if not filename:
                continue
            payload = part.get_payload(decode=True) or b""
            files.append(UploadFile(filename=filename, data=payload))
        return files

    def _project_dir(self, project_id: str) -> Path:
        if not project_id:
            raise InvalidPath("projeto é obrigatório")
        return resolve_project_dir(self.context.config.projects_dir, project_id)

    def _create_project(self) -> None:
        data = self._read_json()
        project = create_project(
            self.context.config.projects_dir, str(data.get("name", ""))
        )
        self._send_json(
            HTTPStatus.CREATED, {"ok": True, "project_id": project.name}
        )

    def _rename_project(self) -> None:
        data = self._read_json()
        project = rename_project(
            self.context.config.projects_dir,
            str(data.get("project", "")),
            str(data.get("name", "")),
        )
        self._send_json(
            HTTPStatus.OK, {"ok": True, "project_id": project.name}
        )

    def _delete_project(self) -> None:
        data = self._read_json()
        delete_project(
            self.context.config.projects_dir, str(data.get("project", ""))
        )
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _upload_model(self, query: dict[str, list[str]]) -> None:
        project_id = (query.get("project") or [""])[0]
        project_dir = self._project_dir(project_id)
        relative_path = import_model(project_dir, self._read_multipart())
        self._send_json(
            HTTPStatus.CREATED, {"ok": True, "path": relative_path}
        )

    def _rename_model(self) -> None:
        data = self._read_json()
        project_dir = self._project_dir(str(data.get("project", "")))
        relative_path = rename_model(
            project_dir,
            str(data.get("path", "")),
            str(data.get("name", "")),
        )
        self._send_json(
            HTTPStatus.OK, {"ok": True, "path": relative_path}
        )

    def _delete_model(self) -> None:
        data = self._read_json()
        project_dir = self._project_dir(str(data.get("project", "")))
        delete_model(
            project_dir, str(data.get("path", ""))
        )
        self._send_json(HTTPStatus.OK, {"ok": True})

    def _send_converted_glb(self, query: dict[str, list[str]]) -> None:
        project_id = (query.get("project") or [""])[0]
        relative_path = (query.get("path") or [""])[0]
        if not project_id or not relative_path:
            raise InvalidPath("project e path são obrigatórios")

        project_dir = resolve_project_dir(
            self.context.config.projects_dir, project_id
        )
        source = resolve_model_source(project_dir, relative_path)
        if source.suffix.lower() not in {".blend", ".fbx"}:
            raise InvalidPath("/api/glb só aceita arquivos .blend ou .fbx")

        cached = self.context.converter.ensure_glb(
            project_id, relative_path, source
        )
        self._send_file(cached, head=False)

    def _send_asset(self, relative: str, head: bool) -> None:
        project_id, separator, asset_path = relative.partition("/")
        if not separator:
            raise InvalidPath("caminho de asset inválido")

        project_dir = resolve_project_dir(
            self.context.config.projects_dir, project_id
        )
        target = resolve_under(project_dir / "models", asset_path)
        if not target.is_file():
            raise FileNotFoundError(asset_path)
        self._send_file(target, head=head)

    def _send_file(
        self,
        path: Path,
        head: bool = False,
        content_type: str | None = None,
        cache_control: str = "no-store",
    ) -> None:
        if not path.is_file():
            raise FileNotFoundError(str(path))

        size = path.stat().st_size
        if content_type is None:
            content_type = MIME_OVERRIDES.get(path.suffix.lower())
            if content_type is None:
                guessed, _ = mimetypes.guess_type(path.name)
                content_type = guessed or "application/octet-stream"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

        if head:
            return
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile, length=64 * 1024)

    def _send_json(
        self, status: HTTPStatus, payload: dict[str, Any]
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


def build_server(config: AppConfig) -> AppHTTPServer:
    converter = ModelConverter(
        blender_bin=config.blender_bin,
        cache_dir=config.cache_dir,
        exclude_mesh_names=config.exclude_mesh_names,
        timeout_seconds=config.conversion_timeout_seconds,
    )
    context = AppContext(config=config, converter=converter)
    return AppHTTPServer((config.host, config.port), context)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Servidor local da Biblioteca de Modelos 3D"
    )
    parser.add_argument(
        "--host",
        help="endereço de escuta (padrão: config, 0.0.0.0 para rede local)",
    )
    parser.add_argument("--port", type=int, help="porta (padrão: config)")
    parser.add_argument("--blender", help="caminho do executável do Blender")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="arquivo JSON")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def local_network_urls(port: int) -> list[str]:
    """Return best-effort IPv4 URLs for this machine on the local network."""
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        ):
            address = info[4][0]
            if address:
                addresses.add(address)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass

    usable = sorted(
        address
        for address in addresses
        if address and address != "0.0.0.0" and not address.startswith("127.")
    )
    return [f"http://{address}:{port}" for address in usable]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_config(
        Path(args.config),
        {
            "host": args.host,
            "port": args.port,
            "blender_bin": args.blender,
        },
    )

    server = build_server(config)
    address, port = server.server_address[:2]
    logger.info("Projects: %s", config.projects_dir)
    logger.info("Cache: %s", config.cache_dir)
    if config.blender_bin:
        logger.info("Blender: %s", config.blender_bin)
    else:
        logger.warning(
            "Blender não encontrado; .blend e .fbx não poderão ser convertidos. "
            "Defina BLENDER_BIN ou blender_bin em config.json."
        )
    logger.info("Abrindo em http://%s:%s", address, port)
    if address in {"0.0.0.0", "::"}:
        logger.info("Acesso local em http://127.0.0.1:%s", port)
        network_urls = local_network_urls(port)
        for url in network_urls:
            logger.info("Acesso na rede em %s", url)
        if network_urls:
            logger.warning(
                "Acesso externo habilitado sem autenticação. "
                "Use apenas em uma rede confiável."
            )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando…")
    finally:
        server.server_close()
    return 0
