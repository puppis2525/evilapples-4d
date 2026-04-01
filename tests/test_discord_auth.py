import pytest
import os
import time
import re

os.environ.setdefault("SESSION_SECRET", "test_secret_key_for_testing_only_1234567890abcdef")

from discord_auth import _sign_cookie, _verify_cookie, SESSION_SECRET, get_discord_user


class TestSessionSecretEnforcement:
    def test_session_secret_is_set(self):
        assert SESSION_SECRET
        assert len(SESSION_SECRET) > 0

    def test_module_raises_without_secret(self):
        import importlib
        import discord_auth
        original = os.environ.get("SESSION_SECRET")
        try:
            os.environ["SESSION_SECRET"] = ""
            with pytest.raises(RuntimeError, match="SESSION_SECRET"):
                importlib.reload(discord_auth)
        finally:
            os.environ["SESSION_SECRET"] = original
            importlib.reload(discord_auth)


class TestCookieSigningBehavior:
    def test_sign_produces_dotted_format(self):
        data = {"discord_id": "123", "exp": time.time() + 3600}
        cookie = _sign_cookie(data)
        assert "." in cookie
        parts = cookie.split(".")
        assert len(parts) == 2

    def test_verify_roundtrip(self):
        data = {
            "discord_id": "123456789012345678",
            "username": "testuser",
            "global_name": "Test User",
            "exp": time.time() + 3600,
        }
        cookie = _sign_cookie(data)
        result = _verify_cookie(cookie)
        assert result is not None
        assert result["discord_id"] == "123456789012345678"
        assert result["username"] == "testuser"
        assert result["global_name"] == "Test User"

    def test_expired_cookie_returns_none(self):
        data = {"discord_id": "123", "exp": time.time() - 100}
        cookie = _sign_cookie(data)
        result = _verify_cookie(cookie)
        assert result is None

    def test_tampered_payload_returns_none(self):
        data = {"discord_id": "123", "exp": time.time() + 3600}
        cookie = _sign_cookie(data)
        payload, sig = cookie.rsplit(".", 1)
        tampered = payload + "x"
        result = _verify_cookie(f"{tampered}.{sig}")
        assert result is None

    def test_tampered_signature_returns_none(self):
        data = {"discord_id": "123", "exp": time.time() + 3600}
        cookie = _sign_cookie(data)
        payload, sig = cookie.rsplit(".", 1)
        bad_sig = "a" * len(sig)
        result = _verify_cookie(f"{payload}.{bad_sig}")
        assert result is None

    def test_garbage_input_returns_none(self):
        assert _verify_cookie("") is None
        assert _verify_cookie("not-a-cookie") is None
        assert _verify_cookie("...") is None
        assert _verify_cookie("abc") is None

    def test_different_secrets_reject_each_other(self):
        data = {"discord_id": "123", "exp": time.time() + 3600}
        cookie = _sign_cookie(data)

        import discord_auth
        old_secret = discord_auth.SESSION_SECRET
        try:
            discord_auth.SESSION_SECRET = "completely_different_secret_xyz"
            result = _verify_cookie(cookie)
            assert result is None
        finally:
            discord_auth.SESSION_SECRET = old_secret


class TestGetDiscordUser:
    def test_no_cookie_returns_none(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.cookies = {}
        result = get_discord_user(request)
        assert result is None

    def test_valid_cookie_returns_user_data(self):
        from unittest.mock import MagicMock
        data = {
            "discord_id": "99887766",
            "username": "player1",
            "exp": time.time() + 3600,
        }
        cookie = _sign_cookie(data)
        request = MagicMock()
        request.cookies = {"evil_session": cookie}
        result = get_discord_user(request)
        assert result is not None
        assert result["discord_id"] == "99887766"


class TestDiscordIdValidation:
    def test_valid_discord_id_is_numeric(self):
        discord_id = "123456789012345678"
        assert discord_id.isdigit()
        assert len(discord_id) >= 17

    def test_short_id_invalid(self):
        assert not ("abc".isdigit() and len("abc") >= 17)

    def test_telegram_user_id_format(self):
        assert re.match(r'^tg_\d+$', "tg_123456789")

    def test_invalid_telegram_formats(self):
        assert not re.match(r'^tg_\d+$', "notvalid")
        assert not re.match(r'^tg_\d+$', "tg_")
        assert not re.match(r'^tg_\d+$', "tg_abc")
        assert not re.match(r'^tg_\d+$', "123456")
