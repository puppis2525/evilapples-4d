# Evil Apples 4D

## Overview
A full-stack Discord game ecosystem branded as "Evil Apples 4D" — runs a Cards Against Humanity-style game powered by Venice.ai for AI-generated cards, with tournaments, live web leaderboard, and Hall of Fame. Purple devil theme inspired by Evil Apples.

## Architecture

### Backend (server.py)
- **FastAPI** server on port 5000
- Serves the web frontend, static files, and API endpoints
- Launches the Discord bot as a background thread on startup

### Discord Bot (bot.py)
- **discord.py** bot running as a daemon thread
- Quick game commands: `!startgame`, `!join`, `!deal`, `!endgame`
- Tournament commands: `!tournament create/join/start/status/end`
- DM-based card distribution and submission
- Interactive judging with Discord Buttons (ui_components.py)
- Purple-themed embeds matching Evil Apples branding

### Tournament System (tournament.py)
- Multi-round tournament with rotating judges
- Points-based standings with automatic progression
- Final standings and champion announcement
- All results saved to database and website

### AI Card Generation (api_client.py)
- Calls **Venice.ai** API (llama-3.3-70b model) for card generation
- "Divergent Comedy" system prompt for edgy, original cards
- Deduplication using **rapidfuzz** against classic_cards.json
- Auto-retry on duplicate detection
- Parallel card generation for multiple players

### Database (db.py)
- **PostgreSQL** via asyncpg
- Tables: `game_rounds`, `leaderboard`, `tournaments`, `tournament_players`, `tournament_rounds`
- Shared between bot and web frontend

### Frontend (templates/index.html)
- **Tailwind CSS** via CDN with custom Evil Apples purple theme
- Fredoka One brand font + Inter body font
- Floating devil logo, purple glow effects, dot grid background
- Live Leaderboard, Tournaments, and Hall of Fame sections
- Auto-refresh every 15 seconds

## Static Assets
- `static/logo.png` - Evil Apples 4D devil logo (purple face with white eyes)

## API Endpoints
- `GET /` - Main leaderboard page
- `GET /api/leaderboard` - JSON leaderboard data
- `GET /api/rounds` - JSON recent rounds data
- `GET /api/rounds/{slug}` - JSON single round detail
- `GET /api/tournaments` - JSON tournament list
- `GET /api/tournaments/{id}` - JSON tournament detail with players/rounds
- `GET /api/generate?card_type=white|black` - Generate a card via Venice.ai
- `GET /rounds/{slug}` - Individual round page

## Environment Secrets
- `DISCORD_TOKEN` - Discord bot token
- `VENICE_API_KEY` - Venice.ai API key
- `DATABASE_URL` - PostgreSQL connection string (auto-provided)

## Key Files
- `server.py` - FastAPI server entry point
- `bot.py` - Discord bot with game + tournament commands
- `tournament.py` - Tournament game loop and UI components
- `api_client.py` - Venice.ai integration + deduplication
- `ui_components.py` - Discord UI components (judging buttons)
- `db.py` - Database operations (games + tournaments)
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
