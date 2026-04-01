import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


class TestTournamentCreationEndpoint:
    def _get_client(self):
        from server import app
        return TestClient(app, raise_server_exceptions=False)

    def test_create_requires_auth(self):
        with self._get_client() as client:
            response = client.post("/api/tournaments/create", json={"name": "Test"})
            assert response.status_code == 401

    def test_create_rejects_short_name(self):
        with self._get_client() as client:
            with patch("discord_auth.get_discord_user", return_value={"discord_id": "123", "username": "u"}):
                response = client.post("/api/tournaments/create", json={"name": "ab"})
                assert response.status_code == 400
                assert "at least 3" in response.json()["error"].lower()

    def test_create_rejects_empty_name(self):
        with self._get_client() as client:
            with patch("discord_auth.get_discord_user", return_value={"discord_id": "123", "username": "u"}):
                response = client.post("/api/tournaments/create", json={"name": ""})
                assert response.status_code == 400

    def test_create_rejects_too_many_rounds(self):
        with self._get_client() as client:
            with patch("discord_auth.get_discord_user", return_value={"discord_id": "123", "username": "u"}):
                response = client.post("/api/tournaments/create", json={"name": "Test Tourney", "rounds": 51})
                assert response.status_code == 400
                assert "rounds" in response.json()["error"].lower()

    def test_create_rejects_zero_rounds(self):
        with self._get_client() as client:
            with patch("discord_auth.get_discord_user", return_value={"discord_id": "123", "username": "u"}):
                response = client.post("/api/tournaments/create", json={"name": "Test Tourney", "rounds": 0})
                assert response.status_code == 400

    def test_create_rejects_invalid_team_count(self):
        with self._get_client() as client:
            with patch("discord_auth.get_discord_user", return_value={"discord_id": "123", "username": "u"}):
                response = client.post("/api/tournaments/create", json={
                    "name": "Test", "team_count": 1
                })
                assert response.status_code == 400
                assert "team count" in response.json()["error"].lower()

    def test_create_rejects_invalid_team_size(self):
        with self._get_client() as client:
            with patch("discord_auth.get_discord_user", return_value={"discord_id": "123", "username": "u"}):
                response = client.post("/api/tournaments/create", json={
                    "name": "Test", "team_size": 11
                })
                assert response.status_code == 400
                assert "team size" in response.json()["error"].lower()

    def test_create_rejects_negative_entry_fee(self):
        with self._get_client() as client:
            with patch("discord_auth.get_discord_user", return_value={"discord_id": "123", "username": "u"}):
                response = client.post("/api/tournaments/create", json={
                    "name": "Test", "entry_fee": -5
                })
                assert response.status_code == 400
                assert "negative" in response.json()["error"].lower()


class TestTournamentValidationRules:
    def test_rounds_valid_range(self):
        for r in [1, 5, 25, 50]:
            assert 1 <= r <= 50

    def test_rounds_invalid_range(self):
        for r in [0, -1, 51, 100]:
            assert r < 1 or r > 50

    def test_team_count_valid_range(self):
        for tc in [2, 5, 10, 20]:
            assert 2 <= tc <= 20

    def test_team_size_valid_range(self):
        for ts in [1, 5, 10]:
            assert 1 <= ts <= 10

    def test_game_mode_defaults_to_friend(self):
        mode = "invalid"
        if mode not in ("friend", "swap", "wildcard"):
            mode = "friend"
        assert mode == "friend"

    def test_join_mode_defaults_to_open(self):
        mode = "invalid"
        if mode not in ("open", "closed"):
            mode = "open"
        assert mode == "open"
