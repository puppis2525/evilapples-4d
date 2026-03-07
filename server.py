import os
import asyncio
import threading
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
import uvicorn

from db import init_db, get_recent_rounds, get_leaderboard, get_round_by_slug
from api_client import generate_card, is_duplicate
from bot import run_bot

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    def start_bot():
        try:
            run_bot()
        except Exception as e:
            logger.error(f"Discord bot failed to start: {e}")

    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()

    yield


app = FastAPI(title="Cards Against Humanity - AI Edition", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


@app.get("/favicon.ico")
async def favicon():
    return Response(content=b"", media_type="image/x-icon", status_code=204)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/leaderboard")
async def api_leaderboard():
    data = await get_leaderboard(20)
    return JSONResponse(content=data)


@app.get("/api/rounds")
async def api_rounds():
    data = await get_recent_rounds(20)
    for item in data:
        if "created_at" in item and item["created_at"]:
            item["created_at"] = item["created_at"].isoformat()
    return JSONResponse(content=data)


@app.get("/api/generate")
async def api_generate(card_type: str = "white"):
    if card_type not in ("white", "black"):
        return JSONResponse(content={"error": "card_type must be 'white' or 'black'"}, status_code=400)
    card = await generate_card(card_type)
    return JSONResponse(content={"card": card, "is_duplicate": is_duplicate(card)})


@app.get("/api/rounds/{slug}")
async def api_round_detail(slug: str):
    round_data = await get_round_by_slug(slug)
    if not round_data:
        return JSONResponse(content={"error": "Round not found"}, status_code=404)
    if "created_at" in round_data and round_data["created_at"]:
        round_data["created_at"] = round_data["created_at"].isoformat()
    return JSONResponse(content=round_data)


@app.get("/rounds/{slug}")
async def round_page(request: Request, slug: str):
    round_data = await get_round_by_slug(slug)
    return templates.TemplateResponse("index.html", {"request": request, "round": round_data})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
