import os
import sys
import asyncio
import threading
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("server")


class RateLimiter:
    TIERS = {
        "strict": {"requests": 5, "window": 60},
        "moderate": {"requests": 30, "window": 60},
        "relaxed": {"requests": 60, "window": 60},
    }

    ROUTE_TIERS = {
        "/api/store/checkout": "strict",
        "/api/stripe/create-checkout": "strict",
        "/api/stripe/tournament/entry-checkout": "strict",
        "/api/stripe/connect/onboard": "strict",
        "/api/admin/": "moderate",
        "/auth/discord/login": "moderate",
        "/api/generate": "moderate",
    }

    def __init__(self):
        self._hits = defaultdict(list)
        self._last_cleanup = time.time()

    def _cleanup(self):
        now = time.time()
        if now - self._last_cleanup < 30:
            return
        self._last_cleanup = now
        stale = [k for k, v in self._hits.items() if not v or v[-1] < now - 120]
        for k in stale:
            del self._hits[k]

    def _get_tier(self, path: str):
        for prefix, tier in self.ROUTE_TIERS.items():
            if path.startswith(prefix):
                return tier
        if path.startswith("/api/"):
            return "relaxed"
        return None

    def is_allowed(self, client_ip: str, path: str) -> bool:
        tier_name = self._get_tier(path)
        if tier_name is None:
            return True

        tier = self.TIERS[tier_name]
        now = time.time()
        key = f"{client_ip}:{tier_name}"

        self._cleanup()

        timestamps = self._hits[key]
        cutoff = now - tier["window"]
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

        if len(timestamps) >= tier["requests"]:
            return False

        timestamps.append(now)
        return True


rate_limiter = RateLimiter()


RATE_LIMIT_SKIP = {"/static", "/api/stripe/webhook", "/favicon.ico"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(s) for s in RATE_LIMIT_SKIP):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        if not rate_limiter.is_allowed(client_ip, path):
            logger.warning(f"Rate limited: {client_ip} on {path}")
            return JSONResponse(
                status_code=429,
                content={"error": "Too many requests. Please slow down."},
                headers={"Retry-After": "60"}
            )

        return await call_next(request)


def delayed_bot_start():
    time.sleep(5)
    try:
        from bot import run_bot
        logger.info("Starting Discord bot...")
        run_bot()
    except Exception as e:
        logger.error(f"Discord bot failed to start: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up...")

    try:
        from db import init_db, init_deck_tables, init_store_tables, seed_store_decks, init_paid_tournament_tables, init_bot_stats_table
        await init_db()
        await init_deck_tables()
        await init_store_tables()
        await init_paid_tournament_tables()
        await init_bot_stats_table()
        logger.info("Database initialized successfully")
        await seed_store_decks()
    except Exception as e:
        logger.error(f"Database init failed (will retry on first request): {e}")

    is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    if is_production or not os.environ.get("REPLIT_DEV_DOMAIN"):
        bot_thread = threading.Thread(target=delayed_bot_start, daemon=True)
        bot_thread.start()
        logger.info("Bot thread launched (will connect after 5s delay)")
    else:
        logger.info("Skipping bot startup in development (deployed version handles Discord)")

    yield
    logger.info("Application shutting down...")


app = FastAPI(title="Evil Apples 4D", lifespan=lifespan)
app.add_middleware(RateLimitMiddleware)

from stripe_routes import router as stripe_router
from stripe_client import get_stripe_client
from discord_auth import router as auth_router, get_discord_user
app.include_router(stripe_router)
app.include_router(auth_router)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/logo.png", media_type="image/png")


ADSENSE_PUBLISHER_ID = os.environ.get("ADSENSE_PUBLISHER_ID", "")


def ad_context():
    return {"adsense_publisher_id": ADSENSE_PUBLISHER_ID}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, **ad_context()})


@app.get("/howtoplay", response_class=HTMLResponse)
async def howtoplay_page(request: Request):
    return templates.TemplateResponse("howtoplay.html", {"request": request, **ad_context()})


@app.get("/premium", response_class=HTMLResponse)
async def premium_page(request: Request):
    return templates.TemplateResponse("premium.html", {"request": request})


@app.get("/premium/success", response_class=HTMLResponse)
async def premium_success_page(request: Request):
    return templates.TemplateResponse("premium_success.html", {"request": request})


