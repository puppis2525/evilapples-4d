import os
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from stripe_client import get_stripe_client, get_publishable_key
from db import activate_premium, deactivate_premium

logger = logging.getLogger("stripe_routes")

router = APIRouter(prefix="/api/stripe")

PREMIUM_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
PLATFORM_FEE_PERCENT = 10

if not PREMIUM_PRICE_ID:
    logger.warning("STRIPE_PRICE_ID not set — checkout will not work until it is configured")


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
    if not PREMIUM_PRICE_ID:
        return JSONResponse(
            content={"error": "Premium pricing not configured. Contact the admin."},
            status_code=503
        )

    try:
        body = await request.json()
        guild_id = body.get("guild_id", "")
        guild_name = body.get("guild_name", "Unknown Server")
        user_id = body.get("user_id", "")
        user_name = body.get("user_name", "Unknown")

        if not guild_id or not user_id:
            return JSONResponse(content={"error": "Missing guild_id or user_id"}, status_code=400)

        import re
        if not re.match(r'^\d{17,20}$', guild_id):
            return JSONResponse(content={"error": "Invalid guild_id format"}, status_code=400)

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
                "guild_name": guild_name[:256],
                "user_id": user_id,
                "user_name": user_name[:128],
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

        if not webhook_secret:
            logger.error("STRIPE_WEBHOOK_SECRET not set — rejecting webhook for security")
            return JSONResponse(content={"error": "Webhook not configured"}, status_code=500)

        event = stripe_mod.Webhook.construct_event(payload, sig, webhook_secret)

        event_type = event.get("type", "") if isinstance(event, dict) else event["type"]

        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            metadata = session.get("metadata", {})

            if metadata.get("type") == "tournament_entry":
                payment_status = session.get("payment_status", "")
                if payment_status == "paid":
                    tournament_id = metadata.get("tournament_id", "")
                    discord_user_id = metadata.get("discord_user_id", "")
                    entry_fee = int(metadata.get("entry_fee_cents", "0"))
                    session_id = session.get("id", "")
                    if tournament_id and discord_user_id:
                        from db import record_tournament_entry, join_tournament, get_tournament_entry, get_db_pool
                        existing = await get_tournament_entry(int(tournament_id), discord_user_id)
                        if existing and existing["status"] == "paid":
                            logger.info(f"Tournament entry already recorded: user={discord_user_id} tournament={tournament_id}")
                        else:
                            pool = await get_db_pool()
                            async with pool.acquire() as conn:
                                t = await conn.fetchrow("SELECT status FROM tournaments WHERE id = $1", int(tournament_id))
                            if t and t["status"] == "registration":
                                payment_intent_id = session.get("payment_intent", "")
                                await record_tournament_entry(
                                    int(tournament_id), discord_user_id, session_id, entry_fee,
                                    stripe_payment_intent_id=payment_intent_id
                                )
                                team_id = metadata.get("team_id", "")
                                if team_id:
                                    from db import join_tournament_team
                                    await join_tournament_team(int(tournament_id), int(team_id), discord_user_id, discord_user_id)
                                else:
                                    await join_tournament(int(tournament_id), discord_user_id, discord_user_id)
                                logger.info(f"Tournament entry paid: user={discord_user_id} tournament={tournament_id} fee=${entry_fee/100:.2f} pi={payment_intent_id}")
                            else:
                                logger.warning(f"Tournament entry webhook: tournament {tournament_id} not in registration (status={t['status'] if t else 'missing'})")
                else:
                    logger.warning(f"Tournament entry not paid: status={payment_status}")

            elif metadata.get("type") == "tournament_sponsor":
                payment_status = session.get("payment_status", "")
                if payment_status == "paid":
                    t_id = metadata.get("tournament_id", "")
                    sponsor_name = metadata.get("sponsor_name", "Sponsor")
                    logo_url = metadata.get("logo_url", "")
                    amount = int(metadata.get("amount_cents", "0"))
                    session_id = session.get("id", "")
                    if t_id:
                        from db import create_tournament_sponsor
                        await create_tournament_sponsor(int(t_id), sponsor_name, amount, stripe_session_id=session_id, logo_url=logo_url)
                        logger.info(f"Sponsor payment: {sponsor_name} for tournament {t_id} ${amount/100:.2f}")

            elif metadata.get("type") == "deck_purchase":
                payment_status = session.get("payment_status", "")
                if payment_status == "paid":
                    listing_ids_str = metadata.get("listing_ids", "") or metadata.get("listing_id", "")
                    discord_user_id = metadata.get("discord_user_id", "")
                    session_id = session.get("id", "")
                    if listing_ids_str and discord_user_id:
                        from db import record_store_purchase, fulfill_store_purchase
                        lid_list = [int(x.strip()) for x in listing_ids_str.split(",") if x.strip()]
                        for lid in lid_list:
                            was_new = await record_store_purchase(discord_user_id, lid, f"{session_id}_{lid}")
                            if was_new:
                                await fulfill_store_purchase(discord_user_id, lid)
                                logger.info(f"Deck purchase fulfilled: user={discord_user_id} listing={lid}")
                            else:
                                logger.info(f"Deck purchase already recorded: listing={lid} session={session_id}")
                else:
                    logger.warning(f"Deck purchase session not paid: status={payment_status}")
            else:
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
            status = subscription.get("status", "")
            subscription_id = subscription.get("id", "")

            if status in ("canceled", "unpaid", "past_due", "paused"):
                if subscription_id:
                    await deactivate_premium(subscription_id)
                    logger.info(f"Premium deactivated (status={status}) for subscription {subscription_id}")
            elif status == "active":
                if subscription_id:
                    await reactivate_premium(subscription_id)
                    logger.info(f"Premium reactivated for subscription {subscription_id}")

        return JSONResponse(content={"received": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=400)


async def reactivate_premium(subscription_id: str):
    from db import get_db_pool
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE premium_servers SET active = TRUE WHERE stripe_subscription_id = $1",
            subscription_id
        )


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


