"""stripe-python 16 webhook compatibility and premium Price entitlement.

Uses Stripe's own event parser and, when a local Postgres socket is up,
the real premium table. No live Stripe, Discord, or Venice calls.
"""
import hashlib
import hmac
import json
import os
import time

import asyncpg
import pytest
import stripe

from stripe_client import as_stripe_dict, stripe_id
import stripe_routes
from stripe_events import reset_webhook_idempotency_for_tests

LOCAL_DSN = "postgresql:///ea_qa_stripe16?host=/var/run/postgresql"
WEBHOOK_SECRET = "whsec_local_disposable"
SCHEMER = "price_test_schemer"
OVERLORD = "price_test_overlord"
GUILD = "123456789012345678"
GUILD_RETRIEVE = "123456789012345679"
GUILD_FAIL = "123456789012345680"


def sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    timestamp = str(int(time.time()))
    signed = f"{timestamp}.{payload.decode()}"
    digest = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def event_bytes(event_id: str, event_type: str, obj: dict) -> bytes:
    return json.dumps({
        "id": event_id,
        "object": "event",
        "type": event_type,
        "data": {"object": obj},
    }).encode()


def premium_session(event_id: str, *, price_id: str, claimed_plan: str, guild_id: str = GUILD,
                    payment_status: str = "paid", include_items: bool = True,
                    session_id: str = "cs_prem_1", subscription_id: str = "sub_prem_1") -> bytes:
    session = {
        "id": session_id,
        "object": "checkout.session",
        "mode": "subscription",
        "payment_status": payment_status,
        "customer": "cus_prem_1",
        "subscription": subscription_id,
        "metadata": {
            "guild_id": guild_id,
            "guild_name": "QA Guild",
            "user_id": "223456789012345678",
            "user_name": "Ada",
            "plan": claimed_plan,
        },
    }
    if include_items:
        session["line_items"] = {
            "object": "list",
            "data": [{"id": "li_1", "price": {"id": price_id, "object": "price"}}],
        }
    return event_bytes(event_id, "checkout.session.completed", session)


def post_event(client, payload: bytes, secret: str = WEBHOOK_SECRET):
    return client.post(
        "/api/stripe/webhook",
        content=payload,
        headers={"stripe-signature": sign(payload, secret)},
    )


