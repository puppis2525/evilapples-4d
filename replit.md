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
- Quick game commands: `!startgame [mode]`, `!join`, `!deal`, `!timer`, `!endgame`
- **Game modes**: `!startgame` (friend, default), `!startgame swap`, `!startgame wildcard`
  - **Friend mode**: 2-20 players, no timer, first to 7 points wins
  - **Swap mode**: 5+ players, hands rotated between players each round
  - **Wildcard mode**: 5+ players, no pre-made cards — players type custom answers via Discord modal
- **Score tracking**: `game["scores"]` dict tracks points per player; auto-ends at 7 points with game-over embed; warns at 5 points
- Tournament commands: `!tournament create/join/start/status/end` (supports paid tournaments with `$fee` syntax)
- **Paid Tournaments**: Hosts set entry fees (e.g., `!tournament create MyTourney 5 $10`); uses Stripe Connect destination charges — platform keeps 10% application fee, host receives 90% instantly at payment time; winners with connected accounts can receive prize transfers via source_transaction
- Premium command: `!premium` — shows Pro status or upgrade link
- Free tier: 3 games/day per server; Pro: unlimited
- Solo mode: `!solo [1-5]` — Play against AI opponents with Venice.ai judging
- **Round-robin judge rotation**: Judge rotates each round among all players (like the mobile game)
- **Dr. Evil fallback**: When fewer than 3 players, Dr. Evil (Venice.ai) judges automatically instead of a human
- The game starter is a player, not a permanent judge; anyone can `!deal`
- Configurable countdown timer: `!timer <10-60>` — any player can set per-channel, skips non-submitters on expiry
- **Deck selection at game start**: If the host owns purchased decks, a dropdown menu appears letting them pick 1-3 decks to play with (or skip to use default cards)
- Multi-deck support: cards are pooled from all selected decks for more variety
- In-channel card viewing & submission via ephemeral "View My Hand" button (no DMs needed)
- DM-based submission still supported as fallback
- Interactive judging with Discord Buttons (ui_components.py)
- Purple-themed embeds matching Evil Apples branding

### Tournament System (tournament.py)
- Multi-round tournament with rotating judges
- Points-based standings with automatic progression
- Final standings and champion announcement
- All results saved to database and website
- `all_players` dict preserved across rounds (bug fix: judge rotation no longer removes players)

### Card System (api_client.py)
- **Default card pool**: 166 answer cards + 430 prompt cards in classic_cards.json — dealt randomly to all players for free
- **Hand size**: 7 cards per player (configurable via `HAND_SIZE` in api_client.py)
- **Card exchange**: Players can swap up to 3 cards for new ones, once per round (Exchange button in hand view)
- Prompts drawn randomly from the 430-card seed pool in classic_cards.json (falls back to Venice.ai generation if pool is empty)
- `deal_default_hands()` and `deal_default_solo()` draw from the default pool
- Custom deck cards override the default pool when a deck is selected
- AI card generation (`generate_hand()`) available as fallback
- Deduplication using **rapidfuzz** against classic_cards.json
- Auto-retry on duplicate detection

### Premium / Stripe (stripe_client.py, stripe_routes.py)
- Stripe integration via Replit Connectors (OAuth-based credential fetching)
- Per-server Pro subscription at $5.99/month
- Checkout flow: /premium page -> Stripe Checkout -> webhook activates premium
- Bot checks `is_premium_server()` before enforcing free tier limits
- Webhook handles: checkout.session.completed, subscription.deleted, subscription.updated

