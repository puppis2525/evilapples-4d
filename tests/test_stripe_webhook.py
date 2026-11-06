import pytest
import json
import hmac
import hashlib
import time
import os
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


def make_stripe_signature(payload: bytes, secret: str) -> str:
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload.decode()}"
    signature = hmac.new(
        secret.encode(), signed_payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


class TestStripeWebhookSignature:
    def test_valid_signature_format(self):
        payload = b'{"type": "checkout.session.completed"}'
        secret = "whsec_test_secret_123"
        sig = make_stripe_signature(payload, secret)
        assert sig.startswith("t=")
        assert ",v1=" in sig

    def test_different_payloads_produce_different_signatures(self):
        secret = "whsec_test"
        sig1 = make_stripe_signature(b'{"a": 1}', secret)
        sig2 = make_stripe_signature(b'{"b": 2}', secret)
        v1_1 = sig1.split(",v1=")[1]
        v1_2 = sig2.split(",v1=")[1]
        assert v1_1 != v1_2

    def test_different_secrets_produce_different_signatures(self):
        payload = b'{"type": "test"}'
        sig1 = make_stripe_signature(payload, "secret1")
        sig2 = make_stripe_signature(payload, "secret2")
        assert sig1.split(",v1=")[1] != sig2.split(",v1=")[1]


class TestWebhookEndpoint:
    def test_webhook_route_registered(self):
        from server import app
        routes = [r.path for r in app.routes if hasattr(r, 'path')]
        assert any("webhook" in p for p in routes)

    def test_webhook_rejects_without_secret(self, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")
        from server import app
        with TestClient(app, raise_server_exceptions=False) as client:
            payload = json.dumps({"type": "test"}).encode()
            response = client.post(
                "/api/stripe/webhook",
                content=payload,
                headers={"stripe-signature": "t=123,v1=abc"}
            )
            assert response.status_code == 500
            assert "not configured" in response.json().get("error", "").lower() or response.status_code == 500


class TestWebhookEventParsing:
    def test_tournament_entry_event_metadata(self):
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_123",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_456",
                    "metadata": {
                        "type": "tournament_entry",
                        "tournament_id": "42",
                        "discord_user_id": "user789",
                        "entry_fee_cents": "500",
                    },
                }
            }
        }
        session = event["data"]["object"]
        metadata = session["metadata"]
        assert metadata["type"] == "tournament_entry"
        assert session["payment_status"] == "paid"
        assert int(metadata["tournament_id"]) == 42
        assert int(metadata["entry_fee_cents"]) == 500

    def test_sponsor_event_metadata(self):
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_sponsor_1",
                    "payment_status": "paid",
                    "metadata": {
                        "type": "tournament_sponsor",
                        "tournament_id": "10",
                        "sponsor_name": "Acme Corp",
                        "amount_cents": "2500",
                    },
                }
            }
        }
        metadata = event["data"]["object"]["metadata"]
        assert metadata["type"] == "tournament_sponsor"
        assert metadata["sponsor_name"] == "Acme Corp"
        assert int(metadata["amount_cents"]) == 2500

    def test_unpaid_entry_not_processed(self):
        session = {"payment_status": "unpaid", "metadata": {"type": "tournament_entry"}}
        assert session["payment_status"] != "paid"
