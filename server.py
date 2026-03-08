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
        from db import init_db, init_deck_tables, init_store_tables, seed_store_decks
        await init_db()
        await init_deck_tables()
        await init_store_tables()
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


ADMIN_TOKEN = os.environ.get("SESSION_SECRET", "")


@app.get("/store", response_class=HTMLResponse)
async def store_page(request: Request):
    return templates.TemplateResponse("store.html", {"request": request})


@app.get("/store/success", response_class=HTMLResponse)
async def store_success_page(request: Request):
    return templates.TemplateResponse("store_success.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


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


if __name__ == "__main__":
    logger.info("Starting Evil Apples 4D server on port 5000...")
    uvicorn.run(app, host="0.0.0.0", port=5000)
