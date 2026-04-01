import pytest
from unittest.mock import patch, AsyncMock
from tests.helpers import make_get_db_pool_mock
from server import RateLimiter, RATE_LIMIT_SKIP


class TestRateLimiterTierDetection:
    def setup_method(self):
        self.limiter = RateLimiter()

    def test_strict_tier_checkout(self):
        assert self.limiter._get_tier("/api/store/checkout") == "strict"

    def test_strict_tier_stripe_checkout(self):
        assert self.limiter._get_tier("/api/stripe/create-checkout") == "strict"

    def test_strict_tier_tournament_checkout(self):
        assert self.limiter._get_tier("/api/stripe/tournament/entry-checkout") == "strict"

    def test_strict_tier_connect(self):
        assert self.limiter._get_tier("/api/stripe/connect/onboard") == "strict"

    def test_moderate_tier_admin(self):
        assert self.limiter._get_tier("/api/admin/something") == "moderate"

    def test_moderate_tier_login(self):
        assert self.limiter._get_tier("/auth/discord/login") == "moderate"

    def test_moderate_tier_generate(self):
        assert self.limiter._get_tier("/api/generate") == "moderate"

    def test_relaxed_tier_generic_api(self):
        assert self.limiter._get_tier("/api/leaderboard") == "relaxed"

    def test_no_tier_for_static(self):
        assert self.limiter._get_tier("/static/style.css") is None

    def test_no_tier_for_root(self):
        assert self.limiter._get_tier("/") is None

    def test_no_tier_for_html_pages(self):
        assert self.limiter._get_tier("/howtoplay") is None

    def test_no_tier_for_health(self):
        assert self.limiter._get_tier("/health") is None


class TestRateLimiterTierConfig:
    def test_strict_tier_config(self):
        assert RateLimiter.TIERS["strict"]["requests"] == 5
        assert RateLimiter.TIERS["strict"]["window"] == 60

    def test_moderate_tier_config(self):
        assert RateLimiter.TIERS["moderate"]["requests"] == 30
        assert RateLimiter.TIERS["moderate"]["window"] == 60

    def test_relaxed_tier_config(self):
        assert RateLimiter.TIERS["relaxed"]["requests"] == 60
        assert RateLimiter.TIERS["relaxed"]["window"] == 60

    def test_all_tiers_have_positive_values(self):
        for tier_name, tier in RateLimiter.TIERS.items():
            assert tier["requests"] > 0
            assert tier["window"] > 0


class TestRateLimiterSkipPaths:
    def test_static_files_skipped(self):
        assert "/static" in RATE_LIMIT_SKIP

    def test_webhook_skipped(self):
        assert "/api/stripe/webhook" in RATE_LIMIT_SKIP

    def test_favicon_skipped(self):
        assert "/favicon.ico" in RATE_LIMIT_SKIP


class TestRateLimiterDBBehavior:
    @pytest.mark.asyncio
    async def test_no_tier_returns_true_immediately(self):
        limiter = RateLimiter()
        result = await limiter.is_allowed("1.2.3.4", "/static/foo")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_tier_for_root_returns_true(self):
        limiter = RateLimiter()
        result = await limiter.is_allowed("1.2.3.4", "/")
        assert result is True

    @pytest.mark.asyncio
    async def test_strict_fails_closed_on_db_error(self):
        limiter = RateLimiter()
        with patch("db.get_db_pool", AsyncMock(side_effect=Exception("DB down"))):
            result = await limiter.is_allowed("1.2.3.4", "/api/store/checkout")
            assert result is False

    @pytest.mark.asyncio
    async def test_relaxed_fails_open_on_db_error(self):
        limiter = RateLimiter()
        with patch("db.get_db_pool", AsyncMock(side_effect=Exception("DB down"))):
            result = await limiter.is_allowed("1.2.3.4", "/api/leaderboard")
            assert result is True

    @pytest.mark.asyncio
    async def test_moderate_fails_open_on_db_error(self):
        limiter = RateLimiter()
        with patch("db.get_db_pool", AsyncMock(side_effect=Exception("DB down"))):
            result = await limiter.is_allowed("1.2.3.4", "/api/admin/test")
            assert result is True

    @pytest.mark.asyncio
    async def test_allowed_when_under_limit(self):
        limiter = RateLimiter()
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.fetchrow = AsyncMock(return_value={"cnt": 1})
        mock_conn.execute = AsyncMock()

        with patch("db.get_db_pool", get_pool):
            result = await limiter.is_allowed("1.2.3.4", "/api/leaderboard")
            assert result is True

    @pytest.mark.asyncio
    async def test_blocked_when_over_limit(self):
        limiter = RateLimiter()
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.fetchrow = AsyncMock(return_value={"cnt": 100})
        mock_conn.execute = AsyncMock()

        with patch("db.get_db_pool", get_pool):
            result = await limiter.is_allowed("1.2.3.4", "/api/leaderboard")
            assert result is False

    @pytest.mark.asyncio
    async def test_uses_atomic_cte_query(self):
        limiter = RateLimiter()
        get_pool, mock_conn = make_get_db_pool_mock()
        mock_conn.fetchrow = AsyncMock(return_value={"cnt": 1})
        mock_conn.execute = AsyncMock()

        with patch("db.get_db_pool", get_pool):
            await limiter.is_allowed("1.2.3.4", "/api/leaderboard")
            sql = mock_conn.fetchrow.call_args[0][0]
            assert "WITH inserted AS" in sql
            assert "INSERT INTO rate_limit_entries" in sql
