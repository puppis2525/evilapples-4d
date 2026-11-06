"""Stripe credentials that work outside Replit and still accept the Replit connector.

Direct keys (STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY) are preferred.
The Replit connector is used only when those keys are unset and the connector
environment is present. Secret values are not logged.
"""
import hashlib
import os
from datetime import datetime, timezone

import stripe

import logging

logger = logging.getLogger("stripe_client")


def checkout_idempotency_key(*parts) -> str:
    """Same UTC day, same parts -> same Stripe idempotency key.

    Stripe stores the key for about 24 hours, so a double-click reuses the
    Checkout Session instead of opening a second one.
    """
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    raw = "|".join([day, *[str(p) for p in parts]])
    return "ea_" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def stable_idempotency_key(*parts) -> str:
    """Key for refunds and prize transfers. Not date-bucketed."""
    raw = "|".join(str(p) for p in parts)
    return "ea_" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def _env_credentials():
    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    publishable = os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip()
    if not secret and not publishable:
        return None
    if not secret or not publishable:
        raise Exception(
            "Set both STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY, "
            "or leave both empty to use the Replit Stripe connector."
        )
    return {"publishable_key": publishable, "secret_key": secret, "source": "env"}


async def _replit_connector_credentials():
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    repl_identity = os.environ.get("REPL_IDENTITY")
    web_repl_renewal = os.environ.get("WEB_REPL_RENEWAL")

    if not hostname or (not repl_identity and not web_repl_renewal):
        return None

    if repl_identity:
        token = f"repl {repl_identity}"
    else:
        token = f"depl {web_repl_renewal}"

    is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    environment = "production" if is_production else "development"

    import aiohttp
    url = f"https://{hostname}/api/v2/connection"
    params = {
        "include_secrets": "true",
        "connector_names": "stripe",
        "environment": environment,
    }
    headers = {
        "Accept": "application/json",
        "X-Replit-Token": token,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers) as resp:
            data = await resp.json()

    items = data.get("items", [])
    if not items:
        raise Exception(f"Stripe {environment} connection not found")

    settings = items[0].get("settings", {})
    publishable = settings.get("publishable")
    secret = settings.get("secret")

    if not publishable or not secret:
        raise Exception(f"Stripe {environment} keys not found")

    return {"publishable_key": publishable, "secret_key": secret, "source": "replit"}


async def get_stripe_credentials():
    env_creds = _env_credentials()
    if env_creds is not None:
        logger.info("Stripe credentials source=env")
        return env_creds

    replit_creds = await _replit_connector_credentials()
    if replit_creds is not None:
        logger.info("Stripe credentials source=replit")
        return replit_creds

    raise Exception(
        "Stripe is not configured. Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY."
    )


async def get_stripe_client():
    creds = await get_stripe_credentials()
    stripe.api_key = creds["secret_key"]
    return stripe


async def get_publishable_key():
    creds = await get_stripe_credentials()
    return creds["publishable_key"]