@app.get("/api/leaderboard")
async def api_leaderboard():
    from db import get_leaderboard
    try:
        data = await get_leaderboard(20)
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Leaderboard error: {e}")
        return JSONResponse(content=[], status_code=200)


@app.get("/api/rounds")
async def api_rounds():
    from db import get_recent_rounds
    try:
        data = await get_recent_rounds(20)
        for item in data:
            if "created_at" in item and item["created_at"]:
                item["created_at"] = item["created_at"].isoformat()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Rounds error: {e}")
        return JSONResponse(content=[], status_code=200)


@app.get("/api/generate")
async def api_generate(card_type: str = "white"):
    from api_client import generate_card, is_duplicate
    if card_type not in ("white", "black"):
        return JSONResponse(content={"error": "card_type must be 'white' or 'black'"}, status_code=400)
    card = await generate_card(card_type)
    return JSONResponse(content={"card": card, "is_duplicate": is_duplicate(card)})


@app.get("/tournaments", response_class=HTMLResponse)
async def tournaments_page(request: Request):
    return templates.TemplateResponse("tournaments.html", {"request": request, **ad_context()})


@app.get("/tournaments/revenue", response_class=HTMLResponse)
async def tournament_revenue_page(request: Request):
    return templates.TemplateResponse("tournament_revenue.html", {"request": request})


@app.get("/tournaments/create", response_class=HTMLResponse)
async def tournament_create_page(request: Request):
    return templates.TemplateResponse("tournament_create.html", {"request": request})


@app.get("/tournaments/{tournament_id}", response_class=HTMLResponse)
async def tournament_detail_page(request: Request, tournament_id: int):
    return templates.TemplateResponse("tournament_detail.html", {"request": request, "tournament_id": tournament_id})


@app.get("/api/tournaments")
async def api_tournaments(request: Request):
    status_filter = request.query_params.get("status", "all")
    from db import get_web_tournaments
    try:
        data = await get_web_tournaments(status_filter=status_filter, limit=50)
        for item in data:
            for key in ("created_at", "completed_at"):
                if key in item and item[key]:
                    item[key] = item[key].isoformat()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Tournaments error: {e}")
        return JSONResponse(content=[], status_code=200)


@app.get("/api/tournaments/fee-tier")
async def api_tournament_fee_tier(request: Request, guild_id: str = ""):
    from db import get_server_monthly_tournament_count, get_tiered_fee_percent
    monthly_count = await get_server_monthly_tournament_count(guild_id) if guild_id else 0
    fee_percent = get_tiered_fee_percent(monthly_count)
    return JSONResponse(content={
        "monthly_count": monthly_count,
        "fee_percent": fee_percent,
        "tiers": [
            {"range": "1-2", "fee": 10},
            {"range": "3-5", "fee": 15},
            {"range": "6-10", "fee": 20},
            {"range": "11+", "fee": 25},
        ]
    })


@app.get("/api/tournaments/{tournament_id}")
async def api_tournament_detail(tournament_id: int):
    from db import get_full_tournament_detail
    try:
        data = await get_full_tournament_detail(tournament_id)
        if not data:
            return JSONResponse(content={"error": "Tournament not found"}, status_code=404)
        t = data["tournament"]
        for key in ("created_at", "completed_at"):
            if key in t and t[key]:
                t[key] = t[key].isoformat()
        for r in data["rounds"]:
            if "completed_at" in r and r["completed_at"]:
                r["completed_at"] = r["completed_at"].isoformat()
        for p in data["players"]:
            if "joined_at" in p and p["joined_at"]:
                p["joined_at"] = p["joined_at"].isoformat()
        for team in data["teams"]:
            for m in team.get("members", []):
                if "joined_at" in m and m["joined_at"]:
                    m["joined_at"] = m["joined_at"].isoformat()
        for s in data["sponsors"]:
            if "created_at" in s and s["created_at"]:
                s["created_at"] = s["created_at"].isoformat()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Tournament detail error: {e}")
        return JSONResponse(content={"error": "Server error"}, status_code=500)


