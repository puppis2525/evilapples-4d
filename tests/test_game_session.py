import pytest
import json
from unittest.mock import patch, AsyncMock, MagicMock
from tests.helpers import make_get_db_pool_mock


class TestGameSessionLifecycle:
    @pytest.mark.asyncio
    async def test_save_game_session(self):
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.execute = AsyncMock()

        with patch("db.get_db_pool", get_pool):
            from db import save_game_session
            game_data = {"players": ["p1", "p2"], "round": 1, "state": "playing"}
            await save_game_session("channel_123", "channel", game_data)
            mock_conn.execute.assert_called_once()
            call_args = mock_conn.execute.call_args
            assert "active_game_sessions" in call_args[0][0]
            assert call_args[0][1] == "channel_123"
            assert call_args[0][2] == "channel"

    @pytest.mark.asyncio
    async def test_save_game_session_upserts(self):
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.execute = AsyncMock()

        with patch("db.get_db_pool", get_pool):
            from db import save_game_session
            await save_game_session("ch_1", "channel", {"round": 1})
            sql = mock_conn.execute.call_args[0][0]
            assert "ON CONFLICT" in sql
            assert "DO UPDATE" in sql

    @pytest.mark.asyncio
    async def test_load_game_session_returns_data(self):
        game_data = {"players": ["p1"], "state": "waiting"}
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.fetchrow = AsyncMock(return_value={"game_data": json.dumps(game_data)})

        with patch("db.get_db_pool", get_pool):
            from db import load_game_session
            result = await load_game_session("ch_1")
            assert result is not None
            assert result["state"] == "waiting"
            assert result["players"] == ["p1"]

    @pytest.mark.asyncio
    async def test_load_game_session_returns_none_when_missing(self):
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        with patch("db.get_db_pool", get_pool):
            from db import load_game_session
            result = await load_game_session("nonexistent_key")
            assert result is None

    @pytest.mark.asyncio
    async def test_delete_game_session(self):
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.execute = AsyncMock()

        with patch("db.get_db_pool", get_pool):
            from db import delete_game_session
            await delete_game_session("ch_123")
            mock_conn.execute.assert_called_once()
            assert "DELETE" in mock_conn.execute.call_args[0][0]
            assert "active_game_sessions" in mock_conn.execute.call_args[0][0]

    @pytest.mark.asyncio
    async def test_load_all_game_sessions_with_type(self):
        mock_rows = [
            {"session_key": "ch_1", "game_data": '{"state":"waiting"}'},
            {"session_key": "ch_2", "game_data": '{"state":"playing"}'},
        ]
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        with patch("db.get_db_pool", get_pool):
            from db import load_all_game_sessions
            result = await load_all_game_sessions(session_type="channel")
            assert len(result) == 2
            assert "ch_1" in result
            assert "ch_2" in result
            assert result["ch_1"]["state"] == "waiting"

    @pytest.mark.asyncio
    async def test_load_all_game_sessions_without_type(self):
        mock_rows = [
            {"session_key": "ch_1", "session_type": "channel", "game_data": '{"state":"waiting"}'},
        ]
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        with patch("db.get_db_pool", get_pool):
            from db import load_all_game_sessions
            result = await load_all_game_sessions()
            assert len(result) == 1


class TestRoundRecording:
    def _make_conn_with_transaction(self):
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.execute = AsyncMock()
        tx_ctx = MagicMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=None)
        tx_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction = MagicMock(return_value=tx_ctx)
        return get_pool, mock_conn

    @pytest.mark.asyncio
    async def test_save_round_records_winner(self):
        get_pool, mock_conn = self._make_conn_with_transaction()

        with patch("db.get_db_pool", get_pool):
            from db import save_round
            await save_round("What is evil?", "Being this card", "123456", "EvilPlayer", "test-slug-1")
            assert mock_conn.execute.call_count >= 2
            leaderboard_sql = mock_conn.execute.call_args_list[1][0][0]
            assert "ON CONFLICT (discord_id, platform)" in leaderboard_sql
            assert "platform" in leaderboard_sql

    @pytest.mark.asyncio
    async def test_batch_update_games_played(self):
        get_pool, mock_conn = self._make_conn_with_transaction()

        with patch("db.get_db_pool", get_pool):
            from db import batch_update_games_played
            players = {"user1": "Player1", "user2": "Player2"}
            await batch_update_games_played(players)
            assert mock_conn.execute.call_count >= 2
            first_sql = mock_conn.execute.call_args_list[0][0][0]
            assert "ON CONFLICT (discord_id, platform)" in first_sql

    @pytest.mark.asyncio
    async def test_get_leaderboard(self):
        mock_rows = [MagicMock(), MagicMock()]
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)

        with patch("db.get_db_pool", get_pool):
            from db import get_leaderboard
            results = await get_leaderboard(10)
            mock_conn.fetch.assert_called_once()
            sql = mock_conn.fetch.call_args[0][0]
            assert "leaderboard" in sql.lower()
