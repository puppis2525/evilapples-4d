# Evil Apples 4D

## Overview
Evil Apples 4D is a comprehensive Discord game ecosystem offering a Cards Against Humanity-style experience. It features AI-generated cards via Venice.ai, a tournament system with a live web leaderboard and Hall of Fame, and premium subscriptions powered by Stripe. The project aims to provide an engaging, full-stack gaming platform with a distinctive purple devil theme, inspired by the original Evil Apples game. It targets a broad audience of Discord users, offering both free and premium tiers, and includes an extensive deck store for personalized gameplay.

## User Preferences
I prefer clear, concise language. Please prioritize iterative development and ask before making any major architectural changes or significant code refactors.

## System Architecture

### Core System
The application is a full-stack project utilizing a **FastAPI** backend (`server.py`) serving a web frontend, static assets, and API endpoints. A **discord.py** bot (`bot.py`) runs as a background thread for game interactions. **PostgreSQL** (`db.py`) serves as the primary data store, managed via `asyncpg`, handling game states, user data, tournaments, and premium subscriptions.

### Game Mechanics
- **Game Modes**: Supports classic "friend" mode, "swap" mode for hand rotation, and "wildcard" mode for custom user-typed answers.
- **Scoring**: Points-based system, typically first to 7 points wins, with automatic game conclusion.
- **Judge Rotation**: Round-robin judge rotation among players. If fewer than 3 players, or if a human judge fails to pick, Dr. Evil (Venice.ai) acts as judge.
- **Card System**: Uses a default pool of classic cards (`classic_cards.json`), with **Venice.ai** for AI-generated cards as fallback. Players can select custom decks, and cards are dealt based on player-specific deck selections. Hand size is 7 cards, with one-time per-round card exchange. **Rapidfuzz** is used for card deduplication.
- **Game State Persistence**: Active game sessions (both channel and solo) are persisted to the `active_game_sessions` PostgreSQL table as JSONB. On bot restart, games in safe states (`waiting_for_players`, `finished`, `idle`) are restored; mid-round sessions are cleaned up.
- **In-channel Interaction**: Game actions (viewing hands, submitting cards, judging) are managed via Discord buttons and ephemeral messages, minimizing DM usage.
- **Premium Tiers**: Three-tier system — **Minion** (free: 3 games/day, no tournaments, no custom decks), **Schemer** ($19/mo: unlimited games, 1 tournament/month with 50-player cap, custom decks), **Overlord** ($99/mo: unlimited everything, priority AI). Legacy "pro" plan maps to Overlord behavior. Plan enforcement via `get_plan_limits()` and `get_server_plan()` in `db.py`.

### Tournament System
- **Multi-round structure**: Supports individual and team-based tournaments with rotating judges and points-based standings.
- **Paid Tournaments**: Hosts can set entry fees using Stripe Connect, with tiered platform fees (10/15/20/25% based on monthly tournament count per server) and instant payouts.
- **Web Integration**: Tournaments created on the web can be linked to Discord channels.
- **Sponsorships**: Paid sponsor slots are available on tournament pages.
- **Revenue Page**: `/tournaments/revenue` explains the earnings model for hosts and players, with example scenarios and fee tier breakdown.

### User Interface (UI/UX)
- The web frontend utilizes **Tailwind CSS** with a custom Evil Apples purple theme, Fredoka One font for branding, and Inter for body text.
- Design elements include a floating devil logo, purple glow effects, and a dot grid background.
- Pages include: Leaderboard, Tournaments (listing, creation, detail), Premium pricing, Deck Store, and Admin panel with full analytics dashboard.

### Authentication & Authorization
- **Discord OAuth2** for user login on the website, parsing user ID, username, and avatar.
- Session management via signed cookies (HMAC-SHA256, 30-day expiry).
- Admin panel access is token-gated (`SESSION_SECRET`).
- Admin analytics dashboard at `/admin` with tabbed interface (Analytics, Store Manager, Template Decks, Ads), Chart.js visualizations, and date range filtering.

### Ad Revenue System
- **Text Ads**: `text_ads` table stores managed ads with title, body, URL, display_name, active flag, impressions, and clicks.
- **In-Bot Ads**: After each round's winner announcement, free-tier servers see a text ad. Premium servers are ad-free.
- **Web Ad Slots**: All public pages (index, store, tournaments, howtoplay) show a sponsored text ad slot above the footer, loaded from `/api/ads/random`.
- **Click Tracking**: `/ad/click/{ad_id}` increments clicks and redirects to destination URL.
- **Admin Management**: Full CRUD at `/api/admin/ads` (GET/POST/PUT/DELETE), with an Ads tab in the admin dashboard showing impressions, clicks, CTR, and pause/delete controls.

### Rate Limiting
- In-memory, per-IP rate limiting across three tiers (Strict, Moderate, Relaxed) for API endpoints, excluding webhooks and static files.

### Telegram Bot & Website (`telegram/`)
- **Bot**: `telegram/bot.py` — full gameplay bot using `python-telegram-bot` v21.6 (asyncio-native)
  - **Game Modes**: friend (2+ players), swap (5+ players, cards rotate), wildcard (2+ players, write your own answers)
  - **Core Flow**: `/startgame`, `/join`, `/deal` → card submission via DM inline buttons (or text in wildcard mode) → player voting via inline keyboard → highest-voted wins (AI fallback if no votes)
  - **Solo Mode**: `/solo`, `/solodeal`, `/soloend` — play vs AI opponents with Dr. Evil judging
  - **Tournaments**: `/tournament create/join/start/status/end` — multi-round with standings
  - **Deck System**: `/decks`, `/deck upload/delete`, `/store` — custom deck upload via CSV, premium-gated for groups
  - **Premium**: `/premium` — shows $4.99 one-time group unlock; groups need unlock for custom decks
  - **Wired via lifespan**: `telegram/server.py` starts bot polling alongside FastAPI
- **Workflow**: "Start Telegram bot" (`cd telegram && python server.py`) runs as a separate workflow on port 5001
- **Production**: Both Discord and Telegram servers launch via `start.sh` (deployment run command)
- **Secret**: Telegram bot token is read from `TELEGRAM_BOT_CODE` env var (falls back to `TELEGRAM_BOT_TOKEN`)
- **Framework**: FastAPI + Jinja2 templates on port 5001
- **Templates**: `telegram/templates/` — `base.html` (shared nav/footer), `index.html`, `howtoplay.html`, `store.html`, `premium.html`, `leaderboard.html`
- **Static**: `telegram/static/style.css` — CSS-variable-based dark purple design system (no Tailwind)
- **Pages**: Home (hero + stats + leaderboard + hall of fame + commands), How to Play (all commands including tournaments/decks/premium), Store (deck listings), Premium (feature grid), Leaderboard (top 50)
- **APIs**: `/api/stats` (groups/players/rounds counts), `/api/leaderboard`, `/api/rounds`
- **DB**: `get_tg_stats()` in `telegram/db.py` aggregates player count, round count, and latest group count
- **Shared DB**: Same PostgreSQL as main Evil Apples 4D; `platform='telegram'` column distinguishes data

## External Dependencies

- **Discord API**: For bot interaction and OAuth2 login.
- **Venice.ai**: For AI-generated cards.
- **Stripe**:
    - **Stripe Checkout**: For premium subscriptions and deck purchases.
    - **Stripe Connect**: For paid tournament entry fees and host payouts (destination charges).
    - **Stripe Webhooks**: For handling payment events and subscription status updates.
- **PostgreSQL**: As the primary database.
- **Replit Connectors**: For managing Stripe credentials.