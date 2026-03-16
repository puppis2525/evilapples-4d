import os
import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

import re
import sys

from db import (
    init_db, get_leaderboard, get_recent_rounds, record_bot_group_count,
    get_tg_stats, get_tg_store_listings, get_tg_store_listing,
    get_store_listing, has_purchased
)
from bot import build_application, set_commands

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("tg_server")

PORT = int(os.environ.get("PORT", 5001))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_CODE", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "EvilApplesBot")
IS_PRODUCTION = os.environ.get("REPLIT_DEPLOYMENT") == "1"

tg_app = None


def _webhook_url() -> str:
    explicit = os.environ.get("TELEGRAM_WEBHOOK_URL", "")
    if explicit:
        return explicit
    domains = os.environ.get("REPLIT_DOMAINS", "")
    host = domains.split(",")[0].strip() if domains else "evilapples4tg.com"
    return f"https://{host}/telegram/webhook"


templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_app
    logger.info("Starting Evil Apples Telegram server...")

    await init_db()
    logger.info("Database initialized")

    if TELEGRAM_BOT_TOKEN:
        tg_app = build_application()
        await tg_app.initialize()
        await set_commands(tg_app)
        await tg_app.start()

        if IS_PRODUCTION:
            url = _webhook_url()
            await tg_app.bot.set_webhook(url=url, allowed_updates=["message", "callback_query"])
            logger.info(f"Telegram bot started (webhook: {url})")
        else:
            await tg_app.updater.start_polling(allowed_updates=["message", "callback_query"])
            logger.info("Telegram bot started (polling)")
    else:
        logger.error(
            "TELEGRAM_BOT_TOKEN is not set! The Telegram bot will NOT start. "
            "Set this environment variable with your bot token from @BotFather to enable the bot."
        )

    yield

    if tg_app:
        if IS_PRODUCTION:
            await tg_app.bot.delete_webhook()
            logger.info("Telegram webhook removed")
        else:
            await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        logger.info("Telegram bot stopped")


app = FastAPI(title="Evil Apples for Telegram", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def _ctx(request: Request, active_page: str = "", **extra):
    ctx = {"request": request, "bot_username": BOT_USERNAME, "active_page": active_page}
    ctx.update(extra)
    return ctx


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    try:
        leaders = await get_leaderboard(10)
        rounds = await get_recent_rounds(5)
    except Exception as e:
        logger.error(f"Home page data error: {e}")
        leaders = []
        rounds = []

    return templates.TemplateResponse(
        "index.html",
        _ctx(request, "home", leaders=leaders, rounds=rounds)
    )


@app.get("/howtoplay", response_class=HTMLResponse)
async def howtoplay(request: Request):
    return templates.TemplateResponse("howtoplay.html", _ctx(request, "howtoplay"))


@app.get("/store", response_class=HTMLResponse)
async def store(request: Request):
    try:
        listings = await get_tg_store_listings()
    except Exception as e:
        logger.error(f"Store page data error: {e}")
        listings = []

    return templates.TemplateResponse(
        "store.html",
        _ctx(request, "store", listings=listings)
    )


@app.get("/store/success", response_class=HTMLResponse)
async def store_success(request: Request):
    return templates.TemplateResponse("store_success.html", _ctx(request, "store"))


@app.get("/api/store/listings")
async def api_store_listings():
    try:
        listings = await get_tg_store_listings()
        return JSONResponse(content=listings)
    except Exception as e:
        logger.error(f"Store listings API error: {e}")
        return JSONResponse(content={"error": "Failed to load listings"}, status_code=500)


@app.post("/api/store/checkout")
async def store_checkout(request: Request):
    try:
        body = await request.json()
        listing_ids = body.get("listing_ids", [])
        listing_id = body.get("listing_id")
        user_id = body.get("discord_user_id", "").strip()

        if listing_id and not listing_ids:
            listing_ids = [listing_id]

        if not listing_ids or not user_id:
            return JSONResponse(content={"error": "Missing listing_ids or user_id"}, status_code=400)

        if not re.match(r'^tg_\d+$', user_id):
            return JSONResponse(content={"error": "Invalid Telegram user ID"}, status_code=400)

        listings = []
        for lid in listing_ids:
            listing = await get_store_listing(int(lid))
            if not listing or not listing.get("active"):
                return JSONResponse(content={"error": f"Listing {lid} not found"}, status_code=404)
            already = await has_purchased(user_id, int(lid))
            if already:
                return JSONResponse(content={"error": f"You already own '{listing['name']}'!"}, status_code=400)
            listings.append(listing)

        from stripe_client import get_stripe_client
        stripe_mod = await get_stripe_client()

        host = request.headers.get("host", "localhost:5001")
        protocol = "https" if "replit" in host or "evilapples4tg" in host else "http"
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
            cancel_url=f"{base_url}/store?platform=telegram&user_id={user_id}",
            metadata={
                "type": "deck_purchase",
                "listing_ids": ",".join(str(l["id"]) for l in listings),
                "listing_id": str(listings[0]["id"]),
                "discord_user_id": user_id,
                "user_id": user_id,
            },
        )

        return JSONResponse(content={"url": session.url})
    except Exception as e:
        logger.error(f"Store checkout error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/premium", response_class=HTMLResponse)
async def premium(request: Request):
    return templates.TemplateResponse("premium.html", _ctx(request, "premium"))


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request):
    try:
        leaders = await get_leaderboard(50)
    except Exception as e:
        logger.error(f"Leaderboard page data error: {e}")
        leaders = []

    return templates.TemplateResponse(
        "leaderboard.html",
        _ctx(request, "leaderboard", leaders=leaders)
    )


@app.get("/api/stats")
async def api_stats():
    try:
        stats = await get_tg_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        logger.error(f"Stats API error: {e}")
        return JSONResponse(content={"error": "Failed to load stats"}, status_code=500)


@app.get("/api/leaderboard")
async def api_leaderboard():
    try:
        rows = await get_leaderboard(20)
        return JSONResponse(content=rows)
    except Exception as e:
        logger.error(f"Leaderboard API error: {e}")
        return JSONResponse(content={"error": "Failed to load leaderboard"}, status_code=500)


@app.get("/api/rounds")
async def api_rounds():
    try:
        rows = await get_recent_rounds(10)
        return JSONResponse(content=[
            {**r, "created_at": r["created_at"].isoformat() if r.get("created_at") else None}
            for r in rows
        ])
    except Exception as e:
        logger.error(f"Rounds API error: {e}")
        return JSONResponse(content={"error": "Failed to load rounds"}, status_code=500)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if tg_app is None:
        return JSONResponse(content={"error": "Bot not initialized"}, status_code=503)
    import json
    from telegram import Update
    body = await request.body()
    update = Update.de_json(json.loads(body), tg_app.bot)
    await tg_app.process_update(update)
    return JSONResponse(content={"ok": True})


@app.get("/health")
async def health():
    mode = "webhook" if IS_PRODUCTION else "polling"
    return {"status": "ok", "service": "evil-apples-telegram", "bot_mode": mode}


if __name__ == "__main__":
    logger.info(f"Starting Evil Apples Telegram server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
