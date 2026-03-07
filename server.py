import os
import sys
import asyncio
import threading
import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("server")


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
        from db import init_db, init_deck_tables
        await init_db()
        await init_deck_tables()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database init failed (will retry on first request): {e}")

    bot_thread = threading.Thread(target=delayed_bot_start, daemon=True)
    bot_thread.start()
    logger.info("Bot thread launched (will connect after 5s delay)")

    yield
    logger.info("Application shutting down...")


app = FastAPI(title="Evil Apples 4D", lifespan=lifespan)

from stripe_routes import router as stripe_router
app.include_router(stripe_router)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/logo.png", media_type="image/png")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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


@app.get("/api/tournaments")
async def api_tournaments():
    from db import get_recent_tournaments
    try:
        data = await get_recent_tournaments(20)
        for item in data:
            for key in ("created_at", "completed_at"):
                if key in item and item[key]:
                    item[key] = item[key].isoformat()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Tournaments error: {e}")
        return JSONResponse(content=[], status_code=200)


@app.get("/api/tournaments/{tournament_id}")
async def api_tournament_detail(tournament_id: int):
    from db import get_tournament_details
    try:
        data = await get_tournament_details(tournament_id)
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
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"Tournament detail error: {e}")
        return JSONResponse(content={"error": "Server error"}, status_code=500)


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


if __name__ == "__main__":
    logger.info("Starting Evil Apples 4D server on port 5000...")
    uvicorn.run(app, host="0.0.0.0", port=5000)
