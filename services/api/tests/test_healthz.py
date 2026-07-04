from fastapi.testclient import TestClient

from spykt_api.main import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "api"}


def test_clerk_webhook_stub_acks():
    r = client.post("/webhooks/clerk", json={"type": "user.created", "data": {}})
    assert r.status_code == 200
    assert r.json() == {"received": True}
