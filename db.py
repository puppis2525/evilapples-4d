import os
import asyncio
import logging
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

logger = logging.getLogger("db")

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
                        max_size=15,
                        command_timeout=30,
                        server_settings={"application_name": "evil_apples_4d"}
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
                    logger.error("All database connection attempts failed")
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
        async with conn.transaction():
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
        async with conn.transaction():
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


def get_tiered_fee_percent(monthly_count: int) -> int:
    if monthly_count < 2:
        return 10
    elif monthly_count < 5:
        return 15
    elif monthly_count < 10:
        return 20
    else:
        return 25


async def get_server_monthly_tournament_count(guild_id: str) -> int:
    if not guild_id:
        return 0
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) as cnt FROM tournaments
               WHERE guild_id = $1
               AND created_at >= date_trunc('month', CURRENT_TIMESTAMP)""",
            guild_id
        )
        return row["cnt"] if row else 0


async def create_tournament(name: str, guild_id: str, channel_id: str, creator_id: str, creator_name: str, rounds_total: int = 5, max_players: int = 20, platform_fee_percent: int = 10):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO tournaments (name, guild_id, channel_id, creator_id, creator_name, rounds_total, max_players, platform_fee_percent)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
            name, guild_id, channel_id, creator_id, creator_name, rounds_total, max_players, platform_fee_percent
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


async def get_tournament_by_id(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tournaments WHERE id = $1", tournament_id)
        return dict(row) if row else None


async def link_tournament_to_channel(tournament_id: int, guild_id: str, channel_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournaments SET guild_id = $1, channel_id = $2 WHERE id = $3",
            guild_id, channel_id, tournament_id
        )


async def get_team_standings(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT tt.id, tt.name, tt.captain_name,
                      COALESCE(SUM(tp.points), 0) as total_points,
                      COALESCE(SUM(tp.rounds_won), 0) as total_wins,
                      COUNT(tp.id) as member_count
               FROM tournament_teams tt
               LEFT JOIN tournament_players tp ON tp.team_id = tt.id
               WHERE tt.tournament_id = $1
               GROUP BY tt.id, tt.name, tt.captain_name
               ORDER BY total_points DESC, total_wins DESC""",
            tournament_id
        )
        return [dict(r) for r in rows]


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
        async with conn.transaction():
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


async def init_paid_tournament_tables():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS connected_accounts (
                discord_user_id VARCHAR(64) PRIMARY KEY,
                stripe_account_id VARCHAR(128) NOT NULL,
                onboarding_complete BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_entries (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                discord_user_id VARCHAR(64) NOT NULL,
                stripe_session_id VARCHAR(256),
                stripe_payment_intent_id VARCHAR(256),
                amount_cents INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(32) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(tournament_id, discord_user_id)
            );
        """)
        try:
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS entry_fee_cents INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS prize_pool_cents INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS platform_fee_percent INTEGER DEFAULT 10")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS payout_status VARCHAR(32) DEFAULT 'none'")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS host_stripe_account_id VARCHAR(128)")
            await conn.execute("ALTER TABLE tournament_entries ADD COLUMN IF NOT EXISTS stripe_payment_intent_id VARCHAR(256)")
        except Exception:
            pass

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_teams (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                name VARCHAR(128) NOT NULL,
                captain_discord_id VARCHAR(64),
                captain_name VARCHAR(128) DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_sponsors (
                id SERIAL PRIMARY KEY,
                tournament_id INTEGER REFERENCES tournaments(id) ON DELETE CASCADE,
                sponsor_name VARCHAR(256) NOT NULL,
                logo_url TEXT DEFAULT '',
                amount_cents INTEGER NOT NULL DEFAULT 0,
                stripe_session_id VARCHAR(256),
                status VARCHAR(32) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        try:
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS team_count INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS team_size INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS join_mode VARCHAR(32) DEFAULT 'open'")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS prize_distribution TEXT DEFAULT '[]'")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS sponsor_enabled BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS sponsor_slots INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS sponsor_price_cents INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS web_created BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS game_mode VARCHAR(32) DEFAULT 'friend'")
            await conn.execute("ALTER TABLE tournament_players ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES tournament_teams(id) ON DELETE SET NULL")
        except Exception:
            pass


async def save_connected_account(discord_user_id: str, stripe_account_id: str, onboarding_complete: bool = False):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO connected_accounts (discord_user_id, stripe_account_id, onboarding_complete)
               VALUES ($1, $2, $3)
               ON CONFLICT (discord_user_id) DO UPDATE SET
                   stripe_account_id = $2, onboarding_complete = $3""",
            discord_user_id, stripe_account_id, onboarding_complete
        )


