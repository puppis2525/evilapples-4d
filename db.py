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


async def init_deck_tables():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_decks (
                id SERIAL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                description TEXT DEFAULT '',
                creator_name VARCHAR(128) DEFAULT 'Unknown',
                discord_user_id VARCHAR(64),
                prompt_count INTEGER DEFAULT 0,
                answer_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
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
        try:
            await conn.execute(
                "ALTER TABLE custom_decks ADD COLUMN IF NOT EXISTS discord_user_id VARCHAR(64)"
            )
        except Exception:
            pass


async def create_deck(name: str, description: str, creator_name: str, discord_user_id: str = None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO custom_decks (name, description, creator_name, discord_user_id)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            name, description, creator_name, discord_user_id
        )
        return row["id"]


async def add_cards_to_deck(deck_id: int, cards: list):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for card in cards:
            await conn.execute(
                "INSERT INTO custom_cards (deck_id, card_type, card_text) VALUES ($1, $2, $3)",
                deck_id, card["type"], card["text"]
            )
        prompt_count = await conn.fetchval(
            "SELECT COUNT(*) FROM custom_cards WHERE deck_id = $1 AND card_type = 'prompt'",
            deck_id
        )
        answer_count = await conn.fetchval(
            "SELECT COUNT(*) FROM custom_cards WHERE deck_id = $1 AND card_type = 'answer'",
            deck_id
        )
        await conn.execute(
            "UPDATE custom_decks SET prompt_count = $2, answer_count = $3 WHERE id = $1",
            deck_id, prompt_count, answer_count
        )


async def get_all_decks(discord_user_id: str = None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if discord_user_id:
            rows = await conn.fetch(
                "SELECT id, name, description, creator_name, discord_user_id, prompt_count, answer_count, created_at FROM custom_decks WHERE discord_user_id = $1 ORDER BY created_at DESC",
                discord_user_id
            )
        else:
            rows = await conn.fetch(
                "SELECT id, name, description, creator_name, discord_user_id, prompt_count, answer_count, created_at FROM custom_decks ORDER BY created_at DESC"
            )
        return [dict(r) for r in rows]


async def get_deck_cards(deck_id: int, card_type: str = None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if card_type:
            rows = await conn.fetch(
                "SELECT card_text FROM custom_cards WHERE deck_id = $1 AND card_type = $2",
                deck_id, card_type
            )
        else:
            rows = await conn.fetch(
                "SELECT card_type, card_text FROM custom_cards WHERE deck_id = $1",
                deck_id
            )
        return [dict(r) for r in rows]


async def delete_deck(deck_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM custom_decks WHERE id = $1", deck_id)


async def get_deck_by_name(name: str, discord_user_id: str = None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if discord_user_id:
            row = await conn.fetchrow(
                "SELECT * FROM custom_decks WHERE LOWER(name) = LOWER($1) AND discord_user_id = $2",
                name, discord_user_id
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM custom_decks WHERE LOWER(name) = LOWER($1)", name
            )
        return dict(row) if row else None


async def get_deck_by_id(deck_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM custom_decks WHERE id = $1", deck_id
        )
        return dict(row) if row else None


async def get_random_cards(deck_id: int, card_type: str, count: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT card_text FROM custom_cards WHERE deck_id = $1 AND card_type = $2 ORDER BY RANDOM() LIMIT $3",
            deck_id, card_type, count
        )
        return [r["card_text"] for r in rows]


async def init_store_tables():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
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
                stripe_session_id VARCHAR(256) UNIQUE,
                status VARCHAR(32) DEFAULT 'completed',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        try:
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_store_purchases_session ON store_purchases(stripe_session_id)"
            )
        except Exception:
            pass


async def get_store_listings(active_only: bool = True):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if active_only:
            rows = await conn.fetch(
                "SELECT * FROM store_listings WHERE active = TRUE ORDER BY sort_order, created_at DESC"
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM store_listings ORDER BY sort_order, created_at DESC"
            )
        results = []
        for r in rows:
            item = dict(r)
            deck_ids_str = item.get("deck_ids", "")
            preview_cards = []
            if deck_ids_str:
                try:
                    first_deck_id = int(deck_ids_str.split(",")[0].strip())
                    sample = await conn.fetch(
                        "SELECT card_text FROM custom_cards WHERE deck_id = $1 ORDER BY RANDOM() LIMIT 2",
                        first_deck_id
                    )
                    preview_cards = [s["card_text"] for s in sample]
                except Exception:
                    pass
            item["preview_cards"] = preview_cards
            results.append(item)
        return results


async def get_store_listing(listing_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM store_listings WHERE id = $1", listing_id
        )
        return dict(row) if row else None


async def create_store_listing(name, description, image_url, price_cents, bundle_size, deck_ids, featured):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO store_listings (name, description, image_url, price_cents, bundle_size, deck_ids, featured)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
            name, description, image_url, price_cents, bundle_size, deck_ids, featured
        )
        return row["id"]


async def update_store_listing(listing_id, **kwargs):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        allowed = ["name", "description", "image_url", "price_cents", "bundle_size", "deck_ids", "active", "featured", "sort_order"]
        sets = []
        vals = []
        idx = 1
        for key in allowed:
            if key in kwargs:
                sets.append(f"{key} = ${idx}")
                vals.append(kwargs[key])
                idx += 1
        if not sets:
            return
        vals.append(listing_id)
        query = f"UPDATE store_listings SET {', '.join(sets)} WHERE id = ${idx}"
        await conn.execute(query, *vals)


async def delete_store_listing(listing_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM store_listings WHERE id = $1", listing_id)


async def record_store_purchase(discord_user_id, listing_id, stripe_session_id):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchrow(
            """INSERT INTO store_purchases (discord_user_id, listing_id, stripe_session_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (stripe_session_id) DO NOTHING
               RETURNING id""",
            discord_user_id, listing_id, stripe_session_id
        )
        return result is not None


async def fulfill_store_purchase(discord_user_id: str, listing_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        listing = await conn.fetchrow(
            "SELECT * FROM store_listings WHERE id = $1", listing_id
        )
        if not listing:
            logger.warning(f"Fulfill: listing {listing_id} not found")
            return

        deck_ids_str = listing.get("deck_ids", "")
        if not deck_ids_str:
            logger.warning(f"Fulfill: listing {listing_id} has no deck_ids")
            return

        source_deck_ids = []
        for part in deck_ids_str.split(","):
            part = part.strip()
            if part.isdigit():
                source_deck_ids.append(int(part))

        if not source_deck_ids:
            logger.warning(f"Fulfill: listing {listing_id} has no valid deck_ids")
            return

        for source_id in source_deck_ids:
            source_deck = await conn.fetchrow(
                "SELECT * FROM custom_decks WHERE id = $1", source_id
            )
            if not source_deck:
                logger.warning(f"Fulfill: source deck {source_id} not found")
                continue

            existing = await conn.fetchrow(
                """SELECT id FROM custom_decks
                   WHERE LOWER(name) = LOWER($1) AND discord_user_id = $2""",
                source_deck["name"], discord_user_id
            )
            if existing:
                logger.info(f"Fulfill: user {discord_user_id} already has deck '{source_deck['name']}'")
                continue

            new_deck = await conn.fetchrow(
                """INSERT INTO custom_decks (name, description, creator_name, discord_user_id)
                   VALUES ($1, $2, $3, $4) RETURNING id""",
                source_deck["name"],
                source_deck.get("description", ""),
                source_deck.get("creator_name", "Store"),
                discord_user_id
            )
            new_deck_id = new_deck["id"]

            cards = await conn.fetch(
                "SELECT card_type, card_text FROM custom_cards WHERE deck_id = $1",
                source_id
            )

            if cards:
                await conn.executemany(
                    "INSERT INTO custom_cards (deck_id, card_type, card_text) VALUES ($1, $2, $3)",
                    [(new_deck_id, c["card_type"], c["card_text"]) for c in cards]
                )

                prompt_count = sum(1 for c in cards if c["card_type"] == "prompt")
                answer_count = sum(1 for c in cards if c["card_type"] == "answer")
                await conn.execute(
                    "UPDATE custom_decks SET prompt_count = $2, answer_count = $3 WHERE id = $1",
                    new_deck_id, prompt_count, answer_count
                )

            logger.info(f"Fulfill: copied deck '{source_deck['name']}' ({len(cards)} cards) to user {discord_user_id}")


async def get_user_purchases(discord_user_id):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT sp.*, sl.name as listing_name FROM store_purchases sp
               JOIN store_listings sl ON sp.listing_id = sl.id
               WHERE sp.discord_user_id = $1 ORDER BY sp.created_at DESC""",
            discord_user_id
        )
        return [dict(r) for r in rows]


async def has_purchased(discord_user_id, listing_id):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM store_purchases WHERE discord_user_id = $1 AND listing_id = $2",
            discord_user_id, listing_id
        )
        return row is not None


async def is_premium_server(guild_id: str) -> bool:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT active FROM premium_servers WHERE guild_id = $1 AND active = TRUE",
            guild_id
        )
        return row is not None


async def get_premium_server(guild_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM premium_servers WHERE guild_id = $1",
            guild_id
        )
        return dict(row) if row else None


async def activate_premium(guild_id: str, guild_name: str, plan: str,
                           stripe_customer_id: str, stripe_subscription_id: str,
                           activated_by: str, activated_by_name: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO premium_servers
               (guild_id, guild_name, plan, stripe_customer_id, stripe_subscription_id,
                activated_by, activated_by_name, active)
               VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
               ON CONFLICT (guild_id) DO UPDATE SET
                   guild_name = $2, plan = $3, stripe_customer_id = $4,
                   stripe_subscription_id = $5, activated_by = $6,
                   activated_by_name = $7, active = TRUE""",
            guild_id, guild_name, plan, stripe_customer_id,
            stripe_subscription_id, activated_by, activated_by_name
        )


async def deactivate_premium(stripe_subscription_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE premium_servers SET active = FALSE WHERE stripe_subscription_id = $1",
            stripe_subscription_id
        )


async def get_premium_stats():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE active = TRUE) as active FROM premium_servers"
        )
        return dict(row) if row else {"total": 0, "active": 0}
