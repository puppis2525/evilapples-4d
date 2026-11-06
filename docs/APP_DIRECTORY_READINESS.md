# Discord App Directory readiness

Checked November 6, 2026 against Discord's published docs. This is not an approval, a submission, or a legal opinion. Discovery was not enabled. No application was sent to Discord.

## Eligibility blockers

These are reasons the current product should not be treated as ready for App Directory discovery. They come from the docs below and from this repository. They are not a Discord decision.

1. **Age-restricted content.** The App Directory content policy says a discoverable app's content must not include sexually explicit text, violent content, or illegal, dangerous, and regulated goods such as alcohol or drug use. `classic_cards.json` and the seeded deck names include that kind of material. The AI judge prompt tells the model to reward shock value. Turning `MATURE_CONTENT_ENFORCEMENT` off does not remove the cards.
2. **Age-restricted commands.** Discord's application-command docs say an app with discovery enabled cannot contain age-restricted commands or content. This build marks gameplay slash commands `nsfw: true` when enforcement is `nsfw_channel` (the default). That flag is the correct control for mature play, and it is also a discovery blocker.
3. **Paid tournaments.** Entry fees are collected and a share is paid out through Stripe Connect. The same content policy lists gambling-adjacent behavior. Whether these tournaments qualify is for the owner and for Discord. This repo does not clear them.
4. **Third-party names.** Seeded deck titles include Simpsons, Rick and Morty, Family Guy, Stranger Things, Pokemon, and Netflix. The content policy says the app name, description, and commands must not contain IP-violating content. Deck titles are product content. Command descriptions in this branch do not use "Cards Against Humanity".
5. **Portal fields this repo cannot set.** Verification, a support-server invite, a description, at least one tag, an install URL, `privacy_policy_url`, and `terms_of_service_url` are Developer Portal settings. They are not in git.
6. **Legal page is not finished.** `/privacy` and `/terms` now match the code and say what is still unconfirmed. They are not a counsel-approved policy. See owner inputs below.
7. **No production host was selected.** That was intentional.

Sources used:

- [App Directory content requirements](https://support-dev.discord.com/hc/en-us/articles/9489299950487-App-Directory-App-Content-Requirements-Policy)
- [App profile pages](https://support-dev.discord.com/hc/en-us/articles/6378525413143-App-Directory-App-profile-pages), including the FAQ that meeting the checklist does not guarantee a listing
- [Application commands: age-restricted commands](https://docs.discord.com/developers/interactions/application-commands)
- [Privileged intent review](https://docs.discord.com/developers/gateway/getting-started-with-privileged-intent-review) (threshold described there as unique users who can see the app, with annual reapplication once access is required)

Slash commands do not need the message content intent. That part of the directory guidance is done in code. The content blockers above are not.

## What the code does now

- Slash commands call the existing gameplay and admin functions. `!` commands remain in the bot and run only when `ENABLE_LEGACY_PREFIX_COMMANDS=true`, which also turns on the message content intent.
- Default intents: message content off, members off, presences off. Winner names use the game record, then the cache, then one member fetch.
- Gameplay commands are guild-only. `/tournament create`, `start`, `end`, and `link` require Manage Server in Discord's default command permissions, and start/link still require the creator. `/endgame` allows the host or Manage Messages. Deck changes still require Schemer or Overlord and ownership.
- Default mature gate: age-restricted channel, and the slash commands are marked `nsfw`.
- Stripe uses `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` when both are set. The Replit connector remains the fallback when both are empty. Webhook signatures are checked with `STRIPE_WEBHOOK_SECRET` and do not call Replit. Event ids go in `stripe_webhook_events` (migration 14). A failed handler releases the id so Stripe can retry. Checkout, refund, and prize transfer calls send idempotency keys.
- `PUBLIC_BASE_URL` is the canonical origin when set. The bot does not start without `DISCORD_TOKEN`. `DISABLE_DISCORD_BOT=true` keeps the site up with no Discord connection. Replit dev still skips the bot so the deployed process keeps the session.
- Telegram routes `/privacy` and `/terms` were added. Telegram commands and checkout behavior are otherwise intact.

## Checks that were actually run

No Discord, Stripe, or Venice credentials were present. Postgres was not running, so `/health` returned degraded (`database: disconnected`, HTTP 503). That is what the health test expects.

```text
python3 -m pytest tests/test_directory_prep.py tests/test_stripe_webhook.py \
  tests/test_health.py tests/test_migrations.py tests/test_cors.py tests/test_discord_auth.py -q
70 passed

python3 -m pytest tests/test_game_session.py tests/test_slug_generation.py \
  tests/test_tournament_validation.py tests/test_rate_limiter.py -q
58 passed, 3 failed
```

Together that is the whole `tests/` tree: **128 passed, 3 failed**.

The three failures are `TestRateLimiterDBBehavior` in `tests/test_rate_limiter.py`. They expect `RateLimiter.is_allowed` to query `rate_limit_entries` and to fail closed on the strict tier when the database raises. `server.py` keeps the buckets in process memory and does not call the database. This branch did not change that method. The privacy page describes the in-memory limiter on purpose.

Also executed:

- Importing `bot` registers 25 slash commands. Message content, members, and presences are off.
- Importing `telegram/server.py` shows `/privacy` and `/terms`.

`/health` inside the pytest client did not start the Discord bot, because `DISCORD_TOKEN` was unset.

## Live checks that were not run

These need owner input. Do not treat the offline suite as a substitute.

- Create the slash commands in a real test guild (`DISCORD_COMMAND_SCOPE=guild` and `DISCORD_GUILD_ID`), then invite the bot and run `/startgame`, `/join`, `/deal`, `/endgame`, `/solo start`, `/tournament create`, and `/deck list` in an age-restricted channel and again in a normal channel.
- Confirm a member without Manage Server cannot see tournament create/start/end/link, and that a non-host without Manage Messages cannot end the game.
- With `ENABLE_LEGACY_PREFIX_COMMANDS=true` and the portal intent on, repeat one `!` command. With the flag off, confirm `!startgame` does nothing in a guild.
- Point Stripe CLI or the dashboard webhook at a non-production URL, pay in test mode, and confirm a replayed `evt_` does not double-grant premium, a deck, a tournament entry, or a Telegram group unlock.
- Confirm the Stripe Price ids charge the advertised $19, $99, and Telegram $4.99.
- Open `/privacy` and `/terms` on the host you choose. This session did not deploy.

## Owner inputs still required

- Legal entity name, postal address, and whether `support@evilapples4d.com` is a monitored mailbox. It is printed because the public site already shows it. It was not verified.
- The age rule. The March 14, 2026 pages said 13+ to use the service and 18+ to pay. The cards are mature, and Discord's age-restricted commands are 18+. The code does not collect a date of birth. Counsel has to choose the sentence.
- Governing law. The published line is still "United States" plus arbitration, with no state or venue in the repo.
- A decision: remove age-restricted content and the `nsfw` command flag in order to pursue discovery, or keep the mature game and do not enable discovery. This branch keeps the mature game.
- Discord application id, support server, description, tags, and install URL.
- A test guild id if you want fast command sync.
- `PUBLIC_BASE_URL`, `DATABASE_URL`, Discord, Stripe, and Venice values for the host you pick. This work does not pick the host.
- Confirmation that the Stripe prices match $19 and $99.
- Whether legacy `!` commands should be on in production. Default is off.