@app.post("/api/tournaments/create")
async def api_create_tournament(request: Request):
    from discord_auth import get_discord_user
    from db import create_web_tournament, get_connected_account, get_server_monthly_tournament_count, get_tiered_fee_percent
    import json

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"error": "Login required"}, status_code=401)

    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name or len(name) < 3:
            return JSONResponse(content={"error": "Tournament name must be at least 3 characters"}, status_code=400)

        rounds_total = int(body.get("rounds", 5))
        if rounds_total < 1 or rounds_total > 50:
            return JSONResponse(content={"error": "Rounds must be between 1 and 50"}, status_code=400)

        team_count = int(body.get("team_count", 2))
        team_size = int(body.get("team_size", 5))
        if team_count < 2 or team_count > 20:
            return JSONResponse(content={"error": "Team count must be between 2 and 20"}, status_code=400)
        if team_size < 1 or team_size > 10:
            return JSONResponse(content={"error": "Team size must be between 1 and 10"}, status_code=400)

        join_mode = body.get("join_mode", "open")
        if join_mode not in ("open", "closed"):
            join_mode = "open"

        game_mode = body.get("game_mode", "friend")
        if game_mode not in ("friend", "swap", "wildcard"):
            game_mode = "friend"

        entry_fee_dollars = float(body.get("entry_fee", 0))
        entry_fee_cents = int(entry_fee_dollars * 100)
        if entry_fee_cents < 0:
            return JSONResponse(content={"error": "Entry fee cannot be negative"}, status_code=400)

        prize_dist = body.get("prize_distribution", [])
        prize_distribution_str = json.dumps(prize_dist)

        host_stripe_account_id = ""
        if entry_fee_cents > 0:
            account = await get_connected_account(user["discord_id"])
            if not account or not account.get("onboarding_complete"):
                return JSONResponse(content={"error": "Connect your Stripe account first to create paid tournaments"}, status_code=400)
            host_stripe_account_id = account["stripe_account_id"]

        sponsor_enabled = bool(body.get("sponsor_enabled", False))
        sponsor_slots = int(body.get("sponsor_slots", 0))
        sponsor_price_cents = int(float(body.get("sponsor_price", 0)) * 100)

        guild_id = body.get("guild_id", "")
        channel_id = body.get("channel_id", "")

        monthly_count = await get_server_monthly_tournament_count(guild_id) if guild_id else 0
        fee_percent = get_tiered_fee_percent(monthly_count)

        tournament_id = await create_web_tournament(
            name=name, creator_id=user["discord_id"], creator_name=user.get("global_name", user.get("username", "Unknown")),
            rounds_total=rounds_total, team_count=team_count, team_size=team_size,
            join_mode=join_mode, game_mode=game_mode, entry_fee_cents=entry_fee_cents,
            prize_distribution=prize_distribution_str, host_stripe_account_id=host_stripe_account_id,
            sponsor_enabled=sponsor_enabled, sponsor_slots=sponsor_slots,
            sponsor_price_cents=sponsor_price_cents, guild_id=guild_id, channel_id=channel_id,
            platform_fee_percent=fee_percent,
        )

        return JSONResponse(content={"success": True, "tournament_id": tournament_id, "fee_percent": fee_percent})
    except Exception as e:
        logger.error(f"Create tournament error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/tournaments/{tournament_id}/join")