async def premium_row(guild_id: str):
    conn = await asyncpg.connect(LOCAL_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT plan, active, stripe_subscription_id, stripe_customer_id FROM premium_servers WHERE guild_id = $1",
            guild_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def event_ids():
    conn = await asyncpg.connect(LOCAL_DSN)
    try:
        rows = await conn.fetch("SELECT event_id FROM stripe_webhook_events ORDER BY event_id")
        return [row["event_id"] for row in rows]
    finally:
        await conn.close()


async def wipe_premium_state():
    conn = await asyncpg.connect(LOCAL_DSN)
    try:
        await conn.execute("DELETE FROM stripe_webhook_events")
        await conn.execute("DELETE FROM premium_servers")
    finally:
        await conn.close()
    reset_webhook_idempotency_for_tests()


def postgres_up() -> bool:
    import asyncio

    async def ping():
        conn = await asyncpg.connect(LOCAL_DSN)
        await conn.close()

    try:
        asyncio.run(ping())
        return True
    except Exception:
        return False


@pytest.fixture
def price_map():
    saved = dict(stripe_routes.PLAN_PRICE_MAP)
    stripe_routes.PLAN_PRICE_MAP.clear()
    stripe_routes.PLAN_PRICE_MAP["schemer"] = SCHEMER
    stripe_routes.PLAN_PRICE_MAP["overlord"] = OVERLORD
    yield
    stripe_routes.PLAN_PRICE_MAP.clear()
    stripe_routes.PLAN_PRICE_MAP.update(saved)


class TestStripeObjectAccess:
    def test_sdk_event_is_not_a_dict_and_get_raises(self):
        payload = premium_session("evt_unit_1", price_id=SCHEMER, claimed_plan="overlord")
        event = stripe.Webhook.construct_event(payload, sign(payload), WEBHOOK_SECRET)
        session = event["data"]["object"]
        assert not isinstance(event, dict)
        with pytest.raises(AttributeError):
            session.get("metadata", {})
        plain = as_stripe_dict(event)
        assert plain["data"]["object"]["line_items"]["data"][0]["price"]["id"] == SCHEMER
        assert plain["data"]["object"]["metadata"]["plan"] == "overlord"

    def test_payment_intent_and_account_ids(self):
        payment = stripe.PaymentIntent.construct_from(
            {"id": "pi_1", "latest_charge": {"id": "ch_1", "object": "charge"}},
            "sk_test_x",
        )
        account = stripe.Account.construct_from(
            {"id": "acct_1", "charges_enabled": True},
            "sk_test_x",
        )
        assert stripe_id(as_stripe_dict(payment).get("latest_charge")) == "ch_1"
        assert as_stripe_dict(account).get("charges_enabled") is True

    def test_price_map_ignores_metadata_plan_string(self, price_map):
        session = as_stripe_dict(stripe.checkout.Session.construct_from({
            "id": "cs_map",
            "metadata": {"plan": "overlord"},
            "line_items": {"data": [{"price": {"id": SCHEMER}}]},
        }, "sk_test_x"))
        assert stripe_routes.plan_for_price_ids(
            stripe_routes.price_ids_from_stripe_object(session)
        ) == "schemer"

    def test_unknown_and_ambiguous_prices_grant_nothing(self, price_map):
        assert stripe_routes.plan_for_price_ids(["price_other"]) is None
        assert stripe_routes.plan_for_price_ids([SCHEMER, OVERLORD]) is None


@pytest.fixture(scope="module")
def sdk_client():
    if not postgres_up():
        pytest.skip("local disposable Postgres database ea_qa_stripe16 is not running")
    import db
    old_url = db.DATABASE_URL
    old_env = os.environ.get("DATABASE_URL")
    old_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    os.environ["DATABASE_URL"] = LOCAL_DSN
    os.environ["STRIPE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    os.environ["DISABLE_DISCORD_BOT"] = "true"
    os.environ.pop("DISCORD_TOKEN", None)
    db.DATABASE_URL = LOCAL_DSN
    db._pools.clear()
    from fastapi.testclient import TestClient
    from server import app
    with TestClient(app) as client:
        yield client
    db.DATABASE_URL = old_url
    db._pools.clear()
    if old_env is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old_env
    if old_secret is None:
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    else:
        os.environ["STRIPE_WEBHOOK_SECRET"] = old_secret
    reset_webhook_idempotency_for_tests()


@pytest.fixture
def clean_state(sdk_client):
    import asyncio
    asyncio.run(wipe_premium_state())
    return sdk_client


class TestPremiumWebhookAgainstPostgres:
    def test_signed_completion_uses_price_not_metadata(self, clean_state, price_map):
        import asyncio
        payload = premium_session(
            "evt_prem_once", price_id=SCHEMER, claimed_plan="overlord",
        )
        response = post_event(clean_state, payload)
        assert response.status_code == 200
        assert response.json().get("duplicate") is not True
        row = asyncio.run(premium_row(GUILD))
        assert row["plan"] == "schemer"
        assert row["active"] is True
        assert row["stripe_subscription_id"] == "sub_prem_1"
        assert row["stripe_customer_id"] == "cus_prem_1"

    def test_duplicate_event_does_not_apply_twice(self, clean_state, price_map):
        import asyncio
        calls = {"n": 0}
        real = stripe_routes.activate_premium

        async def counting(**kwargs):
            calls["n"] += 1
            return await real(**kwargs)

        stripe_routes.activate_premium = counting
        try:
            payload = premium_session(
                "evt_prem_dup", price_id=OVERLORD, claimed_plan="schemer",
                guild_id=GUILD, session_id="cs_dup", subscription_id="sub_dup",
            )
            first = post_event(clean_state, payload)
            second = post_event(clean_state, payload)
        finally:
            stripe_routes.activate_premium = real
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert calls["n"] == 1
        row = asyncio.run(premium_row(GUILD))
        assert row["plan"] == "overlord"
        assert asyncio.run(event_ids()).count("evt_prem_dup") == 1

    def test_failure_releases_and_retry_activates_once(self, clean_state, price_map):
        import asyncio
        real = stripe_routes.activate_premium
        state = {"fail": True}

        async def flaky(**kwargs):
            if state["fail"]:
                state["fail"] = False
                raise RuntimeError("simulated database blip")
            return await real(**kwargs)

        stripe_routes.activate_premium = flaky
        try:
            payload = premium_session(
                "evt_prem_retry", price_id=SCHEMER, claimed_plan="schemer",
                guild_id=GUILD_FAIL, session_id="cs_fail", subscription_id="sub_fail",
            )
            failed = post_event(clean_state, payload)
            retried = post_event(clean_state, payload)
            third = post_event(clean_state, payload)
        finally:
            stripe_routes.activate_premium = real
        assert failed.status_code == 500
        assert "blip" not in failed.text
        assert retried.status_code == 200
        assert retried.json().get("duplicate") is not True
        assert third.status_code == 200
        assert third.json()["duplicate"] is True
        row = asyncio.run(premium_row(GUILD_FAIL))
        assert row["plan"] == "schemer"
        assert row["active"] is True

    def test_invalid_signature_and_bad_metadata_do_not_activate(self, clean_state, price_map):
        import asyncio
        payload = premium_session("evt_prem_bad_sig", price_id=OVERLORD, claimed_plan="overlord")
        rejected = post_event(clean_state, payload, secret="wrong-secret")
        assert rejected.status_code == 400
        assert "signature" in rejected.json()["error"].lower()

        missing_guild = premium_session(
            "evt_prem_no_guild", price_id=OVERLORD, claimed_plan="overlord",
            guild_id="", session_id="cs_noguild",
        )
        # guild_id "" fails the digit check; rebuild with a short id via raw event
        raw = json.loads(missing_guild)
        raw["data"]["object"]["metadata"]["guild_id"] = "42"
        missing_guild = json.dumps(raw).encode()
        missing = post_event(clean_state, missing_guild)
        assert missing.status_code == 200

        unknown = premium_session(
            "evt_prem_unknown_price", price_id="price_not_configured", claimed_plan="overlord",
            guild_id=GUILD, session_id="cs_unknown",
        )
        unknown_response = post_event(clean_state, unknown)
        assert unknown_response.status_code == 200

        unpaid = premium_session(
            "evt_prem_unpaid", price_id=OVERLORD, claimed_plan="overlord",
            payment_status="unpaid", session_id="cs_unpaid",
        )
        unpaid_response = post_event(clean_state, unpaid)
        assert unpaid_response.status_code == 200

        assert asyncio.run(premium_row(GUILD)) is None
        stored = asyncio.run(event_ids())
        assert "evt_prem_bad_sig" not in stored
        assert "evt_prem_no_guild" in stored
        assert "evt_prem_unknown_price" in stored
        assert "evt_prem_unpaid" in stored

    def test_retrieve_supplies_price_when_event_omits_line_items(self, clean_state, price_map, monkeypatch):
        import asyncio
        retrieved = stripe.checkout.Session.construct_from({
            "id": "cs_retrieve",
            "object": "checkout.session",
            "line_items": {
                "object": "list",
                "data": [{"id": "li_r", "price": {"id": OVERLORD, "object": "price"}}],
            },
        }, "sk_test_x")

        class SessionApi:
            @staticmethod
            def retrieve(session_id, expand=None):
                assert session_id == "cs_retrieve"
                assert "line_items" in (expand or [])
                return retrieved

        class StripeApi:
            checkout = type("Checkout", (), {"Session": SessionApi})

        async def client():
            return StripeApi

        monkeypatch.setattr(stripe_routes, "get_stripe_client", client)
        payload = premium_session(
            "evt_prem_retrieve", price_id=SCHEMER, claimed_plan="schemer",
            guild_id=GUILD_RETRIEVE, include_items=False,
            session_id="cs_retrieve", subscription_id="sub_retrieve",
        )
        response = post_event(clean_state, payload)
        assert response.status_code == 200
        row = asyncio.run(premium_row(GUILD_RETRIEVE))
        assert row["plan"] == "overlord"

    def test_subscription_deleted_uses_sdk_object(self, clean_state, price_map):
        import asyncio
        created = premium_session(
            "evt_prem_for_delete", price_id=SCHEMER, claimed_plan="schemer",
            session_id="cs_del", subscription_id="sub_del",
        )
        assert post_event(clean_state, created).status_code == 200
        deleted = event_bytes("evt_sub_deleted", "customer.subscription.deleted", {
            "id": "sub_del",
            "object": "subscription",
            "status": "canceled",
        })
        response = post_event(clean_state, deleted)
        assert response.status_code == 200
        row = asyncio.run(premium_row(GUILD))
        assert row["active"] is False
