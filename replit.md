# Evil Apples 4D

## Overview
A full-stack Discord game ecosystem branded as "Evil Apples 4D" — runs a Cards Against Humanity-style game powered by Venice.ai for AI-generated cards, with tournaments, live web leaderboard, Hall of Fame, and Stripe-powered premium subscriptions. Purple devil theme inspired by Evil Apples.

## Architecture

### Backend (server.py)
- **FastAPI** server on port 5000
- Serves the web frontend, static files, and API endpoints
- Launches the Discord bot as a background thread on startup (5s delay for production stability)
- Stripe webhook and checkout routes via stripe_routes.py

### Discord Bot (bot.py)
- **discord.py** bot running as a daemon thread
- Quick game commands: `!startgame`, `!join`, `!deal`, `!endgame`
- Tournament commands: `!tournament create/join/start/status/end`
- Premium command: `!premium` — shows Pro status or upgrade link
- Free tier: 3 games/day per server; Pro: unlimited
- DM-based card distribution and submission
- Interactive judging with Discord Buttons (ui_components.py)
- Purple-themed embeds matching Evil Apples branding

### Tournament System (tournament.py)
- Multi-round tournament with rotating judges
- Points-based standings with automatic progression
- Final standings and champion announcement
- All results saved to database and website
- `all_players` dict preserved across rounds (bug fix: judge rotation no longer removes players)

### AI Card Generation (api_client.py)
- Calls **Venice.ai** API (llama-3.3-70b model) for card generation
- "Divergent Comedy" system prompt for edgy, original cards
- Deduplication using **rapidfuzz** against classic_cards.json
- Auto-retry on duplicate detection
- Parallel card generation for multiple players

### Premium / Stripe (stripe_client.py, stripe_routes.py)
- Stripe integration via Replit Connectors (OAuth-based credential fetching)
- Per-server Pro subscription at $5.99/month
- Checkout flow: /premium page -> Stripe Checkout -> webhook activates premium
- Bot checks `is_premium_server()` before enforcing free tier limits
- Webhook handles: checkout.session.completed, subscription.deleted, subscription.updated

### Database (db.py)
- **PostgreSQL** via asyncpg
- Tables: `game_rounds`, `leaderboard`, `tournaments`, `tournament_players`, `tournament_rounds`, `premium_servers`
- Shared between bot and web frontend
- Connection pool with auto-reconnect

### Frontend
- **templates/index.html** — Main leaderboard, tournaments, Hall of Fame
- **templates/premium.html** — Pricing page (Free vs Pro comparison, checkout form)
- **templates/premium_success.html** — Post-checkout success page
- **Tailwind CSS** via CDN with custom Evil Apples purple theme
- Fredoka One brand font + Inter body font
- Floating devil logo, purple glow effects, dot grid background
- Auto-refresh every 15 seconds

## Static Assets
- `static/logo.png` - Evil Apples 4D devil logo (purple face with white eyes)

## API Endpoints
- `GET /` - Main leaderboard page
- `GET /premium` - Premium pricing page
- `GET /premium/success` - Post-checkout success page
- `GET /decks` - Card deck management page (upload CSV, view decks)
- `GET /api/leaderboard` - JSON leaderboard data
- `GET /api/rounds` - JSON recent rounds data
- `GET /api/rounds/{slug}` - JSON single round detail
- `GET /api/tournaments` - JSON tournament list
- `GET /api/tournaments/{id}` - JSON tournament detail with players/rounds
- `GET /api/generate?card_type=white|black` - Generate a card via Venice.ai
- `GET /rounds/{slug}` - Individual round page
- `GET /api/decks` - JSON list of all custom decks
- `GET /api/decks/{id}` - JSON cards in a specific deck
- `POST /api/decks/upload` - Upload a CSV deck (multipart form)
- `DELETE /api/decks/{id}` - Delete a deck
- `POST /api/stripe/create-checkout` - Create Stripe checkout session
- `POST /api/stripe/webhook` - Stripe webhook handler
- `GET /api/stripe/config` - Stripe publishable key
- `GET /api/stripe/premium-status/{guild_id}` - Check server premium status

## Environment Secrets
- `DISCORD_TOKEN` - Discord bot token
- `VENICE_API_KEY` - Venice.ai API key
- `DATABASE_URL` - PostgreSQL connection string (auto-provided)
- `SESSION_SECRET` - Session secret
- `STRIPE_PRICE_ID` - Stripe price ID for Pro plan (set after running seed_products.py)
- Stripe keys managed via Replit Connectors (no manual API keys needed)

## Key Files
- `server.py` - FastAPI server entry point
- `bot.py` - Discord bot with game + tournament + premium + deck commands
- `tournament.py` - Tournament game loop and UI components
- `api_client.py` - Venice.ai integration + deduplication
- `ui_components.py` - Discord UI components (judging buttons)
- `db.py` - Database operations (games + tournaments + premium + decks)
- `stripe_client.py` - Stripe credential fetching via Replit Connectors
- `stripe_routes.py` - Stripe checkout and webhook routes
- `seed_products.py` - Script to create Stripe products and prices
- `classic_cards.json` - Classic CAH cards for deduplication
- `templates/index.html` - Web frontend (Evil Apples 4D themed)
- `templates/premium.html` - Premium pricing page
- `templates/premium_success.html` - Checkout success page
- `templates/decks.html` - Card deck upload and management page
- `static/logo.png` - Brand logo

## Dependencies
- fastapi, uvicorn, jinja2
- discord.py
- httpx (Venice.ai API calls)
- asyncpg (PostgreSQL)
- rapidfuzz (card deduplication)
- python-slugify (round slug generation)
- stripe (Stripe API)
- aiohttp (async HTTP for Replit Connectors)
- python-multipart (CSV file uploads)

## Deployment
- Target: VM (always-on, for Discord bot)
- Run command: `python server.py`
- Domain: EvilApples4d.com (custom domain)
