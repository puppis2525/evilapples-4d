"""Member display names without the privileged guild-members intent."""
import logging

logger = logging.getLogger("discord_names")


async def resolve_member_display_name(guild, user_id, fallback="Unknown") -> str:
    if not fallback:
        fallback = "Unknown"
    if guild is None or user_id is None:
        return fallback
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return fallback
    get_member = getattr(guild, "get_member", None)
    if callable(get_member):
        member = get_member(uid)
        if member is not None and getattr(member, "display_name", None):
            return member.display_name
    fetch_member = getattr(guild, "fetch_member", None)
    if callable(fetch_member):
        try:
            member = await fetch_member(uid)
            if member is not None and getattr(member, "display_name", None):
                return member.display_name
        except Exception as exc:
            logger.debug("member lookup failed for %s: %s", uid, type(exc).__name__)
    return fallback