async def get_connected_account(discord_user_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM connected_accounts WHERE discord_user_id = $1",
            discord_user_id
        )
        return dict(row) if row else None


async def mark_account_onboarded(stripe_account_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE connected_accounts SET onboarding_complete = TRUE WHERE stripe_account_id = $1",
            stripe_account_id
        )


async def create_paid_tournament(name: str, guild_id: str, channel_id: str, creator_id: str,
                                  creator_name: str, rounds_total: int, entry_fee_cents: int,
                                  host_stripe_account_id: str, max_players: int = 20, platform_fee_percent: int = 10):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO tournaments (name, guild_id, channel_id, creator_id, creator_name,
               rounds_total, max_players, entry_fee_cents, host_stripe_account_id, payout_status, platform_fee_percent)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', $10) RETURNING id""",
            name, guild_id, channel_id, creator_id, creator_name,
            rounds_total, max_players, entry_fee_cents, host_stripe_account_id, platform_fee_percent
        )
        return row["id"]


async def record_tournament_entry(tournament_id: int, discord_user_id: str, stripe_session_id: str,
                                   amount_cents: int, stripe_payment_intent_id: str = None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO tournament_entries (tournament_id, discord_user_id, stripe_session_id,
                       stripe_payment_intent_id, amount_cents, status)
                   VALUES ($1, $2, $3, $4, $5, 'paid')
                   ON CONFLICT (tournament_id, discord_user_id) DO UPDATE SET
                       stripe_session_id = $3, stripe_payment_intent_id = $4, amount_cents = $5, status = 'paid'""",
                tournament_id, discord_user_id, stripe_session_id, stripe_payment_intent_id, amount_cents
            )
            await conn.execute(
                """UPDATE tournaments SET prize_pool_cents = (
                       SELECT COALESCE(SUM(amount_cents), 0) FROM tournament_entries
                       WHERE tournament_id = $1 AND status = 'paid'
                   ) WHERE id = $1""",
                tournament_id
            )


async def get_tournament_entry(tournament_id: int, discord_user_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM tournament_entries WHERE tournament_id = $1 AND discord_user_id = $2",
            tournament_id, discord_user_id
        )
        return dict(row) if row else None


async def get_paid_tournament_entries(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM tournament_entries WHERE tournament_id = $1 AND status = 'paid'",
            tournament_id
        )
        return [dict(r) for r in rows]


async def update_tournament_payout(tournament_id: int, status: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournaments SET payout_status = $2 WHERE id = $1",
            tournament_id, status
        )


async def refund_tournament_entries(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE tournament_entries SET status = 'refunded' WHERE tournament_id = $1",
                tournament_id
            )
            await conn.execute(
                "UPDATE tournaments SET prize_pool_cents = 0, payout_status = 'refunded' WHERE id = $1",
                tournament_id
            )


async def create_web_tournament(name: str, creator_id: str, creator_name: str,
                                rounds_total: int, team_count: int, team_size: int,
                                join_mode: str, game_mode: str, entry_fee_cents: int,
                                prize_distribution: str, host_stripe_account_id: str,
                                sponsor_enabled: bool, sponsor_slots: int,
                                sponsor_price_cents: int, guild_id: str = '', channel_id: str = '',
                                platform_fee_percent: int = 10):
    pool = await get_db_pool()
    max_players = team_count * team_size if team_count and team_size else 20
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO tournaments (name, guild_id, channel_id, creator_id, creator_name,
               rounds_total, max_players, entry_fee_cents, host_stripe_account_id,
               payout_status, team_count, team_size, join_mode, game_mode,
               prize_distribution, sponsor_enabled, sponsor_slots, sponsor_price_cents,
               web_created, platform_fee_percent)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending',
                       $10, $11, $12, $13, $14, $15, $16, $17, TRUE, $18)
               RETURNING id""",
            name, guild_id, channel_id, creator_id, creator_name,
            rounds_total, max_players, entry_fee_cents, host_stripe_account_id,
            team_count, team_size, join_mode, game_mode,
            prize_distribution, sponsor_enabled, sponsor_slots, sponsor_price_cents,
            platform_fee_percent
        )
        tournament_id = row["id"]
        for i in range(1, team_count + 1):
            await conn.execute(
                "INSERT INTO tournament_teams (tournament_id, name) VALUES ($1, $2)",
                tournament_id, f"Team {i}"
            )
        return tournament_id


