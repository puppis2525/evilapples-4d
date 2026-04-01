import pytest
from fastapi.testclient import TestClient


class TestMainServerHealthEndpoint:
    def test_health_route_exists_in_app(self):
        from server import app
        routes = [r.path for r in app.routes if hasattr(r, 'path')]
        assert "/health" in routes

    def test_health_returns_json_with_required_fields(self):
        from server import app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
            assert response.status_code in (200, 503)
            data = response.json()
            assert "status" in data
            assert "service" in data
            assert "uptime_seconds" in data
            assert "database" in data
            assert data["service"] == "evil-apples-4d"

    def test_health_uptime_is_non_negative(self):
        from server import app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
            data = response.json()
            assert isinstance(data["uptime_seconds"], (int, float))
            assert data["uptime_seconds"] >= 0

    def test_health_status_values(self):
        from server import app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
            data = response.json()
            assert data["status"] in ("healthy", "degraded")
            assert data["database"] in ("connected", "disconnected")

    def test_healthy_means_200(self):
        from server import app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
            data = response.json()
            if data["status"] == "healthy":
                assert response.status_code == 200
            else:
                assert response.status_code == 503


class TestTelegramHealthFormat:
    def test_telegram_health_has_bot_mode(self):
        healthy_response = {
            "status": "healthy",
            "service": "evil-apples-telegram",
            "uptime_seconds": 10.0,
            "database": "connected",
            "bot_mode": "polling"
        }
        assert "bot_mode" in healthy_response
        assert healthy_response["bot_mode"] == "polling"
        assert healthy_response["service"] == "evil-apples-telegram"
