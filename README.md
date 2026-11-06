# Evil Apples 4D

Evil Apples is a mature party card game. This repository runs three pieces:

- a FastAPI site (`server.py`) for the leaderboard, store, tournaments, premium checkout, and Discord login
- a discord.py bot (`bot.py`) started from that site
- a Telegram bot and site (`telegram/`) that shares the same Postgres database

Slash commands are the Discord interface. `!` commands still exist and stay off unless you opt in. Nothing in this repo chooses a production host, submits the Discord App Directory listing, or ships a license.

The card pool and AI judge include sexual, violent, and substance-related humor. That content is an App Directory eligibility blocker. See [docs/APP_DIRECTORY_READINESS.md](docs/APP_DIRECTORY_READINESS.md). This README does not claim the app is approved.

## Run it locally

Python 3.11 or newer, PostgreSQL, and the packages in `pyproject.toml`. Stay on Starlette 0.52.x (the version in `uv.lock`). Starlette 1.x changes `TemplateResponse` and breaks these pages. FastAPI 0.135.x matches that Starlette.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r <(python - <<'PY'
import tomllib
print("\n".join(tomllib.load(open("pyproject.toml","rb"))["project"]["dependencies"]))
PY
)
cp .env.example .env
# fill .env outside git. Do not commit it.
```

Minimum for the website and tests:

- `SESSION_SECRET` — required at import
- `DATABASE_URL` — required for real reads and writes
- `DISCORD_TOKEN` — required only if the bot should connect
- `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` — required for checkout outside Replit
- `STRIPE_WEBHOOK_SECRET` — required or webhooks are rejected
- `VENICE_API_KEY` — required only when a round needs AI generation or judging

```bash
python server.py
```

That listens on port 5000. The Discord bot connects only when `DISCORD_TOKEN` is set, `DISABLE_DISCORD_BOT` is not true, and this is not a Replit dev domain (the deployed Replit process still owns the bot there).

Telegram stays a second process and is unchanged as a product:

```bash
# from the repository root, so imports resolve
python telegram/server.py
```

`start.sh` runs the Discord site on port 5000 and the Telegram site on port 5001, and restarts Telegram if it exits. It does not select a host. Put TLS in front of both ports yourself.

Set `PUBLIC_BASE_URL` to the https origin players should see (`https://example.invalid` is not a recommendation; use the origin you control). Stripe return URLs use it even when the app sits behind a proxy. If it is unset, redirects use the request host.

## Discord commands

| Action | Slash | Who can use it |
| --- | --- | --- |
| Help | `/help` | Everyone. Not age-restricted. |
| Start, join, deal, decks, timer, solo | `/startgame`, `/join`, `/deal`, `/decks`, `/timer`, `/solo ...` | Members, in an age-restricted channel by default |
| End a channel game | `/endgame` | Game host, or Manage Messages |
| Premium | `/premium` | Members. Not age-restricted. |
| Tournament create, start, end, link | `/tournament ...` | Manage Server, plus the creator check on start, end, and link |
| Tournament join, status, teams | `/tournament join`, `status`, `teams` | Members |
| Decks | `/deck ...` | Members. Schemer or Overlord, and you can only change your own decks |

`/tournament create` takes `name`, `rounds`, and `entry_fee_dollars`. `/deck upload` takes a CSV attachment. `/solo start` takes `opponents` from 1 to 5.

Default `MATURE_CONTENT_ENFORCEMENT=nsfw_channel` marks gameplay slash commands `nsfw` and refuses them outside age-restricted channels. `off` removes that gate. It does not make the card text acceptable for a general audience.

### Legacy `!` commands

`ENABLE_LEGACY_PREFIX_COMMANDS=false` by default. The bot does not request the message content intent, and guild `!` commands do nothing.

Set it to `true` only if you also enable Message Content in the Developer Portal. Direct-message card numbers for a round that is already going still work without that intent, because Discord delivers DM text anyway. They do not start a game.

The guild members intent and the presence intent stay off. Winner names come from the game record, the member cache, then one `fetch_member` call.

Command registration:

- `DISCORD_COMMAND_SCOPE=global` (default) syncs commands for every server. Discord can take up to an hour.
- `DISCORD_COMMAND_SCOPE=guild` with `DISCORD_GUILD_ID` syncs one server for testing. That scope is not what the directory install uses.

## Stripe outside Replit

Set both `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY`. The Replit connector is used only when both are empty and the connector environment is present. Setting only one of the two keys is an error.

Checkout creation sends an idempotency key scoped to the UTC day plus the payer and the purchase, so a double-click reuses the session. Refunds and winner transfers use a stable key so a retry does not create a second movement. Webhooks:

1. Rejected when `STRIPE_WEBHOOK_SECRET` is missing.
2. Rejected when `Stripe-Signature` does not match. Verification uses the secret only. It does not call Replit.
3. The event id is stored in `stripe_webhook_events` (migration 14). A repeat is acknowledged and not applied again.
4. If the handler throws, the id is released so Stripe can retry. The HTTP body does not include the exception text.

Point the webhook at `https://<your-origin>/api/stripe/webhook`. Price ids (`STRIPE_PRICE_ID_SCHEMER`, `STRIPE_PRICE_ID_OVERLORD`, or legacy `STRIPE_PRICE_ID`) must match the amounts advertised on `/premium` (Schemer $19/month, Overlord $99/month). The Telegram group unlock is a separate one-time $4.99 charge in the Telegram checkout code.

Tournament platform fees in code are 10, 15, 20, and 25 percent by how many tournaments that guild created in the calendar month. Checkout uses the percent saved on the tournament.

## Tests

```bash
python -m pytest tests -q
```

`tests/conftest.py` sets a fake `SESSION_SECRET` if you did not. The suite does not use live Discord, Stripe, or Venice credentials. What it actually ran, and what still needs a real guild, is listed in [docs/APP_DIRECTORY_READINESS.md](docs/APP_DIRECTORY_READINESS.md).

## Pages

- `/privacy` and `/terms` on the Discord site
- `/privacy` and `/terms` on the Telegram site

Both describe the current data flow and mark contact, entity, age rule, and governing law as unconfirmed. Do not treat them as a lawyer-approved policy.

## What this repo will not do

- pick or provision a production host
- submit the App Directory listing
- merge, deploy, or spend
- add a license
- print or commit secret values