async def api_join_tournament(tournament_id: int, request: Request):
    from discord_auth import get_discord_user
    from db import get_db_pool, join_tournament_team, get_tournament_player_count

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"error": "Login required"}, status_code=401)

    try:
        body = await request.json()
        team_id = int(body.get("team_id", 0))
        if not team_id:
            return JSONResponse(content={"error": "Select a team to join"}, status_code=400)

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tournament = await conn.fetchrow("SELECT * FROM tournaments WHERE id = $1", tournament_id)

        if not tournament:
            return JSONResponse(content={"error": "Tournament not found"}, status_code=404)
        if tournament["status"] != "registration":
            return JSONResponse(content={"error": "Tournament is not accepting registrations"}, status_code=400)

        player_count = await get_tournament_player_count(tournament_id)
        max_players = tournament.get("max_players", 0)
        if max_players and player_count >= max_players:
            return JSONResponse(content={"error": "Tournament is full"}, status_code=400)

        join_mode = tournament.get("join_mode", "open")
        if join_mode == "closed":
            from db import get_team_members
            pool2 = await get_db_pool()
            async with pool2.acquire() as conn2:
                team_row = await conn2.fetchrow(
                    "SELECT captain_discord_id FROM tournament_teams WHERE id = $1 AND tournament_id = $2",
                    team_id, tournament_id
                )
            if not team_row:
                return JSONResponse(content={"error": "Team not found"}, status_code=404)
            if team_row["captain_discord_id"] and team_row["captain_discord_id"] != user["discord_id"]:
                return JSONResponse(content={"error": "This team is invite-only. Only the captain can add members."}, status_code=403)

        team_size = tournament.get("team_size", 0)
        if team_size:
            from db import get_team_members
            members = await get_team_members(team_id)
            if len(members) >= team_size:
                return JSONResponse(content={"error": "This team is full"}, status_code=400)

        entry_fee = tournament.get("entry_fee_cents", 0) or 0
        if entry_fee > 0:
            from db import get_tournament_entry
            existing = await get_tournament_entry(tournament_id, user["discord_id"])
            if not existing or existing["status"] != "paid":
                stripe_mod = await get_stripe_client()
                host = request.headers.get("host", "localhost:5000")
                protocol = "https" if "replit" in host or "evilapples4d" in host else "http"
                base_url = f"{protocol}://{host}"

                fee_percent = tournament.get("platform_fee_percent", 10)
                application_fee = int(entry_fee * fee_percent / 100)
                host_account = tournament.get("host_stripe_account_id", "")

                checkout_params = {
                    "payment_method_types": ["card"],
                    "line_items": [{
                        "price_data": {
                            "currency": "usd",
                            "product_data": {
                                "name": f"Tournament Entry: {tournament['name']}",
                                "description": f"Entry fee for {tournament['name']} - Team assignment pending",
                            },
                            "unit_amount": entry_fee,
                        },
                        "quantity": 1,
                    }],
                    "mode": "payment",
                    "success_url": f"{base_url}/tournaments/{tournament_id}?paid=1",
                    "cancel_url": f"{base_url}/tournaments/{tournament_id}?cancelled=1",
                    "metadata": {
                        "type": "tournament_entry",
                        "tournament_id": str(tournament_id),
                        "discord_user_id": user["discord_id"],
                        "entry_fee_cents": str(entry_fee),
                        "team_id": str(team_id),
                    },
                }

                if host_account:
                    checkout_params["payment_intent_data"] = {
                        "application_fee_amount": application_fee,
                        "transfer_data": {"destination": host_account},
                    }

                session = stripe_mod.checkout.Session.create(**checkout_params)
                return JSONResponse(content={"payment_required": True, "url": session.url})

        discord_name = user.get("global_name", user.get("username", "Unknown"))
        await join_tournament_team(tournament_id, team_id, user["discord_id"], discord_name)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Join tournament error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/tournaments/{tournament_id}/teams")
async def api_claim_team(tournament_id: int, request: Request):
    from discord_auth import get_discord_user
    from db import claim_team

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"error": "Login required"}, status_code=401)

    try:
        body = await request.json()
        team_id = int(body.get("team_id", 0))
        team_name = body.get("team_name", "").strip()

        if not team_id:
            return JSONResponse(content={"error": "Select a team"}, status_code=400)

        discord_name = user.get("global_name", user.get("username", "Unknown"))
        success = await claim_team(tournament_id, team_id, user["discord_id"], discord_name, team_name)
        if not success:
            return JSONResponse(content={"error": "Team already claimed by another captain"}, status_code=400)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Claim team error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.put("/api/tournaments/{tournament_id}/teams/{team_id}")
async def api_update_team(tournament_id: int, team_id: int, request: Request):
    from discord_auth import get_discord_user
    from db import update_tournament_team, move_player_to_team, get_db_pool

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"error": "Login required"}, status_code=401)

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tournament = await conn.fetchrow("SELECT creator_id FROM tournaments WHERE id = $1", tournament_id)
        if not tournament or tournament["creator_id"] != user["discord_id"]:
            return JSONResponse(content={"error": "Only the host can manage teams"}, status_code=403)

        body = await request.json()
        if "name" in body:
            await update_tournament_team(tournament_id, team_id, name=body["name"])
        if "move_player" in body:
            await move_player_to_team(tournament_id, body["move_player"], team_id)

        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Update team error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/tournaments/{tournament_id}/close-registration")
async def api_close_registration(tournament_id: int, request: Request):
    from discord_auth import get_discord_user
    from db import close_tournament_registration, get_db_pool

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"error": "Login required"}, status_code=401)

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tournament = await conn.fetchrow("SELECT creator_id FROM tournaments WHERE id = $1", tournament_id)
        if not tournament or tournament["creator_id"] != user["discord_id"]:
            return JSONResponse(content={"error": "Only the host can close registration"}, status_code=403)

        await close_tournament_registration(tournament_id)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Close registration error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/tournaments/{tournament_id}/sponsor")
