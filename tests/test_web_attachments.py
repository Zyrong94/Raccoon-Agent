import base64
import io
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from raccoon.bootstrap.runtime import create_runtime
from raccoon.bootstrap.web_app import create_web_app
from raccoon.runtime.model_types import ProviderCapabilities
from tests.runtime_fixtures import FakeMcpManager
from tests.web_fixtures import WebProvider, response, wait_idle


def png():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    return output.getvalue()


class AttachmentProvider(WebProvider):
    capabilities = ProviderCapabilities(supports_vision=True, supports_pdf_input=True)

    def __init__(self):
        super().__init__([response("files received")] * 8)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return super().complete(request)


@contextmanager
def web(tmp_path, *, session_id=None, provider=None):
    provider = provider or AttachmentProvider()
    runtime = create_runtime(project_root=tmp_path, provider=provider, tools=[],
        session_id=session_id, resume_session=session_id is not None,
        mcp_manager_factory=lambda configs: FakeMcpManager())
    with TestClient(create_web_app(runtime), base_url="http://127.0.0.1:8765") as client:
        state = client.get("/api/state").json()
        client.headers["X-Raccoon-Token"] = state["token"]
        yield client, runtime, provider, {"project_id": state["project"]["id"], "session_id": state["session"]["id"]}


def upload(client, scope, raw, name="image.png", mime="image/png"):
    return client.post("/api/attachments", params={**scope, "name": name}, content=raw, headers={"Content-Type": mime})


def test_upload_only_stages_safe_bytes_and_authenticates(tmp_path):
    with web(tmp_path) as (client, runtime, provider, scope):
        result = upload(client, scope, png())
        assert result.status_code == 201, result.text
        item = result.json()
        assert set(item) == {"id", "kind", "name", "content_type", "size", "content_url", "download_url"}
        assert item["kind"] == "image" and item["name"] == "image.png"
        assert item["content_type"] == "image/png" and item["size"] == len(png())
        assert item["content_url"].startswith("/api/")
        assert scope["session_id"] in item["content_url"]
        assert client.get(item["content_url"]).content == png()
        assert provider.requests == []
        assert client.get("/api/state").json()["messages"] == []
        client.headers.pop("X-Raccoon-Token")
        assert upload(client, scope, png()).status_code == 403


def test_attachment_only_reaches_provider_and_restores_on_restart(tmp_path):
    with web(tmp_path) as (client, runtime, provider, scope):
        image = upload(client, scope, png()).json()
        pdf = upload(client, scope, b"%PDF-1.4\n% tiny fixture", "paper.pdf", "application/pdf").json()
        text = upload(client, scope, b"some source code", "source.py", "text/plain").json()
        result = client.post("/api/messages", json={**scope, "submission_id": uuid4().hex, "message": "", "attachment_ids": [a["id"] for a in [image, pdf, text]]})
        assert result.status_code == 202, result.text
        state = wait_idle(client)
        user = next(m for m in state["messages"] if m["role"] == "user")
        assert user["attachments"] == [image, pdf, text]
        parts = [p for m in provider.requests[0].messages if m.role == "user" for p in m.content_parts or []]
        assert any(p.type == "image" and base64.b64decode(p.data_base64) == png() for p in parts)
        assert any(p.type == "file" and p.media_type == "application/pdf" for p in parts)
        assert any(p.type == "text" and "some source code" in p.text for p in parts)
        assert "data_base64" not in (runtime.catalog.root / "sessions" / f"{scope['session_id']}.jsonl").read_text(encoding="utf-8")
    with web(tmp_path, session_id=scope["session_id"]) as (client, runtime, provider, restored_scope):
        restored = next(m for m in client.get("/api/state").json()["messages"] if m["role"] == "user")
        assert restored["attachments"] == [image, pdf, text]
        assert client.get(image["content_url"]).content == png()
        assert client.get(text["download_url"]).content == b"some source code"


