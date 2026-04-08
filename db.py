import os
import json
import asyncio
import logging
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

logger = logging.getLogger("db")

_pools: dict = {}


async def get_db_pool():
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    pool = _pools.get(loop_id)
    if pool is None or pool._closed:
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Creating database connection pool (attempt {attempt}/{max_retries})...")
                pool = await asyncio.wait_for(
                    asyncpg.create_pool(
                        DATABASE_URL,
                        min_size=2,
                        max_size=10,
                        command_timeout=30,
                        server_settings={"application_name": "evil_apples_4d"}
                    ),
                    timeout=15
                )
                logger.info("Database pool created successfully (max_size=10)")
                _pools[loop_id] = pool
                return pool
            except (asyncio.TimeoutError, OSError, asyncpg.PostgresError) as e:
                logger.warning(f"Database connection attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 * attempt)
                else:
                    logger.error("All database connection attempts failed")
                    raise
    if pool and not pool._closed:
        in_use = pool.get_size() - pool.get_idle_size()
        max_sz = pool.get_max_size()
        if max_sz > 0 and in_use >= max_sz * 0.8:
            logger.warning("DB pool pressure: %d/%d connections in use (max %d)", in_use, pool.get_size(), max_sz)
    return pool


async def init_db():
    from migrations import run_migrations
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await run_migrations(conn)


async def get_random_active_ad():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, body, url, display_name FROM text_ads WHERE active = TRUE ORDER BY RANDOM() LIMIT 1"
        )
        if row:
            return dict(row)
        return None


async def record_ad_impression(ad_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE text_ads SET impressions = impressions + 1 WHERE id = $1", ad_id)


async def record_ad_click(ad_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE text_ads SET clicks = clicks + 1 WHERE id = $1", ad_id)


async def get_all_text_ads():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM text_ads ORDER BY created_at DESC")
        result = []
        for row in rows:
            d = dict(row)
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result


async def create_text_ad(title: str, body: str, url: str, display_name: str = ""):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO text_ads (title, body, url, display_name) VALUES ($1, $2, $3, $4) RETURNING id",
            title, body, url, display_name
        )
        return row["id"]


async def update_text_ad(ad_id: int, **kwargs):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        for key, value in kwargs.items():
            if key in ("title", "body", "url", "display_name", "active"):
                await conn.execute(f"UPDATE text_ads SET {key} = $1 WHERE id = $2", value, ad_id)


async def delete_text_ad(ad_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM text_ads WHERE id = $1", ad_id)


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
                """INSERT INTO leaderboard (discord_id, discord_name, wins, games_played, platform, updated_at)
                   VALUES ($1, $2, 1, 1, 'discord', NOW())
                   ON CONFLICT (discord_id, platform) DO UPDATE
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
                    """INSERT INTO leaderboard (discord_id, discord_name, wins, games_played, platform, updated_at)
                       VALUES ($1, $2, 0, 1, 'discord', NOW())
                       ON CONFLICT (discord_id, platform) DO UPDATE
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
    return 80


def get_host_share_percent(monthly_count: int) -> int:
    return 20


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


async def create_tournament(name: str, guild_id: str, channel_id: str, creator_id: str, creator_name: str, rounds_total: int = 5, max_players: int = 20, platform_fee_percent: int = 80):
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


async def cancel_tournament(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournaments SET status = 'cancelled', completed_at = NOW() WHERE id = $1",
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
                                  host_stripe_account_id: str, max_players: int = 20, platform_fee_percent: int = 80):
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


async def mark_entry_refunded(tournament_id: int, discord_user_id: str):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournament_entries SET status = 'refunded', refunded_at = NOW() WHERE tournament_id = $1 AND discord_user_id = $2",
            tournament_id, discord_user_id
        )


async def mark_tournament_refunded(tournament_id: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tournaments SET prize_pool_cents = 0, payout_status = 'refunded', status = 'cancelled' WHERE id = $1",
            tournament_id
        )


async def create_web_tournament(name: str, creator_id: str, creator_name: str,
                                rounds_total: int, team_count: int, team_size: int,
                                join_mode: str, game_mode: str, entry_fee_cents: int,
                                prize_distribution: str, host_stripe_account_id: str,
                                sponsor_enabled: bool, sponsor_slots: int,
                                sponsor_price_cents: int, guild_id: str = '', channel_id: str = '',
                                platform_fee_percent: int = 80):
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


FREE_DAILY_GAME_LIMIT = 3

PLAN_LIMITS = {
    "overlord": {"tournaments_per_month": None, "max_players": None, "daily_game_limit": None, "custom_decks": True, "label": "Overlord"},
    "pro": {"tournaments_per_month": None, "max_players": None, "daily_game_limit": None, "custom_decks": True, "label": "Overlord (Legacy Pro)"},
    "schemer": {"tournaments_per_month": 1, "max_players": 50, "daily_game_limit": None, "custom_decks": True, "label": "Schemer"},
}

MINION_LIMITS = {"tournaments_per_month": 0, "max_players": 0, "daily_game_limit": FREE_DAILY_GAME_LIMIT, "custom_decks": False, "label": "Minion"}


async def get_plan_limits(guild_id: str) -> dict:
    server = await get_premium_server(guild_id)
    plan = server["plan"] if server and server.get("active") else "minion"
    return PLAN_LIMITS.get(plan, MINION_LIMITS).copy()


async def get_server_plan(guild_id: str) -> str:
    server = await get_premium_server(guild_id)
    if server and server.get("active"):
        return server["plan"]
    return "minion"


async def is_premium_server(guild_id: str) -> bool:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT active FROM premium_servers WHERE guild_id = $1 AND active = TRUE",
            guild_id
        )
        return row is not None


