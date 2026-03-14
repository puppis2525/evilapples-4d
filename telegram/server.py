import os
import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from db import (
    init_db, get_leaderboard, get_recent_rounds, record_bot_group_count,
    get_tg_stats, get_tg_store_listings
)
from bot import build_application, set_commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("tg_server")

PORT = int(os.environ.get("PORT", 5001))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "EvilApplesTGBot")

tg_app = None

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
        await tg_app.updater.start_polling(allowed_updates=["message", "callback_query"])
        logger.info("Telegram bot started (polling)")
    else:
        logger.error(
            "TELEGRAM_BOT_TOKEN is not set! The Telegram bot will NOT start. "
            "Set this environment variable with your bot token from @BotFather to enable the bot."
        )

    yield

    if tg_app:
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "evil-apples-telegram"}


if __name__ == "__main__":
    logger.info(f"Starting Evil Apples Telegram server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
