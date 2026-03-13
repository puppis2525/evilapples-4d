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
                    await conn.execute("ALTER TABLE game_rounds ADD COLUMN IF NOT EXISTS platform VARCHAR(16) DEFAULT 'discord';")
                    await conn.execute("ALTER TABLE leaderboard ADD COLUMN IF NOT EXISTS platform VARCHAR(16) DEFAULT 'discord';")
                    await conn.execute("ALTER TABLE leaderboard DROP CONSTRAINT IF EXISTS leaderboard_discord_id_key;")
                    await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS leaderboard_uid_platform ON leaderboard(discord_id, platform);")
            except Exception as e:
                logger.warning(f"Migration note: {e}")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tg_bot_stats (
                id SERIAL PRIMARY KEY,
                group_count INTEGER NOT NULL DEFAULT 0,
                recorded_at TIMESTAMP DEFAULT NOW()
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
                completed_at TIMESTAMP,
                entry_fee_cents INTEGER DEFAULT 0,
                prize_pool_cents INTEGER DEFAULT 0,
                platform_fee_percent INTEGER DEFAULT 10,
                payout_status VARCHAR(32) DEFAULT 'none',
                host_stripe_account_id VARCHAR(128),
                team_count INTEGER DEFAULT 0,
                team_size INTEGER DEFAULT 0,
                join_mode VARCHAR(32) DEFAULT 'open',
                prize_distribution TEXT DEFAULT '[]',
                sponsor_enabled BOOLEAN DEFAULT FALSE,
                sponsor_slots INTEGER DEFAULT 0,
                sponsor_price_cents INTEGER DEFAULT 0,
                web_created BOOLEAN DEFAULT FALSE,
                game_mode VARCHAR(32) DEFAULT 'friend'
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
                team_id INTEGER,
                UNIQUE(tournament_id, discord_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_decks (
                id SERIAL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                description TEXT DEFAULT '',
                creator_name VARCHAR(128) DEFAULT 'Unknown',
                prompt_count INTEGER DEFAULT 0,
                answer_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                discord_user_id VARCHAR(64)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_cards (
                id SERIAL PRIMARY KEY,
                deck_id INTEGER REFERENCES custom_decks(id) ON DELETE CASCADE,
                card_type VARCHAR(16) NOT NULL,
                card_text TEXT NOT NULL
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS store_listings (
                id SERIAL PRIMARY KEY,
                name VARCHAR(256) NOT NULL,
                description TEXT DEFAULT '',
                image_url VARCHAR(512) DEFAULT '',
                price_cents INTEGER DEFAULT 99,
                bundle_size INTEGER DEFAULT 1,
                deck_ids TEXT DEFAULT '',
                active BOOLEAN DEFAULT TRUE,
                featured BOOLEAN DEFAULT FALSE,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS store_purchases (
                id SERIAL PRIMARY KEY,
                discord_user_id VARCHAR(64) NOT NULL,
                listing_id INTEGER REFERENCES store_listings(id),
                stripe_session_id VARCHAR(256),
                fulfilled BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS premium_servers (
                id SERIAL PRIMARY KEY,
                guild_id VARCHAR(64) UNIQUE NOT NULL,
                guild_name VARCHAR(256) NOT NULL DEFAULT 'Unknown',
                plan VARCHAR(32) NOT NULL DEFAULT 'pro',
                stripe_customer_id VARCHAR(128),
                stripe_subscription_id VARCHAR(128),
                activated_by VARCHAR(64),
                activated_by_name VARCHAR(128),
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP
            );
        """)

    logger.info("Database initialized for Telegram bot")


# ─── Core game functions ──────────────────────────────────────────────────────

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


async def get_tg_stats() -> dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        players = await conn.fetchval(
            "SELECT COUNT(*) FROM leaderboard WHERE platform = 'telegram' AND games_played > 0"
        ) or 0
        rounds = await conn.fetchval(
            "SELECT COUNT(*) FROM game_rounds WHERE platform = 'telegram'"
        ) or 0
        groups = await conn.fetchval(
            "SELECT group_count FROM tg_bot_stats ORDER BY recorded_at DESC LIMIT 1"
        ) or 0
        return {"players": players, "rounds": rounds, "groups": groups}


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


# ─── Tournament functions (Task #8) ──────────────────────────────────────────

async def create_tg_tournament(name: str, chat_id: int, creator_id: str,
                               creator_name: str, rounds_total: int = 5) -> int:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO tournaments
                (name, guild_id, channel_id, creator_id, creator_name,
                 status, rounds_total, current_round, max_players)
            VALUES ($1, $2, $3, $4, $5, 'registration', $6, 0, 50)
            RETURNING id
        """, name, f"tg_chat_{chat_id}", str(chat_id), creator_id, creator_name, rounds_total)
        return row["id"]


async def get_active_tg_tournament(chat_id: int) -> dict | None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM tournaments
            WHERE channel_id = $1
              AND status IN ('registration', 'active')
            ORDER BY created_at DESC
            LIMIT 1
        """, str(chat_id))
        return dict(row) if row else None


async def join_tg_tournament(tournament_id: int, tg_user_id: str, username: str) -> bool:
    pool = await get_db_pool()
    uid = f"tg_{tg_user_id}"
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM tournament_players WHERE tournament_id = $1 AND discord_id = $2",
            tournament_id, uid
        )
        if existing:
            return False
        await conn.execute("""
            INSERT INTO tournament_players (tournament_id, discord_id, discord_name, points, rounds_won)
            VALUES ($1, $2, $3, 0, 0)
        """, tournament_id, uid, username)
        return True


async def get_tg_tournament_players(tournament_id: int) -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT discord_id, discord_name, points, rounds_won
            FROM tournament_players
            WHERE tournament_id = $1
            ORDER BY points DESC, rounds_won DESC
        """, tournament_id)
        return [dict(r) for r in rows]


async def get_tg_tournament_player_count(tournament_id: int) -> int:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM tournament_players WHERE tournament_id = $1",
            tournament_id
        )
        return row["cnt"] if row else 0


async def start_tg_tournament(tournament_id: int) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tournaments SET status = 'active', current_round = 1
            WHERE id = $1
        """, tournament_id)


async def advance_tg_tournament_round(tournament_id: int, round_number: int,
                                      prompt: str, winning_card: str,
                                      winner_tg_id: str, winner_name: str) -> bool:
    pool = await get_db_pool()
    uid = f"tg_{winner_tg_id}"
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tournament_players
            SET points = points + 1, rounds_won = rounds_won + 1
            WHERE tournament_id = $1 AND discord_id = $2
        """, tournament_id, uid)

        row = await conn.fetchrow(
            "SELECT rounds_total FROM tournaments WHERE id = $1", tournament_id
        )
        rounds_total = row["rounds_total"] if row else 5

        if round_number >= rounds_total:
            await conn.execute("""
                UPDATE tournaments
                SET status = 'ended', completed_at = NOW(), current_round = $2
                WHERE id = $1
            """, tournament_id, round_number)
            return True
        else:
            await conn.execute("""
                UPDATE tournaments SET current_round = $2 WHERE id = $1
            """, tournament_id, round_number + 1)
            return False


async def end_tg_tournament(tournament_id: int) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE tournaments
            SET status = 'ended', completed_at = NOW()
            WHERE id = $1
        """, tournament_id)


# ─── Premium functions (Task #9) ─────────────────────────────────────────────

async def is_premium_group(chat_id: int) -> bool:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id FROM premium_servers
            WHERE guild_id = $1 AND active = TRUE
        """, f"tg_chat_{chat_id}")
        return row is not None


# ─── Deck functions (Task #9) ─────────────────────────────────────────────────

async def get_tg_user_decks(tg_user_id: str) -> list[dict]:
    pool = await get_db_pool()
    uid = f"tg_{tg_user_id}"
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, description, prompt_count, answer_count, created_at
            FROM custom_decks
            WHERE discord_user_id = $1
            ORDER BY created_at DESC
        """, uid)
        return [dict(r) for r in rows]


async def create_tg_deck(name: str, description: str,
                         tg_user_id: str, creator_name: str) -> int:
    pool = await get_db_pool()
    uid = f"tg_{tg_user_id}"
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("""
            SELECT id FROM custom_decks
            WHERE LOWER(name) = LOWER($1) AND discord_user_id = $2
        """, name, uid)
        if existing:
            return None
        row = await conn.fetchrow("""
            INSERT INTO custom_decks (name, description, creator_name, discord_user_id)
            VALUES ($1, $2, $3, $4)
            RETURNING id
        """, name, description, creator_name, uid)
        return row["id"]


async def add_cards_to_tg_deck(deck_id: int, cards: list[tuple[str, str]]) -> dict:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for card_type, card_text in cards:
            await conn.execute("""
                INSERT INTO custom_cards (deck_id, card_type, card_text) VALUES ($1, $2, $3)
            """, deck_id, card_type, card_text)
        prompt_count = await conn.fetchval(
            "SELECT COUNT(*) FROM custom_cards WHERE deck_id = $1 AND card_type = 'prompt'", deck_id
        )
        answer_count = await conn.fetchval(
            "SELECT COUNT(*) FROM custom_cards WHERE deck_id = $1 AND card_type = 'answer'", deck_id
        )
        await conn.execute("""
            UPDATE custom_decks SET prompt_count = $2, answer_count = $3 WHERE id = $1
        """, deck_id, prompt_count, answer_count)
        return {"prompt_count": prompt_count, "answer_count": answer_count}


async def get_tg_deck_cards(deck_id: int, card_type: str) -> list[str]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT card_text FROM custom_cards
            WHERE deck_id = $1 AND card_type = $2
            ORDER BY RANDOM()
        """, deck_id, card_type)
        return [r["card_text"] for r in rows]


async def get_random_cards_from_decks(deck_ids: list[int], card_type: str, count: int) -> list[str]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT card_text FROM custom_cards
            WHERE deck_id = ANY($1) AND card_type = $2
            ORDER BY RANDOM()
            LIMIT $3
        """, deck_ids, card_type, count)
        return [r["card_text"] for r in rows]


async def delete_tg_deck(deck_id: int, tg_user_id: str) -> bool:
    pool = await get_db_pool()
    uid = f"tg_{tg_user_id}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM custom_decks WHERE id = $1 AND discord_user_id = $2",
            deck_id, uid
        )
        if not row:
            return False
        await conn.execute("DELETE FROM custom_decks WHERE id = $1", deck_id)
        return True


# ─── Store functions (Task #9) ────────────────────────────────────────────────

async def get_tg_store_listings() -> list[dict]:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, description, price_cents, featured
            FROM store_listings
            WHERE active = TRUE
            ORDER BY featured DESC, sort_order ASC, id ASC
        """)
        return [dict(r) for r in rows]


async def get_tg_store_listing(listing_id: int) -> dict | None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, name, description, price_cents, bundle_size
            FROM store_listings
            WHERE id = $1 AND active = TRUE
        """, listing_id)
        return dict(row) if row else None
