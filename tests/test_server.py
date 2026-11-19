from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.server import build_server, load_config


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.projects = root / "projects"
        cls.static = root / "static"
        cls.static.mkdir(parents=True)
        (cls.static / "index.html").write_text("<!doctype html><title>ok</title>")
        cls.model = cls.projects / "P/models/model/file.glb"
        cls.model.parent.mkdir(parents=True)
        cls.model.write_bytes(b"glTF-test-bytes")
        (root / "secret.txt").write_text("secret")

        config = load_config(
            root / "missing-config.json",
            {
                "host": "127.0.0.1",
                "port": 0,
                "projects_dir": str(cls.projects),
                "static_dir": str(cls.static),
                "cache_dir": str(root / "cache"),
                "blender_bin": "",
            },
        )
        cls.server = build_server(config)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def get(self, path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, response.read(), response.headers.get("content-type", "")
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers.get("content-type", "")

    def test_projects_endpoint(self) -> None:
        status, body, content_type = self.get("/api/projects")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        data = json.loads(body)
        self.assertEqual(data["projects"][0]["name"], "P")
        self.assertEqual(data["projects"][0]["models"][0]["name"], "file")
        self.assertEqual(
            data["projects"][0]["models"][0]["asset_url"],
            "/assets/P/model/file.glb",
        )

    def test_static_and_health(self) -> None:
        status, body, _ = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>ok</title>", body)
        status, body, _ = self.get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_asset_is_served(self) -> None:
        status, body, content_type = self.get("/assets/P/model/file.glb")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"glTF-test-bytes")
        self.assertEqual(content_type, "model/gltf-binary")

    def test_path_traversal_is_rejected(self) -> None:
        status, _, _ = self.get("/assets/P/%2E%2E/secret.txt")
        self.assertEqual(status, 400)
        status, _, _ = self.get("/static/%2E%2E/secret.txt")
        self.assertEqual(status, 400)

    def test_api_glb_rejects_non_blend(self) -> None:
        query = urllib.parse.urlencode(
            {"project": "P", "path": "model/file.glb"}
        )
        status, body, _ = self.get(f"/api/glb?{query}")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))


class ConfigTests(unittest.TestCase):
    def test_default_host_accepts_lan_connections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp) / "missing.json", {})

        self.assertEqual(config.host, "0.0.0.0")


class CrudServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.projects = self.root / "projects"
        self.projects.mkdir()
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("<!doctype html><title>ok</title>")

        config = load_config(
            self.root / "missing-config.json",
            {
                "host": "127.0.0.1",
                "port": 0,
                "projects_dir": str(self.projects),
                "static_dir": str(static),
                "cache_dir": str(self.root / "cache"),
                "blender_bin": "",
            },
        )
        self.server = build_server(config)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(
        self,
        path: str,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
        method: str = "GET",
    ) -> tuple[int, bytes]:
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post_json(self, path: str, payload: dict) -> tuple[int, bytes]:
        return self.request(
            path,
            data=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            method="POST",
        )

    def post_files(
        self, path: str, files: list[tuple[str, bytes]]
    ) -> tuple[int, bytes]:
        boundary = "----biblioteca-test"
        chunks = []
        for filename, content in files:
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode("utf-8")
                + content
                + b"\r\n"
            )
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        return self.request(
            path,
            data=b"".join(chunks),
            content_type=f"multipart/form-data; boundary={boundary}",
            method="POST",
        )

    def test_project_lifecycle(self) -> None:
        status, body = self.post_json("/api/projects", {"name": "Novo"})
        self.assertEqual(status, 201, body)
        self.assertEqual(json.loads(body)["project_id"], "Novo")
        self.assertTrue((self.projects / "Novo/models").is_dir())

        status, body = self.post_json("/api/projects", {"name": "Novo"})
        self.assertEqual(status, 409, body)

        status, body = self.post_json(
            "/api/projects/rename", {"project": "Novo", "name": "Renomeado"}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["project_id"], "Renomeado")
        self.assertTrue((self.projects / "Renomeado").is_dir())

        status, body = self.request("/api/projects")
        self.assertEqual(status, 200)
        names = [item["name"] for item in json.loads(body)["projects"]]
        self.assertEqual(names, ["Renomeado"])

        status, body = self.post_json(
            "/api/projects/delete", {"project": "Renomeado"}
        )
        self.assertEqual(status, 200, body)
        self.assertFalse((self.projects / "Renomeado").exists())

    def test_model_lifecycle_with_sidecar(self) -> None:
        status, body = self.post_json("/api/projects", {"name": "Plantas"})
        self.assertEqual(status, 201, body)

        status, body = self.post_files(
            "/api/models/upload?project=Plantas",
            [
                ("samambaia.glb", b"glb-bytes"),
                ("textura.png", b"png-bytes"),
            ],
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(json.loads(body)["path"], "samambaia/samambaia.glb")
        self.assertTrue(
            (self.projects / "Plantas/models/samambaia/textura.png").is_file()
        )

        status, body = self.request(
            "/assets/Plantas/samambaia/samambaia.glb"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"glb-bytes")

        status, body = self.post_json(
            "/api/models/rename",
            {
                "project": "Plantas",
                "path": "samambaia/samambaia.glb",
                "name": "costela",
            },
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["path"], "costela/costela.glb")
        self.assertTrue(
            (self.projects / "Plantas/models/costela/textura.png").is_file()
        )

        status, body = self.post_json(
            "/api/models/delete",
            {"project": "Plantas", "path": "costela/costela.glb"},
        )
        self.assertEqual(status, 200, body)
        self.assertFalse(
            (self.projects / "Plantas/models/costela").exists()
        )

    def test_crud_rejects_traversal(self) -> None:
        status, body = self.post_json(
            "/api/projects", {"name": "../fora"}
        )
        self.assertEqual(status, 400, body)
        self.assertFalse((self.root / "fora").exists())

        status, body = self.post_json("/api/projects", {"name": "Plantas"})
        self.assertEqual(status, 201, body)

        status, body = self.post_files(
            "/api/models/upload?project=Plantas",
            [("../fora.glb", b"glb")],
        )
        self.assertEqual(status, 400, body)

        status, body = self.post_json(
            "/api/models/rename",
            {
                "project": "Plantas",
                "path": "../fora.glb",
                "name": "x",
            },
        )
        self.assertEqual(status, 400, body)

        status, body = self.request(
            "/api/models/upload?project=../Plantas", data=b"", method="POST"
        )
        self.assertEqual(status, 400, body)


if __name__ == "__main__":
    unittest.main()