async def is_user_premium(discord_id: str) -> bool:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM premium_servers WHERE activated_by = $1 AND active = TRUE LIMIT 1",
            discord_id
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


async def init_bot_stats_table():
    pass


async def record_bot_guild_count(count: int):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bot_stats (guild_count) VALUES ($1)", count
        )


async def get_latest_bot_guild_count():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT guild_count, recorded_at FROM bot_stats ORDER BY recorded_at DESC LIMIT 1"
        )
        return dict(row) if row else {"guild_count": 0, "recorded_at": None}


async def get_admin_analytics(days: int = None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        date_filter = ""
        if days and isinstance(days, int) and days > 0:
            days = min(days, 3650)
            date_filter = f"AND created_at >= NOW() - INTERVAL '{days} days'"
            date_filter_updated = f"AND updated_at >= NOW() - INTERVAL '{days} days'"
        else:
            date_filter_updated = ""

        total_users = await conn.fetchval("SELECT COUNT(*) FROM leaderboard")
        total_games = await conn.fetchval("SELECT COUNT(*) FROM game_rounds")
        total_wins = await conn.fetchval("SELECT COALESCE(SUM(wins), 0) FROM leaderboard")

        filtered_users = await conn.fetchval(f"SELECT COUNT(*) FROM leaderboard WHERE 1=1 {date_filter_updated}")
        filtered_games = await conn.fetchval(f"SELECT COUNT(*) FROM game_rounds WHERE 1=1 {date_filter}")

        games_over_time = await conn.fetch(f"""
            SELECT DATE(created_at) as day, COUNT(*) as count
            FROM game_rounds WHERE 1=1 {date_filter}
            GROUP BY DATE(created_at) ORDER BY day
        """)

        users_over_time = await conn.fetch(f"""
            SELECT DATE(updated_at) as day, COUNT(*) as count
            FROM leaderboard WHERE 1=1 {date_filter_updated}
            GROUP BY DATE(updated_at) ORDER BY day
        """)

        total_tournaments = await conn.fetchval("SELECT COUNT(*) FROM tournaments")
        tournament_by_status = await conn.fetch(
            "SELECT status, COUNT(*) as count FROM tournaments GROUP BY status"
        )
        filtered_tournaments = await conn.fetchval(f"SELECT COUNT(*) FROM tournaments WHERE 1=1 {date_filter}")
        tournaments_over_time = await conn.fetch(f"""
            SELECT DATE(created_at) as day, COUNT(*) as count
            FROM tournaments WHERE 1=1 {date_filter}
            GROUP BY DATE(created_at) ORDER BY day
        """)

        premium_stats = await conn.fetchrow(
            "SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE active = TRUE) as active FROM premium_servers"
        )
        premium_plan_breakdown = await conn.fetch(
            "SELECT plan, COUNT(*) as count FROM premium_servers WHERE active = TRUE GROUP BY plan"
        )
        premium_over_time = await conn.fetch(f"""
            SELECT DATE(created_at) as day, COUNT(*) as count
            FROM premium_servers WHERE 1=1 {date_filter}
            GROUP BY DATE(created_at) ORDER BY day
        """)

        store_total_purchases = await conn.fetchval("SELECT COUNT(*) FROM store_purchases WHERE status = 'completed'")
        filtered_purchases = await conn.fetchval(f"SELECT COUNT(*) FROM store_purchases WHERE status = 'completed' {date_filter}")
        top_decks = await conn.fetch("""
            SELECT sl.name, COUNT(sp.id) as sales
            FROM store_purchases sp
            JOIN store_listings sl ON sp.listing_id = sl.id
            WHERE sp.status = 'completed'
            GROUP BY sl.name ORDER BY sales DESC LIMIT 10
        """)
        store_revenue_cents = await conn.fetchval("""
            SELECT COALESCE(SUM(sl.price_cents), 0)
            FROM store_purchases sp
            JOIN store_listings sl ON sp.listing_id = sl.id
            WHERE sp.status = 'completed'
        """)
        store_sales_over_time = await conn.fetch(f"""
            SELECT DATE(sp.created_at) as day, COUNT(*) as count, COALESCE(SUM(sl.price_cents), 0) as revenue_cents
            FROM store_purchases sp
            JOIN store_listings sl ON sp.listing_id = sl.id
            WHERE sp.status = 'completed' {date_filter.replace('created_at', 'sp.created_at') if date_filter else ''}
            GROUP BY DATE(sp.created_at) ORDER BY day
        """)

        tournament_entry_revenue = await conn.fetchval(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM tournament_entries WHERE status = 'completed'"
        )
        tournament_platform_fees = await conn.fetchval("""
            SELECT COALESCE(SUM(
                te.amount_cents * t.platform_fee_percent / 100
            ), 0)
            FROM tournament_entries te
            JOIN tournaments t ON te.tournament_id = t.id
            WHERE te.status = 'completed'
        """)
        tournament_prize_payouts = await conn.fetchval("""
            SELECT COALESCE(SUM(prize_pool_cents), 0) FROM tournaments WHERE payout_status = 'paid'
        """)

        sponsor_revenue = await conn.fetchval(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM tournament_sponsors WHERE status = 'paid'"
        )
        sponsor_count = await conn.fetchval(
            "SELECT COUNT(*) FROM tournament_sponsors WHERE status = 'paid'"
        )

        connected_accounts_total = await conn.fetchval("SELECT COUNT(*) FROM connected_accounts")
        connected_accounts_onboarded = await conn.fetchval(
            "SELECT COUNT(*) FROM connected_accounts WHERE onboarding_complete = TRUE"
        )

        top_players = await conn.fetch(
            "SELECT discord_name, wins, games_played FROM leaderboard ORDER BY wins DESC LIMIT 10"
        )

        guild_stats = await conn.fetchrow(
            "SELECT guild_count, recorded_at FROM bot_stats ORDER BY recorded_at DESC LIMIT 1"
        )
        guild_history = await conn.fetch(f"""
            SELECT DATE(recorded_at) as day, MAX(guild_count) as count
            FROM bot_stats WHERE 1=1 {date_filter.replace('created_at', 'recorded_at') if date_filter else ''}
            GROUP BY DATE(recorded_at) ORDER BY day
        """)

        total_decks = await conn.fetchval("SELECT COUNT(*) FROM custom_decks")
        total_cards = await conn.fetchval("SELECT COUNT(*) FROM custom_cards")

        return {
            "users": {
                "total": total_users,
                "filtered": filtered_users,
                "over_time": [{"day": str(r["day"]), "count": r["count"]} for r in users_over_time],
            },
            "games": {
                "total": total_games,
                "total_wins": total_wins,
                "filtered": filtered_games,
                "over_time": [{"day": str(r["day"]), "count": r["count"]} for r in games_over_time],
            },
            "tournaments": {
                "total": total_tournaments,
                "filtered": filtered_tournaments,
                "by_status": {r["status"]: r["count"] for r in tournament_by_status},
                "over_time": [{"day": str(r["day"]), "count": r["count"]} for r in tournaments_over_time],
            },
            "premium": {
                "total": premium_stats["total"] if premium_stats else 0,
                "active": premium_stats["active"] if premium_stats else 0,
                "by_plan": {r["plan"]: r["count"] for r in premium_plan_breakdown},
                "over_time": [{"day": str(r["day"]), "count": r["count"]} for r in premium_over_time],
            },
            "store": {
                "total_purchases": store_total_purchases,
                "filtered_purchases": filtered_purchases,
                "revenue_cents": store_revenue_cents,
                "top_decks": [{"name": r["name"], "sales": r["sales"]} for r in top_decks],
                "over_time": [{"day": str(r["day"]), "count": r["count"], "revenue_cents": r["revenue_cents"]} for r in store_sales_over_time],
            },
            "revenue": {
                "tournament_entry_total_cents": tournament_entry_revenue,
                "tournament_platform_fees_cents": tournament_platform_fees,
                "tournament_prize_payouts_cents": tournament_prize_payouts,
                "store_revenue_cents": store_revenue_cents,
                "sponsor_revenue_cents": sponsor_revenue,
                "sponsor_count": sponsor_count,
            },
            "connected_accounts": {
                "total": connected_accounts_total,
                "onboarded": connected_accounts_onboarded,
            },
            "top_players": [{"name": r["discord_name"], "wins": r["wins"], "games_played": r["games_played"]} for r in top_players],
            "guilds": {
                "current": guild_stats["guild_count"] if guild_stats else 0,
                "last_updated": str(guild_stats["recorded_at"]) if guild_stats and guild_stats["recorded_at"] else None,
                "history": [{"day": str(r["day"]), "count": r["count"]} for r in guild_history],
            },
            "content": {
                "total_decks": total_decks,
                "total_cards": total_cards,
            },
        }


STORE_DECK_META = {
    "420": ("420 Blaze It", "Light up the party with 20 weed-themed answer cards"),
    "addictions": ("Addictions", "20 cards about all the things you just can't quit"),
    "adulting": ("Adulting", "20 cards for when you're pretending to be a grown-up"),
    "adulting2": ("Adulting 2", "28 more cards for when adulting gets even harder"),
    "animals": ("Animals", "81 wild answer cards from the animal kingdom"),
    "babes": ("Babes", "20 cards featuring the hottest names in Hollywood"),
    "british": ("British AF", "20 cards from across the pond, innit"),
    "cats": ("Cats", "34 purrfectly twisted feline answer cards"),
    "college1": ("College 1", "30 cards about the best/worst years of your life"),
    "college2": ("College 2", "30 more cards of college chaos"),
    "college3": ("College 3", "30 more cards of campus mayhem"),
    "college4": ("College 4", "40 cards to finish your college education"),
    "crime1": ("Crime 1", "31 cards from the wrong side of the law"),
    "crime2": ("Crime 2", "32 more cards of criminal mischief"),
    "cuties": ("Cuties", "4 adorable cards to lighten up the darkness"),
    "dads": ("Dads", "34 cards about fatherhood, for better or worse"),
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
    "paranormal2": ("Paranormal", "10 spooky answer cards featuring ghosts, aliens, and things that go bump in the night"),
    "texas": ("Texas", "30 big ol' Texas-sized answer cards — everything's bigger and filthier in the Lone Star State"),
    "outerspace2": ("Outer Space 2", "30 more cards from the final frontier — zero gravity, maximum filth"),
    "monsters2": ("Monsters! 2", "30 more monstrous answer cards for the creature-feature crowd"),
    "alliterativeanimals": ("Alliterative Animals", "30 cards featuring animals doing alliteratively awful things"),
    "abracadabra": ("Abracadabra", "30 cards about magic, sorcery, and pulling things out of surprising places"),
    "wildwest": ("Wild West", "30 cards from the Old West, where the cowboys are rowdy and the saloons are rowdier"),
    "nerdorama": ("Nerd-O-Rama", "30 cards for the pasty, pocket-protected, Cheeto-fingered nerd in all of us"),
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

        batch2_file = os.path.join(os.path.dirname(__file__),
            "attached_assets", "store_decks_batch2.tsv")
        display_name_to_key = {v[0]: k for k, v in STORE_DECK_META.items()}
        if os.path.exists(batch2_file):
            with open(batch2_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        card_text = parts[0].strip()
                        deck_display = parts[1].strip()
                        deck_key = display_name_to_key.get(deck_display, deck_display)
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


async def save_game_session(session_key, session_type, game_data):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO active_game_sessions (session_key, session_type, game_data, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW())
            ON CONFLICT (session_key) DO UPDATE
            SET game_data = $3::jsonb, updated_at = NOW()
        """, str(session_key), session_type, json.dumps(game_data))


async def load_game_session(session_key):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT game_data FROM active_game_sessions WHERE session_key = $1",
            str(session_key)
        )
        if row:
            return json.loads(row["game_data"])
        return None


async def delete_game_session(session_key):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM active_game_sessions WHERE session_key = $1",
            str(session_key)
        )


async def load_all_game_sessions(session_type=None):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if session_type:
            rows = await conn.fetch(
                "SELECT session_key, game_data FROM active_game_sessions WHERE session_type = $1",
                session_type
            )
        else:
            rows = await conn.fetch("SELECT session_key, session_type, game_data FROM active_game_sessions")
        result = {}
        for row in rows:
            result[row["session_key"]] = json.loads(row["game_data"])
        return result
