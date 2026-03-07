import os
import logging
import stripe

logger = logging.getLogger("stripe_client")


async def get_stripe_credentials():
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    repl_identity = os.environ.get("REPL_IDENTITY")
    web_repl_renewal = os.environ.get("WEB_REPL_RENEWAL")

    if repl_identity:
        token = f"repl {repl_identity}"
    elif web_repl_renewal:
        token = f"depl {web_repl_renewal}"
    else:
        raise Exception("No Replit identity token found")

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

    return {"publishable_key": publishable, "secret_key": secret}


async def get_stripe_client():
    creds = await get_stripe_credentials()
    stripe.api_key = creds["secret_key"]
    return stripe


async def get_publishable_key():
    creds = await get_stripe_credentials()
    return creds["publishable_key"]
