import pytest
from fastapi.testclient import TestClient


class TestCORSConfiguration:
    def test_main_server_has_cors_middleware(self):
        from server import app
        middleware_types = [str(m) for m in app.user_middleware]
        cors_found = any("CORSMiddleware" in str(m) for m in app.user_middleware)
        assert cors_found

    def test_default_origins_include_app_domains(self):
        from server import _cors_origins
        origin_str = " ".join(_cors_origins)
        assert "evilapples4d.com" in origin_str
        assert "evilapples4tg.com" in origin_str

    def test_cors_allows_configured_origin(self):
        from server import app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.options(
                "/health",
                headers={
                    "Origin": "https://evilapples4d.com",
                    "Access-Control-Request-Method": "GET",
                }
            )
            cors_header = response.headers.get("access-control-allow-origin", "")
            assert cors_header == "https://evilapples4d.com" or cors_header == "*"

    def test_cors_rejects_unknown_origin(self):
        from server import app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.options(
                "/health",
                headers={
                    "Origin": "https://evil-hacker-site.com",
                    "Access-Control-Request-Method": "GET",
                }
            )
            cors_header = response.headers.get("access-control-allow-origin", "")
            assert "evil-hacker-site.com" not in cors_header

    def test_cors_allowed_methods(self):
        from server import app
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.options(
                "/health",
                headers={
                    "Origin": "https://evilapples4d.com",
                    "Access-Control-Request-Method": "POST",
                }
            )
            methods = response.headers.get("access-control-allow-methods", "")
            assert "POST" in methods or response.status_code == 200