@pytest.mark.parametrize("raw,name,mime", [
    (b"<svg><script>alert(1)</script></svg>", "attack.png", "image/png"),
    (b"<html>attack</html>", "attack.gif", "image/gif"),
    (png(), "wrong.jpg", "image/jpeg"),
    (png()[:-5], "broken.png", "image/png"),
    (b"<svg/>", "image.svg", "image/svg+xml"),
    (b"x", "../secret.txt", "text/plain"),
    (b"x", "C:\\secret.txt", "text/plain"),
])
def test_rejects_mime_spoof_corruption_and_path_names(tmp_path, raw, name, mime):
    with web(tmp_path) as (client, _, provider, scope):
        result = upload(client, scope, raw, name, mime)
        assert result.status_code in {400, 422}, result.text
        assert str(tmp_path) not in result.text
        assert provider.requests == []


def test_generic_files_are_downloads_with_nosniff_and_binary_integrity(tmp_path):
    with web(tmp_path) as (client, _, _, scope):
        raw = b"<html><script>alert(1)</script></html>\x00\xff"
        item = upload(client, scope, raw, "page.html", "text/html").json()
        assert item["kind"] == "file"
        for url in [item["content_url"], item["download_url"]]:
            result = client.get(url)
            assert result.content == raw
            assert result.headers["x-content-type-options"] == "nosniff"
            assert result.headers["content-type"] == "application/octet-stream"
            assert result.headers["content-disposition"].startswith("attachment;")


def test_rejects_foreign_duplicate_and_malformed_refs_before_inference(tmp_path):
    with web(tmp_path) as (client, runtime, provider, scope):
        item = upload(client, scope, png()).json()
        for ids in [[item["id"], item["id"]], ["../secret"], ["unknown"], [item["id"]] * 17]:
            result = client.post("/api/messages", json={**scope, "submission_id": uuid4().hex, "message": "hi", "attachment_ids": ids})
            assert result.status_code in {400, 422}
        runtime.session.store.append_user_message(runtime.session.data, "Existing conversation", user_timezone="UTC")
        opened = client.post("/api/sidebar/new", json={"project_id": scope["project_id"]})
        assert opened.status_code == 200, opened.text
        assert client.get(item["content_url"]).status_code == 200
        foreign_id = opened.json()["session"]["id"]
        assert foreign_id != scope["session_id"]
        foreign = {**scope, "session_id": foreign_id}
        assert client.post("/api/messages", json={**foreign, "submission_id": uuid4().hex, "message": "hi", "attachment_ids": [item["id"]]}).status_code == 400
        assert upload(client, scope, png()).status_code == 201
        assert provider.requests == []


def test_rejects_images_for_nonvision_model_but_keeps_staged_file(tmp_path):
    provider = AttachmentProvider()
    provider.capabilities = ProviderCapabilities()
    with web(tmp_path, provider=provider) as (client, _, _, scope):
        assert client.get("/api/state").json()["input_capabilities"] == {"images": False, "pdf": False}
        item = upload(client, scope, png()).json()
        result = client.post("/api/messages", json={**scope, "submission_id": uuid4().hex, "message": "look", "attachment_ids": [item["id"]]})
        assert result.status_code == 400
        assert client.get(item["content_url"]).content == png()
        assert provider.requests == []


def test_tampered_owned_file_is_not_read_or_submitted(tmp_path):
    with web(tmp_path) as (client, runtime, provider, scope):
        item = upload(client, scope, png()).json()
        directory = runtime.catalog.root / "attachments" / scope["session_id"]
        path = next(directory.glob("*-image.png"))
        path.write_bytes(b"tampered")
        assert client.get(item["content_url"]).status_code == 404
        accepted = client.post("/api/messages", json={
            **scope, "submission_id": "tampered", "message": "hi", "attachment_ids": [item["id"]],
        })
        assert accepted.status_code == 202
        assert wait_idle(client)["status"] == "error"
        assert provider.requests == []