### Deck Store & Fulfillment
- **41 themed decks** in the store at $0.99 each (420, Addictions, Adulting, Babes, British AF, College, Cuties, Dads, Deckronomicon, Drunk AF, Family Guy, Food, Grateful, Gross 1&2, History, Horror, Illegal Activities, Insults, Internet, Kama Sutra, Kinks, Masturbation, Medical, Monsters, Naughty 17, Netflix & Chill, Office, Poke, Pussy, Rick & Morty, Sci-Fi, Sex, Sex Ed, Modern Sex, Shitty Jobs, Simpsons, Sins, Space, Stinky Pinky, Stranger Things)
- Admins upload "template" decks (CSV) via admin panel — these have `discord_user_id=NULL`
- **Bundle discount**: Select 3+ decks in the cart for 15% off the total price
- Cart-based store UI: click decks to select, floating cart bar shows count, total, discount hint, and Buy Now button
- Store listings reference template deck IDs via `deck_ids` field
- Checkout supports single or multi-deck purchases via `listing_ids` array
- When a user purchases listings via Stripe, the webhook records each purchase and triggers fulfillment
- Fulfillment copies all cards from the template deck(s) into a new personal deck owned by the buyer
- Purchased decks persist across all sessions — buyers use `!deck list` and `!deck use` in Discord
- Idempotent: duplicate webhooks are ignored (UNIQUE on stripe_session_id), duplicate deck copies are skipped

### Database (db.py)
- **PostgreSQL** via asyncpg
- Tables: `game_rounds`, `leaderboard`, `tournaments`, `tournament_players`, `tournament_rounds`, `premium_servers`, `custom_decks`, `custom_cards`, `store_listings`, `store_purchases`
- Shared between bot and web frontend
- Connection pool: min 2, max 15 connections, 30s command timeout
- All multi-step write operations wrapped in `async with conn.transaction()` for atomicity
- Unique index on `custom_decks(LOWER(name), discord_user_id)` prevents duplicate deck fulfillment

### Rate Limiting (server.py)
- In-memory per-IP rate limiter with three tiers:
  - **Strict** (5 req/min): checkout endpoints, Stripe Connect onboarding
  - **Moderate** (30 req/min): admin API, Discord OAuth login, card generation
  - **Relaxed** (60 req/min): all other `/api/` endpoints
- Stripe webhooks, static files, and favicon are excluded
- Uses `X-Forwarded-For` header for real client IP behind proxies
- Auto-cleanup of stale tracking data every 30 seconds

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
- `POST /api/stripe/webhook` - Stripe webhook handler (handles Pro, deck purchases, and tournament entries)
- `GET /api/stripe/config` - Stripe publishable key
- `POST /api/stripe/connect/onboard` - Start Stripe Connect onboarding for tournament hosts
- `GET /api/stripe/connect/onboard/return` - Handle Stripe Connect return
- `GET /api/stripe/connect/status` - Check if user has connected Stripe account
- `POST /api/stripe/tournament/entry-checkout` - Create checkout for tournament entry fee
- `GET /api/stripe/premium-status/{guild_id}` - Check server premium status

### Discord OAuth2 Login (discord_auth.py)
- Users can sign in with Discord via OAuth2 on the website
- Automatically parses Discord User ID, username, avatar
- Signed cookie session (HMAC-SHA256 with SESSION_SECRET), 30-day expiry
- Auth routes: `/auth/discord/login`, `/auth/discord/callback`, `/auth/discord/logout`, `/api/auth/user`
- Store and Decks pages auto-fill Discord User ID when signed in
- Discord app OAuth2 redirect URLs must include both production and dev callback URLs

## Environment Secrets
- `DISCORD_TOKEN` - Discord bot token
- `DISCORD_CLIENT_ID` - Discord OAuth2 client ID (for website login)
- `DISCORD_CLIENT_SECRET` - Discord OAuth2 client secret (for website login)
- `VENICE_API_KEY` - Venice.ai API key
- `DATABASE_URL` - PostgreSQL connection string (auto-provided)
- `SESSION_SECRET` - Session secret (also used for cookie signing and admin auth)
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
- `discord_auth.py` - Discord OAuth2 login (sign in with Discord, session cookies)
- `static/auth.js` - Shared auth UI (Sign In button, user dropdown, auto-fill Discord ID)
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
- `templates/howtoplay.html` - How to Play guide (gameplay instructions, all commands, tips)
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
