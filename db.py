import os
import logging
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

logger = logging.getLogger("db")

_pool = None


async def get_db_pool():
    global _pool
    if _pool is None or _pool._closed:
        logger.info("Creating database connection pool...")
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=30,
            server_settings={"application_name": "evil_apples_4d"}
        )
        logger.info("Database pool created successfully")
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tournaments (
                id SERIAL PRIMARY KEY,
                name VARCHAR(256) NOT NULL,
                guild_id VARCHAR(64) NOT NULL,
                channel_id VARCHAR(64) NOT NULL,
                creator_id VARCHAR(64) NOT NULL,
                creator_name VARCHAR(128) NOT NULL DEFAULT 'Unknown',
                status VARCHAR(32) NOT NULL DEFAULT 'registration',
                rounds_total INTEGER NOT NULL DEFAULT 5,
                current_round INTEGER NOT NULL DEFAULT 0,
                max_players INTEGER NOT NULL DEFAULT 20,
                created_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_players (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                discord_id VARCHAR(64) NOT NULL,
                discord_name VARCHAR(128) NOT NULL,
                points INTEGER DEFAULT 0,
                rounds_won INTEGER DEFAULT 0,
                joined_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(tournament_id, discord_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_rounds (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                round_number INTEGER NOT NULL,
                prompt TEXT,
                winning_card TEXT,
                winner_discord_id VARCHAR(64),
                winner_name VARCHAR(128),
                completed_at TIMESTAMP,
                UNIQUE(tournament_id, round_number)
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


async def create_tournament(name: str, guild_id: str, channel_id: str, creator_id: str, creator_name: str, rounds_total: int = 5, max_players: int = 20):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO tournaments (name, guild_id, channel_id, creator_id, creator_name, rounds_total, max_players)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
            name, guild_id, channel_id, creator_id, creator_name, rounds_total, max_players
        )
        return row["id"]


async def get_active_tournament(channel_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM tournaments
               WHERE channel_id = $1 AND status IN ('registration', 'active')
               ORDER BY created_at DESC LIMIT 1""",
            channel_id
        )
        return dict(row) if row else None


async def join_tournament(tournament_id: int, discord_id: str, discord_name: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO tournament_players (tournament_id, discord_id, discord_name)
               VALUES ($1, $2, $3)
               ON CONFLICT (tournament_id, discord_id) DO NOTHING""",
            tournament_id, discord_id, discord_name
        )


async def get_tournament_players(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT discord_id, discord_name, points, rounds_won
               FROM tournament_players
               WHERE tournament_id = $1
               ORDER BY points DESC, rounds_won DESC""",
            tournament_id
        )
        return [dict(r) for r in rows]


async def get_tournament_player_count(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM tournament_players WHERE tournament_id = $1",
            tournament_id
        )
        return row["cnt"]


async def start_tournament(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournaments SET status = 'active', current_round = 1 WHERE id = $1",
            tournament_id
        )


async def advance_tournament_round(tournament_id: int, round_number: int, prompt: str, winning_card: str, winner_discord_id: str, winner_name: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO tournament_rounds (tournament_id, round_number, prompt, winning_card, winner_discord_id, winner_name, completed_at)
               VALUES ($1, $2, $3, $4, $5, $6, NOW())""",
            tournament_id, round_number, prompt, winning_card, winner_discord_id, winner_name
        )
        await conn.execute(
            """UPDATE tournament_players
               SET points = points + 1, rounds_won = rounds_won + 1
               WHERE tournament_id = $1 AND discord_id = $2""",
            tournament_id, winner_discord_id
        )
        tournament = await conn.fetchrow("SELECT * FROM tournaments WHERE id = $1", tournament_id)
        if tournament and round_number >= tournament["rounds_total"]:
            await conn.execute(
                "UPDATE tournaments SET status = 'completed', current_round = $2, completed_at = NOW() WHERE id = $1",
                tournament_id, round_number
            )
            return True
        else:
            await conn.execute(
                "UPDATE tournaments SET current_round = $2 + 1 WHERE id = $1",
                tournament_id, round_number
            )
            return False


async def end_tournament(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournaments SET status = 'completed', completed_at = NOW() WHERE id = $1",
            tournament_id
        )


async def get_recent_tournaments(limit: int = 10):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT t.id, t.name, t.status, t.rounds_total, t.current_round, t.creator_name, t.created_at, t.completed_at,
                      (SELECT COUNT(*) FROM tournament_players WHERE tournament_id = t.id) as player_count
               FROM tournaments t
               ORDER BY t.created_at DESC LIMIT $1""",
            limit
        )
        return [dict(r) for r in rows]


async def get_tournament_details(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        tournament = await conn.fetchrow("SELECT * FROM tournaments WHERE id = $1", tournament_id)
        if not tournament:
            return None
        players = await conn.fetch(
            """SELECT discord_id, discord_name, points, rounds_won
               FROM tournament_players WHERE tournament_id = $1
               ORDER BY points DESC, rounds_won DESC""",
            tournament_id
        )
        rounds = await conn.fetch(
            """SELECT round_number, prompt, winning_card, winner_name, completed_at
               FROM tournament_rounds WHERE tournament_id = $1
               ORDER BY round_number""",
            tournament_id
        )
        return {
            "tournament": dict(tournament),
            "players": [dict(p) for p in players],
            "rounds": [dict(r) for r in rounds]
        }
