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
- Quick game commands: `!startgame`, `!join`, `!deal`, `!timer`, `!endgame`
- Tournament commands: `!tournament create/join/start/status/end`
- Premium command: `!premium` — shows Pro status or upgrade link
- Free tier: 3 games/day per server; Pro: unlimited
- Solo mode: `!solo [1-5]` — Play against AI opponents with Venice.ai judging
- Configurable countdown timer: `!timer <10-60>` — Judge sets per-channel, skips non-submitters on expiry
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

### Deck Store & Fulfillment
- Admins upload "template" decks (CSV) via admin panel — these have `discord_user_id=NULL`
- Store listings reference template deck IDs via `deck_ids` field
- When a user purchases a listing via Stripe, the webhook records the purchase and triggers fulfillment
- Fulfillment copies all cards from the template deck(s) into a new personal deck owned by the buyer
- Purchased decks persist across all sessions — buyers use `!deck list` and `!deck use` in Discord
- Idempotent: duplicate webhooks are ignored (UNIQUE on stripe_session_id), duplicate deck copies are skipped

### Database (db.py)
- **PostgreSQL** via asyncpg
- Tables: `game_rounds`, `leaderboard`, `tournaments`, `tournament_players`, `tournament_rounds`, `premium_servers`, `custom_decks`, `custom_cards`, `store_listings`, `store_purchases`
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
- `GET /decks` - Card deck info page (how to upload, user lookup)
- `GET /store` - Deck store (purchase decks via Stripe)
- `GET /store/success` - Post-purchase success page
- `GET /admin` - Admin panel (token-gated, manage store listings)
- `GET /api/leaderboard` - JSON leaderboard data
- `GET /api/rounds` - JSON recent rounds data
- `GET /api/rounds/{slug}` - JSON single round detail
- `GET /api/tournaments` - JSON tournament list
- `GET /api/tournaments/{id}` - JSON tournament detail with players/rounds
- `GET /api/generate?card_type=white|black` - Generate a card via Venice.ai
- `GET /rounds/{slug}` - Individual round page
- `GET /api/decks?user_id=X` - JSON list of user's custom decks (requires user_id)
- `GET /api/store/listings` - JSON active store listings
- `POST /api/store/checkout` - Create Stripe checkout for deck purchase
- `GET /api/admin/listings` - JSON all listings (admin-only)
- `POST /api/admin/listings` - Create listing (admin-only)
- `PUT /api/admin/listings/{id}` - Update listing (admin-only)
- `DELETE /api/admin/listings/{id}` - Delete listing (admin-only)
- `POST /api/admin/upload-image` - Upload listing image (admin-only)
- `POST /api/admin/upload-deck` - Upload template deck CSV (admin-only)
- `GET /api/admin/template-decks` - List template decks (admin-only)
- `POST /api/stripe/create-checkout` - Create Stripe checkout session (Pro)
- `POST /api/stripe/webhook` - Stripe webhook handler (handles both Pro and deck purchases)
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
- `db.py` - Database operations (games + tournaments + premium + decks + store)
- `stripe_client.py` - Stripe credential fetching via Replit Connectors
- `stripe_routes.py` - Stripe checkout and webhook routes (Pro subs + deck purchases)
- `seed_products.py` - Script to create Stripe products and prices
- `classic_cards.json` - Classic CAH cards for deduplication
- `templates/index.html` - Web frontend (Evil Apples 4D themed)
- `templates/premium.html` - Premium pricing page
- `templates/premium_success.html` - Checkout success page
- `templates/decks.html` - Card deck info and user lookup page
- `templates/store.html` - Deck store (browse & purchase)
- `templates/store_success.html` - Purchase success page
- `templates/admin.html` - Admin panel (manage store listings, pricing, bundling)
- `static/logo.png` - Brand logo
- `static/surreal-dali-deck.png` - Sample deck artwork (5:7 portrait)

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
