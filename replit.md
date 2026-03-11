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
- **In-channel Interaction**: Game actions (viewing hands, submitting cards, judging) are managed via Discord buttons and ephemeral messages, minimizing DM usage.
- **Premium Features**: A Pro subscription offers unlimited games per server (vs. 3 free games/day).

### Tournament System
- **Multi-round structure**: Supports individual and team-based tournaments with rotating judges and points-based standings.
- **Paid Tournaments**: Hosts can set entry fees using Stripe Connect, with tiered platform fees (10/15/20/25% based on monthly tournament count per server) and instant payouts.
- **Web Integration**: Tournaments created on the web can be linked to Discord channels.
- **Sponsorships**: Paid sponsor slots are available on tournament pages.
- **Revenue Page**: `/tournaments/revenue` explains the earnings model for hosts and players, with example scenarios and fee tier breakdown.

### User Interface (UI/UX)
- The web frontend utilizes **Tailwind CSS** with a custom Evil Apples purple theme, Fredoka One font for branding, and Inter for body text.
- Design elements include a floating devil logo, purple glow effects, and a dot grid background.
- Pages include: Leaderboard, Tournaments (listing, creation, detail), Premium pricing, Deck Store, and Admin panel.

### Authentication & Authorization
- **Discord OAuth2** for user login on the website, parsing user ID, username, and avatar.
- Session management via signed cookies (HMAC-SHA256, 30-day expiry).
- Admin panel access is token-gated.

### Rate Limiting
- In-memory, per-IP rate limiting across three tiers (Strict, Moderate, Relaxed) for API endpoints, excluding webhooks and static files.

## External Dependencies

- **Discord API**: For bot interaction and OAuth2 login.
- **Venice.ai**: For AI-generated cards.
- **Stripe**:
    - **Stripe Checkout**: For premium subscriptions and deck purchases.
    - **Stripe Connect**: For paid tournament entry fees and host payouts (destination charges).
    - **Stripe Webhooks**: For handling payment events and subscription status updates.
- **PostgreSQL**: As the primary database.
- **Replit Connectors**: For managing Stripe credentials.