async def api_sponsor_tournament(tournament_id: int, request: Request):
    from discord_auth import get_discord_user
    from db import get_db_pool, get_tournament_sponsor_count, create_tournament_sponsor

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"error": "Login required"}, status_code=401)

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tournament = await conn.fetchrow("SELECT * FROM tournaments WHERE id = $1", tournament_id)

        if not tournament:
            return JSONResponse(content={"error": "Tournament not found"}, status_code=404)

        if not tournament.get("sponsor_enabled"):
            return JSONResponse(content={"error": "This tournament doesn't accept sponsors"}, status_code=400)

        current_sponsors = await get_tournament_sponsor_count(tournament_id)
        if current_sponsors >= tournament.get("sponsor_slots", 0):
            return JSONResponse(content={"error": "All sponsor slots are taken"}, status_code=400)

        body = await request.json()
        sponsor_name = body.get("sponsor_name", "").strip()
        if not sponsor_name:
            return JSONResponse(content={"error": "Sponsor name is required"}, status_code=400)

        sponsor_price = tournament.get("sponsor_price_cents", 0)
        if sponsor_price > 0:
            stripe_mod = await get_stripe_client()
            host = request.headers.get("host", "localhost:5000")
            protocol = "https" if "replit" in host or "evilapples4d" in host else "http"
            base_url = f"{protocol}://{host}"

            fee_percent = tournament.get("platform_fee_percent", 10)
            application_fee = int(sponsor_price * fee_percent / 100)
            host_account = tournament.get("host_stripe_account_id", "")

            checkout_params = {
                "payment_method_types": ["card"],
                "line_items": [{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"Sponsor: {tournament['name']}",
                            "description": f"Sponsorship for tournament: {tournament['name']}",
                        },
                        "unit_amount": sponsor_price,
                    },
                    "quantity": 1,
                }],
                "mode": "payment",
                "success_url": f"{base_url}/tournaments/{tournament_id}?sponsored=1",
                "cancel_url": f"{base_url}/tournaments/{tournament_id}",
                "metadata": {
                    "type": "tournament_sponsor",
                    "tournament_id": str(tournament_id),
                    "sponsor_name": sponsor_name,
                    "sponsor_discord_id": user["discord_id"],
                    "amount_cents": str(sponsor_price),
                },
            }

            if host_account:
                checkout_params["payment_intent_data"] = {
                    "application_fee_amount": application_fee,
                    "transfer_data": {"destination": host_account},
                }

            session = stripe_mod.checkout.Session.create(**checkout_params)
            return JSONResponse(content={"payment_required": True, "url": session.url})

        sponsor_id = await create_tournament_sponsor(tournament_id, sponsor_name, 0)
        return JSONResponse(content={"success": True, "sponsor_id": sponsor_id})
    except Exception as e:
        logger.error(f"Sponsor tournament error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/tournaments/{tournament_id}/remove-player")
async def api_remove_player(tournament_id: int, request: Request):
    from discord_auth import get_discord_user
    from db import remove_tournament_player, get_db_pool

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"error": "Login required"}, status_code=401)

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tournament = await conn.fetchrow("SELECT creator_id FROM tournaments WHERE id = $1", tournament_id)
        if not tournament or tournament["creator_id"] != user["discord_id"]:
            return JSONResponse(content={"error": "Only the host can remove players"}, status_code=403)

        body = await request.json()
        discord_id = body.get("discord_id", "")
        if not discord_id:
            return JSONResponse(content={"error": "Missing player ID"}, status_code=400)

        await remove_tournament_player(tournament_id, discord_id)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Remove player error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/rounds/{slug}")
async def api_round_detail(slug: str):
    from db import get_round_by_slug
    try:
        round_data = await get_round_by_slug(slug)
        if not round_data:
            return JSONResponse(content={"error": "Round not found"}, status_code=404)
        if "created_at" in round_data and round_data["created_at"]:
            round_data["created_at"] = round_data["created_at"].isoformat()
        return JSONResponse(content=round_data)
    except Exception as e:
        logger.error(f"Round detail error: {e}")
        return JSONResponse(content={"error": "Server error"}, status_code=500)


@app.get("/rounds/{slug}")
async def round_page(request: Request, slug: str):
    from db import get_round_by_slug
    try:
        round_data = await get_round_by_slug(slug)
    except Exception:
        round_data = None
    return templates.TemplateResponse("index.html", {"request": request, "round": round_data})


