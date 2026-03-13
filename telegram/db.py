import os
import asyncio
import logging
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

logger = logging.getLogger("tg_db")

_pool = None


async def get_db_pool():
    global _pool
    if _pool is None or _pool._closed:
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Creating database connection pool (attempt {attempt}/{max_retries})...")
                _pool = await asyncio.wait_for(
                    asyncpg.create_pool(
                        DATABASE_URL,
                        min_size=2,
                        max_size=10,
                        command_timeout=30,
                        server_settings={"application_name": "evil_apples_telegram"}
                    ),
                    timeout=15
                )
                logger.info("Database pool created successfully")
                return _pool
            except (asyncio.TimeoutError, OSError, asyncpg.PostgresError) as e:
                logger.warning(f"Database connection attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 * attempt)
                else:
                    raise
    return _pool


async def init_db():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS game_rounds (
                id SERIAL PRIMARY KEY,
                prompt TEXT NOT NULL,
                winning_card TEXT NOT NULL,
                winner_discord_id VARCHAR(64) NOT NULL,
                winner_name VARCHAR(128) NOT NULL DEFAULT 'Unknown',
                slug VARCHAR(256) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS leaderboard (
                id SERIAL PRIMARY KEY,
                discord_id VARCHAR(64) UNIQUE NOT NULL,
                discord_name VARCHAR(128) NOT NULL DEFAULT 'Unknown',
                wins INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """)

        for col, default in [("platform", "'discord'"), ("chat_id", "NULL")]:
            try:
                if col == "platform":
                    await conn.execute(f"ALTER TABLE game_rounds ADD COLUMN IF NOT EXISTS platform VARCHAR(16) DEFAULT 'discord';")
                    await conn.execute(f"ALTER TABLE leaderboard ADD COLUMN IF NOT EXISTS platform VARCHAR(16) DEFAULT 'discord';")
                    await conn.execute(f"ALTER TABLE leaderboard DROP CONSTRAINT IF EXISTS leaderboard_discord_id_key;")
                    await conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS leaderboard_uid_platform ON leaderboard(discord_id, platform);")
            except Exception as e:
                logger.warning(f"Migration note: {e}")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tg_bot_stats (
                id SERIAL PRIMARY KEY,
                group_count INTEGER NOT NULL DEFAULT 0,
                recorded_at TIMESTAMP DEFAULT NOW()
            );
        """)

    logger.info("Database initialized for Telegram bot")


async def save_round(prompt: str, winning_card: str, winner_tg_id: str,
                     winner_name: str, slug: str) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO game_rounds (prompt, winning_card, winner_discord_id, winner_name, slug, platform)
            VALUES ($1, $2, $3, $4, $5, 'telegram')
            ON CONFLICT (slug) DO NOTHING
        """, prompt, winning_card, winner_tg_id, winner_name, slug)


async def update_leaderboard(tg_user_id: str, username: str, won: bool) -> None:
    pool = await get_db_pool()
    uid = f"tg_{tg_user_id}"
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM leaderboard WHERE discord_id = $1 AND platform = 'telegram'", uid
        )
        if existing:
            if won:
                await conn.execute("""
                    UPDATE leaderboard
                    SET wins = wins + 1, games_played = games_played + 1,
                        discord_name = $2, updated_at = NOW()
                    WHERE discord_id = $1 AND platform = 'telegram'
                """, uid, username)
            else:
                await conn.execute("""
                    UPDATE leaderboard
                    SET games_played = games_played + 1,
                        discord_name = $2, updated_at = NOW()
                    WHERE discord_id = $1 AND platform = 'telegram'
                """, uid, username)
        else:
            await conn.execute("""
                INSERT INTO leaderboard (discord_id, discord_name, wins, games_played, platform)
                VALUES ($1, $2, $3, 1, 'telegram')
            """, uid, username, 1 if won else 0)


async def batch_update_games_played(players: dict[str, str]) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for tg_id, username in players.items():
            uid = f"tg_{tg_id}"
            existing = await conn.fetchrow(
                "SELECT id FROM leaderboard WHERE discord_id = $1 AND platform = 'telegram'", uid
            )
            if existing:
                await conn.execute("""
                    UPDATE leaderboard SET games_played = games_played + 1,
                        discord_name = $2, updated_at = NOW()
                    WHERE discord_id = $1 AND platform = 'telegram'
                """, uid, username)
            else:
                await conn.execute("""
                    INSERT INTO leaderboard (discord_id, discord_name, wins, games_played, platform)
                    VALUES ($1, $2, 0, 1, 'telegram')
                    ON CONFLICT DO NOTHING
                """, uid, username)


async def record_bot_group_count(count: int) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tg_bot_stats (group_count) VALUES ($1)", count
        )


async def get_leaderboard(limit: int = 10) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT discord_name, wins, games_played
            FROM leaderboard
            WHERE platform = 'telegram' AND games_played > 0
            ORDER BY wins DESC, games_played ASC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]


async def get_recent_rounds(limit: int = 5) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT prompt, winning_card, winner_name, slug, created_at
            FROM game_rounds
            WHERE platform = 'telegram'
            ORDER BY created_at DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]