@router.post("/connect/onboard")
async def connect_onboard(request: Request):
    from discord_auth import get_discord_user
    from db import get_connected_account, save_connected_account

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"error": "Not logged in. Sign in with Discord first."}, status_code=401)

    discord_id = user["discord_id"]

    try:
        stripe_mod = await get_stripe_client()

        existing = await get_connected_account(discord_id)
        if existing and existing.get("onboarding_complete"):
            return JSONResponse(content={"already_connected": True, "message": "Your Stripe account is already connected."})

        if existing and existing.get("stripe_account_id"):
            account_id = existing["stripe_account_id"]
        else:
            account = stripe_mod.Account.create(
                type="express",
                metadata={"discord_user_id": discord_id},
            )
            account_id = account.id
            await save_connected_account(discord_id, account_id, False)

        host = request.headers.get("host", "localhost:5000")
        protocol = "https" if "replit" in host or "evilapples4d" in host else "http"
        base_url = f"{protocol}://{host}"

        link = stripe_mod.AccountLink.create(
            account=account_id,
            refresh_url=f"{base_url}/api/stripe/connect/onboard/refresh?account_id={account_id}",
            return_url=f"{base_url}/api/stripe/connect/onboard/return?account_id={account_id}",
            type="account_onboarding",
        )

        return JSONResponse(content={"url": link.url})
    except Exception as e:
        logger.error(f"Connect onboard error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/connect/onboard/return")
async def connect_onboard_return(request: Request, account_id: str = ""):
    from db import mark_account_onboarded

    if not account_id:
        return RedirectResponse(url="/howtoplay?connect_error=missing_account")

    try:
        stripe_mod = await get_stripe_client()
        account = stripe_mod.Account.retrieve(account_id)

        if account.get("charges_enabled"):
            await mark_account_onboarded(account_id)
            return RedirectResponse(url="/howtoplay?connect_success=1")
        else:
            return RedirectResponse(url="/howtoplay?connect_error=incomplete")
    except Exception as e:
        logger.error(f"Connect return error: {e}")
        return RedirectResponse(url="/howtoplay?connect_error=1")


@router.get("/connect/onboard/refresh")
async def connect_onboard_refresh(request: Request, account_id: str = ""):
    if not account_id:
        return RedirectResponse(url="/howtoplay?connect_error=1")

    try:
        stripe_mod = await get_stripe_client()

        host = request.headers.get("host", "localhost:5000")
        protocol = "https" if "replit" in host or "evilapples4d" in host else "http"
        base_url = f"{protocol}://{host}"

        link = stripe_mod.AccountLink.create(
            account=account_id,
            refresh_url=f"{base_url}/api/stripe/connect/onboard/refresh?account_id={account_id}",
            return_url=f"{base_url}/api/stripe/connect/onboard/return?account_id={account_id}",
            type="account_onboarding",
        )
        return RedirectResponse(url=link.url)
    except Exception as e:
        logger.error(f"Connect refresh error: {e}")
        return RedirectResponse(url="/howtoplay?connect_error=1")