@app.get("/decks", response_class=HTMLResponse)
async def decks_page(request: Request):
    return templates.TemplateResponse("decks.html", {"request": request})


@app.get("/api/decks")
async def api_decks(request: Request):
    from db import get_all_decks
    try:
        user_id = request.query_params.get("user_id")
        if not user_id:
            return JSONResponse(content=[], status_code=200)
        data = await get_all_decks(discord_user_id=user_id)
        for item in data:
            if "created_at" in item and item["created_at"]:
                item["created_at"] = item["created_at"].isoformat()
            item.pop("discord_user_id", None)
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Decks error: {e}")
        return JSONResponse(content=[], status_code=200)


ADMIN_TOKEN = os.environ.get("SESSION_SECRET", "")


@app.get("/store", response_class=HTMLResponse)
async def store_page(request: Request):
    return templates.TemplateResponse("store.html", {"request": request, **ad_context()})


@app.get("/store/success", response_class=HTMLResponse)
async def store_success_page(request: Request):
    return templates.TemplateResponse("store_success.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/api/admin/analytics")
async def api_admin_analytics(request: Request, days: int = None):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Forbidden"}, status_code=403)
    from db import get_admin_analytics
    try:
        data = await get_admin_analytics(days=days)
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/admin/bot-stats")
async def api_admin_bot_stats(request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Forbidden"}, status_code=403)
    from db import record_bot_guild_count
    try:
        body = await request.json()
        guild_count = body.get("guild_count", 0)
        await record_bot_guild_count(guild_count)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Bot stats error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/store/listings")
async def api_store_listings():
    from db import get_store_listings
    try:
        data = await get_store_listings(active_only=True)
        for item in data:
            if "created_at" in item and item["created_at"]:
                item["created_at"] = item["created_at"].isoformat()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Store listings error: {e}")
        return JSONResponse(content=[], status_code=200)


@app.get("/api/admin/listings")
async def api_admin_listings(request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=403)
    from db import get_store_listings
    try:
        data = await get_store_listings(active_only=False)
        for item in data:
            if "created_at" in item and item["created_at"]:
                item["created_at"] = item["created_at"].isoformat()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Admin listings error: {e}")
        return JSONResponse(content=[], status_code=200)


@app.post("/api/admin/listings")
async def api_admin_create_listing(request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=403)
    from db import create_store_listing
    try:
        body = await request.json()
        listing_id = await create_store_listing(
            name=body.get("name", "Untitled Deck"),
            description=body.get("description", ""),
            image_url=body.get("image_url", ""),
            price_cents=int(body.get("price_cents", 99)),
            bundle_size=int(body.get("bundle_size", 1)),
            deck_ids=body.get("deck_ids", ""),
            featured=bool(body.get("featured", False)),
        )
        return JSONResponse(content={"success": True, "id": listing_id})
    except Exception as e:
        logger.error(f"Create listing error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.put("/api/admin/listings/{listing_id}")
async def api_admin_update_listing(listing_id: int, request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=403)
    from db import update_store_listing
    try:
        body = await request.json()
        kwargs = {}
        if "name" in body:
            kwargs["name"] = body["name"]
        if "description" in body:
            kwargs["description"] = body["description"]
        if "image_url" in body:
            kwargs["image_url"] = body["image_url"]
        if "price_cents" in body:
            kwargs["price_cents"] = int(body["price_cents"])
        if "bundle_size" in body:
            kwargs["bundle_size"] = int(body["bundle_size"])
        if "deck_ids" in body:
            kwargs["deck_ids"] = body["deck_ids"]
        if "active" in body:
            kwargs["active"] = bool(body["active"])
        if "featured" in body:
            kwargs["featured"] = bool(body["featured"])
        if "sort_order" in body:
            kwargs["sort_order"] = int(body["sort_order"])
        await update_store_listing(listing_id, **kwargs)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Update listing error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.delete("/api/admin/listings/{listing_id}")
async def api_admin_delete_listing(listing_id: int, request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=403)
    from db import delete_store_listing
    try:
        await delete_store_listing(listing_id)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Delete listing error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/store/checkout")
async def store_checkout(request: Request):
    try:
        body = await request.json()
        listing_ids = body.get("listing_ids", [])
        listing_id = body.get("listing_id")
        discord_user_id = body.get("discord_user_id", "")

        if listing_id and not listing_ids:
            listing_ids = [listing_id]

        if not listing_ids or not discord_user_id:
            return JSONResponse(content={"error": "Missing listing_ids or discord_user_id"}, status_code=400)

        import re
        if not re.match(r'^\d{17,20}$', discord_user_id):
            return JSONResponse(content={"error": "Invalid Discord User ID"}, status_code=400)

        from db import get_store_listing, has_purchased
        listings = []
        for lid in listing_ids:
            listing = await get_store_listing(int(lid))
            if not listing or not listing.get("active"):
                return JSONResponse(content={"error": f"Listing {lid} not found"}, status_code=404)
            already = await has_purchased(discord_user_id, int(lid))
            if already:
                return JSONResponse(content={"error": f"You already own '{listing['name']}'!"}, status_code=400)
            listings.append(listing)

        stripe_mod = await get_stripe_client()

        host = request.headers.get("host", "localhost:5000")
        protocol = "https" if "replit" in host or "evilapples4d" in host else "http"
        base_url = f"{protocol}://{host}"

        total_cents = sum(l["price_cents"] for l in listings)
        discount = len(listings) >= 3
        if discount:
            total_cents = int(total_cents * 0.85)

        if len(listings) == 1:
            desc = listings[0]["name"]
        else:
            desc = f"{len(listings)} Decks" + (" (15% Bundle Discount)" if discount else "")

        line_items = [{
            "price_data": {
                "currency": "usd",
                "unit_amount": total_cents,
                "product_data": {
                    "name": desc,
                    "description": ", ".join(l["name"] for l in listings)[:500],
                },
            },
            "quantity": 1,
        }]

        session = stripe_mod.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=f"{base_url}/store/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/store",
            metadata={
                "type": "deck_purchase",
                "listing_ids": ",".join(str(l["id"]) for l in listings),
                "listing_id": str(listings[0]["id"]),
                "discord_user_id": discord_user_id,
            },
        )

        return JSONResponse(content={"url": session.url})
    except Exception as e:
        logger.error(f"Store checkout error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/admin/upload-deck")
async def admin_upload_deck(request: Request):
    import csv
    import io
    from db import create_deck, add_cards_to_deck

    try:
        form = await request.form()
        token = form.get("token", "").strip()
        if not ADMIN_TOKEN or token != ADMIN_TOKEN:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=403)

        name = form.get("name", "").strip()
        description = form.get("description", "").strip()
        file = form.get("file")

        if not name:
            return JSONResponse(content={"error": "Deck name is required"}, status_code=400)
        if not file:
            return JSONResponse(content={"error": "CSV file is required"}, status_code=400)

        content = await file.read()
        if len(content) > 2 * 1024 * 1024:
            return JSONResponse(content={"error": "File too large (max 2MB)"}, status_code=400)

        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))

        cards = []
        for row in reader:
            card_type_raw = (row.get("type") or row.get("Type") or row.get("TYPE") or "").strip().lower()
            card_text = (row.get("text") or row.get("Text") or row.get("TEXT") or "").strip()
            if not card_text:
                continue
            if card_type_raw in ("prompt", "black", "question", "b"):
                card_type = "prompt"
            elif card_type_raw in ("answer", "white", "response", "w", "a"):
                card_type = "answer"
            else:
                continue
            cards.append({"type": card_type, "text": card_text})

        if not cards:
            return JSONResponse(content={"error": "No valid cards found"}, status_code=400)

        deck_id = await create_deck(name, description, "Store", discord_user_id=None)
        await add_cards_to_deck(deck_id, cards)

        prompt_count = sum(1 for c in cards if c["type"] == "prompt")
        answer_count = sum(1 for c in cards if c["type"] == "answer")

        return JSONResponse(content={
            "success": True,
            "deck_id": deck_id,
            "name": name,
            "prompt_count": prompt_count,
            "answer_count": answer_count,
            "total": len(cards),
        })
    except Exception as e:
        logger.error(f"Admin deck upload error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/admin/template-decks")
