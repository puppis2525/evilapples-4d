import os
import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from db import init_db, get_leaderboard, get_recent_rounds, record_bot_group_count
from bot import build_application, set_commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("tg_server")

PORT = int(os.environ.get("PORT", 5001))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

tg_app = None


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
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot not started")

    yield

    if tg_app:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        logger.info("Telegram bot stopped")


app = FastAPI(title="Evil Apples for Telegram", lifespan=lifespan)

try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass


@app.get("/", response_class=HTMLResponse)
async def home():
    try:
        leaders = await get_leaderboard(10)
        rounds = await get_recent_rounds(5)
    except Exception:
        leaders = []
        rounds = []

    leaderboard_rows = ""
    for i, row in enumerate(leaders, 1):
        leaderboard_rows += (
            f"<tr><td>{i}</td><td>{row['discord_name']}</td>"
            f"<td>{row['wins']}</td><td>{row['games_played']}</td></tr>"
        )

    hall_rows = ""
    for row in rounds:
        hall_rows += (
            f"<tr><td>{row['prompt']}</td>"
            f"<td>{row['winning_card']}</td>"
            f"<td>{row['winner_name']}</td></tr>"
        )

    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "EvilApplesTGBot")

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Evil Apples for Telegram</title>
  <link href="https://fonts.googleapis.com/css2?family=Luckiest+Guy&family=Inter:wght@400;600&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #1a0030; color: #e2d9f3; font-family: 'Inter', sans-serif; min-height: 100vh; }}
    header {{ background: #2d0050; padding: 1.5rem 2rem; display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #7c3aed; }}
    .logo {{ font-family: 'Luckiest Guy', cursive; font-size: 2rem; color: #c084fc; letter-spacing: 1px; }}
    .logo span {{ color: #fbbf24; }}
    nav a {{ color: #c4b5fd; text-decoration: none; margin-left: 1.5rem; font-weight: 600; }}
    nav a:hover {{ color: #fff; }}
    .hero {{ text-align: center; padding: 4rem 2rem 3rem; }}
    .hero h1 {{ font-family: 'Luckiest Guy', cursive; font-size: 3rem; color: #c084fc; margin-bottom: 1rem; }}
    .hero p {{ font-size: 1.2rem; color: #a78bfa; max-width: 600px; margin: 0 auto 2rem; }}
    .cta-btn {{ display: inline-block; background: #2563eb; color: white; padding: 1rem 2.5rem; border-radius: 50px; font-size: 1.1rem; font-weight: 700; text-decoration: none; transition: transform 0.2s; }}
    .cta-btn:hover {{ transform: scale(1.05); background: #1d4ed8; }}
    .section {{ max-width: 900px; margin: 0 auto 3rem; padding: 0 1.5rem; }}
    h2 {{ font-family: 'Luckiest Guy', cursive; font-size: 1.8rem; color: #c084fc; margin-bottom: 1.5rem; }}
    table {{ width: 100%; border-collapse: collapse; background: #2d0050; border-radius: 12px; overflow: hidden; }}
    th {{ background: #7c3aed; color: white; padding: 0.75rem 1rem; text-align: left; font-weight: 600; }}
    td {{ padding: 0.7rem 1rem; border-bottom: 1px solid #3d0070; color: #ddd6fe; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #3d0070; }}
    .empty {{ text-align: center; color: #7c6fa0; padding: 2rem; font-style: italic; }}
    .commands {{ background: #2d0050; border-radius: 12px; padding: 1.5rem; border-left: 4px solid #7c3aed; }}
    .commands code {{ background: #1a0030; color: #c084fc; padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 0.9rem; }}
    .commands p {{ margin: 0.5rem 0; color: #c4b5fd; }}
    footer {{ text-align: center; padding: 2rem; color: #6b5b8a; font-size: 0.9rem; border-top: 1px solid #3d0070; margin-top: 2rem; }}
  </style>
</head>
<body>
  <header>
    <div class="logo">Evil Apples <span>for Telegram</span></div>
    <nav>
      <a href="/">Home</a>
      <a href="/howtoplay">How to Play</a>
      <a href="https://t.me/{bot_username}" target="_blank">Add to Group</a>
    </nav>
  </header>

  <div class="hero">
    <h1>😈 Evil Apples</h1>
    <p>The dark comedy card game — now in your Telegram groups. Add the bot and start playing in seconds.</p>
    <a class="cta-btn" href="https://t.me/{bot_username}?startgroup=true" target="_blank">
      ➕ Add to Telegram Group
    </a>
  </div>

  <div class="section">
    <h2>🏆 Leaderboard</h2>
    {'<table><thead><tr><th>#</th><th>Player</th><th>Wins</th><th>Games</th></tr></thead><tbody>' + leaderboard_rows + '</tbody></table>' if leaders else '<div class="empty">No games played yet — be the first!</div>'}
  </div>

  <div class="section">
    <h2>🃏 Hall of Fame</h2>
    {'<table><thead><tr><th>Prompt</th><th>Winning Card</th><th>Winner</th></tr></thead><tbody>' + hall_rows + '</tbody></table>' if rounds else '<div class="empty">No rounds played yet — start a game!</div>'}
  </div>

  <div class="section">
    <h2>⚡ Quick Commands</h2>
    <div class="commands">
      <p><code>/startgame</code> — Start a game in your group</p>
      <p><code>/join</code> — Join the current game</p>
      <p><code>/deal</code> — Deal cards and start a round</p>
      <p><code>/scores</code> — Show current scores</p>
      <p><code>/endgame</code> — End the game (host only)</p>
      <p><code>/solo</code> — Play alone vs AI opponents</p>
    </div>
  </div>

  <footer>Evil Apples for Telegram &copy; 2025 &mdash; Powered by Venice.ai</footer>
</body>
</html>""")


@app.get("/howtoplay", response_class=HTMLResponse)
async def howtoplay():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>How to Play — Evil Apples for Telegram</title>
  <link href="https://fonts.googleapis.com/css2?family=Luckiest+Guy&family=Inter:wght@400;600&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1a0030; color: #e2d9f3; font-family: 'Inter', sans-serif; min-height: 100vh; }
    header { background: #2d0050; padding: 1.5rem 2rem; border-bottom: 2px solid #7c3aed; }
    .logo { font-family: 'Luckiest Guy', cursive; font-size: 2rem; color: #c084fc; }
    .logo span { color: #fbbf24; }
    .content { max-width: 750px; margin: 3rem auto; padding: 0 1.5rem; }
    h1 { font-family: 'Luckiest Guy', cursive; font-size: 2.5rem; color: #c084fc; margin-bottom: 2rem; }
    h2 { font-family: 'Luckiest Guy', cursive; font-size: 1.5rem; color: #a78bfa; margin: 2rem 0 1rem; }
    p, li { color: #c4b5fd; line-height: 1.7; margin-bottom: 0.75rem; }
    ol, ul { padding-left: 1.5rem; }
    code { background: #2d0050; color: #c084fc; padding: 0.2rem 0.5rem; border-radius: 4px; }
    .step { background: #2d0050; border-left: 4px solid #7c3aed; padding: 1rem 1.5rem; border-radius: 0 8px 8px 0; margin-bottom: 1rem; }
    footer { text-align: center; padding: 2rem; color: #6b5b8a; font-size: 0.9rem; border-top: 1px solid #3d0070; margin-top: 3rem; }
  </style>
</head>
<body>
  <header><div class="logo">Evil Apples <span>for Telegram</span></div></header>
  <div class="content">
    <h1>😈 How to Play</h1>

    <h2>Setup</h2>
    <div class="step">
      <p>1. Add <strong>@EvilApplesTGBot</strong> to your Telegram group.</p>
      <p>2. Type <code>/startgame</code> in the group to begin.</p>
      <p>3. Everyone types <code>/join</code> to enter (minimum 2 players).</p>
      <p>4. Type <code>/deal</code> when everyone is ready.</p>
    </div>

    <h2>Each Round</h2>
    <ol>
      <li>The bot posts a <strong>Prompt Card</strong> (the black card with a blank).</li>
      <li>One player is the <strong>Judge</strong> — they don't submit a card.</li>
      <li>All other players tap <strong>"View My Hand"</strong> to see their 7 cards in a private message.</li>
      <li>Each player picks their funniest answer card by tapping it.</li>
      <li>When everyone has submitted, the Judge sees all answers and picks the winner.</li>
      <li>Winner gets 1 point. <strong>First to 7 points wins the game!</strong></li>
    </ol>

    <h2>Game Modes</h2>
    <ul>
      <li><code>/startgame friend</code> — Casual mode, no timer (default)</li>
      <li><code>/startgame swap</code> — Cards are shuffled between players (5+ players)</li>
    </ul>

    <h2>Dr. Evil (AI Judge)</h2>
    <p>If there are fewer than 3 players, Dr. Evil (powered by Venice.ai) automatically judges the round — and roasts everyone's submissions.</p>

    <h2>Solo Mode</h2>
    <p>Play alone against AI opponents in any chat:</p>
    <ol>
      <li>Type <code>/solo</code> to start a game with 3 AI opponents.</li>
      <li>Type <code>/solodeal</code> to deal a round.</li>
      <li>Pick your card — Dr. Evil will judge and roast everyone.</li>
      <li>Type <code>/soloend</code> when done.</li>
    </ol>
  </div>
  <footer>Evil Apples for Telegram &copy; 2025</footer>
</body>
</html>""")


@app.get("/api/leaderboard")
async def api_leaderboard():
    try:
        rows = await get_leaderboard(20)
        return JSONResponse(content=rows)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/rounds")
async def api_rounds():
    try:
        rows = await get_recent_rounds(10)
        return JSONResponse(content=[
            {**r, "created_at": r["created_at"].isoformat() if r.get("created_at") else None}
            for r in rows
        ])
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "evil-apples-telegram"}


if __name__ == "__main__":
    logger.info(f"Starting Evil Apples Telegram server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
