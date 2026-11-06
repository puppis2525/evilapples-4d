# Invite-only distribution, not App Directory discovery

Checked November 6, 2026 against Discord's published docs. The owner decision is to keep the mature game and share it by a guild-install invite link. Do not enable App Directory discovery and do not remove age-restricted command flags to pursue a listing. This note is not an approval, a submission, or a legal opinion. No application was sent to Discord, and no invite was created or submitted.

An invite link does not exempt the app from Discord's Developer Policy, Community Guidelines, or content rules. The blockers below are why discovery stays off. They can still matter for an app that is only shared by link.

## Why discovery stays off

These come from the docs below and from this repository. They are not a Discord decision, and they are not cleared by distributing the bot outside the directory.

1. **Age-restricted content.** The App Directory content policy says a discoverable app's content must not include sexually explicit text, violent content, or illegal, dangerous, and regulated goods such as alcohol or drug use. `classic_cards.json` and the seeded deck names include that kind of material. The AI judge prompt tells the model to reward shock value. Turning `MATURE_CONTENT_ENFORCEMENT` off does not remove the cards.
2. **Age-restricted commands.** Discord's application-command docs say an app with discovery enabled cannot contain age-restricted commands or content. This build keeps gameplay slash commands `nsfw: true` when enforcement is `nsfw_channel` (the default). That flag stays. It is the control for mature play, and it is also why discovery must stay off.
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

Slash commands do not need the message content intent. The content blockers above are why this app is not Directory-discoverable. They are not solved by an invite link.

## Guild install link

The install URL is OAuth, not an App Directory listing. This repository has no application id. Use the Application ID from the Developer Portal General Information page. That value is `DISCORD_CLIENT_ID` after you copy it into the environment. The placeholder below is not a real id.

```text
https://discord.com/oauth2/authorize?client_id=YOUR_DISCORD_APPLICATION_ID&permissions=19456&integration_type=0&scope=bot%20applications.commands
```

