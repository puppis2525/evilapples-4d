"""Remember Stripe webhook event ids so retries do not apply a payment twice.

The database row is the cross-process record. Process memory covers a single
process when DATABASE_URL is unset or the table is unreachable. A failed
handler releases the id so Stripe can retry.
"""
import logging
import os

logger = logging.getLogger("stripe_events")

_done: set[str] = set()
_inflight: set[str] = set()


def reset_webhook_idempotency_for_tests() -> None:
    _done.clear()
    _inflight.clear()


async def _db_pool():
    if not os.environ.get("DATABASE_URL", "").strip():
        return None
    try:
        from db import get_db_pool
        return await get_db_pool()
    except Exception as exc:
        logger.warning("Stripe webhook idempotency database unavailable: %s", type(exc).__name__)
        return None


async def begin_webhook_event(event_id: str, event_type: str) -> str:
    """Return 'new' or 'duplicate'. Events without an id are treated as new."""
    if not event_id:
        return "new"
    if event_id in _done or event_id in _inflight:
        return "duplicate"

    pool = await _db_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO stripe_webhook_events (event_id, event_type)
                       VALUES ($1, $2)
                       ON CONFLICT (event_id) DO NOTHING
                       RETURNING event_id""",
                    event_id,
                    event_type or "",
                )
            if row is None:
                _done.add(event_id)
                return "duplicate"
            _inflight.add(event_id)
            return "new"
        except Exception as exc:
            logger.warning("Stripe webhook idempotency insert failed: %s", type(exc).__name__)

    _inflight.add(event_id)
    return "new"


async def complete_webhook_event(event_id: str) -> None:
    if not event_id:
        return
    _inflight.discard(event_id)
    _done.add(event_id)


async def release_webhook_event(event_id: str) -> None:
    """Drop a failed event so a Stripe retry can be processed."""
    if not event_id:
        return
    _inflight.discard(event_id)
    _done.discard(event_id)
    pool = await _db_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM stripe_webhook_events WHERE event_id = $1",
                event_id,
            )
    except Exception as exc:
        logger.warning("Could not release Stripe webhook %s: %s", event_id, type(exc).__name__)
