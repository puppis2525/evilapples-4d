# Evil Apples 4D

## Overview
A full-stack Discord game ecosystem branded as "Evil Apples 4D" — runs a Cards Against Humanity-style game powered by Venice.ai for AI-generated cards, with a live web leaderboard. Purple devil theme inspired by Evil Apples.

## Architecture

### Backend (server.py)
- **FastAPI** server on port 5000
- Serves the web frontend, static files, and API endpoints
- Launches the Discord bot as a background thread on startup

### Discord Bot (bot.py)
- **discord.py** bot running as a daemon thread
- Game commands: `!startgame`, `!join`, `!deal`, `!endgame`
- DM-based card distribution and submission
- Interactive judging with Discord Buttons (ui_components.py)
- Purple-themed embeds matching Evil Apples branding

### AI Card Generation (api_client.py)
- Calls **Venice.ai** API (llama-3.3-70b model) for card generation
- "Divergent Comedy" system prompt for edgy, original cards
- Deduplication using **rapidfuzz** against classic_cards.json
- Auto-retry on duplicate detection
- Parallel card generation for multiple players

### Database (db.py)
- **PostgreSQL** via asyncpg
- Tables: `game_rounds` (winning combos), `leaderboard` (player stats)
- Shared between bot and web frontend

### Frontend (templates/index.html)
- **Tailwind CSS** via CDN with custom Evil Apples purple theme
- Fredoka One brand font + Inter body font
- Floating devil logo, purple glow effects, dot grid background
- Live Leaderboard with auto-refresh every 15 seconds
- Hall of Fame with black/white card styling

## Static Assets
- `static/logo.png` - Evil Apples 4D devil logo (purple face with white eyes)

## API Endpoints
- `GET /` - Main leaderboard page
- `GET /api/leaderboard` - JSON leaderboard data
- `GET /api/rounds` - JSON recent rounds data
- `GET /api/rounds/{slug}` - JSON single round detail
- `GET /api/generate?card_type=white|black` - Generate a card via Venice.ai
- `GET /rounds/{slug}` - Individual round page

## Environment Secrets
- `DISCORD_TOKEN` - Discord bot token
- `VENICE_API_KEY` - Venice.ai API key
- `DATABASE_URL` - PostgreSQL connection string (auto-provided)

## Key Files
- `server.py` - FastAPI server entry point
- `bot.py` - Discord bot with game logic
- `api_client.py` - Venice.ai integration + deduplication
- `ui_components.py` - Discord UI components (judging buttons)
- `db.py` - Database operations
- `classic_cards.json` - Classic CAH cards for deduplication
- `templates/index.html` - Web frontend (Evil Apples 4D themed)
- `static/logo.png` - Brand logo

## Dependencies
- fastapi, uvicorn, jinja2
- discord.py
- httpx (Venice.ai API calls)
- asyncpg (PostgreSQL)
- rapidfuzz (card deduplication)
- python-slugify (round slug generation)