async def get_tournament_teams(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT tt.id, tt.name, tt.captain_discord_id, tt.captain_name,
                      (SELECT COUNT(*) FROM tournament_players tp WHERE tp.team_id = tt.id) as member_count
               FROM tournament_teams tt
               WHERE tt.tournament_id = $1
               ORDER BY tt.id""",
            tournament_id
        )
        return [dict(r) for r in rows]


async def get_team_members(team_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT discord_id, discord_name, points, rounds_won FROM tournament_players WHERE team_id = $1 ORDER BY points DESC",
            team_id
        )
        return [dict(r) for r in rows]


async def join_tournament_team(tournament_id: int, team_id: int, discord_id: str, discord_name: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            team = await conn.fetchrow(
                "SELECT id FROM tournament_teams WHERE id = $1 AND tournament_id = $2",
                team_id, tournament_id
            )
            if not team:
                raise ValueError(f"Team {team_id} does not belong to tournament {tournament_id}")
            await conn.execute(
                """INSERT INTO tournament_players (tournament_id, discord_id, discord_name, team_id)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (tournament_id, discord_id) DO UPDATE SET team_id = $4""",
                tournament_id, discord_id, discord_name, team_id
            )


async def claim_team(tournament_id: int, team_id: int, discord_id: str, discord_name: str, team_name: str = ''):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        team = await conn.fetchrow("SELECT * FROM tournament_teams WHERE id = $1 AND tournament_id = $2", team_id, tournament_id)
        if not team:
            return False
        if team["captain_discord_id"] and team["captain_discord_id"] != discord_id:
            return False
        updates = {"captain_discord_id": discord_id, "captain_name": discord_name}
        if team_name:
            await conn.execute(
                "UPDATE tournament_teams SET captain_discord_id = $1, captain_name = $2, name = $3 WHERE id = $4",
                discord_id, discord_name, team_name, team_id
            )
        else:
            await conn.execute(
                "UPDATE tournament_teams SET captain_discord_id = $1, captain_name = $2 WHERE id = $3",
                discord_id, discord_name, team_id
            )
        return True


async def create_tournament_sponsor(tournament_id: int, sponsor_name: str, amount_cents: int,
                                     stripe_session_id: str = '', logo_url: str = ''):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO tournament_sponsors (tournament_id, sponsor_name, logo_url, amount_cents, stripe_session_id, status)
               VALUES ($1, $2, $3, $4, $5, 'paid') RETURNING id""",
            tournament_id, sponsor_name, logo_url, amount_cents, stripe_session_id
        )
        return row["id"]


async def get_tournament_sponsors(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, sponsor_name, logo_url, amount_cents, status, created_at FROM tournament_sponsors WHERE tournament_id = $1 AND status = 'paid' ORDER BY created_at",
            tournament_id
        )
        return [dict(r) for r in rows]


async def get_tournament_sponsor_count(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM tournament_sponsors WHERE tournament_id = $1 AND status = 'paid'",
            tournament_id
        )
        return row["cnt"]


async def close_tournament_registration(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournaments SET status = 'ready' WHERE id = $1 AND status = 'registration'",
            tournament_id
        )


async def get_web_tournaments(status_filter: str = None, limit: int = 50):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if status_filter and status_filter != 'all':
            rows = await conn.fetch(
                """SELECT t.id, t.name, t.status, t.rounds_total, t.current_round,
                          t.creator_name, t.creator_id, t.created_at, t.completed_at,
                          t.entry_fee_cents, t.prize_pool_cents, t.team_count, t.team_size,
                          t.join_mode, t.game_mode, t.sponsor_enabled, t.prize_distribution,
                          (SELECT COUNT(*) FROM tournament_players WHERE tournament_id = t.id) as player_count,
                          (SELECT COUNT(*) FROM tournament_sponsors WHERE tournament_id = t.id AND status = 'paid') as sponsor_count
                   FROM tournaments t
                   WHERE t.status = $1
                   ORDER BY t.created_at DESC LIMIT $2""",
                status_filter, limit
            )
        else:
            rows = await conn.fetch(
                """SELECT t.id, t.name, t.status, t.rounds_total, t.current_round,
                          t.creator_name, t.creator_id, t.created_at, t.completed_at,
                          t.entry_fee_cents, t.prize_pool_cents, t.team_count, t.team_size,
                          t.join_mode, t.game_mode, t.sponsor_enabled, t.prize_distribution,
                          (SELECT COUNT(*) FROM tournament_players WHERE tournament_id = t.id) as player_count,
                          (SELECT COUNT(*) FROM tournament_sponsors WHERE tournament_id = t.id AND status = 'paid') as sponsor_count
                   FROM tournaments t
                   ORDER BY t.created_at DESC LIMIT $1""",
                limit
            )
        return [dict(r) for r in rows]