@router.get("/connect/status")
async def connect_status(request: Request):
    from discord_auth import get_discord_user
    from db import get_connected_account

    user = get_discord_user(request)
    if not user:
        return JSONResponse(content={"connected": False, "logged_in": False})

    account = await get_connected_account(user["discord_id"])
    if account and account.get("onboarding_complete"):
        return JSONResponse(content={
            "connected": True,
            "logged_in": True,
            "stripe_account_id": account["stripe_account_id"],
        })
    return JSONResponse(content={"connected": False, "logged_in": True})


@router.post("/tournament/entry-checkout")
async def tournament_entry_checkout(request: Request):
    try:
        from discord_auth import get_discord_user
        body = await request.json()
        tournament_id = body.get("tournament_id")
        discord_user_id = body.get("discord_user_id", "")

        user = get_discord_user(request)
        if user and user.get("discord_id"):
            discord_user_id = user["discord_id"]

        if not tournament_id or not discord_user_id:
            return JSONResponse(content={"error": "Missing tournament_id or discord_user_id"}, status_code=400)

        from db import get_tournament_entry, get_tournament_player_count
        import re
        if not re.match(r'^\d{17,20}$', discord_user_id):
            return JSONResponse(content={"error": "Invalid discord_user_id"}, status_code=400)

        from db import get_db_pool
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            tournament = await conn.fetchrow("SELECT * FROM tournaments WHERE id = $1", int(tournament_id))

        if not tournament:
            return JSONResponse(content={"error": "Tournament not found"}, status_code=404)

        if tournament["status"] != "registration":
            return JSONResponse(content={"error": "Tournament is not accepting entries"}, status_code=400)

        entry_fee = tournament["entry_fee_cents"]
        if not entry_fee or entry_fee <= 0:
            return JSONResponse(content={"error": "This is a free tournament"}, status_code=400)

        max_players = tournament.get("max_players", 0)
        if max_players and max_players > 0:
            player_count = await get_tournament_player_count(int(tournament_id))
            if player_count >= max_players:
                return JSONResponse(content={"error": "Tournament is full"}, status_code=400)

        existing_entry = await get_tournament_entry(int(tournament_id), discord_user_id)
        if existing_entry and existing_entry["status"] == "paid":
            return JSONResponse(content={"error": "Already paid", "already_paid": True}, status_code=400)

        stripe_mod = await get_stripe_client()

        host = request.headers.get("host", "localhost:5000")
        protocol = "https" if "replit" in host or "evilapples4d" in host else "http"
        base_url = f"{protocol}://{host}"

        fee_percent = tournament.get("platform_fee_percent", PLATFORM_FEE_PERCENT)
        application_fee = int(entry_fee * fee_percent / 100)

        host_account = tournament.get("host_stripe_account_id", "")

        checkout_params = {
            "payment_method_types": ["card"],
            "line_items": [{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Tournament Entry: {tournament['name']}",
                        "description": f"Entry fee for {tournament['name']} ({tournament['rounds_total']} rounds)",
                    },
                    "unit_amount": entry_fee,
                },
                "quantity": 1,
            }],
            "mode": "payment",
            "success_url": f"{base_url}/howtoplay?tournament_paid=1&tid={tournament_id}",
            "cancel_url": f"{base_url}/howtoplay?tournament_cancelled=1",
            "metadata": {
                "type": "tournament_entry",
                "tournament_id": str(tournament_id),
                "discord_user_id": discord_user_id,
                "entry_fee_cents": str(entry_fee),
            },
        }

        if host_account:
            checkout_params["payment_intent_data"] = {
                "application_fee_amount": application_fee,
                "transfer_data": {
                    "destination": host_account,
                },
            }

        session = stripe_mod.checkout.Session.create(**checkout_params)

        return JSONResponse(content={"url": session.url})
    except Exception as e:
        logger.error(f"Tournament entry checkout error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


async def refund_tournament_entries(tournament_id: int):
    from db import get_paid_tournament_entries, mark_entry_refunded, mark_tournament_refunded

    entries = await get_paid_tournament_entries(tournament_id)
    if not entries:
        await mark_tournament_refunded(tournament_id)
        return {"refunded": 0, "failed": 0, "total_cents": 0, "errors": []}

    stripe_mod = await get_stripe_client()
    refunded = 0
    failed = 0
    total_refunded_cents = 0
    errors = []

    for entry in entries:
        pi_id = entry.get("stripe_payment_intent_id")
        user_id = entry["discord_user_id"]
        amount = entry.get("amount_cents", 0)

        if not pi_id:
            failed += 1
            errors.append({"user_id": user_id, "error": "No payment intent on record — manual refund required"})
            logger.warning(f"Refund: no payment_intent for entry user={user_id} tournament={tournament_id}, skipping")
            continue

        try:
            stripe_mod.Refund.create(payment_intent=pi_id)
            await mark_entry_refunded(tournament_id, user_id)
            refunded += 1
            total_refunded_cents += amount
            logger.info(f"Refund: tournament={tournament_id} user={user_id} amount=${amount/100:.2f} pi={pi_id}")
        except Exception as e:
            failed += 1
            err_msg = str(e)
            errors.append({"user_id": user_id, "error": err_msg})
            logger.error(f"Refund failed: tournament={tournament_id} user={user_id} pi={pi_id}: {err_msg}")

    await mark_tournament_refunded(tournament_id)
    return {"refunded": refunded, "failed": failed, "total_cents": total_refunded_cents, "errors": errors}


async def process_tournament_payout(tournament_id: int, winner_discord_id: str = None):
    """Process tournament completion and optional winner prize transfers.

    With destination charges, the host receives their share (minus platform fee)
    at payment time via Stripe Connect. This function:
    1. Logs the final tournament financials
    2. Optionally transfers a prize to the winner from the host's connected account
       using reverse transfers (host funds the winner prize from their received share)
    3. Marks the tournament payout status
    """
    from db import get_db_pool, get_paid_tournament_entries, update_tournament_payout, get_connected_account

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        tournament = await conn.fetchrow("SELECT * FROM tournaments WHERE id = $1", tournament_id)

    if not tournament:
        logger.error(f"Payout: tournament {tournament_id} not found")
        return

    if tournament.get("payout_status") == "completed":
        logger.info(f"Payout: tournament {tournament_id} already paid out, skipping")
        return

    host_account = tournament.get("host_stripe_account_id")
    if not host_account:
        logger.info(f"Payout: tournament {tournament_id} is free (no host account)")
        await update_tournament_payout(tournament_id, "completed")
        return

    entries = await get_paid_tournament_entries(tournament_id)
    total_collected = sum(e["amount_cents"] for e in entries)

    if total_collected <= 0:
        logger.info(f"Payout: no paid entries for tournament {tournament_id}")
        await update_tournament_payout(tournament_id, "completed")
        return

    fee_percent = tournament.get("platform_fee_percent", PLATFORM_FEE_PERCENT)
    platform_fee = int(total_collected * fee_percent / 100)
    host_received = total_collected - platform_fee

    logger.info(
        f"Payout: tournament {tournament_id} — collected ${total_collected/100:.2f}, "
        f"platform fee ${platform_fee/100:.2f}, host received ${host_received/100:.2f} "
        f"(via destination charges to {host_account})"
    )

    payout_ok = True

    if winner_discord_id:
        winner_account = await get_connected_account(winner_discord_id)
        if winner_account and winner_account.get("onboarding_complete") and winner_account.get("stripe_account_id"):
            try:
                stripe_mod = await get_stripe_client()

                entries_with_pi = [e for e in entries if e.get("stripe_payment_intent_id")]
                if entries_with_pi:
                    first_pi = entries_with_pi[0]["stripe_payment_intent_id"]
                    pi_obj = stripe_mod.PaymentIntent.retrieve(first_pi)
                    source_charge = pi_obj.get("latest_charge") if pi_obj else None
                else:
                    source_charge = None

                prize_amount = int(host_received * 0.5)
                if prize_amount > 0 and source_charge:
                    transfer = stripe_mod.Transfer.create(
                        amount=prize_amount,
                        currency="usd",
                        destination=winner_account["stripe_account_id"],
                        source_transaction=source_charge,
                        metadata={
                            "tournament_id": str(tournament_id),
                            "prize_type": "winner",
                            "winner_discord_id": winner_discord_id,
                        },
                    )
                    logger.info(
                        f"Prize payout: tournament {tournament_id} winner {winner_discord_id} "
                        f"received ${prize_amount/100:.2f} (transfer {transfer.id}, "
                        f"source_transaction={source_charge})"
                    )
                elif prize_amount > 0:
                    logger.warning(
                        f"Prize payout skipped for tournament {tournament_id}: "
                        f"no source charge available for winner transfer"
                    )
            except Exception as e:
                payout_ok = False
                logger.error(f"Winner prize transfer failed for tournament {tournament_id}: {e}")

    if payout_ok:
        await update_tournament_payout(tournament_id, "completed")
    else:
        await update_tournament_payout(tournament_id, "partial")
        logger.warning(f"Tournament {tournament_id} payout partially completed — winner transfer failed")
