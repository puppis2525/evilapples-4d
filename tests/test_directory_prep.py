"""Offline checks for slash commands, age gating, config, and Stripe.

These tests do not call Discord, Stripe, or Venice.
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
import pytest
from discord.ext import commands

import bot as gamebot
from access_checks import can_end_game, can_end_tournament
from app_config import (
    mature_play_allowed,
    message_content_intent_enabled,
    public_base_url,
    should_start_discord_bot,
)
from slash_commands import (
    EXPECTED_SLASH_COMMANDS,
    MANAGE_GUILD_COMMANDS,
    _invoke,
    describe_slash_commands,
    register_slash_commands,
)
from stripe_client import (
    checkout_idempotency_key,
    get_stripe_credentials,
    stable_idempotency_key,
)
from stripe_events import (
    begin_webhook_event,
    complete_webhook_event,
    release_webhook_event,
    reset_webhook_idempotency_for_tests,
)


def _run(coro):
    return asyncio.run(coro)


class Perms:
    def __init__(self, **flags):
        for key, value in flags.items():
            setattr(self, key, value)

    def __getattr__(self, name):
        return False


class Ctx:
    def __init__(self, author_id=10, name="Ada", channel_id=7, nsfw=True, perms=None):
        self.sent = []
        self.guild = SimpleNamespace(id=555)
        self.author = SimpleNamespace(
            id=author_id,
            display_name=name,
            mention=f"<@{author_id}>",
            guild_permissions=perms if perms is not None else Perms(),
        )
        self.channel = SimpleNamespace(id=channel_id, nsfw=nsfw, guild=self.guild)
        self.invoked_subcommand = None
        self.command = SimpleNamespace(qualified_name="startgame")
        self.message = SimpleNamespace(attachments=[], content="")

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class Resp:
    def __init__(self):
        self.done = False
        self.messages = []

    def is_done(self):
        return self.done

    async def defer(self, *args, **kwargs):
        self.done = True

    async def send_message(self, content=None, **kwargs):
        self.done = True
        self.messages.append(("response", content, kwargs))


class Follow:
    def __init__(self, bucket):
        self.bucket = bucket

    async def send(self, content=None, **kwargs):
        self.bucket.append(("follow", content, kwargs))


class Interaction:
    def __init__(self, nsfw=True):
        self.response = Resp()
        self.followup = Follow(self.response.messages)
        self.user = SimpleNamespace(id=42, display_name="Eve", mention="@Eve", guild_permissions=Perms())
        self.guild = SimpleNamespace(id=9)
        self.channel = SimpleNamespace(id=3, nsfw=nsfw, guild=self.guild)


def _sign(payload: bytes, secret: str) -> str:
    timestamp = str(int(time.time()))
    signed = f"{timestamp}.{payload.decode()}"
    digest = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class TestConfig:
    def test_legacy_prefix_defaults_off(self, monkeypatch):
        monkeypatch.delenv("ENABLE_LEGACY_PREFIX_COMMANDS", raising=False)
        assert message_content_intent_enabled() is False

    def test_legacy_prefix_flag(self, monkeypatch):
        monkeypatch.setenv("ENABLE_LEGACY_PREFIX_COMMANDS", "true")
        assert message_content_intent_enabled() is True

    def test_imported_bot_does_not_request_privileged_intents(self):
        assert gamebot.bot.intents.message_content is False
        assert gamebot.bot.intents.members is False
        assert gamebot.bot.intents.presences is False

    def test_public_base_url_prefers_explicit_value(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://play.example")
        request = SimpleNamespace(headers={"host": "internal:5000", "x-forwarded-proto": "http"})
        assert public_base_url(request) == "https://play.example"

    def test_public_base_url_uses_request_host(self, monkeypatch):
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        request = SimpleNamespace(headers={"host": "localhost:5000", "x-forwarded-proto": ""})
        assert public_base_url(request) == "http://localhost:5000"

    def test_bot_starts_only_with_token_outside_replit_dev(self, monkeypatch):
        monkeypatch.delenv("DISABLE_DISCORD_BOT", raising=False)
        monkeypatch.delenv("REPLIT_DEPLOYMENT", raising=False)
        monkeypatch.delenv("REPLIT_DEV_DOMAIN", raising=False)
        monkeypatch.delenv("DISCORD_TOKEN", raising=False)
        assert should_start_discord_bot() is False
        monkeypatch.setenv("DISCORD_TOKEN", "not-a-real-token")
        assert should_start_discord_bot() is True
        monkeypatch.setenv("REPLIT_DEV_DOMAIN", "dev.example")
        monkeypatch.delenv("REPLIT_DEPLOYMENT", raising=False)
        assert should_start_discord_bot() is False
        monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
        assert should_start_discord_bot() is True
        monkeypatch.setenv("DISABLE_DISCORD_BOT", "true")
        assert should_start_discord_bot() is False

    def test_env_example_has_no_live_values(self):
        example = open(".env.example", encoding="utf-8").read()
        replit = open(".replit", encoding="utf-8").read()
        assert "sk_live" not in example
        assert "sk_test" not in example
        assert "whsec_" not in example
        assert "price_1" not in example
        for line in replit.splitlines():
            if "price_" in line and "=" in line:
                value = line.split("=", 1)[1].strip().strip('"')
                assert value not in example
                assert value not in open("README.md", encoding="utf-8").read()


class TestSlashCommands:
    def test_registered_names_permissions_and_nsfw(self):
        rows = describe_slash_commands(gamebot.bot)
        assert [row["name"] for row in rows] == sorted(EXPECTED_SLASH_COMMANDS)
        for row in rows:
            if row["name"] in ("help", "premium"):
                assert row["nsfw"] is False
            else:
                assert row["nsfw"] is True
            if row["name"] in MANAGE_GUILD_COMMANDS:
                assert row["default_permissions"].manage_guild is True
            else:
                assert row["default_permissions"] is None
            if row["name"] == "help":
                assert row["guild_only"] is False
            else:
                assert row["guild_only"] is True

    def test_nsfw_flag_follows_enforcement_switch(self, monkeypatch):
        monkeypatch.setenv("MATURE_CONTENT_ENFORCEMENT", "off")
        fresh = commands.Bot(command_prefix="!", intents=__import__("discord").Intents.default(), help_command=None)
        register_slash_commands(fresh)
        rows = describe_slash_commands(fresh)
        assert rows
        assert all(row["nsfw"] is False for row in rows)

    def test_slash_dispatch_calls_handler_and_blocks_sfw_channel(self):
        seen = {}

        async def handler(ctx, mode="friend"):
            seen["mode"] = mode
            seen["author"] = ctx.author.id
            await ctx.send("started")

        allowed = Interaction(nsfw=True)
        _run(_invoke(allowed, handler, "swap"))
        assert seen["mode"] == "swap"
        assert seen["author"] == 42
        assert any(item[0] == "follow" and item[1] == "started" for item in allowed.response.messages)

        seen.clear()
        blocked = Interaction(nsfw=False)
        _run(_invoke(blocked, handler, "friend"))
        assert seen == {}
        assert blocked.response.messages[0][0] == "response"
        assert "Age-Restricted" in blocked.response.messages[0][1]


class TestAgeAndPermissions:
    def test_prefix_gate_blocks_sfw_channel(self, monkeypatch):
        monkeypatch.delenv("MATURE_CONTENT_ENFORCEMENT", raising=False)
        ctx = Ctx(nsfw=False)
        with pytest.raises(commands.CheckFailure):
            _run(gamebot.enforce_mature_channel(ctx))
        assert ctx.sent
        assert "Age-Restricted" in ctx.sent[0][0][0]

    def test_prefix_gate_allows_nsfw_and_skips_help(self, monkeypatch):
        monkeypatch.setenv("MATURE_CONTENT_ENFORCEMENT", "nsfw_channel")
        ctx = Ctx(nsfw=True)
        _run(gamebot.enforce_mature_channel(ctx))
        assert ctx.sent == []
        ctx.command = SimpleNamespace(qualified_name="help")
        ctx.channel.nsfw = False
        _run(gamebot.enforce_mature_channel(ctx))
        assert ctx.sent == []

    def test_enforcement_off_allows_any_channel(self, monkeypatch):
        monkeypatch.setenv("MATURE_CONTENT_ENFORCEMENT", "off")
        ok, message = mature_play_allowed(SimpleNamespace(nsfw=False), source="prefix")
        assert ok is True
        assert message == ""

    def test_end_game_permissions(self):
        assert can_end_game(1, 1, Perms()) is True
        assert can_end_game(2, 1, Perms()) is False
        assert can_end_game(2, 1, Perms(manage_messages=True)) is True
        assert can_end_tournament(5, 5, Perms()) is True
        assert can_end_tournament(6, 5, Perms()) is False
        assert can_end_tournament(6, 5, Perms(manage_guild=True)) is True


class TestGameplay:
    @pytest.fixture
    def isolated(self, monkeypatch):
        gamebot.active_games.clear()

        async def noop(*args, **kwargs):
            return None

        async def zero(*args, **kwargs):
            return 0

        monkeypatch.setattr(gamebot, "_persist_game", noop)
        monkeypatch.setattr(gamebot, "_remove_game", noop)
        monkeypatch.setattr(gamebot, "get_user_deck_count", zero)
        yield
        gamebot.active_games.clear()

    def test_start_join_and_end(self, isolated):
        host = Ctx(author_id=10, name="Host")
        _run(gamebot.start_game(host, "friend"))
        assert host.channel.id in gamebot.active_games
        assert gamebot.active_games[host.channel.id]["host_id"] == "10"
        assert any("FRIEND" in (item[1].get("embed").title if item[1].get("embed") else "") or "Evil Apples" in str(item) for item in host.sent)

        again = Ctx(author_id=10, channel_id=8)
        _run(gamebot.start_game(again, "nope"))
        assert "Unknown game mode" in again.sent[0][0][0]

        stranger = Ctx(author_id=11, name="Sam", perms=Perms())
        _run(gamebot.join_game(stranger))
        assert "11" in gamebot.active_games[host.channel.id]["players"]

        _run(gamebot.end_game(stranger))
        assert host.channel.id in gamebot.active_games
        assert "Manage Messages" in stranger.sent[-1][0][0]

        mod = Ctx(author_id=12, perms=Perms(manage_messages=True))
        _run(gamebot.end_game(mod))
        assert host.channel.id not in gamebot.active_games

    def test_round_win_ends_the_game(self, isolated):
        channel = SimpleNamespace(id=77, guild=None, sent=[])

        async def send(*args, **kwargs):
            channel.sent.append((args, kwargs))

        channel.send = send
        game = {
            "players": {"1": "Ada", "2": "Bea"},
            "scores": {"1": 6, "2": 1},
            "win_score": 7,
            "warn_score": 5,
            "state": "judging",
            "game_mode": "friend",
        }
        gamebot.active_games[77] = game
        ended = _run(gamebot.handle_round_winner(game, "1", "Ada", channel))
        assert ended is True
        assert game["state"] == "game_over"
        assert 77 not in gamebot.active_games


class TestStripePortable:
    def test_missing_keys_fail_without_replit(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        monkeypatch.delenv("STRIPE_PUBLISHABLE_KEY", raising=False)
        monkeypatch.delenv("REPLIT_CONNECTORS_HOSTNAME", raising=False)
        monkeypatch.delenv("REPL_IDENTITY", raising=False)
        monkeypatch.delenv("WEB_REPL_RENEWAL", raising=False)
        with pytest.raises(Exception, match="STRIPE_SECRET_KEY"):
            _run(get_stripe_credentials())

    def test_partial_keys_do_not_fall_through(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_only")
        monkeypatch.delenv("STRIPE_PUBLISHABLE_KEY", raising=False)
        with pytest.raises(Exception, match="both"):
            _run(get_stripe_credentials())

    def test_env_keys_skip_the_network(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
        monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_example")

        def explode(*args, **kwargs):
            raise AssertionError("network client should not be constructed")

        monkeypatch.setattr("aiohttp.ClientSession", explode)
        creds = _run(get_stripe_credentials())
        assert creds["source"] == "env"
        assert creds["secret_key"] == "sk_test_example"

    def test_idempotency_keys_do_not_echo_inputs(self):
        secret = "sk_test_super_secret_value"
        first = checkout_idempotency_key("premium", secret, "guild")
        second = checkout_idempotency_key("premium", secret, "guild")
        other = checkout_idempotency_key("premium", secret, "other")
        assert first == second
        assert first != other
        assert secret not in first
        assert secret not in stable_idempotency_key("refund", secret)

    def test_checkout_passes_idempotency_key(self, monkeypatch):
        import stripe_routes
        stripe_routes.PLAN_PRICE_MAP["schemer"] = "price_test_schemer"
        captured = {}

        class SessionApi:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(url="https://checkout.test/session")

        class StripeMod:
            checkout = SimpleNamespace(Session=SessionApi)

        async def client():
            return StripeMod

        class Req:
            headers = {"host": "pay.example", "x-forwarded-proto": "https"}

            async def json(self):
                return {
                    "guild_id": "123456789012345678",
                    "guild_name": "Server",
                    "user_id": "223456789012345678",
                    "user_name": "Ada",
                    "plan": "schemer",
                }

        monkeypatch.setattr(stripe_routes, "get_stripe_client", client)
        response = _run(stripe_routes.create_checkout(Req()))
        assert response.status_code == 200
        assert captured["idempotency_key"].startswith("ea_")
        assert "sk_" not in captured["idempotency_key"]
        assert captured["metadata"]["plan"] == "schemer"

    def test_memory_idempotency_and_release(self):
        reset_webhook_idempotency_for_tests()
        assert _run(begin_webhook_event("evt_a", "ping")) == "new"
        assert _run(begin_webhook_event("evt_a", "ping")) == "duplicate"
        _run(release_webhook_event("evt_a"))
        assert _run(begin_webhook_event("evt_a", "ping")) == "new"
        _run(complete_webhook_event("evt_a"))
        assert _run(begin_webhook_event("evt_a", "ping")) == "duplicate"
        reset_webhook_idempotency_for_tests()


class TestWebhookHttp:
    def test_signature_duplicate_and_failure(self, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_only")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        reset_webhook_idempotency_for_tests()
        from server import app
        from fastapi.testclient import TestClient
        import stripe_routes

        payload = json.dumps({
            "id": "evt_unknown_1",
            "type": "ping.unknown",
            "data": {"object": {}},
        }).encode()
        bad = _sign(payload, "wrong")
        good = _sign(payload, "whsec_test_only")

        with TestClient(app, raise_server_exceptions=False) as client:
            rejected = client.post(
                "/api/stripe/webhook",
                content=payload,
                headers={"stripe-signature": bad},
            )
            assert rejected.status_code == 400
            assert "signature" in rejected.json()["error"].lower()

            first = client.post(
                "/api/stripe/webhook",
                content=payload,
                headers={"stripe-signature": good},
            )
            second = client.post(
                "/api/stripe/webhook",
                content=payload,
                headers={"stripe-signature": good},
            )
            assert first.status_code == 200
            assert first.json().get("duplicate") is not True
            assert second.status_code == 200
            assert second.json()["duplicate"] is True

            fail_payload = json.dumps({
                "id": "evt_fail_1",
                "type": "checkout.session.completed",
                "data": {"object": {
                    "id": "cs_fail",
                    "metadata": {
                        "guild_id": "1",
                        "guild_name": "G",
                        "user_id": "2",
                        "user_name": "U",
                        "plan": "overlord",
                    },
                    "customer": "cus_test",
                    "subscription": "sub_test",
                }},
            }).encode()

            async def boom(**kwargs):
                raise RuntimeError("database unavailable")

            monkeypatch.setattr(stripe_routes, "activate_premium", boom)
            fail_sig = _sign(fail_payload, "whsec_test_only")
            failed = client.post(
                "/api/stripe/webhook",
                content=fail_payload,
                headers={"stripe-signature": fail_sig},
            )
            retried = client.post(
                "/api/stripe/webhook",
                content=fail_payload,
                headers={"stripe-signature": fail_sig},
            )
            assert failed.status_code == 500
            assert retried.status_code == 500
            assert retried.json().get("duplicate") is not True
            assert "database unavailable" not in retried.text

        reset_webhook_idempotency_for_tests()


class TestLegalPages:
    def test_privacy_and_terms_match_the_implementation(self):
        from server import app
        from fastapi.testclient import TestClient
        with TestClient(app, raise_server_exceptions=False) as client:
            privacy = client.get("/privacy")
            terms = client.get("/terms")
            health = client.get("/health")
        assert privacy.status_code == 200
        assert terms.status_code == 200
        body = privacy.text + terms.text
        assert "Owner confirmation required" in body
        assert "identify" in privacy.text
        assert "Venice" in privacy.text
        assert "Telegram" in privacy.text
        assert "Replit's infrastructure" not in privacy.text
        assert "support@evilapples4d.com" in body
        assert "unconfirmed" in body.lower() or "Unconfirmed" in body or "not verified" in body or "unverified" in body
        assert "Age-Restricted" in terms.text
        assert health.status_code in (200, 503)
        assert health.json()["service"] == "evil-apples-4d"

    def test_telegram_templates_render(self):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader("telegram/templates"))
        privacy = env.get_template("privacy.html").render(bot_username="EvilApplesBot", active_page="privacy")
        terms = env.get_template("terms.html").render(bot_username="EvilApplesBot", active_page="terms")
        assert "Privacy" in privacy
        assert "Venice" in privacy
        assert "$4.99" in terms
        assert 'href="/privacy"' in env.get_template("base.html").render(bot_username="EvilApplesBot", active_page="")

    def test_migration_records_webhook_ids(self):
        from migrations import MIGRATIONS
        migration = [item for item in MIGRATIONS if item["version"] == 14][0]
        sql = " ".join(migration["sql"])
        assert "stripe_webhook_events" in sql
        assert "event_id" in sql
