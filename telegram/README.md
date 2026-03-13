# Evil Apples for Telegram

A standalone Cards Against Humanity-style game bot for Telegram groups — built with python-telegram-bot and FastAPI.

## Setup (Standalone Project)

When migrating this to its own Repl, copy the contents of this `/telegram` folder to the root of a new Python Repl.

### Required Environment Secrets

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | From @BotFather on Telegram |
| `DATABASE_URL` | PostgreSQL connection string (shared with Evil Apples 4D) |
| `VENICE_API_KEY` | Venice.ai API key for AI card generation |
| `TELEGRAM_BOT_USERNAME` | Your bot's @username (without @) |

### Also copy from Evil Apples 4D
- `classic_cards.json` — the default card deck (referenced by api_client.py)

### Run
```bash
pip install -r requirements.txt
python server.py
```

## Project Structure

```
server.py          — FastAPI web server + bot startup
bot.py             — Telegram bot (all commands + callback handlers)
api_client.py      — Venice.ai card generation and AI judging
db.py              — PostgreSQL database layer (shared tables)
requirements.txt   — Python dependencies
templates/         — (future) Jinja2 templates
static/            — (future) CSS, images
```

## Game Commands

| Command | Description |
|---------|-------------|
| `/startgame` | Start a game in the group |
| `/startgame swap` | Start swap mode (5+ players) |
| `/join` | Join the current game |
| `/deal` | Deal a round |
| `/scores` | Show current scores |
| `/endgame` | End the game (host only) |
| `/leaderboard` | Show top players |
| `/solo [N]` | Solo game vs N AI opponents |
| `/solodeal` | Deal a solo round |
| `/soloend` | End solo game |

## Architecture Notes

- **Shared database**: Uses the same PostgreSQL tables as Evil Apples 4D. A `platform` column distinguishes Telegram (`tg_`) vs Discord entries.
- **No Discord code**: Completely standalone — no discord.py, no Discord OAuth.
- **Web server**: Runs on port 5001. Serves a leaderboard homepage and How to Play page.
- **Polling mode**: Bot uses long polling (no webhook needed for development; switch to webhook for production).