async def admin_template_decks(request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Unauthorized"}, status_code=403)
    try:
        from db import get_db_pool
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, name, description, creator_name, prompt_count, answer_count, created_at
                   FROM custom_decks WHERE discord_user_id IS NULL
                   ORDER BY created_at DESC"""
            )
            data = [dict(r) for r in rows]
        for item in data:
            if "created_at" in item and item["created_at"]:
                item["created_at"] = item["created_at"].isoformat()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Template decks error: {e}")
        return JSONResponse(content=[], status_code=200)


@app.post("/api/admin/upload-image")
async def admin_upload_image(request: Request):
    try:
        form = await request.form()
        token = form.get("token", "").strip()
        if not ADMIN_TOKEN or token != ADMIN_TOKEN:
            return JSONResponse(content={"error": "Unauthorized"}, status_code=403)

        file = form.get("file")
        if not file:
            return JSONResponse(content={"error": "No file uploaded"}, status_code=400)

        import uuid
        ext = file.filename.split(".")[-1].lower() if "." in file.filename else "png"
        if ext not in ("png", "jpg", "jpeg", "webp", "gif"):
            return JSONResponse(content={"error": "Invalid image format"}, status_code=400)

        content = await file.read()
        if len(content) > 5 * 1024 * 1024:
            return JSONResponse(content={"error": "File too large (max 5MB)"}, status_code=400)

        filename = f"deck_{uuid.uuid4().hex[:8]}.{ext}"
        filepath = f"static/{filename}"
        with open(filepath, "wb") as f:
            f.write(content)

        return JSONResponse(content={"success": True, "url": f"/static/{filename}"})
    except Exception as e:
        logger.error(f"Upload image error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/ad/click/{ad_id}")
async def ad_click_redirect(ad_id: int):
    from db import record_ad_click
    from starlette.responses import RedirectResponse
    try:
        await record_ad_click(ad_id)
        from db import get_db_pool
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT url FROM text_ads WHERE id = $1", ad_id)
        if row and row["url"]:
            url = row["url"]
            if url.startswith("https://") or url.startswith("http://"):
                return RedirectResponse(url=url)
        return RedirectResponse(url="/")
    except Exception as e:
        logger.error(f"Ad click error: {e}")
        return RedirectResponse(url="/")


@app.get("/api/admin/ads")
async def api_admin_ads(request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Forbidden"}, status_code=403)
    from db import get_all_text_ads
    try:
        data = await get_all_text_ads()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Admin ads error: {e}")
        return JSONResponse(content=[], status_code=200)


@app.post("/api/admin/ads")
async def api_admin_create_ad(request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Forbidden"}, status_code=403)
    from db import create_text_ad
    try:
        body = await request.json()
        title = body.get("title", "").strip()[:128]
        ad_body = body.get("body", "").strip()[:256]
        url = body.get("url", "").strip()[:2000]
        display_name = body.get("display_name", "").strip()[:128]
        if not title or not ad_body or not url:
            return JSONResponse(content={"error": "Title, body, and URL are required"}, status_code=400)
        if not url.startswith("https://") and not url.startswith("http://"):
            return JSONResponse(content={"error": "URL must start with http:// or https://"}, status_code=400)
        ad_id = await create_text_ad(
            title=title,
            body=ad_body,
            url=url,
            display_name=display_name,
        )
        return JSONResponse(content={"success": True, "id": ad_id})
    except Exception as e:
        logger.error(f"Create ad error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.patch("/api/admin/ads/{ad_id}")
async def api_admin_update_ad(ad_id: int, request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Forbidden"}, status_code=403)
    from db import update_text_ad
    try:
        body = await request.json()
        kwargs = {}
        for key in ("title", "body", "url", "display_name"):
            if key in body:
                kwargs[key] = body[key]
        if "active" in body:
            kwargs["active"] = bool(body["active"])
        await update_text_ad(ad_id, **kwargs)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Update ad error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.delete("/api/admin/ads/{ad_id}")
async def api_admin_delete_ad(ad_id: int, request: Request):
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return JSONResponse(content={"error": "Forbidden"}, status_code=403)
    from db import delete_text_ad
    try:
        await delete_text_ad(ad_id)
        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Delete ad error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/ads/random")
async def api_random_ad():
    from db import get_active_text_ad
    try:
        ad = await get_active_text_ad()
        if ad:
            host = os.environ.get("REPLIT_DOMAINS", "evilapples4d.com").split(",")[0]
            return JSONResponse(content={
                "id": ad["id"],
                "title": ad["title"],
                "body": ad["body"],
                "display_name": ad.get("display_name", ""),
                "click_url": f"https://{host}/ad/click/{ad['id']}"
            })
        return JSONResponse(content={})
    except Exception as e:
        logger.error(f"Random ad error: {e}")
        return JSONResponse(content={})


if __name__ == "__main__":
    logger.info("Starting Evil Apples 4D server on port 5000...")
    uvicorn.run(app, host="0.0.0.0", port=5000)
