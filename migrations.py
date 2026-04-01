import logging
import time

logger = logging.getLogger("migrations")

MIGRATIONS = [
    {
        "version": 1,
        "description": "Create core game tables",
        "sql": [
            """CREATE TABLE IF NOT EXISTS active_game_sessions (
                session_key VARCHAR(64) PRIMARY KEY,
                session_type VARCHAR(16) NOT NULL DEFAULT 'channel',
                game_data JSONB NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS game_rounds (
                id SERIAL PRIMARY KEY,
                prompt TEXT NOT NULL,
                winning_card TEXT NOT NULL,
                winner_discord_id VARCHAR(64) NOT NULL,
                winner_name VARCHAR(128) NOT NULL DEFAULT 'Unknown',
                slug VARCHAR(256) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS leaderboard (
                id SERIAL PRIMARY KEY,
                discord_id VARCHAR(64) UNIQUE NOT NULL,
                discord_name VARCHAR(128) NOT NULL DEFAULT 'Unknown',
                wins INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT NOW()
            )""",
        ]
    },
    {
        "version": 2,
        "description": "Create tournament tables",
        "sql": [
            """CREATE TABLE IF NOT EXISTS tournaments (
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
            )""",
            """CREATE TABLE IF NOT EXISTS tournament_players (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                discord_id VARCHAR(64) NOT NULL,
                discord_name VARCHAR(128) NOT NULL,
                points INTEGER DEFAULT 0,
                rounds_won INTEGER DEFAULT 0,
                joined_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(tournament_id, discord_id)
            )""",
            """CREATE TABLE IF NOT EXISTS tournament_rounds (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                round_number INTEGER NOT NULL,
                prompt TEXT,
                winning_card TEXT,
                winner_discord_id VARCHAR(64),
                winner_name VARCHAR(128),
                completed_at TIMESTAMP,
                UNIQUE(tournament_id, round_number)
            )""",
        ]
    },
    {
        "version": 3,
        "description": "Create premium servers and text ads tables",
        "sql": [
            """CREATE TABLE IF NOT EXISTS premium_servers (
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
            )""",
            """CREATE TABLE IF NOT EXISTS text_ads (
                id SERIAL PRIMARY KEY,
                title VARCHAR(128) NOT NULL,
                body VARCHAR(256) NOT NULL,
                url TEXT NOT NULL,
                display_name VARCHAR(128) NOT NULL DEFAULT '',
                active BOOLEAN DEFAULT TRUE,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
        ]
    },
    {
        "version": 4,
        "description": "Create paid tournament tables (connected accounts, entries, teams, sponsors)",
        "sql": [
            """CREATE TABLE IF NOT EXISTS connected_accounts (
                discord_user_id VARCHAR(64) PRIMARY KEY,
                stripe_account_id VARCHAR(128) NOT NULL,
                onboarding_complete BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS tournament_entries (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                discord_user_id VARCHAR(64) NOT NULL,
                stripe_session_id VARCHAR(256),
                stripe_payment_intent_id VARCHAR(256),
                amount_cents INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(32) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW(),
                refunded_at TIMESTAMP,
                UNIQUE(tournament_id, discord_user_id)
            )""",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS entry_fee_cents INTEGER DEFAULT 0",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS prize_pool_cents INTEGER DEFAULT 0",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS platform_fee_percent INTEGER DEFAULT 10",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS payout_status VARCHAR(32) DEFAULT 'none'",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS host_stripe_account_id VARCHAR(128)",
            """CREATE TABLE IF NOT EXISTS tournament_teams (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                name VARCHAR(128) NOT NULL,
                captain_discord_id VARCHAR(64),
                captain_name VARCHAR(128) DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS tournament_sponsors (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                sponsor_name VARCHAR(256) NOT NULL,
                logo_url TEXT DEFAULT '',
                amount_cents INTEGER NOT NULL DEFAULT 0,
                stripe_session_id VARCHAR(256),
                status VARCHAR(32) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS team_count INTEGER DEFAULT 0",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS team_size INTEGER DEFAULT 0",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS join_mode VARCHAR(32) DEFAULT 'open'",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS prize_distribution TEXT DEFAULT '[]'",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS sponsor_enabled BOOLEAN DEFAULT FALSE",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS sponsor_slots INTEGER DEFAULT 0",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS sponsor_price_cents INTEGER DEFAULT 0",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS web_created BOOLEAN DEFAULT FALSE",
            "ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS game_mode VARCHAR(32) DEFAULT 'friend'",
        ]
    },
    {
        "version": 5,
        "description": "Add team_id to tournament_players",
        "sql": [
            "ALTER TABLE tournament_players ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES tournament_teams(id) ON DELETE SET NULL",
        ]
    },
    {
        "version": 6,
        "description": "Create custom deck tables",
        "sql": [
            """CREATE TABLE IF NOT EXISTS custom_decks (
                id SERIAL PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                description TEXT DEFAULT '',
                creator_name VARCHAR(128) DEFAULT 'Unknown',
                discord_user_id VARCHAR(64),
                prompt_count INTEGER DEFAULT 0,
                answer_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS custom_cards (
                id SERIAL PRIMARY KEY,
                deck_id INTEGER REFERENCES custom_decks(id) ON DELETE CASCADE,
                card_type VARCHAR(16) NOT NULL,
                card_text TEXT NOT NULL
            )""",
        ]
    },
    {
        "version": 7,
        "description": "Create unique index on custom decks",
        "sql": [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_custom_decks_name_user ON custom_decks (LOWER(name), discord_user_id) WHERE discord_user_id IS NOT NULL",
        ]
    },
    {
        "version": 8,
        "description": "Create store tables",
        "sql": [
            """CREATE TABLE IF NOT EXISTS store_listings (
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
            )""",
            """CREATE TABLE IF NOT EXISTS store_purchases (
                id SERIAL PRIMARY KEY,
                discord_user_id VARCHAR(64) NOT NULL,
                listing_id INTEGER REFERENCES store_listings(id),
                stripe_session_id VARCHAR(256) UNIQUE,
                status VARCHAR(32) DEFAULT 'completed',
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_store_purchases_session ON store_purchases(stripe_session_id)",
        ]
    },
    {
        "version": 9,
        "description": "Create bot stats table",
        "sql": [
            """CREATE TABLE IF NOT EXISTS bot_stats (
                id SERIAL PRIMARY KEY,
                guild_count INTEGER NOT NULL DEFAULT 0,
                recorded_at TIMESTAMP DEFAULT NOW()
            )""",
        ]
    },
    {
        "version": 10,
        "description": "Add platform columns for Telegram support",
        "sql": [
            "ALTER TABLE game_rounds ADD COLUMN IF NOT EXISTS platform VARCHAR(16) DEFAULT 'discord'",
            "ALTER TABLE leaderboard ADD COLUMN IF NOT EXISTS platform VARCHAR(16) DEFAULT 'discord'",
            "ALTER TABLE leaderboard DROP CONSTRAINT IF EXISTS leaderboard_discord_id_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS leaderboard_uid_platform ON leaderboard(discord_id, platform)",
        ]
    },
    {
        "version": 11,
        "description": "Create Telegram bot stats table",
        "sql": [
            """CREATE TABLE IF NOT EXISTS tg_bot_stats (
                id SERIAL PRIMARY KEY,
                group_count INTEGER NOT NULL DEFAULT 0,
                recorded_at TIMESTAMP DEFAULT NOW()
            )""",
        ]
    },
    {
        "version": 12,
        "description": "Create rate_limit_entries table for DB-backed rate limiting",
        "sql": [
            """CREATE TABLE IF NOT EXISTS rate_limit_entries (
                id SERIAL PRIMARY KEY,
                key VARCHAR(256) NOT NULL,
                hit_at TIMESTAMP NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_key ON rate_limit_entries(key)",
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_hit_at ON rate_limit_entries(hit_at)",
        ]
    },
    {
        "version": 13,
        "description": "Ensure store_purchases has status column for backward compatibility",
        "sql": [
            "ALTER TABLE store_purchases ADD COLUMN IF NOT EXISTS status VARCHAR(32) DEFAULT 'completed'",
        ]
    },
]


async def run_migrations(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            applied_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    try:
        await conn.execute("SELECT pg_advisory_lock(8675309)")
    except Exception:
        logger.warning("Could not acquire advisory lock, proceeding without lock")

    try:
        applied = set()
        rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
        for row in rows:
            applied.add(row["version"])

        pending = [m for m in MIGRATIONS if m["version"] not in applied]
        if not pending:
            logger.info("Database schema is up to date")
            return

        pending.sort(key=lambda m: m["version"])
        for migration in pending:
            version = migration["version"]
            desc = migration["description"]
            logger.info(f"Applying migration {version}: {desc}")
            try:
                for sql in migration["sql"]:
                    await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES ($1, $2) ON CONFLICT (version) DO NOTHING",
                    version, desc
                )
                logger.info(f"Migration {version} applied successfully")
            except Exception as e:
                logger.error(f"Migration {version} failed: {e}")
                raise
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock(8675309)")
        except Exception:
            pass
