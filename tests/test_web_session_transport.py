"""Scoped Web writes share retry receipts only for the same operation and session."""

import asyncio

from httpx import ASGITransport, AsyncClient

from tests.web_fixtures import web


def test_prepare_receipt_deduplicates_retries_and_rejects_conflicting_operations(web, monkeypatch):
    client, runtime, _ = web
    workbench = client.app.state.workbench
    initial = client.get("/api/state").json()
    project_id, first = initial["project"]["id"], initial["session"]["id"]
    runtime.session.store.append_user_message(runtime.session.data, "Existing conversation", user_timezone="UTC")
    second = client.post("/api/sidebar/new", json={"project_id": project_id}).json()["session"]["id"]
    assert second != first
    original = workbench.prepare_session
    calls = []

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def prepare(project_id, session_id):
            if session_id == first:
                entered.set()
                await release.wait()
            calls.append(session_id)
            return await original(project_id, session_id)

        monkeypatch.setattr(workbench, "prepare_session", prepare)
        body = {"project_id": project_id, "session_id": first, "submission_id": "same-request"}
        async with AsyncClient(transport=ASGITransport(app=client.app), base_url=str(client.base_url),
                               headers=client.headers) as requests:
            pending = asyncio.create_task(requests.post("/api/execution/prepare", json=body))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                conflict = await requests.post("/api/mode", json={**body, "mode": "ask"})
                assert conflict.status_code == 409
                duplicate = asyncio.create_task(requests.post("/api/execution/prepare", json=body))
            finally:
                release.set()
            initial_reply, retry = await asyncio.gather(pending, duplicate)
            assert initial_reply.status_code == retry.status_code == 200
            assert initial_reply.json() == retry.json()
            assert calls == [first]
            other = await requests.post("/api/execution/prepare", json={**body, "session_id": second})
            assert other.status_code == 200
            assert calls == [first, second]
        assert workbench.selected_session == (project_id, second)

    client.portal.call(run)
