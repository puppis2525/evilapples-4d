import os
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from stripe_client import get_stripe_client, get_publishable_key
from db import activate_premium, deactivate_premium

logger = logging.getLogger("stripe_routes")

router = APIRouter(prefix="/api/stripe")

PREMIUM_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")


@router.get("/config")
async def stripe_config():
    try:
        pk = await get_publishable_key()
        return JSONResponse(content={"publishable_key": pk})
    except Exception as e:
        logger.error(f"Failed to get Stripe config: {e}")
        return JSONResponse(content={"error": "Stripe not configured"}, status_code=500)


@router.post("/create-checkout")
async def create_checkout(request: Request):
    try:
        body = await request.json()
        guild_id = body.get("guild_id", "")
        guild_name = body.get("guild_name", "Unknown Server")
        user_id = body.get("user_id", "")
        user_name = body.get("user_name", "Unknown")

        if not guild_id or not user_id:
            return JSONResponse(content={"error": "Missing guild_id or user_id"}, status_code=400)

        stripe_mod = await get_stripe_client()

        host = request.headers.get("host", "localhost:5000")
        protocol = "https" if "replit" in host or "evilapples4d" in host else "http"
        base_url = f"{protocol}://{host}"

        session = stripe_mod.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price": PREMIUM_PRICE_ID,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{base_url}/premium/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/premium",
            metadata={
                "guild_id": guild_id,
                "guild_name": guild_name,
                "user_id": user_id,
                "user_name": user_name,
            },
        )

        return JSONResponse(content={"url": session.url})
    except Exception as e:
        logger.error(f"Checkout error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        stripe_mod = await get_stripe_client()
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

        if webhook_secret:
            event = stripe_mod.Webhook.construct_event(payload, sig, webhook_secret)
        else:
            import json
            event = json.loads(payload)

        event_type = event.get("type", "") if isinstance(event, dict) else event["type"]

        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            metadata = session.get("metadata", {})
            guild_id = metadata.get("guild_id", "")
            guild_name = metadata.get("guild_name", "Unknown")
            user_id = metadata.get("user_id", "")
            user_name = metadata.get("user_name", "Unknown")
            customer_id = session.get("customer", "")
            subscription_id = session.get("subscription", "")

            if guild_id:
                await activate_premium(
                    guild_id=guild_id,
                    guild_name=guild_name,
                    plan="pro",
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    activated_by=user_id,
                    activated_by_name=user_name,
                )
                logger.info(f"Premium activated for guild {guild_id} ({guild_name})")

        elif event_type == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            subscription_id = subscription.get("id", "")
            if subscription_id:
                await deactivate_premium(subscription_id)
                logger.info(f"Premium deactivated for subscription {subscription_id}")

        elif event_type == "customer.subscription.updated":
            subscription = event["data"]["object"]
            if subscription.get("status") in ("canceled", "unpaid", "past_due"):
                subscription_id = subscription.get("id", "")
                if subscription_id:
                    await deactivate_premium(subscription_id)
                    logger.info(f"Premium deactivated (status change) for subscription {subscription_id}")

        return JSONResponse(content={"received": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=400)


@router.get("/premium-status/{guild_id}")
async def premium_status(guild_id: str):
    from db import get_premium_server
    server = await get_premium_server(guild_id)
    if server and server.get("active"):
        return JSONResponse(content={
            "premium": True,
            "plan": server["plan"],
            "guild_name": server["guild_name"],
        })
    return JSONResponse(content={"premium": False})