async def get_full_tournament_detail(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        tournament = await conn.fetchrow("SELECT * FROM tournaments WHERE id = $1", tournament_id)
        if not tournament:
            return None
        t = dict(tournament)

        players = await conn.fetch(
            """SELECT tp.discord_id, tp.discord_name, tp.points, tp.rounds_won, tp.team_id, tp.joined_at
               FROM tournament_players tp WHERE tp.tournament_id = $1
               ORDER BY tp.points DESC, tp.rounds_won DESC""",
            tournament_id
        )

        teams = await conn.fetch(
            """SELECT tt.id, tt.name, tt.captain_discord_id, tt.captain_name
               FROM tournament_teams tt WHERE tt.tournament_id = $1
               ORDER BY tt.id""",
            tournament_id
        )

        sponsors = await conn.fetch(
            "SELECT id, sponsor_name, logo_url, amount_cents FROM tournament_sponsors WHERE tournament_id = $1 AND status = 'paid' ORDER BY created_at",
            tournament_id
        )

        rounds = await conn.fetch(
            """SELECT round_number, prompt, winning_card, winner_name, completed_at
               FROM tournament_rounds WHERE tournament_id = $1 ORDER BY round_number""",
            tournament_id
        )

        team_list = []
        for team in teams:
            td = dict(team)
            td["members"] = [dict(p) for p in players if p["team_id"] == team["id"]]
            team_list.append(td)

        return {
            "tournament": t,
            "players": [dict(p) for p in players],
            "teams": team_list,
            "sponsors": [dict(s) for s in sponsors],
            "rounds": [dict(r) for r in rounds],
        }


async def update_tournament_team(tournament_id: int, team_id: int, name: str = None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if name:
            await conn.execute(
                "UPDATE tournament_teams SET name = $1 WHERE id = $2 AND tournament_id = $3",
                name, team_id, tournament_id
            )


async def move_player_to_team(tournament_id: int, discord_id: str, team_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournament_players SET team_id = $1 WHERE tournament_id = $2 AND discord_id = $3",
            team_id, tournament_id, discord_id
        )


async def remove_tournament_player(tournament_id: int, discord_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM tournament_players WHERE tournament_id = $1 AND discord_id = $2",
            tournament_id, discord_id
        )


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
        try:
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_custom_decks_name_user ON custom_decks (LOWER(name), discord_user_id) WHERE discord_user_id IS NOT NULL"
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


async def get_random_cards_multi(deck_ids: list[int], card_type: str, count: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT card_text FROM custom_cards WHERE deck_id = ANY($1) AND card_type = $2 ORDER BY RANDOM() LIMIT $3",
            deck_ids, card_type, count
        )
        return [r["card_text"] for r in rows]


async def get_user_deck_count(discord_user_id: str) -> int:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM custom_decks WHERE discord_user_id = $1",
            discord_user_id
        )


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
        async with conn.transaction():
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


STORE_DECK_META = {
    "420": ("420 Blaze It", "Light up the party with 20 weed-themed answer cards"),
    "addictions": ("Addictions", "20 cards about all the things you just can't quit"),
    "adulting": ("Adulting", "48 cards for when you're pretending to be a grown-up"),
    "babes": ("Babes", "20 cards featuring the hottest names in Hollywood"),
    "british": ("British AF", "20 cards from across the pond, innit"),
    "college": ("College", "20 cards about the best/worst years of your life"),
    "cuties": ("Cuties", "4 adorable cards to lighten up the darkness"),
    "dads": ("Dads", "20 cards about fatherhood, for better or worse"),
    "deckronomicon": ("Deckronomicon", "20 dark and supernatural cards from the beyond"),
    "drunkaf": ("Drunk AF", "20 cards for when you've had way too many"),
    "familyguy": ("Family Guy", "20 cards featuring your favorite Quahog residents"),
    "food": ("Food", "20 cards mixing food with filth"),
    "grateful": ("Grateful", "20 cards about the little things in life"),
    "gross": ("Gross", "20 cards that'll make you gag"),
    "gross2": ("Gross 2", "20 more cards that'll make you gag even harder"),
    "history": ("History", "20 cards from the filthiest moments in history"),
    "horror": ("Horror", "20 terrifying cards from your worst nightmares"),
    "illegalactivities": ("Illegal Activities", "20 cards about things you definitely shouldn't do"),
    "insults": ("Insults", "20 creative ways to call someone terrible"),
    "internet": ("Internet", "20 cards about online life and digital disasters"),
    "kamasutra": ("Kama Sutra", "20 cards featuring exotic and disturbing positions"),
    "kinks": ("Kinks", "20 cards for the adventurous and depraved"),
    "masturbation": ("Masturbation", "20 cards about solo adventures"),
    "medical": ("Medical", "20 cards from the doctor's office of horrors"),
    "monsters": ("Monsters", "20 cards featuring creatures from your darkest fears"),
    "naughty17": ("Naughty 17", "20 holiday cards that'll get you on the naughty list"),
    "netflix": ("Netflix & Chill", "20 cards about binge-watching and TV obsessions"),
    "office": ("Office", "20 cards about workplace nightmares"),
    "poke": ("Poke", "20 cards for Pokemon fans with dirty minds"),
    "pussy": ("Pussy", "20 cards celebrating lady parts"),
    "rickmorty": ("Rick & Morty", "20 cards from the multiverse of madness"),
    "scifi": ("Sci-Fi", "20 cards from galaxies far, far away"),
    "sex": ("Sex", "20 cards about getting busy"),
    "sexed": ("Sex Ed", "20 cards about the birds, bees, and bodily fluids"),
    "sexmodern": ("Modern Sex", "20 cards about dating and sex in the modern age"),
    "shittyjobs": ("Shitty Jobs", "20 cards about the worst jobs imaginable"),
    "simpsons": ("Simpsons", "20 cards from Springfield's finest"),
    "sins": ("Sins", "20 sinfully good cards"),
    "space": ("Space", "20 cards from the final frontier"),
    "stinkypinky": ("Stinky Pinky", "20 rhyming cards that are pure poetry"),
    "stranger": ("Stranger Things", "20 cards from the Upside Down"),
    "surreal": ("Surreal", "30 mind-bending cards from the fever dreams of deranged artists"),
}


async def seed_store_decks():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        import os
        card_file = os.path.join(os.path.dirname(__file__),
            "attached_assets",
            "Pasted-Card-Text-Hidden-Deck-an-eighth-of-strawberry-kush-FALS_1772996060719.txt")

        decks = {}
        if os.path.exists(card_file):
            with open(card_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("Card Text"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        card_text = parts[0].strip()
                        deck_key = parts[2].strip()
                        if deck_key not in decks:
                            decks[deck_key] = []
                        decks[deck_key].append(card_text)

        existing_names = set()
        rows = await conn.fetch(
            "SELECT name FROM store_listings"
        )
        for r in rows:
            existing_names.add(r["name"])

        created = 0
        for deck_key, meta in STORE_DECK_META.items():
            display_name, description = meta

            if display_name in existing_names:
                continue

            cards = decks.get(deck_key, [])

            if not cards:
                card_rows = await conn.fetch(
                    "SELECT card_text FROM custom_cards cc JOIN custom_decks cd ON cc.deck_id = cd.id WHERE cd.name = $1 AND cd.discord_user_id IS NULL",
                    display_name
                )
                if card_rows:
                    continue

            deck_id = await conn.fetchval(
                """INSERT INTO custom_decks (name, description, creator_name, discord_user_id, prompt_count, answer_count)
                   VALUES ($1, $2, 'Store', NULL, 0, $3) RETURNING id""",
                display_name, description, len(cards)
            )

            for card_text in cards:
                await conn.execute(
                    "INSERT INTO custom_cards (deck_id, card_type, card_text) VALUES ($1, 'answer', $2)",
                    deck_id, card_text
                )

            await conn.execute(
                """INSERT INTO store_listings (name, description, image_url, price_cents, bundle_size, deck_ids, featured, active)
                   VALUES ($1, $2, '', 99, 1, $3, false, true)""",
                display_name, description, str(deck_id)
            )
            created += 1

        if created > 0:
            logger.info(f"Seeded {created} new store decks and listings")
        else:
            logger.info("All store decks already present")
