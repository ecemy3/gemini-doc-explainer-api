import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from database import app_db


def _fake_client_with_text(text: str):
    return SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda *args, **kwargs: SimpleNamespace(text=text)
        )
    )


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch):
    main.rate_limit_store.clear()
    main.in_memory_cache.clear()
    app_db.clear_all()
    for key in list(main.METRICS.keys()):
        main.METRICS[key] = 0

    main.redis_client = None
    main.redis_disabled = True


@pytest.fixture
def client():
    return TestClient(main.app)


def test_root(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["message"] == "Service is running. Go to /docs"


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_explain_success(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    payload = json.dumps(
        {
            "level": "beginner",
            "summary": "Simple explanation",
            "key_points": ["a", "b", "c"],
            "warnings": [],
            "confidence": 0.92,
        }
    )
    monkeypatch.setattr(main, "get_genai_client", lambda: _fake_client_with_text(payload))

    response = client.post(
        "/explain",
        json={"text": "Machine learning is useful.", "level": "beginner"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["level"] == "beginner"
    assert len(data["key_points"]) == 3
    assert "request_id" in data


def test_explain_invalid_json_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "get_genai_client", lambda: _fake_client_with_text("not json"))

    response = client.post(
        "/explain",
        json={"text": "Some text", "level": "intermediate"},
    )

    assert response.status_code == 502
    assert "Invalid JSON from Gemini" in response.json()["detail"]


def test_explain_empty_response_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "get_genai_client", lambda: _fake_client_with_text(""))

    response = client.post(
        "/explain",
        json={"text": "Some text", "level": "expert"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Gemini returned empty response"


def test_explain_rate_limit_returns_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    payload = json.dumps(
        {
            "level": "beginner",
            "summary": "Simple explanation",
            "key_points": ["a", "b", "c"],
            "warnings": [],
            "confidence": 0.9,
        }
    )
    monkeypatch.setattr(main, "get_genai_client", lambda: _fake_client_with_text(payload))
    monkeypatch.setattr(main, "RATE_LIMIT_MAX_REQUESTS", 2)

    first = client.post("/explain", json={"text": "t1", "level": "beginner"})
    second = client.post("/explain", json={"text": "t2", "level": "beginner"})
    third = client.post("/explain", json={"text": "t3", "level": "beginner"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_explain_validation_error_returns_422(client: TestClient):
    response = client.post(
        "/explain",
        json={"text": "valid text", "level": "advanced"},
    )

    assert response.status_code == 422


def test_explain_cache_hit_avoids_second_upstream_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    payload = json.dumps(
        {
            "level": "intermediate",
            "summary": "Balanced explanation",
            "key_points": ["k1", "k2", "k3"],
            "warnings": [],
            "confidence": 0.9,
        }
    )
    call_counter = {"count": 0}

    def _generate_content(*args, **kwargs):
        call_counter["count"] += 1
        return SimpleNamespace(text=payload)

    fake_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=_generate_content)
    )
    monkeypatch.setattr(main, "get_genai_client", lambda: fake_client)

    first = client.post(
        "/explain",
        json={"text": "same text", "level": "intermediate"},
    )
    second = client.post(
        "/explain",
        json={"text": "same text", "level": "intermediate"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_counter["count"] == 1
    assert main.METRICS["cache_hits"] == 1


def test_metrics_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    payload = json.dumps(
        {
            "level": "expert",
            "summary": "Advanced explanation",
            "key_points": ["x", "y", "z"],
            "warnings": [],
            "confidence": 0.88,
        }
    )
    monkeypatch.setattr(main, "get_genai_client", lambda: _fake_client_with_text(payload))

    explain_response = client.post(
        "/explain",
        json={"text": "metrics text", "level": "expert"},
    )
    metrics_response = client.get("/metrics")

    assert explain_response.status_code == 200
    assert metrics_response.status_code == 200
    data = metrics_response.json()
    assert data["requests_total"] == 1
    assert data["success_total"] == 1
    assert data["upstream_calls_total"] == 1
    assert data["cache_backend"] in {"memory", "redis"}


def test_admin_db_overview_endpoint(client: TestClient):
    response = client.get("/admin/db/overview")
    assert response.status_code == 200
    data = response.json()
    assert "counts" in data
    assert "users" in data["counts"]


def test_admin_db_table_invalid_name_returns_400(client: TestClient):
    response = client.get("/admin/db/table/not_a_real_table")
    assert response.status_code == 400
