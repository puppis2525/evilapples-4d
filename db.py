import os
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")


async def get_pool():
    return await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)


_pool = None


async def get_db_pool():
    global _pool
    if _pool is None:
        _pool = await get_pool()
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


async def save_round(prompt: str, winning_card: str, winner_discord_id: str, winner_name: str, slug: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO game_rounds (prompt, winning_card, winner_discord_id, winner_name, slug)
               VALUES ($1, $2, $3, $4, $5)""",
            prompt, winning_card, winner_discord_id, winner_name, slug
        )
        await conn.execute(
            """INSERT INTO leaderboard (discord_id, discord_name, wins, games_played, updated_at)
               VALUES ($1, $2, 1, 1, NOW())
               ON CONFLICT (discord_id) DO UPDATE
               SET wins = leaderboard.wins + 1,
                   games_played = leaderboard.games_played + 1,
                   discord_name = $2,
                   updated_at = NOW()""",
            winner_discord_id, winner_name
        )


async def batch_update_games_played(players: dict):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for discord_id, discord_name in players.items():
            await conn.execute(
                """INSERT INTO leaderboard (discord_id, discord_name, wins, games_played, updated_at)
                   VALUES ($1, $2, 0, 1, NOW())
                   ON CONFLICT (discord_id) DO UPDATE
                   SET games_played = leaderboard.games_played + 1,
                       discord_name = $2,
                       updated_at = NOW()""",
                discord_id, discord_name
            )


async def get_recent_rounds(limit: int = 20):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT prompt, winning_card, winner_name, slug, created_at
               FROM game_rounds ORDER BY created_at DESC LIMIT $1""",
            limit
        )
        return [dict(r) for r in rows]


async def get_leaderboard(limit: int = 20):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT discord_name, wins, games_played
               FROM leaderboard ORDER BY wins DESC LIMIT $1""",
            limit
        )
        return [dict(r) for r in rows]


async def get_round_by_slug(slug: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT prompt, winning_card, winner_name, slug, created_at
               FROM game_rounds WHERE slug = $1""",
            slug
        )
        return dict(row) if row else None
