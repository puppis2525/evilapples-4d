from unittest.mock import AsyncMock, MagicMock


def make_mock_pool(mock_conn=None):
    if mock_conn is None:
        mock_conn = AsyncMock()
    mock_pool = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire.return_value = ctx
    return mock_pool, mock_conn


def make_get_db_pool_mock(mock_conn=None):
    mock_pool, mock_conn = make_mock_pool(mock_conn)
    get_pool = AsyncMock(return_value=mock_pool)
    return get_pool, mock_conn
