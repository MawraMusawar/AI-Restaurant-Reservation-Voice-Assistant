from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import app


def test_health_endpoint_reports_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["integrations"]) == {"deepgram", "groq", "elevenlabs", "database"}


def test_cors_origins_are_parsed_from_comma_separated_string() -> None:
    raw = " http://localhost:3000, http://127.0.0.1:3000 ,"
    settings = Settings(_env_file=None, cors_origins=raw)
    assert settings.cors_origin_list == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_secrets_are_not_exposed_in_repr_or_status() -> None:
    settings = Settings(_env_file=None, groq_api_key=SecretStr("super-secret-value"))
    assert "super-secret-value" not in repr(settings)
    status = settings.integration_status()
    assert status["groq"] is True
    assert "super-secret-value" not in str(status)