- `integration_type=0` is a guild install ([application integration types](https://discord.com/developers/docs/resources/application#application-object-application-integration-types)).
- Scopes are `bot` and `applications.commands` only ([OAuth2 scopes](https://discord.com/developers/docs/topics/oauth2#shared-resources-oauth2-scopes) and the [bot authorization flow](https://discord.com/developers/docs/topics/oauth2#bot-authorization-flow)). Do not add `identify`, `email`, `guilds`, `guilds.members.read`, or `messages.read` on this link. Website login separately uses `identify`.
- `permissions=19456` is View Channel (1024) + Send Messages (2048) + Embed Links (16384). That is enough for slash-mode embeds and buttons. Read Message History is not included. Add it only together with the Message Content intent if legacy `!` commands are turned on.

Manual Developer Portal decisions this repo does not perform:

- Leave Discovery off. Do not submit the listing.
- Keep the age-restricted command behavior. Do not clear `nsfw` to seek discovery.
- Public Bot, if enabled, allows another server admin to open the invite. That is still not Directory discovery. If it stays off, only the app team can add the bot.
- Guild install on, user install off.
- Privileged intents off unless the legacy prefix flag is intentionally enabled, in which case Message Content only.
- Support server, description, privacy URL, and terms URL remain optional portal fields. Set them in the portal if you want them on the app profile. They are not written by this code.

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

The three failures are `TestRateLimiterDBBehavior` in `tests/test_rate_limiter.py`. They expect `RateLimiter.is_allowed` to query `rate_limit_entries` and to fail closed on the strict tier when the database raises. `server.py` keeps the buckets in process memory and does not call the database. The privacy page describes the in-memory limiter on purpose.

### Rate-limiter baseline, same three cases

Compared on November 6, 2026 in this VM. The rate limiter and `tests/test_rate_limiter.py` were not edited to change the result. Mature-content enforcement was not changed.

| | SHA | Worktree |
| --- | --- | --- |
| PR base (`origin/main`) | `b572ae756beff60569efd16f266c4917ed89e3e7` | `/tmp/evilapples-base-b572ae7` (detached) |
| Code under test | `bc70f62f6aa847e893ed9a067d11a8fca1e29a98` | `/workspace` at the time of the pytest and Postgres runs. The commit that added this section does not change `server.py` or the rate-limiter test. |

`git diff` of `class RateLimiter` through `rate_limiter = RateLimiter()` between those SHAs was empty. `git hash-object tests/test_rate_limiter.py` was `62a9a0bcba32b8ccc87fc86dc157b3fd18490f44` on both, and `cmp` said the files were identical.

Same interpreter and packages for both runs: Python 3.12, FastAPI 0.135.1, Starlette 0.52.1, pytest 9.2.0, asyncpg 0.32.0, stripe 16.1.0. `SESSION_SECRET` was the test placeholder. `DATABASE_URL` was empty. No Discord, Stripe, or Venice credentials.

```text
cd /tmp/evilapples-base-b572ae7
SESSION_SECRET=test_secret_key_for_testing_only_1234567890abcdef DATABASE_URL= \
  python3 -m pytest \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_strict_fails_closed_on_db_error \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_blocked_when_over_limit \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_uses_atomic_cte_query \
  -q --tb=short
# 3 failed in 0.40s

cd /workspace
SESSION_SECRET=test_secret_key_for_testing_only_1234567890abcdef DATABASE_URL= \
  python3 -m pytest \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_strict_fails_closed_on_db_error \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_blocked_when_over_limit \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_uses_atomic_cte_query \
  -q --tb=short
# 3 failed in 0.41s
```

The same three cases failed on both SHAs, with the same assertions:

- `test_strict_fails_closed_on_db_error`: `assert True is False` (line 96). The in-memory limiter allows the request and never calls `db.get_db_pool`.
- `test_blocked_when_over_limit`: `assert True is False` (line 132). The mocked count of 100 is not read.
- `test_uses_atomic_cte_query`: `TypeError: 'NoneType' object is not subscriptable` (line 143). `fetchrow` was not called, so there is no SQL to inspect.

That is a pre-existing failure, not a regression on this branch.

### Local Postgres, migrations and webhooks

Run. Not a hosted or live database. PostgreSQL 16.15 was installed from Ubuntu packages and started only on this VM (`pg_ctlcluster 16 main`). Two databases, `ea_qa_base` and `ea_qa_head`, used peer auth on the local socket `postgresql:///<db>?host=/var/run/postgresql`. No password, and no Discord, Stripe, or Venice credentials. The webhook secret was the literal `whsec_local_disposable`.

Base SHA, `db.init_db()` only:

- `schema_migrations` versions: 1 through 13
- `stripe_webhook_events` does not exist (`to_regclass` returned null)

Head SHA, FastAPI `TestClient` startup (which calls `init_db`) against `ea_qa_head`:

- versions 1 through 14, including migration 14
- table `stripe_webhook_events` exists
- Discord bot startup was skipped because no token was set
- store seeding read local files only and inserted 58 decks into this disposable database

Signed webhook posts to `/api/stripe/webhook` on that local app:

| Request | Result |
| --- | --- |
| Unknown event, bad signature | HTTP 400 `Invalid webhook signature`. No row. |
| `evt_local_qa_1` / `ping.unknown`, valid signature | HTTP 200 `{"received": true}` |
| Same event posted again | HTTP 200 `{"received": true, "duplicate": true}` |
| Rows after those posts | one row: `evt_local_qa_1` / `ping.unknown` |

A `checkout.session.completed` payload did not reach premium activation on that pass. With stripe-python 16.1.0, `stripe.Webhook.construct_event` returns a `StripeObject`, and `session.get(...)` raises `AttributeError`. That incompatibility is fixed later on this branch; see the Stripe SDK 16 section below. `pyproject.toml` still says `stripe>=14.4.1` and does not pin a version.

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
- Confirm in the Developer Portal, by hand: Discovery stays off, Public Bot is whatever you intend for invite sharing, and guild install is allowed. This repository does not make those clicks.
- The age-restricted gameplay flag stays. Do not remove it to pursue discovery.
- Discord application id, support server, description, tags, and install URL.
- A test guild id if you want fast command sync.
- `PUBLIC_BASE_URL`, `DATABASE_URL`, Discord, Stripe, and Venice values for the host you pick. This work does not pick the host.
- Confirmation that the Stripe prices match $19 and $99.
- Whether legacy `!` commands should be on in production. Default is off.

## Stripe SDK 16 premium entitlement

Fixed on this branch without pinning stripe-python. `stripe.Webhook.construct_event` returns a `StripeObject`. Current stripe-python does not subclass `dict`, and `.get` raises. The documented conversion is `StripeObject.to_dict()` ([working with API resources](https://github.com/stripe/stripe-python#working-with-api-resources)). `as_stripe_dict()` does that for webhook events, expanded Checkout Sessions, Account retrieve, and PaymentIntent retrieve.

Premium activation no longer uses `metadata.plan`. The granted plan is the single configured Price id on the session line items, or on `Session.retrieve(..., expand=["line_items", "subscription"])` when the event omits them. Metadata that names a different plan is ignored. An unknown Price, an unpaid session, or a guild id that is not 17–20 digits does not activate. `customer.subscription.updated` will not reactivate a subscription whose Price is present and not one of the configured ids.

Checked with stripe-python 16.1.0, FastAPI 0.135.1, Starlette 0.52.1, and disposable local Postgres 16 (`ea_qa_stripe16` on the local socket only; no Stripe secret key and no network call):

```text
python3 -m pytest tests/test_stripe_sdk16_webhook.py \
  tests/test_directory_prep.py::TestWebhookHttp::test_signature_duplicate_and_failure \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_strict_fails_closed_on_db_error \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_blocked_when_over_limit \
  tests/test_rate_limiter.py::TestRateLimiterDBBehavior::test_uses_atomic_cte_query \
  -q --tb=line
# 11 passed, 3 failed in 20.67s
```

The 11 passes include: a signed `checkout.session.completed` whose metadata says `overlord` while the Price id is the configured Schemer price, and the database row is `schemer`; a duplicate event calls `activate_premium` once; a raised activation returns HTTP 500 without the exception text, the retry activates, and a third post is `duplicate: true`; invalid signature is HTTP 400 with no premium row; short guild id, unknown Price, and unpaid session do not activate; retrieve supplies the Price when the event has no line items; `customer.subscription.deleted` clears `active`.

The 3 failures are the same pre-existing rate-limiter cases as the base SHA `b572ae756beff60569efd16f266c4917ed89e3e7`. This change does not touch `RateLimiter` or `tests/test_rate_limiter.py`.

A full `python3 -m pytest tests -q --tb=line` on the entitlement code, before the in-memory webhook fixture was given `payment_status: paid` and a configured Price id, was **137 passed, 4 failed** in 461.54s. The extra failure was that fixture still expecting HTTP 500 for an unpaid session. After that fixture update, the targeted rerun above is the result. The other suite cases were not started again.

Still unresolved for Stripe:

- Tournament entry, deck purchase, and Telegram group unlock still select the record from metadata written by our checkout code. Those charges use one-off `price_data`, not the Schemer/Overlord Price ids, so there is no catalog Price to map. The metadata is on the signed event, not a raw client body.
- If line items are absent, a failed `Session.retrieve` is treated as a retryable handler error. That path was mocked here. No live Stripe payment was made.
- Mature-content enforcement was not changed.
